# Harness 架构配图

配套正文：[Harness 架构图解](../../harness-architecture.md)。核对日期：2026-10-18。

`.mmd` 是可编辑的 Mermaid 图源，`.svg` 是可直接查看的静态图。正文嵌入 SVG，因此阅读时不需要 Mermaid 插件或网络。事实解释与适用范围以正文为准。

| 文件前缀 | 读者要理解的问题 |
|---|---|
| `01-execution-boundaries` | 哪些路径使用 Harness，谁提交正式作品？ |
| `02-agent-loop` | 模型如何与工具循环，何时续办或结束？ |
| `03-worker-proposal-sequence` | 作者启动、候选生成与正式采用如何分开？ |
| `04-context-and-state` | 资料来自哪里，谁保存长期状态？ |
| `05-job-states` | 任务如何等待、取消、失败与恢复？ |
| `06-events-and-truth` | 执行事件与正式提交分别走哪条通道？ |

## 更新与渲染

先修改 `.mmd`，运行同目录的 `render.mjs`，再同时提交图源与 SVG。不要只手工修改 SVG。

渲染使用 Mermaid **11.4.1** 的 `dist/mermaid.min.js` 和仓库前端已有的 Playwright。渲染脚本只读取图源并输出 SVG；可选的第三个参数指定 PNG 检查目录，不进入前端构建、不修改应用依赖。

在仓库根目录运行以下 PowerShell 命令（首次下载需要网络；已有该渲染器时直接使用本地路径）：

```powershell
New-Item -ItemType Directory -Force .tmp/harness-diagrams | Out-Null
Invoke-WebRequest -Uri 'https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js' -OutFile .tmp/harness-diagrams/mermaid.min.js
node product-design/diagrams/harness/render.mjs .tmp/harness-diagrams/mermaid.min.js .tmp/harness-diagrams/previews
```

前提：前端依赖已安装，Playwright 的 Chromium 可用。临时下载和 PNG 仅供渲染检查，不纳入产品设计资产。脚本不主动下载依赖、不访问模型、不读取项目数据或连接凭据。

维护时检查：中文字体是否正确；标题与箭头标签是否重叠；图示路径是否仍与源码一致；0.3 要求与尚未完成的实现是否仍分开表述。
