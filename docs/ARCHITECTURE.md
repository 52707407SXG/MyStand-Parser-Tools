# My Stand 解析工具架构决策

本文件回答一个问题：资料解析能力应该放在哪里，避免主站越装越重，也避免 Agent 需要解析资料时没有工具可用。

## 总原则

- Agent 只调用统一入口，不直接读复杂格式。
- 主站安装轻、稳、常用的解析能力。
- Skill 安装“调用规则、判断规则、输出规范”，不把重依赖塞进 Skill。
- Parser Worker 承担复杂、慢、依赖重、可能吃 GPU/CPU 的任务。
- 暂不支持的格式必须明确返回原因和下一步，不伪造解析结果。

## 四类归属

| 归属 | 放什么 | 原因 | 当前决策 |
| --- | --- | --- | --- |
| 主站本机工具 | Markdown/TXT/CSV/JSON、HTML、普通 Office、文本型 PDF、简单图片 OCR、DXF | 高频、轻量、调用快、失败影响小 | 已安装并接入统一入口 |
| Agent Skill | 工具选择规则、知识图谱生成规则、Feishu/WPS/公众号取数流程、失败重试策略、标准 JSON 解释 | Skill 轻，适合教 Agent 怎么用工具和怎么判断 | 需要后续做成 My Stand 标配 Skill |
| Parser Worker | Docling、MinerU、复杂扫描 PDF、版式恢复、DWG/CAD 转换、设计图视觉理解、大批量 OCR | 依赖重、慢、可能影响主站性能 | 不压主站，后续独立部署 |
| 暂不本机安装 | 视频理解、音频转写、大型视觉模型、专业闭源 CAD 套件 | 不是知识库第一阶段刚需，成本和维护复杂度高 | 只预留接口 |

## 为什么不把所有东西都装主站

My Stand 主站的第一职责是稳定服务经纪人和 Agent。解析工具如果把主站拖慢，反而会影响业务使用。

所以主站只保留“够快、够稳、常用”的工具；重解析工具独立出去，Agent 仍然通过同一个入口调用，体验不变。

## Skill 的正确位置

Skill 不是解析引擎本身。Skill 应该写这些内容：

- 遇到飞书链接、WPS 链接、公众号链接、Office/PDF/图片/CAD 时，先调用哪个工具。
- 什么时候认为本机轻解析不够，需要转 Parser Worker。
- 解析结果如何转成知识库文档或知识图谱节点。
- 错误如何向用户说明，如何使用最近一次成功快照。
- 如何保护私密链接、引用 ID、客户资料和加密内容。

在 Agent 运行时里，这份 Skill/TOOLS 应作为系统级标准工具说明书注入到 `<standard_tool_manual>`，不放进特征卡、房源笔记、业绩明细等业务上下文。业务上下文每次任务会变，TOOLS 是 Agent 出厂工具箱。

## 系统级 Foundation 引用

知识图谱还可以作为 Agent 的系统级底色，例如人设、专职、判断框架、性格和回复方式。这个层级不是普通资料上下文，也不是 `standard_tool_manual`。

当前预留方式：

- 运行时系统块：`<system_foundation_context>`
- 支持类型：只允许知识图谱引用 ID，例如 `KGREF-...`
- 配置入口：服务端环境变量 `MYSTAND_AGENT_FOUNDATION_REFS`、`MYSTAND_SUFEN_FOUNDATION_REFS`、`MYSTAND_MINER_FOUNDATION_REFS`
- 失败规则：引用 ID 不可读取时，明确写成系统底色缺口，不允许 Agent 猜内容
- 禁止：普通笔记、房源笔记、文档知识库直接变成人设底色；这些资料应进入任务上下文或引用资料区

## 当前最优解

第一阶段先落地：

- 本机轻解析入口：`/opt/mystand-parser-tools/mystand-parser`
- 标准输出：Markdown + JSON + warnings/errors
- 支持样本：Markdown、TXT、CSV、JSON、HTML、DOCX、XLSX、PPTX、PDF、PNG OCR、DXF、ZIP
- 重工具不继续装主站，只在 TOOLS 中注册 Worker 路由

第二阶段再做：

- My Stand parser Skill：给 Agent/Miner/SuFen 统一安装，模板见 `AGENT-SKILL.md`
- Parser Worker API：接 Docling/MinerU/CAD/视觉 OCR
- 外部平台连接器：飞书、WPS、公众号、多维表格
- 解析快照和引用 ID：让 Agent 使用稳定快照，不每次重新抓外部链接
