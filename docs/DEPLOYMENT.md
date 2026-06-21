# Deployment

## 推荐服务器位置

```text
/opt/mystand-parser-tools
```

这个目录是全站级工具位置，不放进站小伴仓库，也不放进某个业务模块目录。My Stand 网站、站小伴、SuFen、Miner 等都通过统一命令调用它。

## 安装

```bash
cd /opt
git clone git@github.com:52707407SXG/MyStand-Parser-Tools.git mystand-parser-tools
cd /opt/mystand-parser-tools
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
chmod +x bin/mystand-parser
```

可选：注册全局命令。

```bash
ln -sf /opt/mystand-parser-tools/bin/mystand-parser /usr/local/bin/mystand-parser
```

兼容旧路径：

```bash
/opt/mystand-parser-tools/bin/mystand-parser install-links --prefix /opt/mystand-parser-tools
```

这会同时保留：

```text
/opt/mystand-parser-tools/bin/mystand-parser
/opt/mystand-parser-tools/mystand-parser
```

## My Stand / Agent 接口

服务端统一配置：

```bash
MYSTAND_PARSER_COMMAND=/opt/mystand-parser-tools/bin/mystand-parser
MYSTAND_AGENT_PARSER_TIMEOUT_MS=60000
MYSTAND_AGENT_PARSER_CONCURRENCY=3
```

小伴、SuFen、Miner 不应该各自实现 Excel/PDF/公众号解析。它们应该调用这个命令：

```bash
$MYSTAND_PARSER_COMMAND --input <文件或URL> --output <结果.json>
```

如果模块更适合 HTTP 调用，可启动本机内部服务：

```bash
mystand-parser serve --host 127.0.0.1 --port 8790 --timeout 90 --max-workers 2 --job-ttl 86400 --max-jobs 100 --job-history-ttl 86400 --max-job-history 1000
```

内部接口：

- `GET /health`
- `POST /parse`
- `POST /jobs`
- `GET /jobs/:id`

`/parse` 会走同步解析并受 timeout、并发和请求体大小限制；重活建议走 `/jobs`。`/jobs` 使用轻量本地队列，包含 job id、pending/running/done/failed 状态、timeout、活跃任务 TTL、完成记录 TTL/数量上限和临时文件清理。

`--max-jobs` / `MYSTAND_PARSER_MAX_JOBS` 只限制 `pending/running` 活跃任务。`max-jobs` 检查和 job 插入在同一把锁内完成，避免并发提交时突破队列上限。已经完成的 `done/failed` 记录不会占用新任务名额，历史记录由 `--max-job-history` / `MYSTAND_PARSER_MAX_JOB_HISTORY` 和 `--job-history-ttl` / `MYSTAND_PARSER_JOB_HISTORY_TTL_SECONDS` 控制。

默认空 token 只允许本机客户端。公开绑定必须显式确认并配置 token：

```bash
MYSTAND_PARSER_HTTP_TOKEN=change-me \
MYSTAND_PARSER_ALLOWED_ROOTS=/var/lib/mystand/uploads,/tmp \
mystand-parser serve --host 0.0.0.0 --port 8790 --allow-public-bind --require-token
```

请求头：

```text
Authorization: Bearer change-me
```

或：

```text
x-mystand-parser-token: change-me
```

HTTP 服务不得裸奔公网；生产建议仍由 My Stand 后端或内网网关代理。
如果经过 Nginx/后端反代，parser 即使只绑定 `127.0.0.1` 也必须启用 `--require-token`，否则 parser 看到的请求来源会是本机反代，空 token 会被放行。
HTTP 服务启用 token、`--require-token` 或公开绑定时，本地文件读取会进入白名单模式。生产必须设置 `MYSTAND_PARSER_ALLOWED_ROOTS`，例如上传目录和临时目录；未设置时会安全拒绝本地文件解析，而不是默认读全服务器文件系统。

## 站小伴接入方式

站小伴本体只保留 `ParserAdapter`：

- 检测 `MYSTAND_PARSER_COMMAND` 或默认 `/opt/mystand-parser-tools/bin/mystand-parser`。
- 文件/URL 到来时自动调用 parser。
- 读取标准 JSON，放入小伴上下文、证据层和记忆索引。
- `errors` 非空时，小伴必须说明失败原因，不伪造。
- 本机没有 parser 时，只允许极轻文本读取，并提示需要安装 MyStand Parser Tools。

用户不需要输入任何“解析口令”。

## Worker 扩展

主站本机只承担轻解析。复杂扫描 PDF、复杂 OCR、DWG/CAD 转换、Docling/MinerU、视觉理解可接独立 Parser Worker，但仍返回同一 JSON。

## 安全限制

- 最大文件大小通过 `MYSTAND_PARSER_MAX_FILE_BYTES` 控制。
- HTTP/token 模式的本地文件读取目录通过 `MYSTAND_PARSER_ALLOWED_ROOTS` 控制。
- PDF 大小通过 `MYSTAND_PARSER_MAX_PDF_BYTES` 控制，超限返回 `worker_required`。
- URL 响应体大小通过 `MYSTAND_PARSER_MAX_URL_BYTES` 控制。
- ZIP 总大小通过 `MYSTAND_PARSER_ZIP_MAX_TOTAL_BYTES` 控制。
- ZIP 文件数量通过 `MYSTAND_PARSER_ZIP_MAX_FILES` 控制。
- ZIP 路径穿越会被拦截。
- URL 只允许 `http` / `https`，并拦截 localhost、IPv4/IPv6 内网 IP、IPv6 mapped localhost、十进制/八进制写法本地 IP、`.local`、`.internal`、`.lan`，包含 DNS 解析到内网的情况。
- 普通 URL 抓取会在 redirect 前校验跳转目标，并在响应 `final URL` 再校验一次；`agent-browser` 兜底会校验浏览器返回的 `finalUrl`，命中内网/保留地址时返回错误，不输出正文。
- HTTP 请求体大小通过 `MYSTAND_PARSER_HTTP_MAX_BODY_BYTES` 或 `--max-body-bytes` 控制。
- HTTP token 通过 `MYSTAND_PARSER_HTTP_TOKEN` 或 `--token` 控制。
- 反代场景必须加 `MYSTAND_PARSER_REQUIRE_TOKEN=1` 或 `--require-token`。
- 活跃 Job TTL 通过 `MYSTAND_PARSER_JOB_TTL_SECONDS` 或 `--job-ttl` 控制，避免 pending/running 异常长期堆积。
- 最大活跃任务数通过 `MYSTAND_PARSER_MAX_JOBS` 或 `--max-jobs` 控制，只计算 pending/running。
- 完成记录 TTL 通过 `MYSTAND_PARSER_JOB_HISTORY_TTL_SECONDS` 或 `--job-history-ttl` 控制。
- 完成记录数量通过 `MYSTAND_PARSER_MAX_JOB_HISTORY` 或 `--max-job-history` 控制。
