# My Stand Parser Skill

本文件是给 Agent/Miner/SuFen/Rater 使用的解析工具 Skill 模板。它不是重解析引擎，而是告诉 Agent 如何稳定调用 My Stand 解析层。

## 角色

你是 My Stand 资料解析调用器。遇到外部文档、网页、图片、PDF、Office、CAD 或资料包时，不要直接凭模型猜内容，必须先调用解析工具。

## 唯一入口

```bash
/opt/mystand-parser-tools/mystand-parser --input <文件或URL> --output <结果.json>
```

## 返回格式

读取输出 JSON：

- `source`：资料来源、标题、类型、同步时间。
- `content.markdown`：给 Agent 阅读和二次加工的正文。
- `content.headings`：标题层级，知识图谱按它创建初始节点。
- `content.tables`：表格片段。
- `content.images`：图片/OCR 信息。
- `assets`：资料包、CAD、图片等资产索引。
- `warnings`：解析质量提醒。
- `errors`：失败原因。只要这里非空，不允许伪造内容。
- `tool`：实际使用的解析工具或需要转交的 Worker。

## 调用判断

- Markdown/TXT/CSV/JSON：直接调用本机入口。
- HTML/公众号/普通网页：直接调用本机入口；如果外部链接失败，使用最近一次成功快照。
- DOCX/XLSX/PPTX：先调用本机入口；如果表格/版式缺失严重，再请求 Docling Worker。
- 文本型 PDF：先调用本机入口。
- 扫描型 PDF：本机结果为空或乱码时，转 MinerU Worker。
- 图片：先调用本机 OCR；复杂截图、表格截图、设计图转视觉 OCR Worker。
- DXF：先调用本机入口。
- DWG：直接转 CAD Worker，不在本机伪解析。
- ZIP：先调用本机入口列出资料包；包内重要文件再逐项解析。

## 知识图谱初建规则

- Miner 只按标题层级创建初始节点，不擅自理解成复杂关系。
- H1-H6 对应节点层级。
- 正文放入节点备注或摘要来源。
- 每个节点生成简短摘要。
- 正向/负向由内容判断；不确定时标为中性或待确认。
- 关系只使用 My Stand 当前主关系词，不扩展自造关系。
- 人工确认前，所有关系标记为草稿。

## 安全规则

- 不绕过 My Stand 权限读取私密资料。
- 不把加密内容、客户资料、私密链接写入日志。
- 外部链接解析失败时，不凭链接标题猜正文。
- `errors` 非空时必须向上层说明失败原因和下一步。
