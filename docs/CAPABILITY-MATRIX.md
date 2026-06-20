# Capability Matrix

| 能力 | 当前状态 | 首选工具 | 说明 |
| --- | --- | --- | --- |
| Markdown/TXT/LOG | 可用 | native-text | 轻量原生读取 |
| CSV | 可用 | native-csv | 转 Markdown 表格 |
| JSON | 可用 | native-json | 格式化 JSON 代码块 |
| XML | 可用 | native-xml | 格式化 XML 代码块，失败返回原文 |
| HTML/普通网页 | 可用 | trafilatura/html2text | 动态页面可接 agent-browser |
| 微信公众号文章 | 可用 | wechat-article-parser | 可能遇到微信验证码/限流，会结构化报错 |
| DOCX/XLSX/PPTX/PDF | 可用但依赖 MarkItDown | markitdown | 复杂版式需 Docling Worker |
| 图片 OCR | 条件可用 | tesseract | 需要系统安装 tesseract 和中文语言包 |
| DXF | 可用 | ezdxf | 输出图层、文字、实体统计 |
| DWG | 预留 | CAD Worker | 不在本机伪解析 |
| ZIP | 可用 | native-zip | 列文件，读取包内文本类文件 |
| 飞书/WPS/多维表格 | 基础可识别 | trafilatura/agent-browser | 全量结构化读取需要授权连接器或快照 |

## 体检结论

从 My Stand 主仓库抽出的旧解析层方向正确，但不是独立包：

- wrapper 写死了服务器路径。
- 没有独立 `pyproject.toml` / `requirements.txt`。
- 本机没有样例文件和 portable 验证流程。
- 依赖未安装时无法直接使用。

本仓库修正为独立可安装工具包，并保留后续 Worker 扩展口。
