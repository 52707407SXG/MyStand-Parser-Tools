# Source Map

- `src/mystand_parser_tools/cli.py`：统一解析入口。
- `src/mystand_parser_tools/server.py`：本机 HTTP 服务和轻量 job 队列。
- `src/mystand_parser_tools/xiaoban.py`：站小伴原生工具注册适配层。
- `bin/mystand-parser`：源码仓库运行脚本。
- `.github/workflows/ci.yml`：GitHub Actions 样例验证。
- `scripts/verify_parser_samples.py`：本地样例验证。
- `docs/TOOLS.md`：Agent 工具注册索引。
- `docs/AGENT-SKILL.md`：Agent 使用解析工具的 Skill 模板。
- `docs/ARCHITECTURE.md`：轻解析、Skill、Worker 分层。
- `docs/DEPLOYMENT.md`：服务器安装和接入方式。
- `docs/CAPABILITY-MATRIX.md`：当前能力、依赖和缺口。
