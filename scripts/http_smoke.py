#!/usr/bin/env python3
"""Smoke-test the MyStand Parser Tools local HTTP service."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mystand_parser_tools import cli as parser_cli
from mystand_parser_tools import server as parser_server


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


class _HeaderStub:
    def get_content_charset(self) -> str:
        return "utf-8"


class _ResponseStub:
    headers = _HeaderStub()

    def __init__(self, final_url: str, body: str = "<html><body>ok</body></html>"):
        self.final_url = final_url
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def geturl(self) -> str:
        return self.final_url

    def read(self) -> bytes:
        return self.body


class _OpenerStub:
    def __init__(self, final_url: str):
        self.final_url = final_url

    def open(self, request: Request, timeout: int = 20) -> _ResponseStub:
        return _ResponseStub(self.final_url)


def smoke_url_final_guards() -> None:
    try:
        parser_cli.SafeRedirectHandler().redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/internal")
    except ValueError as exc:
        assert "已拦截" in str(exc), exc
    else:
        raise AssertionError("redirect to localhost was not blocked")

    original_getaddrinfo = parser_cli.socket.getaddrinfo
    parser_cli.socket.getaddrinfo = lambda *args, **kwargs: [(None, None, None, "", ("93.184.216.34", 443))]
    try:
        try:
            parser_cli.fetch_url("https://example.com/article", opener=_OpenerStub("http://127.0.0.1/private"))
        except ValueError as exc:
            assert "已拦截" in str(exc), exc
        else:
            raise AssertionError("fetch_url accepted blocked final URL")
    finally:
        parser_cli.socket.getaddrinfo = original_getaddrinfo


def smoke_agent_browser_final_guard() -> None:
    result = parser_cli.build_result("https://example.com/dynamic")
    original_command = parser_cli.AGENT_BROWSER_COMMAND
    original_run = parser_cli.subprocess.run
    with tempfile.NamedTemporaryFile("w", delete=False) as command_file:
        command_file.write("#!/usr/bin/env node\n")
        command_path = command_file.name
    parser_cli.AGENT_BROWSER_COMMAND = command_path
    parser_cli.subprocess.run = lambda *args, **kwargs: types.SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"finalUrl": "http://127.0.0.1/browser-final", "text": "secret"}),
        stderr="",
    )
    try:
        text = parser_cli.parse_with_agent_browser("https://example.com/dynamic", result)
        assert text == "", text
        assert any("最终 URL" in item and "已拦截" in item for item in result["errors"]), result
    finally:
        parser_cli.AGENT_BROWSER_COMMAND = original_command
        parser_cli.subprocess.run = original_run
        Path(command_path).unlink(missing_ok=True)


def smoke_job_queue_submit_lock() -> None:
    queue = parser_server.ParserJobQueue(max_workers=1, max_jobs=1, timeout=1, ttl_seconds=60, max_job_history=10)
    original_uuid4 = parser_server.uuid.uuid4
    original_run_parser_process = parser_server.run_parser_process
    barrier = threading.Barrier(8)
    counter_lock = threading.Lock()
    counter = {"value": 0}

    def fake_uuid4():
        barrier.wait(timeout=5)
        with counter_lock:
            counter["value"] += 1
            value = counter["value"]
        return types.SimpleNamespace(hex=f"job-{value}")

    def slow_parser(input_uri: str, timeout: int) -> dict:
        time.sleep(0.25)
        return {"ok": True, "payload": {"content": {"markdown": "ok"}, "errors": []}, "error": ""}

    parser_server.uuid.uuid4 = fake_uuid4
    parser_server.run_parser_process = slow_parser
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda index: queue.submit(f"input-{index}"), range(8)))
        accepted = [item for item in results if item.get("ok", True)]
        rejected = [item for item in results if not item.get("ok", True)]
        assert len(accepted) == 1, results
        assert len(rejected) == 7, results
        assert all(item["error"] == "queue_full" for item in rejected), results
        assert queue.stats()["active"] <= 1, queue.stats()
    finally:
        parser_server.uuid.uuid4 = original_uuid4
        parser_server.run_parser_process = original_run_parser_process
        queue.executor.shutdown(wait=True, cancel_futures=True)


def main() -> int:
    smoke_url_final_guards()
    smoke_agent_browser_final_guard()
    smoke_job_queue_submit_lock()

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
