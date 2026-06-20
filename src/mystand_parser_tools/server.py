"""Local HTTP service for MyStand Parser Tools."""

from __future__ import annotations

import json
import os
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


LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_MAX_BODY_BYTES = int(os.environ.get("MYSTAND_PARSER_HTTP_MAX_BODY_BYTES", str(1024 * 1024)))
DEFAULT_JOB_TTL_SECONDS = int(os.environ.get("MYSTAND_PARSER_JOB_TTL_SECONDS", str(24 * 60 * 60)))
DEFAULT_MAX_JOBS = int(os.environ.get("MYSTAND_PARSER_MAX_JOBS", "100"))


class ParserJobQueue:
    def __init__(self, max_workers: int = 2, timeout: int = 90, ttl_seconds: int = DEFAULT_JOB_TTL_SECONDS, max_jobs: int = DEFAULT_MAX_JOBS):
        self.timeout = timeout
        self.ttl_seconds = ttl_seconds
        self.max_jobs = max_jobs
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.sync_semaphore = threading.BoundedSemaphore(max_workers)
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def submit(self, input_uri: str) -> dict[str, Any]:
        self.cleanup()
        with self.lock:
            if len(self.jobs) >= self.max_jobs:
                return {
                    "ok": False,
                    "error": "queue_full",
                    "message": f"parser job queue is full: {len(self.jobs)} >= {self.max_jobs}",
                    "status": "rejected",
                }
        job_id = uuid.uuid4().hex
        record = {
            "ok": True,
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
        self.cleanup()
        with self.lock:
            record = self.jobs.get(job_id)
            return dict(record) if record else None

    def parse_sync(self, input_uri: str) -> dict[str, Any]:
        if not self.sync_semaphore.acquire(blocking=False):
            return {
                "ok": False,
                "error": "too_many_requests",
                "message": "同步解析并发已满，请改用 /jobs 异步队列。",
            }
        try:
            parsed = run_parser_process(input_uri, self.timeout)
            failure = summarize_parse_failure(parsed)
            return {
                "ok": parsed["ok"],
                "status": "done" if parsed["ok"] else "failed",
                "result": parsed.get("payload") or {},
                "error": "" if parsed["ok"] else failure["error"],
                "message": "" if parsed["ok"] else failure["message"],
                "stderr": parsed.get("error", ""),
            }
        finally:
            self.sync_semaphore.release()

    def cleanup(self) -> int:
        cutoff = now_ms() - max(1, self.ttl_seconds) * 1000
        removed = 0
        with self.lock:
            for job_id, record in list(self.jobs.items()):
                finished_at = int(record.get("finishedAt") or 0)
                created_at = int(record.get("createdAt") or 0)
                if finished_at and finished_at < cutoff:
                    self.jobs.pop(job_id, None)
                    removed += 1
                elif not finished_at and created_at and created_at < cutoff and record.get("status") in {"pending", "failed"}:
                    self.jobs.pop(job_id, None)
                    removed += 1
        return removed

    def _run_job(self, job_id: str) -> None:
        self._patch(job_id, status="running", startedAt=now_ms())
        record = self.get(job_id)
        if not record:
            return
        parsed = run_parser_process(record["input"], self.timeout)
        failure = summarize_parse_failure(parsed)
        self._patch(
            job_id,
            status="done" if parsed["ok"] else "failed",
            finishedAt=now_ms(),
            result=parsed.get("payload") or {},
            error="" if parsed["ok"] else failure["error"],
            message="" if parsed["ok"] else failure["message"],
            stderr=parsed.get("error", ""),
        )

    def _patch(self, job_id: str, **patch: Any) -> None:
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id] = {**self.jobs[job_id], **patch}


