# MyStand Parser Tools

MyStand Parser Tools 是 My Stand 的全站公共解析工具层。它不属于某一个业务模块，也不只给站小伴使用；小伴、SuFen、Miner、Rater、作品加工、知识库和后续模块都应调用同一个解析入口。

核心目标：

- 把文件、网页、公众号文章、飞书/WPS 链接、图片、压缩包、CAD 等资料解析成统一 JSON。
- Agent 只读取标准结果，不直接凭模型猜文件内容。
- 重解析能力可以后续拆到 Parser Worker，但上层接口不变。

## Install

```bash
git clone git@github.com:52707407SXG/MyStand-Parser-Tools.git
cd MyStand-Parser-Tools
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

本机或服务器直接运行：

```bash
bin/mystand-parser --input README.md --output test-output/readme.json
python -m mystand_parser_tools --input README.md --output test-output/readme.json
```

如果用 pip 安装，命令也会注册为：

```bash
mystand-parser --input README.md --output test-output/readme.json
```

## Standard Output

```json
{
  "source": {
    "type": "file|url|wechat_article|feishu|wps|cad",
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

只要 `errors` 非空，Agent 不允许伪造正文；必须把失败原因告诉用户，并给出下一步。

## Current Coverage

- Markdown / TXT / LOG：原生读取。
- CSV / JSON / XML：原生转换为 Markdown 代码块或表格。
- HTML / 普通网页：Trafilatura，失败后 html2text。
- 微信公众号链接：优先 `wechat-article-parser`，失败后才走普通网页兜底。
- DOCX / XLSX / PPTX / PDF：MarkItDown 轻解析。
- 图片：本机 Tesseract OCR；未安装时返回结构化错误。
- DXF：ezdxf 读取图层、文字和实体统计。
- DWG：明确返回需要 CAD Worker，不在本机伪解析。
- ZIP：列出文件，并读取包内文本类文件。
- 飞书/WPS/公众号网页：按 URL 类型识别；动态页面可走 `agent-browser` 兜底。

详见：

- `docs/TOOLS.md`
- `docs/ARCHITECTURE.md`
- `docs/AGENT-SKILL.md`
- `docs/DEPLOYMENT.md`
- `docs/CAPABILITY-MATRIX.md`

## Verify

```bash
python scripts/verify_parser_samples.py
```

验证真实公众号链接：

```bash
python scripts/verify_parser_samples.py --wechat-url "https://mp.weixin.qq.com/s/OkKlPnSbLOP9J3heC0CaPw"
```

微信可能按 IP 触发验证码或限流。触发时工具会返回结构化错误，不会把验证码页面伪装成正文。
