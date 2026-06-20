"""Local HTTP service for MyStand Parser Tools."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .cli import parse_input


class ParserJobQueue:
    def __init__(self, max_workers: int = 2, timeout: int = 90):
        self.timeout = timeout
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def submit(self, input_uri: str) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        record = {
            "id": job_id,
            "input": input_uri,
            "status": "pending",
            "createdAt": now_ms(),
            "startedAt": 0,
            "finishedAt": 0,
            "result": None,
            "error": "",
        }
        with self.lock:
            self.jobs[job_id] = record
        self.executor.submit(self._run_job, job_id)
        return self.get(job_id) or record

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            record = self.jobs.get(job_id)
            return dict(record) if record else None

    def _run_job(self, job_id: str) -> None:
        self._patch(job_id, status="running", startedAt=now_ms())
        record = self.get(job_id)
        if not record:
            return
        output_path = Path(tempfile.gettempdir()) / f"mystand-parser-job-{job_id}.json"
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "mystand_parser_tools", "--input", record["input"], "--output", str(output_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            payload = {}
            if output_path.exists():
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            status = "done" if completed.returncode == 0 and not payload.get("errors") else "failed"
            self._patch(
                job_id,
                status=status,
                finishedAt=now_ms(),
                result=payload,
                error=(completed.stderr or "").strip(),
            )
        except subprocess.TimeoutExpired:
            self._patch(job_id, status="failed", finishedAt=now_ms(), error="parser job timeout")
        except Exception as exc:  # pragma: no cover - defensive HTTP service guard
            self._patch(job_id, status="failed", finishedAt=now_ms(), error=str(exc))
        finally:
            try:
                output_path.unlink()
            except OSError:
                pass

    def _patch(self, job_id: str, **patch: Any) -> None:
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id] = {**self.jobs[job_id], **patch}


def run_server(host: str = "127.0.0.1", port: int = 8790, max_workers: int = 2, timeout: int = 90) -> None:
    queue = ParserJobQueue(max_workers=max_workers, timeout=timeout)

    class Handler(BaseHTTPRequestHandler):
        server_version = "MyStandParserHTTP/0.1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path == "/health":
                self._json(200, {"ok": True, "name": "mystand-parser-tools", "jobs": {"timeout": timeout, "maxWorkers": max_workers}})
                return
            if self.path.startswith("/jobs/"):
                job_id = self.path.split("/jobs/", 1)[1].split("?", 1)[0]
                job = queue.get(job_id)
                self._json(200 if job else 404, {"ok": bool(job), "job": job, "error": "" if job else "job_not_found"})
                return
            self._json(404, {"ok": False, "error": "route_not_found"})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            payload = self._read_json()
            if payload is None:
                return
            if self.path == "/parse":
                input_uri = str(payload.get("input") or payload.get("path") or payload.get("url") or "").strip()
                if not input_uri:
                    self._json(400, {"ok": False, "error": "invalid_input", "message": "parse requires input/path/url"})
                    return
                result = parse_input(input_uri)
                self._json(200, result)
                return
            if self.path == "/jobs":
                input_uri = str(payload.get("input") or payload.get("path") or payload.get("url") or "").strip()
                if not input_uri:
                    self._json(400, {"ok": False, "error": "invalid_input", "message": "jobs requires input/path/url"})
                    return
                self._json(202, {"ok": True, "job": queue.submit(input_uri)})
                return
            self._json(404, {"ok": False, "error": "route_not_found"})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("content-length", "0") or "0")
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                return json.loads(body or "{}")
            except Exception as exc:
                self._json(400, {"ok": False, "error": "invalid_json", "message": str(exc)})
                return None

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"mystand-parser listening on http://{host}:{port}", flush=True)
    server.serve_forever()


def now_ms() -> int:
    return int(time.time() * 1000)