def run_server(
    host: str = "127.0.0.1",
    port: int = 8790,
    max_workers: int = 2,
    timeout: int = 90,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ttl_seconds: int = DEFAULT_JOB_TTL_SECONDS,
    max_jobs: int = DEFAULT_MAX_JOBS,
    http_token: str | None = None,
    allow_public_bind: bool = False,
    require_token: bool = False,
) -> None:
    http_token = os.environ.get("MYSTAND_PARSER_HTTP_TOKEN", "") if http_token is None else http_token
    require_token = require_token or os.environ.get("MYSTAND_PARSER_REQUIRE_TOKEN", "").lower() in {"1", "true", "yes", "on"}
    if not is_local_bind(host) and not allow_public_bind:
        raise SystemExit("Refusing public parser bind. Use --allow-public-bind intentionally.")
    if not is_local_bind(host) and not http_token:
        raise SystemExit("Public parser bind requires MYSTAND_PARSER_HTTP_TOKEN or --token.")
    if require_token and not http_token:
        raise SystemExit("Parser require-token mode requires MYSTAND_PARSER_HTTP_TOKEN or --token.")

    queue = ParserJobQueue(max_workers=max_workers, timeout=timeout, ttl_seconds=ttl_seconds, max_jobs=max_jobs)

    class Handler(BaseHTTPRequestHandler):
        server_version = "MyStandParserHTTP/0.1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._authorized():
                return
            if self.path == "/health":
                self._json(
                    200,
                    {
                        "ok": True,
                        "name": "mystand-parser-tools",
                        "bind": {"host": host, "port": port, "public": not is_local_bind(host), "tokenRequired": bool(http_token)},
                        "limits": {"maxBodyBytes": max_body_bytes},
                        "jobs": {"timeout": timeout, "maxWorkers": max_workers, "ttlSeconds": ttl_seconds, "maxJobs": max_jobs},
                    },
                )
                return
            if self.path.startswith("/jobs/"):
                job_id = self.path.split("/jobs/", 1)[1].split("?", 1)[0]
                job = queue.get(job_id)
                self._json(200 if job else 404, {"ok": bool(job), "job": job, "error": "" if job else "job_not_found"})
                return
            self._json(404, {"ok": False, "error": "route_not_found"})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._authorized():
                return
            payload = self._read_json()
            if payload is None:
                return
            if self.path == "/parse":
                input_uri = str(payload.get("input") or payload.get("path") or payload.get("url") or "").strip()
                if not input_uri:
                    self._json(400, {"ok": False, "error": "invalid_input", "message": "parse requires input/path/url"})
                    return
                result = queue.parse_sync(input_uri)
                status = 200 if result["ok"] else 429 if result.get("error") == "too_many_requests" else 422
                self._json(status, result)
                return
            if self.path == "/jobs":
                input_uri = str(payload.get("input") or payload.get("path") or payload.get("url") or "").strip()
                if not input_uri:
                    self._json(400, {"ok": False, "error": "invalid_input", "message": "jobs requires input/path/url"})
                    return
                queued = queue.submit(input_uri)
                if not queued.get("ok", True):
                    self._json(429, queued)
                    return
                self._json(202, {"ok": True, "job": queued})
                return
            self._json(404, {"ok": False, "error": "route_not_found"})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _authorized(self) -> bool:
            if http_token:
                bearer = self.headers.get("authorization", "")
                token_header = self.headers.get("x-mystand-parser-token", "")
                expected = f"Bearer {http_token}"
                if bearer == expected or token_header == http_token:
                    return True
                self._json(401, {"ok": False, "error": "unauthorized", "message": "parser HTTP token required"})
                return False
            if is_local_client(self.client_address[0]):
                return True
            self._json(403, {"ok": False, "error": "forbidden", "message": "empty parser HTTP token only allows localhost clients"})
            return False

        def _read_json(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("content-length", "0") or "0")
                if length > max_body_bytes:
                    self._json(413, {"ok": False, "error": "body_too_large", "message": f"request body exceeds {max_body_bytes} bytes"})
                    return None
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


def run_parser_process(input_uri: str, timeout: int) -> dict[str, Any]:
    output_path = Path(tempfile.gettempdir()) / f"mystand-parser-{uuid.uuid4().hex}.json"
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "mystand_parser_tools", "--input", input_uri, "--output", str(output_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        payload = {}
        if output_path.exists():
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        return {
            "ok": completed.returncode == 0 and not payload.get("errors"),
            "payload": payload,
            "error": (completed.stderr or "").strip(),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "payload": {}, "error": "parser job timeout"}
    except Exception as exc:  # pragma: no cover - defensive HTTP service guard
        return {"ok": False, "payload": {}, "error": str(exc)}
    finally:
        try:
            output_path.unlink()
        except OSError:
            pass


def summarize_parse_failure(parsed: dict[str, Any]) -> dict[str, str]:
    payload = parsed.get("payload") or {}
    errors = payload.get("errors") if isinstance(payload, dict) else []
    if isinstance(errors, list) and errors:
        return {"error": "parser_errors", "message": str(errors[0])}
    stderr = str(parsed.get("error") or "").strip()
    if stderr:
        return {"error": "parser_process_failed", "message": stderr}
    return {"error": "parser_failed", "message": "parser failed without a structured error"}


def is_local_bind(host: str) -> bool:
    return host in LOCAL_BIND_HOSTS


def is_local_client(host: str) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def now_ms() -> int:
    return int(time.time() * 1000)
