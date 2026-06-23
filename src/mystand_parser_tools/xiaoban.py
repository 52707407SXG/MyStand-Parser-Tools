"""Xiaoban-Agent tool adapter for MyStand Parser Tools.

This module follows Xiaoban's native ``tools.registry`` contract without
importing Xiaoban at module import time.  Xiaoban only needs a tiny generated
tool file that calls :func:`register_with_xiaoban_registry`.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_PARSER_COMMAND = "/opt/mystand-parser-tools/bin/mystand-parser"
DEFAULT_TIMEOUT_SECONDS = 90


MYSTAND_PARSE_SCHEMA = {
    "name": "mystand_parse",
    "description": (
        "Parse a local file path or http(s) URL using MyStand Parser Tools. "
        "Supports common Office files, PDF, Markdown, text, CSV/JSON/XML, ZIP, "
        "images/OCR when available, WPS/KDocs links, Feishu/Lark document links, "
        "and WeChat article URLs. Returns My Stand standard parser JSON."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "Local file path or http(s) URL to parse.",
            },
            "max_chars": {
                "type": "integer",
                "description": (
                    "Maximum markdown characters to return in the tool response. "
                    "Use include_full_json=true only when the complete parser JSON is needed."
                ),
                "default": 20000,
            },
            "include_full_json": {
                "type": "boolean",
                "description": "Include the complete raw parser JSON result.",
                "default": False,
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Parser subprocess timeout in seconds.",
                "default": DEFAULT_TIMEOUT_SECONDS,
            },
        },
        "required": ["input"],
    },
}


def parser_command_parts() -> list[str]:
    raw = (os.environ.get("MYSTAND_PARSER_COMMAND") or DEFAULT_PARSER_COMMAND).strip()
    if not raw:
        return []
    try:
        parts = shlex.split(raw)
    except ValueError:
        return []
    if not parts:
        return []

    executable = parts[0]
    if Path(executable).exists():
        return parts
    resolved = shutil.which(executable)
    if resolved:
        return [resolved, *parts[1:]]
    return parts


def check_mystand_parser() -> bool:
    parts = parser_command_parts()
    if not parts:
        return False
    executable = parts[0]
    return Path(executable).exists() or shutil.which(executable) is not None


def _tool_error(message: str, **extra: Any) -> str:
    payload = {"error": str(message)}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _tool_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def summarize_parser_result(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
    content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
    markdown = str(content.get("markdown") or "")
    truncated = len(markdown) > max_chars
    if truncated:
        markdown = markdown[:max_chars]

    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []

    return {
        "success": not errors,
        "source": payload.get("source") or {},
        "tool": payload.get("tool") or "",
        "warnings": warnings,
        "errors": errors,
        "content": {
            "markdown": markdown,
            "truncated": truncated,
            "headings": content.get("headings") or [],
            "tables": content.get("tables") or [],
            "images": content.get("images") or [],
        },
        "assets": payload.get("assets") or [],
    }


def mystand_parse_tool_handler(args: dict[str, Any], **kwargs: Any) -> str:
    input_uri = str(args.get("input") or "").strip()
    if not input_uri:
        return _tool_error("input is required")

    command = parser_command_parts()
    if not command:
        return _tool_error("MYSTAND_PARSER_COMMAND is not configured correctly")

    executable = command[0]
    if not Path(executable).exists() and shutil.which(executable) is None:
        return _tool_error(f"MyStand parser command not found: {executable}")

    max_chars = _clamp_int(args.get("max_chars"), 20000, 1000, 50000)
    timeout_seconds = _clamp_int(args.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS, 5, 300)
    include_full_json = bool(args.get("include_full_json", False))

    output_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            prefix="xiaoban-mystand-parse-", suffix=".json", delete=False
        ) as tmp:
            output_path = tmp.name

        completed = subprocess.run(
            [*command, "--input", input_uri, "--output", output_path],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

        payload: dict[str, Any] = {}
        if output_path and Path(output_path).exists():
            raw_output = Path(output_path).read_text(encoding="utf-8")
            if raw_output.strip():
                payload = json.loads(raw_output)

        if completed.returncode != 0 and not payload:
            stderr = (completed.stderr or completed.stdout or "").strip()
            return _tool_error(
                "MyStand parser failed",
                return_code=completed.returncode,
                stderr=stderr[:2000],
            )

        if not isinstance(payload, dict) or not payload:
            return _tool_error("MyStand parser returned no JSON result")

        result = summarize_parser_result(payload, max_chars)
        result["return_code"] = completed.returncode
        if include_full_json:
            result["raw"] = payload
        return _tool_result(result)
    except subprocess.TimeoutExpired:
        return _tool_error(
            f"MyStand parser timed out after {timeout_seconds} seconds",
            timeout_seconds=timeout_seconds,
        )
    except json.JSONDecodeError as exc:
        return _tool_error(f"MyStand parser returned invalid JSON: {exc}")
    except Exception as exc:
        return _tool_error(f"MyStand parser error: {exc}")
    finally:
        if output_path:
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass


def register_with_xiaoban_registry(registry: Any, toolset: str = "file") -> None:
    registry.register(
        name="mystand_parse",
        toolset=toolset,
        schema=MYSTAND_PARSE_SCHEMA,
        handler=mystand_parse_tool_handler,
        check_fn=check_mystand_parser,
        requires_env=[],
        is_async=False,
        description="Parse files and URLs with MyStand Parser Tools",
        emoji="\U0001f4c4",
    )


def build_xiaoban_tool_module(parser_src: str | Path) -> str:
    parser_src_text = str(Path(parser_src).expanduser().resolve())
    return "\n".join(
        [
            '"""Generated Xiaoban tool bridge for MyStand Parser Tools."""',
            "",
            "from __future__ import annotations",
            "",
            "import os",
            "import sys",
            "",
            f"_PARSER_SRC = os.environ.get('MYSTAND_PARSER_PYTHONPATH', {parser_src_text!r})",
            "if _PARSER_SRC and _PARSER_SRC not in sys.path:",
            "    sys.path.insert(0, _PARSER_SRC)",
            "",
            "from mystand_parser_tools.xiaoban import (",
            "    MYSTAND_PARSE_SCHEMA,",
            "    check_mystand_parser,",
            "    mystand_parse_tool_handler,",
            ")",
            "from tools.registry import registry",
            "",
            "registry.register(",
            "    name='mystand_parse',",
            "    toolset='file',",
            "    schema=MYSTAND_PARSE_SCHEMA,",
            "    handler=mystand_parse_tool_handler,",
            "    check_fn=check_mystand_parser,",
            "    requires_env=[],",
            "    is_async=False,",
            "    description='Parse files and URLs with MyStand Parser Tools',",
            "    emoji='\\U0001f4c4',",
            ")",
            "",
        ]
    )
