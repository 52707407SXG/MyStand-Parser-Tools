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
BASE_URL = "http://127.0.0.1:{port}"


def request_json(path: str, payload: dict | None = None, timeout: int = 10, port: int = 8799, headers: dict | None = None) -> tuple[int, dict]:
    data = None
    request_headers = dict(headers or {})
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["content-type"] = "application/json"
        method = "POST"
    request = Request(f"{BASE_URL.format(port=port)}{path}", data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def wait_for_health(process: subprocess.Popen[str], port: int = 8799, headers: dict | None = None) -> None:
    deadline = time.time() + 20
    last_error = ""
    while time.time() < deadline:
        if process.poll() is not None:
            stdout = process.stdout.read() if process.stdout else ""
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"parser server exited early: {process.returncode}\nstdout={stdout}\nstderr={stderr}")
        try:
            status, payload = request_json("/health", timeout=2, port=port, headers=headers)
            if status == 200 and payload.get("ok"):
                return
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"parser server did not become healthy: {last_error}")


def start_server(port: int, extra_args: list[str] | None = None) -> subprocess.Popen[str]:
    command = [
        sys.executable,
        "-m",
        "mystand_parser_tools",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--timeout",
        "30",
        "--max-workers",
        "2",
        "--max-body-bytes",
        str(256 * 1024),
        "--job-ttl",
        "60",
        "--max-jobs",
        "1",
        "--job-history-ttl",
        "60",
        "--max-job-history",
        "1",
    ]
    command.extend(extra_args or [])
    return subprocess.Popen(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def stop_server(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def assert_command_fails(args: list[str], expected: str) -> None:
    completed = subprocess.run([sys.executable, "-m", "mystand_parser_tools", *args], cwd=ROOT, text=True, capture_output=True, timeout=10)
    assert completed.returncode != 0, completed.stdout
    assert expected in (completed.stderr + completed.stdout), completed.stderr + completed.stdout


def wait_for_job(job_id: str, port: int = 8799) -> dict:
    deadline = time.time() + 20
    while time.time() < deadline:
        status, job_payload = request_json(f"/jobs/{job_id}", port=port)
        assert status == 200, job_payload
        job = job_payload["job"]
        if job["status"] in {"done", "failed"}:
            assert job["status"] == "done", job
            assert job["result"]["content"]["markdown"], job
            return job
        time.sleep(0.25)
    raise AssertionError(f"job did not finish: {job_id}")


def main() -> int:
    assert_command_fails(["serve", "--host", "0.0.0.0", "--port", "8797"], "Refusing public parser bind")
    assert_command_fails(["serve", "--host", "0.0.0.0", "--port", "8797", "--allow-public-bind"], "requires MYSTAND_PARSER_HTTP_TOKEN")
    assert_command_fails(["serve", "--host", "127.0.0.1", "--port", "8797", "--require-token"], "require-token mode requires")

    process = start_server(8799)
    try:
        wait_for_health(process, port=8799)
        status, parsed = request_json("/parse", {"input": str(ROOT / "README.md")}, port=8799)
        assert status == 200, parsed
        assert parsed["ok"] is True, parsed
        assert parsed["result"]["content"]["markdown"], parsed

        status, failed_parse = request_json("/parse", {"input": str(ROOT / "missing-smoke-file.md")}, port=8799)
        assert status == 422, failed_parse
        assert failed_parse["error"] == "parser_errors", failed_parse
        assert failed_parse["message"], failed_parse

        status, queued = request_json("/jobs", {"input": str(ROOT / "README.md")}, port=8799)
        assert status == 202, queued
        job_id = queued["job"]["id"]
        wait_for_job(job_id, port=8799)

        status, queued_again = request_json("/jobs", {"input": str(ROOT / "README.md")}, port=8799)
        assert status == 202, queued_again
        second_job_id = queued_again["job"]["id"]
        wait_for_job(second_job_id, port=8799)
        status, old_history = request_json(f"/jobs/{job_id}", port=8799)
        assert status == 404, old_history
        assert old_history["error"] == "job_not_found", old_history
        status, second_history = request_json(f"/jobs/{second_job_id}", port=8799)
        assert status == 200, second_history

        status, too_large = request_json("/parse", {"input": "x" * (300 * 1024)}, port=8799)
        assert status == 413, too_large
        assert too_large["error"] == "body_too_large", too_large
    finally:
        stop_server(process)

    full_process = start_server(8801, ["--max-jobs", "0"])
    try:
        wait_for_health(full_process, port=8801)
        status, queue_full = request_json("/jobs", {"input": str(ROOT / "README.md")}, port=8801)
        assert status == 429, queue_full
        assert queue_full["error"] == "queue_full", queue_full
    finally:
        stop_server(full_process)

    token_process = start_server(8800, ["--host", "0.0.0.0", "--allow-public-bind", "--token", "parser-smoke-token", "--require-token"])
    try:
        wait_for_health(token_process, port=8800, headers={"authorization": "Bearer parser-smoke-token"})
        status, unauthorized = request_json("/health", port=8800)
        assert status == 401, unauthorized
        status, authorized = request_json("/health", port=8800, headers={"x-mystand-parser-token": "parser-smoke-token"})
        assert status == 200, authorized
        assert authorized["bind"]["tokenRequired"] is True, authorized
    finally:
        stop_server(token_process)

    print(json.dumps({"ok": True, "service": BASE_URL.format(port=8799)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
