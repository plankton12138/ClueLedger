# README 截图

更新：2026-10-31。当前界面，1600 × 1000，示例作品《雪夜来信》。

| 文件 | 场景 |
|---|---|
| writing.png | 章节、模块目录与开篇讨论 |
| ideas.png | 灵感输入与最近记录 |
| module-library.png | 模块列表与选中内容预览 |
| story-structure.png | 三条故事线、阶段、汇合与全书线索 |
| story-clues.png | 选中节点、冻结表头与相关线索计划 |

所有内容为截图夹具；正文、对话、模块、灵感及结构均由脚本准备，不调用在线或本地模型，不使用作者真实作品。截图由当前页面直接生成，没有替换文字或合成界面。

在仓库根目录运行。先构建前端，并使用全新的隔离目录启动服务：

```powershell
npm --prefix frontend run build
uv run python -m hastings.server --port 8961 --state-dir data/test-tmp/readme-capture/state --library-dir data/test-tmp/readme-capture/library --static-dir frontend/dist
```

另一个终端运行：

```powershell
node scripts/capture_readme_screenshots.cjs http://127.0.0.1:8961 data/test-tmp/readme-capture
```

脚本检查正文、对话、模块预览、构思节点及线索详情均已显示，再写入本目录五张图片；运行摘要保存在隔离目录的 capture.json。复用已经生成的隔离示例时，可将作品 ID 作为最后一个参数，只重新截图。不要指向日常使用的服务或状态目录。

本次在全新隔离目录中完整生成五张截图，逐张检查界面。已移除不再由中英文 README 引用的旧 story-overview.png 与 characters.png。此轮仅更新文档、截图和夹具，不修改产品代码，未运行后端全量测试。
