#!/usr/bin/env python3
"""Smoke-test the MyStand Parser Tools local HTTP service."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8799"


def request_json(path: str, payload: dict | None = None, timeout: int = 10) -> tuple[int, dict]:
    data = None
    headers = {}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
        method = "POST"
    request = Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def wait_for_health(process: subprocess.Popen[str]) -> None:
    deadline = time.time() + 20
    last_error = ""
    while time.time() < deadline:
        if process.poll() is not None:
            stdout = process.stdout.read() if process.stdout else ""
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"parser server exited early: {process.returncode}\nstdout={stdout}\nstderr={stderr}")
        try:
            status, payload = request_json("/health", timeout=2)
            if status == 200 and payload.get("ok"):
                return
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"parser server did not become healthy: {last_error}")


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "mystand_parser_tools",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8799",
        "--timeout",
        "30",
        "--max-workers",
        "2",
        "--max-body-bytes",
        str(256 * 1024),
        "--job-ttl",
        "60",
    ]
    process = subprocess.Popen(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_for_health(process)
        status, parsed = request_json("/parse", {"input": str(ROOT / "README.md")})
        assert status == 200, parsed
        assert parsed["ok"] is True, parsed
        assert parsed["result"]["content"]["markdown"], parsed

        status, queued = request_json("/jobs", {"input": str(ROOT / "README.md")})
        assert status == 202, queued
        job_id = queued["job"]["id"]
        deadline = time.time() + 20
        while time.time() < deadline:
            status, job_payload = request_json(f"/jobs/{job_id}")
            assert status == 200, job_payload
            job = job_payload["job"]
            if job["status"] in {"done", "failed"}:
                assert job["status"] == "done", job
                assert job["result"]["content"]["markdown"], job
                break
            time.sleep(0.25)
        else:
            raise AssertionError(f"job did not finish: {job_id}")

        status, too_large = request_json("/parse", {"input": "x" * (300 * 1024)})
        assert status == 413, too_large
        assert too_large["error"] == "body_too_large", too_large
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    print(json.dumps({"ok": True, "service": BASE_URL}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
