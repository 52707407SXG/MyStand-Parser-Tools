# My Stand Parser TOOLS

这是给全站 Agent 使用的解析工具注册索引。Agent/Miner/SuFen/Rater 遇到外部资料时，按这里选择工具，不直接猜。

## 调用入口

Agent/Miner/SuFen 标准调用一个入口，不直接调用底层库：

```bash
/opt/mystand-parser-tools/mystand-parser --input <文件或URL> --output <结果.json>
```

本入口先走主站本机轻解析；遇到扫描件、复杂 PDF、DWG、设计图等重任务时，返回明确错误或 warning，由后续 Parser Worker 接管。

## 路由规则

| 输入类型 | 首选工具 | 后备工具 | 输出 |
| --- | --- | --- | --- |
| `.docx` / `.pptx` / `.xlsx` | 本机 MarkItDown | 远程 Docling Worker / LibreOffice / Tika | Markdown + JSON |
| `.pdf` 文本型 | 本机 MarkItDown | 远程 Docling Worker / Tika | Markdown + JSON |
| `.pdf` 扫描型 | 远程 MinerU Worker | 远程 Docling OCR / 本机 Tesseract | Markdown + JSON + 图片 |
| 图片 / 截图 | 本机 Tesseract | 远程 MinerU/视觉 OCR Worker | OCR 文本 + 图片资产 |
| HTML / 普通网页 | Trafilatura | Readability / MarkItDown | Markdown |
| 飞书公开文档链接 | Trafilatura | 服务器浏览器 `agent-browser` | Markdown + 标题 |
| 飞书多维表格链接 | 服务器浏览器 `agent-browser` + 截图 OCR | 多维表格专用读取器 / 已授权快照 | 可见网格文本、截图资产，或明确失败原因 |
| WPS/Kdocs 链接 | Trafilatura | 服务器浏览器 `agent-browser` / WPS 专用读取器 | Markdown/表格或明确权限失败 |
| 微信公众号文章 | Trafilatura + 自定义清洗 | Readability | Markdown + 标题/作者/时间 |
| Markdown / TXT | 原生读取 | MarkItDown | Markdown |
| CSV / JSON / XML | 原生解析 | MarkItDown / Tika | 表格/JSON |
| ZIP | 解包后逐项路由 | MarkItDown ZIP | 资料包 |
| CAD `.dxf` | 本机 ezdxf | 远程 CAD Worker | 图层/文字/尺寸 JSON |
| CAD `.dwg` | 远程 CAD Worker | 转 DXF 后 ezdxf | 图层/文字/尺寸 JSON |
| 设计图图片 | 远程视觉 OCR Worker | 人工标注 | 图片说明 + OCR 文本 |

## 工具分工

- MarkItDown：主站轻量快速转 Markdown，适合普通 Office、文本型 PDF、网页、ZIP 等。
- Trafilatura/Readability：主站网页和公众号正文抽取。
- Tesseract：主站本地 OCR 兜底，适合简单图片，不承担复杂扫描件。
- ezdxf：主站读取 DXF 图层、文字、实体统计。
- Docling：远程 Worker 主力文档解析，处理复杂 Office/PDF/HTML/EPUB/图片等，输出结构化 Markdown/JSON。
- MinerU：远程 Worker 处理复杂 PDF、扫描件、中文 OCR、表格、公式、图文混排。
- Apache Tika：远程或本机后备，兜底识别文件类型、抽文本和 metadata。
- LibreOffice：WPS/Office 兼容转换底座，必要时先转成 DOCX/XLSX/PDF。
- agent-browser：服务器浏览器读取动态网页和公开在线文档；普通 URL 抓取失败、空壳、跳转或动态渲染时自动兜底。
- ezdxf/LibreDWG/ODA：CAD/DXF/DWG 专业格式解析。

## 标准输出

解析结果统一转换成：

```json
{
  "source": {
    "type": "file|url|feishu|wps|wechat_article|cad",
    "uri": "",
    "title": "",
    "syncedAt": ""
  },
  "content": {
    "markdown": "",
    "blocks": [],
    "headings": [],
    "tables": [],
    "images": []
  },
  "assets": [],
  "warnings": [],
  "errors": [],
  "tool": ""
}
```

## Agent 调用规则

- Agent 只选择工具，不绕过工具层直接解析复杂外部资料。
- 有 My Stand 快照时优先读取快照；外部源失败时使用最近一次成功快照。
- 外部 URL 不允许只试普通下载。统一入口会先普通解析，失败或疑似空壳时自动调用服务器浏览器；浏览器仍只读到登录页、权限页或表格外壳时，必须把失败原因返回给上层，不伪造正文。
- 飞书多维表格第一阶段允许返回“截图 OCR 可见文本”，只能代表当前浏览器视口里的可见行列，不等于全量结构化表格；Agent 需要据此标注“可见内容”或提示需要继续同步全量表格。
- 解析失败必须返回失败原因和可替代动作，不伪造内容。
- 加密文件、私密链接、客户资料必须走当前账号权限和引用 ID 权限。
