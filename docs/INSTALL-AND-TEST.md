# 安装与验证清单

## 阶段 0：磁盘前置

- 当前根分区已满，安装前必须清理空间。
- 不允许直接删除业务数据库、上传资产、线上静态目录、Git 仓库。
- 优先检查缓存、旧构建产物、重复备份、包管理缓存。

## 阶段 1：轻量工具

- MarkItDown：Office/PDF/HTML/CSV/JSON/XML/ZIP 快速转 Markdown。
- Trafilatura + Readability：网页和公众号正文抽取。
- ezdxf：DXF 图层、文字、尺寸解析。

每个工具必须跑样例：

- Markdown/TXT
- HTML/网页样例
- DOCX
- XLSX
- PPTX
- PDF
- PNG OCR
- DXF

## 阶段 2：重工具 / Worker

- Docling：主力结构化解析，优先作为独立 Parser Worker 部署，不默认压到主站。
- Apache Tika：兜底文本和 metadata，可随 Worker 部署或按需本机启用。
- LibreOffice：WPS/Office 转换底座。
- Tesseract：本地 OCR。
- MinerU：复杂 PDF/OCR 高精度后备，优先作为独立 Parser Worker 部署。
- DWG 工具：LibreDWG 或 ODA 转换后再进 ezdxf，优先作为独立 CAD Worker 部署。

主站策略：先保证轻解析稳定可调用；重工具通过统一入口返回“需要 Worker”的结构化结果，不在主站硬装一整套重依赖。

## 验收标准

- 工具命令存在。
- 能读取样例文件。
- 输出文件非空。
- Markdown 能保留标题/列表/表格基本结构。
- JSON 能描述 source/content/assets/warnings/errors。
- 失败时返回明确错误，不静默成功。
- `install-links` 生成的 `bin/mystand-parser` 和兼容旧路径都能执行。
- HTTP smoke 能启动本机服务并通过 `/health`、`/parse`、`/jobs`。
- `serve --host 0.0.0.0` 未加 `--allow-public-bind` 时必须拒绝启动。
- 公开绑定必须配置 `MYSTAND_PARSER_HTTP_TOKEN` 或 `--token`。
- `--require-token` 未配置 token 时必须拒绝启动。
- 带 token 的公开绑定服务必须拒绝无 token 请求，并接受 `Authorization` 或 `x-mystand-parser-token`。
- `/parse` 失败时顶层必须有 `error/message`。
- `/jobs` 的 `MYSTAND_PARSER_MAX_JOBS` 或 `--max-jobs` 只限制 pending/running 活跃任务；活跃数超限时必须返回 `queue_full`。
- 已完成 job 不得占用新任务名额；完成记录应按 `MYSTAND_PARSER_MAX_JOB_HISTORY` / `--max-job-history` 和 `MYSTAND_PARSER_JOB_HISTORY_TTL_SECONDS` / `--job-history-ttl` 清理。

## CI 命令

```bash
python scripts/verify_parser_samples.py
python -m compileall src scripts
python scripts/http_smoke.py
```
