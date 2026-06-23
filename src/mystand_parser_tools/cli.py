#!/usr/bin/env python3
"""My Stand unified parser entrypoint.

This script is intentionally small and boring: it routes common files and URLs
to lightweight local parsers, then returns one stable JSON shape for Agents.
Heavy parsing such as MinerU, Docling OCR, DWG conversion, and vision models is
registered in TOOLS as remote-worker work, not forced onto the main website box.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import ipaddress
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.dom import minidom


SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
PLAIN_TEXT_EXTS = {".md", ".markdown", ".txt", ".log"}
WORKER_REQUIRED_EXTS = {".dwg", ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav", ".m4a"}
MAX_FILE_BYTES = int(os.environ.get("MYSTAND_PARSER_MAX_FILE_BYTES", str(50 * 1024 * 1024)))
MAX_PDF_BYTES = int(os.environ.get("MYSTAND_PARSER_MAX_PDF_BYTES", str(30 * 1024 * 1024)))
MAX_URL_BYTES = int(os.environ.get("MYSTAND_PARSER_MAX_URL_BYTES", str(10 * 1024 * 1024)))
ZIP_MAX_TOTAL_BYTES = int(os.environ.get("MYSTAND_PARSER_ZIP_MAX_TOTAL_BYTES", str(80 * 1024 * 1024)))
ZIP_MAX_FILES = int(os.environ.get("MYSTAND_PARSER_ZIP_MAX_FILES", "200"))
BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".lan")
URL_AUTH_PATTERNS = [
    "secondary verification",
    "u2f",
    "sign in",
    "signin",
    "验证",
    "请先登录",
    "need to sign in",
    "set access permissions",
]
URL_SOFT_LOGIN_PATTERNS = ["login", "登录", "登录/注册"]
AGENT_BROWSER_COMMAND = os.environ.get("MYSTAND_AGENT_BROWSER_COMMAND", "/opt/agent-tools/browser/agent-browser.mjs")


def classify_url_source(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or ""
    if "feishu.cn" in host or "larksuite.com" in host:
        return "feishu_base" if "/base/" in path else "feishu"
    if "kdocs.cn" in host or "wps.cn" in host:
        return "wps"
    if "mp.weixin.qq.com" in host:
        return "wechat_article"
    return "url"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def build_result(input_uri: str) -> dict[str, Any]:
    parsed = urlparse(input_uri)
    is_url = parsed.scheme in {"http", "https"}
    title = Path(parsed.path if is_url else input_uri).name or input_uri
    return {
        "source": {
            "type": classify_url_source(input_uri) if is_url else "file",
            "uri": input_uri,
            "title": title,
            "mime": mimetypes.guess_type(input_uri)[0] or "",
            "syncedAt": now_iso(),
        },
        "content": {
            "markdown": "",
            "blocks": [],
            "headings": [],
            "tables": [],
            "images": [],
        },
        "assets": [],
        "warnings": [],
        "errors": [],
        "tool": "",
    }


def finalize(result: dict[str, Any], markdown: str, tool: str) -> dict[str, Any]:
    markdown = markdown or ""
    result["content"]["markdown"] = markdown
    result["content"]["headings"] = extract_headings(markdown)
    result["content"]["tables"] = extract_tables(markdown)
    result["content"]["blocks"] = extract_blocks(markdown)
    result["tool"] = tool
    if not markdown.strip() and not result["errors"]:
        result["warnings"].append("解析完成但未提取到正文。")
    return result


def extract_headings(markdown: str) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    for line_no, line in enumerate(markdown.splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append(
                {
                    "level": len(match.group(1)),
                    "text": match.group(2).strip(),
                    "line": line_no,
                }
            )
    return headings


def extract_tables(markdown: str) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    current: list[str] = []
    start_line = 0
    for line_no, line in enumerate(markdown.splitlines(), start=1):
        if "|" in line and line.strip().startswith("|"):
            if not current:
                start_line = line_no
            current.append(line)
        elif current:
            tables.append({"line": start_line, "markdown": "\n".join(current)})
            current = []
    if current:
        tables.append({"line": start_line, "markdown": "\n".join(current)})
    return tables


def extract_blocks(markdown: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for line_no, line in enumerate(markdown.splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        block_type = "paragraph"
        if text.startswith("#"):
            block_type = "heading"
        elif text.startswith(("- ", "* ", "1. ")):
            block_type = "list"
        elif text.startswith(">"):
            block_type = "quote"
        elif text.startswith("|"):
            block_type = "table"
        blocks.append({"type": block_type, "text": text, "line": line_no})
    return blocks[:500]


def read_text_file(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def parse_csv(path: Path) -> str:
    text = read_text_file(path)
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return ""
    widths = [max(len(row[i]) if i < len(row) else 0 for row in rows) for i in range(max(map(len, rows)))]
    normalized = [row + [""] * (len(widths) - len(row)) for row in rows]

    def row_to_md(row: list[str]) -> str:
        cells = [cell.replace("|", "\\|").strip() for cell in row]
        return "| " + " | ".join(cells) + " |"

    header = row_to_md(normalized[0])
    divider = "| " + " | ".join("---" for _ in widths) + " |"
    body = [row_to_md(row) for row in normalized[1:]]
    return "\n".join([header, divider, *body])


def parse_json_file(path: Path) -> str:
    data = json.loads(read_text_file(path))
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"


def parse_xml_file(path: Path, result: dict[str, Any]) -> str:
    text = read_text_file(path)
    try:
        parsed = minidom.parseString(text.encode("utf-8"))
        pretty = parsed.toprettyxml(indent="  ")
    except Exception as exc:
        result["warnings"].append(f"XML 格式化失败，已返回原文：{exc}")
        pretty = text
    return "```xml\n" + pretty.strip()[:80_000] + "\n```"


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        validate_url_allowed(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(url: str, opener: Any | None = None) -> str:
    validate_url_allowed(url)
    request = Request(url, headers={"User-Agent": "MyStandParser/1.0"})
    url_opener = opener or build_opener(SafeRedirectHandler())
    with url_opener.open(request, timeout=20) as response:
        final_url = response.geturl() if hasattr(response, "geturl") else ""
        if final_url:
            validate_url_allowed(str(final_url))
        charset = response.headers.get_content_charset() or "utf-8"
        content_length = get_header(response.headers, "content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_URL_BYTES:
            raise ValueError(f"URL 响应体超过最大限制：{content_length} > {MAX_URL_BYTES}")
        data = response.read(MAX_URL_BYTES + 1)
        if len(data) > MAX_URL_BYTES:
            raise ValueError(f"URL 响应体超过最大限制：{len(data)} > {MAX_URL_BYTES}")
        return data.decode(charset, errors="replace")


def parse_html_text(html: str, result: dict[str, Any]) -> str:
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_links=True,
        )
        if extracted and extracted.strip():
            return extracted
    except Exception as exc:  # pragma: no cover - external library fallback
        result["warnings"].append(f"Trafilatura 解析失败，已尝试后备：{exc}")

    try:
        import html2text

        parser = html2text.HTML2Text()
        parser.ignore_images = False
        parser.ignore_links = False
        return parser.handle(html)
    except Exception as exc:  # pragma: no cover - external library fallback
        result["errors"].append(f"HTML 后备解析失败：{exc}")
        return ""


def parse_wechat_article(url: str, result: dict[str, Any]) -> str:
    try:
        from wechat_article_parser import WeChatVerifyError, parse
    except Exception as exc:
        result["warnings"].append(f"wechat-article-parser 未可用，已改用普通网页解析：{exc}")
        return ""

    timeout = int(os.environ.get("MYSTAND_WECHAT_ARTICLE_TIMEOUT", "20") or "20")
    proxy = os.environ.get("MYSTAND_WECHAT_ARTICLE_PROXY") or None
    try:
        article = parse(url, timeout=timeout, proxy=proxy)
    except WeChatVerifyError:
        result["errors"].append("微信返回验证码/人机验证页面，不是文章正文；请稍后重试、更换出口 IP，或使用已授权快照。")
        return ""
    except Exception as exc:
        result["warnings"].append(f"微信公众号专用解析失败，已改用普通网页解析：{exc}")
        return ""

    markdown = str(getattr(article, "article_markdown", "") or "").strip()
    if getattr(article, "article_title", ""):
        result["source"]["title"] = article.article_title
    result["source"]["accountName"] = getattr(article, "mp_name", "") or ""
    result["source"]["accountAlias"] = getattr(article, "mp_alias", "") or ""
    result["source"]["accountType"] = str(getattr(article, "mp_account_type", "") or "")
    result["source"]["articleId"] = getattr(article, "article_id", "") or ""
    result["source"]["publishTime"] = getattr(article, "article_publish_time", 0) or 0
    result["source"]["description"] = getattr(article, "article_description", "") or ""
    result["assets"].extend({"type": "image", "url": image_url, "source": "wechat-article"} for image_url in getattr(article, "images", []) or [])
    if not getattr(article, "is_valid", False):
        result["warnings"].append("微信公众号文章已读取，但部分元数据不完整；请以正文和 warnings 为准。")
    if not markdown:
        result["errors"].append("微信公众号专用解析未提取到正文。")
    return markdown


def quality_errors_for_url(markdown: str, source_type: str = "url") -> list[str]:
    visible_text = re.sub(r"\s+", "", markdown or "")
    lower_markdown = (markdown or "").lower()
    compact = re.sub(r"\s+", "", markdown or "")
    if len(visible_text) < 20:
        return ["URL 解析结果正文过短，可能是空壳页面、登录页或前端动态文档；需要平台连接器、导出文件或已授权快照。"]
    if any(pattern in lower_markdown for pattern in URL_AUTH_PATTERNS):
        return ["URL 解析结果疑似登录/验证页面，不是目标正文；需要授权连接器或已登录快照。"]
    if source_type == "feishu_base":
        if "## 截图 OCR 可见文本" in markdown:
            return []
        shell_terms = ["数据表", "仪表盘", "字段配置", "视图配置", "添加记录", "新建视图"]
        if sum(1 for term in shell_terms if term in markdown) >= 4 and "|" not in markdown and len(visible_text) < 800:
            return ["飞书多维表格只读取到页面外壳和工具栏，未读取到表格行数据；需要多维表格专用读取器或已授权浏览器表格快照。"]
    if len(visible_text) < 160 and any(pattern in lower_markdown or pattern in compact for pattern in URL_SOFT_LOGIN_PATTERNS):
        return ["URL 解析结果疑似登录/验证页面，不是目标正文；需要授权连接器或已登录快照。"]
    return []


def ocr_screenshot(path: str, result: dict[str, Any]) -> str:
    if not path:
        return ""
    cmd = ["tesseract", path, "stdout", "-l", "chi_sim+eng"]
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=45)
    except FileNotFoundError:
        result["warnings"].append("未安装 tesseract，无法对浏览器截图做 OCR。")
        return ""
    except subprocess.TimeoutExpired:
        result["warnings"].append("浏览器截图 OCR 超时。")
        return ""
    if completed.returncode != 0:
        result["warnings"].append((completed.stderr or "浏览器截图 OCR 失败。").strip())
        return ""
    text = completed.stdout.strip()
    if text:
        result["content"]["images"].append({"path": path, "ocr": True, "source": "agent-browser-screenshot"})
    return text


def parse_with_agent_browser(url: str, result: dict[str, Any], *, capture_screenshot: bool = False) -> str:
    if not Path(AGENT_BROWSER_COMMAND).exists():
        result["warnings"].append(f"浏览器读取工具不存在：{AGENT_BROWSER_COMMAND}")
        return ""
    cmd = [AGENT_BROWSER_COMMAND, url, "--json", "--wait=5000"]
    if not capture_screenshot:
        cmd.append("--no-screenshot")
    env = {**os.environ, "AGENT_BROWSER_OUTPUT_DIR": os.environ.get("AGENT_BROWSER_OUTPUT_DIR", "/tmp/mystand-agent-browser-output")}
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=45, env=env)
    except subprocess.TimeoutExpired:
        result["warnings"].append("浏览器读取超时。")
        return ""
    except Exception as exc:
        result["warnings"].append(f"浏览器读取失败：{exc}")
        return ""

    if completed.returncode != 0 and not completed.stdout.strip():
        result["warnings"].append((completed.stderr or "浏览器读取失败。").strip())
        return ""

    try:
        payload = json.loads(completed.stdout)
    except Exception as exc:
        result["warnings"].append(f"浏览器读取输出不是有效 JSON：{exc}")
        return ""

    if payload.get("title"):
        result["source"]["title"] = payload.get("title")
    if payload.get("finalUrl"):
        final_url = str(payload.get("finalUrl") or "")
        try:
            validate_url_allowed(final_url)
        except ValueError as exc:
            result["errors"].append(f"浏览器最终 URL 被安全策略拦截：{exc}")
            return ""
        result["source"]["finalUrl"] = final_url
    if payload.get("status"):
        result["source"]["status"] = payload.get("status")

    text = str(payload.get("text") or "").strip()
    screenshot_path = str(payload.get("screenshotPath") or "")
    if capture_screenshot and screenshot_path:
        result["assets"].append({"type": "screenshot", "path": screenshot_path})
        ocr_text = ocr_screenshot(screenshot_path, result)
        if ocr_text:
            result["warnings"].append("飞书多维表格已用浏览器截图 OCR 提取可见网格文本；该结果不是全量结构化表格。")
            text = "\n\n".join(part for part in [text, "## 截图 OCR 可见文本\n\n" + ocr_text] if part)
    if not text:
        error = payload.get("error") or "浏览器打开页面后未提取到正文。"
        result["warnings"].append(str(error))
        return ""
    return text


def parse_with_markitdown(path: Path, result: dict[str, Any]) -> str:
    try:
        from markitdown import MarkItDown

        converter = MarkItDown(enable_plugins=False)
        converted = converter.convert(str(path))
        text = getattr(converted, "text_content", "") or ""
        if text.strip():
            return text
        result["warnings"].append("MarkItDown 未提取到正文。")
        return text
    except Exception as exc:
        result["errors"].append(f"MarkItDown 解析失败：{exc}")
        return ""


def parse_image_ocr(path: Path, result: dict[str, Any]) -> str:
    cmd = [
        "tesseract",
        str(path),
        "stdout",
        "-l",
        "chi_sim+eng",
    ]
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        result["errors"].append("未安装 tesseract，本机无法 OCR；请转远程 OCR Worker。")
        return ""
    except subprocess.TimeoutExpired:
        result["errors"].append("Tesseract OCR 超时；请转远程 OCR Worker。")
        return ""

    if completed.returncode != 0:
        result["errors"].append((completed.stderr or "Tesseract OCR 失败。").strip())
        return ""
    result["content"]["images"].append({"path": str(path), "ocr": True})
    return completed.stdout.strip()


def parse_dxf(path: Path, result: dict[str, Any]) -> str:
    try:
        import ezdxf
    except Exception as exc:
        result["errors"].append(f"ezdxf 未可用：{exc}")
        return ""

    try:
        doc = ezdxf.readfile(path)
    except Exception as exc:
        result["errors"].append(f"DXF 读取失败：{exc}")
        return ""

    msp = doc.modelspace()
    layers = sorted({entity.dxf.layer for entity in msp if hasattr(entity.dxf, "layer")})
    counts: dict[str, int] = {}
    texts: list[str] = []
    for entity in msp:
        entity_type = entity.dxftype()
        counts[entity_type] = counts.get(entity_type, 0) + 1
        if entity_type in {"TEXT", "MTEXT"}:
            text = getattr(entity, "text", "")
            if not text and hasattr(entity, "plain_text"):
                text = entity.plain_text()
            if text:
                texts.append(str(text))

    payload = {
        "layers": layers,
        "entityCounts": counts,
        "texts": texts,
    }
    result["assets"].append({"type": "cad", "path": str(path), "format": "dxf"})
    return "# DXF 解析结果\n\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"


def parse_zip(path: Path, result: dict[str, Any]) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            zip_errors = validate_zip_members(archive)
            if zip_errors:
                result["errors"].extend(zip_errors)
                return ""
            result["assets"].append({"type": "archive", "path": str(path), "entries": names})
            lines = ["# ZIP 资料包", "", "## 文件列表", ""]
            lines.extend(f"- {name}" for name in names)
            text_names = [n for n in names if Path(n).suffix.lower() in PLAIN_TEXT_EXTS | {".csv", ".json"}]
            if text_names:
                lines.extend(["", "## 可直接读取的文本", ""])
            for name in text_names[:20]:
                try:
                    raw = archive.read(name)
                    content = raw.decode("utf-8", errors="replace")
                    lines.extend([f"### {name}", "", content[:5000], ""])
                except Exception as exc:
                    result["warnings"].append(f"ZIP 内文件 {name} 读取失败：{exc}")
            return "\n".join(lines)
    except Exception as exc:
        result["errors"].append(f"ZIP 读取失败：{exc}")
        return ""


def parse_path(path: Path, result: dict[str, Any]) -> dict[str, Any]:
    root_errors = validate_allowed_file_roots(path)
    if root_errors:
        result["errors"].extend(root_errors)
        return finalize(result, "", "allowed-root-guard")
    if not path.exists():
        result["errors"].append(f"文件不存在：{path}")
        return finalize(result, "", "missing-file")

    ext = path.suffix.lower()
    guard_errors = validate_local_file(path, ext)
    if guard_errors:
        result["errors"].extend(guard_errors)
        return finalize(result, "", "worker-required" if any("worker_required" in item for item in guard_errors) else "security-guard")
    if ext in PLAIN_TEXT_EXTS:
        return finalize(result, read_text_file(path), "native-text")
    if ext == ".csv":
        return finalize(result, parse_csv(path), "native-csv")
    if ext == ".json":
        return finalize(result, parse_json_file(path), "native-json")
    if ext == ".xml":
        return finalize(result, parse_xml_file(path, result), "native-xml")
    if ext in {".html", ".htm"}:
        return finalize(result, parse_html_text(read_text_file(path), result), "trafilatura/html2text")
    if ext in SUPPORTED_IMAGE_EXTS:
        return finalize(result, parse_image_ocr(path, result), "tesseract")
    if ext == ".dxf":
        return finalize(result, parse_dxf(path, result), "ezdxf")
    if ext == ".pdf":
        pdf_text = parse_with_markitdown(path, result)
        if not pdf_text.strip() and not result["errors"]:
            result["errors"].append("worker_required: PDF 未提取到文本，可能是扫描 PDF 或图片型文件，请交给 OCR/Docling Worker。")
        return finalize(result, pdf_text, "markitdown")
    if ext == ".dwg":
        result["errors"].append("worker_required: DWG 需要远程 CAD Worker 先转换为 DXF，本机轻解析层不直接解析 DWG。")
        return finalize(result, "", "remote-cad-required")
    if ext == ".zip":
        return finalize(result, parse_zip(path, result), "native-zip")

    return finalize(result, parse_with_markitdown(path, result), "markitdown")


def parse_url(url: str, result: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_url_allowed(url)
    except ValueError as exc:
        result["errors"].append(str(exc))
        return finalize(result, "", "url-security-guard")
    source_type = str(result.get("source", {}).get("type") or "url")
    if source_type == "wechat_article":
        wechat_markdown = parse_wechat_article(url, result)
        if wechat_markdown or result["errors"]:
            return finalize(result, wechat_markdown, "wechat-article-parser")

    fetch_errors: list[str] = []
    try:
        html = fetch_url(url)
    except Exception as exc:
        fetch_errors.append(f"URL 读取失败：{exc}")
        html = ""

    markdown = parse_html_text(html, result) if html else ""
    direct_quality_errors = quality_errors_for_url(markdown, source_type) if html else []
    if not html or direct_quality_errors:
        browser_markdown = parse_with_agent_browser(url, result, capture_screenshot=source_type == "feishu_base")
        browser_quality_errors = quality_errors_for_url(browser_markdown, source_type)
        if browser_markdown and not browser_quality_errors:
            if fetch_errors or direct_quality_errors:
                result["warnings"].append("普通 URL 下载未拿到可靠正文，已自动改用服务器浏览器读取成功。")
            return finalize(result, browser_markdown, "agent-browser")
        if browser_markdown:
            markdown = browser_markdown
            result["errors"].extend(browser_quality_errors)
            if direct_quality_errors:
                result["warnings"].extend(direct_quality_errors)
            if fetch_errors:
                result["warnings"].extend(fetch_errors)
            return finalize(result, markdown, "agent-browser")
        result["errors"].extend(fetch_errors or direct_quality_errors)
        return finalize(result, markdown, "trafilatura/html2text+agent-browser")

    markdown = parse_html_text(html, result)
    result["errors"].extend(quality_errors_for_url(markdown, str(result.get("source", {}).get("type") or "url")))
    return finalize(result, markdown, "trafilatura/html2text")


def parse_input(input_uri: str) -> dict[str, Any]:
    result = build_result(input_uri)
    parsed = urlparse(input_uri)
    if parsed.scheme in {"http", "https"}:
        return parse_url(input_uri, result)
    if parsed.scheme:
        result["errors"].append("URL 只允许 http/https。")
        return finalize(result, "", "url-security-guard")
    return parse_path(Path(input_uri).expanduser().resolve(), result)


def validate_local_file(path: Path, ext: str) -> list[str]:
    errors: list[str] = []
    try:
        size = path.stat().st_size
    except OSError as exc:
        return [f"文件状态读取失败：{exc}"]
    if size > MAX_FILE_BYTES:
        errors.append(f"文件超过最大限制：{size} > {MAX_FILE_BYTES}")
    if ext == ".pdf" and size > MAX_PDF_BYTES:
        errors.append("worker_required: PDF 文件过大，请交给异步 Worker 处理。")
    if ext in WORKER_REQUIRED_EXTS:
        errors.append(f"worker_required: {ext} 需要专用 Worker，本机轻解析层不伪解析。")
    return errors


def validate_allowed_file_roots(path: Path) -> list[str]:
    roots = allowed_file_roots()
    enforce = truthy(os.environ.get("MYSTAND_PARSER_ENFORCE_ALLOWED_ROOTS", ""))
    if not roots:
        return ["local_file_roots_required: HTTP/production parser mode requires MYSTAND_PARSER_ALLOWED_ROOTS before reading local files."] if enforce else []
    if any(path_is_inside(path, root) for root in roots):
        return []
    return [f"outside_allowed_roots: 本地文件不在 MYSTAND_PARSER_ALLOWED_ROOTS 白名单内：{path}"]


def allowed_file_roots() -> list[Path]:
    raw = os.environ.get("MYSTAND_PARSER_ALLOWED_ROOTS", "")
    roots: list[Path] = []
    for item in raw.split(","):
        text = item.strip()
        if text:
            roots.append(Path(text).expanduser().resolve())
    return roots


def path_is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_zip_members(archive: zipfile.ZipFile) -> list[str]:
    errors: list[str] = []
    infos = archive.infolist()
    if len(infos) > ZIP_MAX_FILES:
        errors.append(f"ZIP 文件数量超过限制：{len(infos)} > {ZIP_MAX_FILES}")
    total = 0
    for info in infos:
        name = info.filename
        parts = Path(name).parts
        if name.startswith("/") or ".." in parts:
            errors.append(f"ZIP 路径穿越已拦截：{name}")
        total += int(info.file_size or 0)
    if total > ZIP_MAX_TOTAL_BYTES:
        errors.append(f"ZIP 解压后总大小超过限制：{total} > {ZIP_MAX_TOTAL_BYTES}")
    return errors


def validate_url_allowed(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL 只允许 http/https。")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL 缺少 hostname。")
    lowered = host.lower().strip(".")
    if lowered in {"localhost", "127.0.0.1", "::1"} or lowered.endswith(BLOCKED_HOST_SUFFIXES):
        raise ValueError("URL 指向 localhost、内网名称或保留域名，已拦截。")
    for ip in direct_host_ips(lowered):
        if is_blocked_ip(ip):
            raise ValueError("URL 指向内网或保留 IP，已拦截。")
    try:
        ip = ipaddress.ip_address(lowered)
        if is_blocked_ip(ip):
            raise ValueError("URL 指向内网或保留 IP，已拦截。")
        return
    except ValueError as exc:
        if "已拦截" in str(exc):
            raise
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"URL DNS 解析失败：{exc}") from exc
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if is_blocked_ip(ip):
            raise ValueError("URL DNS 解析到内网或保留 IP，已拦截。")


def is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped and is_blocked_ip(mapped):
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def direct_host_ips(host: str) -> list[ipaddress._BaseAddress]:
    ips: list[ipaddress._BaseAddress] = []
    try:
        ips.append(ipaddress.ip_address(host))
    except ValueError:
        pass
    legacy_ipv4 = parse_legacy_ipv4(host)
    if legacy_ipv4:
        ips.append(legacy_ipv4)
    return ips


def parse_legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    if ":" in host or not re.fullmatch(r"[0-9a-fA-FxX.]+", host or ""):
        return None
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    parsed: list[int] = []
    for part in parts:
        if not part:
            return None
        try:
            parsed.append(parse_legacy_ipv4_part(part))
        except ValueError:
            return None
    widths = {
        1: [32],
        2: [8, 24],
        3: [8, 8, 16],
        4: [8, 8, 8, 8],
    }[len(parsed)]
    value = 0
    for number, bits in zip(parsed, widths):
        if number < 0 or number >= (1 << bits):
            return None
        value = (value << bits) | number
    return ipaddress.IPv4Address(value)


def parse_legacy_ipv4_part(part: str) -> int:
    lowered = part.lower()
    if lowered.startswith("0x"):
        return int(lowered, 16)
    if len(lowered) > 1 and lowered.startswith("0"):
        return int(lowered, 8)
    return int(lowered, 10)


def get_header(headers: Any, key: str, default: str = "") -> str:
    getter = getattr(headers, "get", None)
    if callable(getter):
        return str(getter(key, default) or "")
    return default


def truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv and argv[0] == "serve":
        return run_serve_command(argv[1:])
    if argv and argv[0] == "install-links":
        return run_install_links_command(argv[1:])
    if argv and argv[0] == "install-xiaoban-tool":
        return run_install_xiaoban_tool_command(argv[1:])

    parser = argparse.ArgumentParser(description="Parse files or URLs into My Stand standard JSON.")
    parser.add_argument("--input", required=True, help="Local file path or http(s) URL.")
    parser.add_argument("--output", help="Write JSON result to this path. Defaults to stdout.")
    args = parser.parse_args(argv)

    result = parse_input(args.input)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(output_path.parent)) as tmp:
            tmp.write(payload)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, output_path)
    else:
        print(payload)

    return 1 if result["errors"] else 0


def run_serve_command(argv: list[str]) -> int:
    from .server import run_server

    parser = argparse.ArgumentParser(description="Run MyStand Parser Tools local HTTP service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--max-workers", type=int, default=int(os.environ.get("MYSTAND_PARSER_WORKERS", "2")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("MYSTAND_PARSER_JOB_TIMEOUT", "90")))
    parser.add_argument("--max-body-bytes", type=int, default=int(os.environ.get("MYSTAND_PARSER_HTTP_MAX_BODY_BYTES", str(1024 * 1024))))
    parser.add_argument("--job-ttl", type=int, default=int(os.environ.get("MYSTAND_PARSER_JOB_TTL_SECONDS", str(24 * 60 * 60))))
    parser.add_argument("--max-jobs", type=int, default=int(os.environ.get("MYSTAND_PARSER_MAX_JOBS", "100")))
    parser.add_argument("--job-history-ttl", type=int, default=int(os.environ.get("MYSTAND_PARSER_JOB_HISTORY_TTL_SECONDS", str(24 * 60 * 60))))
    parser.add_argument("--max-job-history", type=int, default=int(os.environ.get("MYSTAND_PARSER_MAX_JOB_HISTORY", "1000")))
    parser.add_argument("--token", default=os.environ.get("MYSTAND_PARSER_HTTP_TOKEN", ""))
    parser.add_argument("--require-token", action="store_true", help="Require a parser HTTP token even for localhost clients.")
    parser.add_argument("--allow-public-bind", action="store_true", help="Allow binding to non-localhost addresses. Requires an HTTP token.")
    args = parser.parse_args(argv)
    run_server(
        host=args.host,
        port=args.port,
        max_workers=args.max_workers,
        timeout=args.timeout,
        max_body_bytes=args.max_body_bytes,
        ttl_seconds=args.job_ttl,
        max_jobs=args.max_jobs,
        history_ttl_seconds=args.job_history_ttl,
        max_job_history=args.max_job_history,
        http_token=args.token,
        allow_public_bind=args.allow_public_bind,
        require_token=args.require_token,
    )
    return 0


def run_install_links_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Install compatibility symlinks for MyStand Parser Tools.")
    parser.add_argument("--prefix", default="/opt/mystand-parser-tools")
    args = parser.parse_args(argv)
    prefix = Path(args.prefix).expanduser().resolve()
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "mystand-parser"
    legacy = prefix / "mystand-parser"
    package_root = Path(__file__).resolve().parents[1]
    launcher = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"export PYTHONPATH={json.dumps(str(package_root))}${{PYTHONPATH:+:$PYTHONPATH}}",
            f"exec {json.dumps(sys.executable)} -m mystand_parser_tools \"$@\"",
            "",
        ]
    )
    target.write_text(launcher, encoding="utf-8")
    target.chmod(0o755)
    if legacy.exists() or legacy.is_symlink():
        legacy.unlink()
    if not legacy.exists():
        legacy.symlink_to(target)
    print(json.dumps({"ok": True, "target": str(target), "legacy": str(legacy)}, ensure_ascii=False, indent=2))
    return 0


def run_install_xiaoban_tool_command(argv: list[str]) -> int:
    from .xiaoban import build_xiaoban_tool_module

    parser = argparse.ArgumentParser(description="Install MyStand parser as a native Xiaoban tool.")
    parser.add_argument("--xiaoban-root", default=os.environ.get("XIAOBAN_AGENT_ROOT", "/opt/xiaoban-agent"))
    parser.add_argument("--parser-src", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)

    xiaoban_root = Path(args.xiaoban_root).expanduser().resolve()
    tools_dir = xiaoban_root / "tools"
    if not tools_dir.is_dir():
        raise SystemExit(f"Xiaoban tools directory not found: {tools_dir}")

    target = tools_dir / "mystand_parser_tool.py"
    source = build_xiaoban_tool_module(args.parser_src)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(tools_dir)) as tmp:
        tmp.write(source)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, target)
    print(json.dumps({"ok": True, "target": str(target)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
