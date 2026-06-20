# 全站解析工具

本目录是 My Stand 的全站资料解析工具层，不归属于单个业务模块。

目标：把外部资料解析成 My Stand 标准资料，供知识库、知识图谱、作品加工、Agent、Miner、SuFen、Rater 复用。

## 范围

- 一阶段：飞书/WPS 导出的文档、Office 文件、PDF、Markdown、网页、公众号文章、图片 OCR、CSV/JSON/XML、ZIP。
- 二阶段：录音、视频字幕、设计图、图纸 PDF、CAD/DWG/DXF、Figma/Sketch 等专业格式。

## 原则

- Agent 不直接硬读外部网页或文件格式。
- 先由解析工具层转换成标准 Markdown/JSON/资产，再生成引用 ID。
- 业务模块只消费标准资料，不重复实现解析器。
- 每个解析器必须记录安装方式、适用格式、输出能力、测试样例和失败边界。
- 本机工具、Agent Skill、远程 Parser Worker 的取舍见 `ARCHITECTURE.md`。

## 运行时安装目录

- 推荐：`/opt/mystand-parser-tools`
- Python venv：`/opt/mystand-parser-tools/venv`
- 测试输出：`/opt/mystand-parser-tools/test-output`

历史注意：2026-06-15 安装前曾发现根分区接近满载，已清理旧 dev/static 备份和未使用 Docker 缓存。后续扩容或安装重解析 Worker 前仍要先检查磁盘空间。
