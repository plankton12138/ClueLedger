# V04-4：前端接线

日期：2026-10-20。范围：计划 M06——目录选择、Markdown 稿纸、三个设定入口、对话与文件结果、差异与历史、导入与外部刷新；附带补齐 Git 的 HTTP 端点。

## 目标

浏览器经真实后端完成：打开或新建作品目录、阅读与搜索、查看对话过程与结果、读差异、撤销、刷新外部修改。界面上没有「仅存在于前端」的能力。

## 实际改动

后端（补齐 M06 的 Git 面）：

- `src/hastings/workspace/api.py`：`git/status`、`git/diff`、`git/log`、`git/commits/{sha}/diff`、`git/init`、`git/record`、`git/revert`。未初始化时 status 返回 `initialized=false`，供界面显示「启用版本记录」。
- `src/hastings/workspace/git.py`：`log()` 不再按受管路径过滤——初始化提交只含 `.gitignore`，也属于作者能看到的历史起点。
- `src/hastings/workspace/service.py`：`tool_finished` 事件带上与开始事件相同的定位参数（路径、查询词），界面据此显示「写入 正文/…」而不是解析工具的 JSON 输出。

前端：

- `src/api/workspace.ts`：`/api/file-workspaces` 客户端（错误信封转带 code 的异常）与 SSE 解析（fetch 流式读取，浏览器与测试同一段代码）。
- `src/state/workspace.ts`：zustand store——目录、文件、搜索、导入、对话与事件、Git 状态与动作；一轮结束回读目录与当前文件。
- `src/components/workspace/`：`WorkspaceShell`（顶栏 + 三列）、`DirectoryDialog`（按路径打开/新建）、`Paper`（只读 Markdown，`skipHtml`）、`ConversationPanel`（事件过程、结果卡、停止、撤销/补记）、`ChangesDrawer`（状态、差异、历史、撤销、启用版本记录）。
- `src/App.tsx`：默认页面只挂工作区壳层，0.3 写作台组件保留在源码中但不再被引用。
- `src/index.css`：稿纸排版（标题、段落、引用、代码），不引入 typography 插件。
- `frontend/e2e/walkthrough-v04-workspace.mjs`：浏览器走查脚本，自己经真实接口准备作品。

## 契约与数据影响

- 新增 7 个 Git 端点；`tool_finished` 事件多 `args` 字段（向后兼容，只增不删）。
- 前端构建产物 `frontend/dist` 更新为新壳层；旧构建（0.3 写作台）不再被托管。
- 无数据库、无旧接口参与。

## 实现取舍

- **不迁移 0.3 的响应式布局**：0.3 的三列断点规则围绕旧桌面结构设计；0.4 先用固定三列，宽度不足时的折叠规则留给下一轮，不在本阶段混着改。
- **SSE 用 fetch 手工解析**：不用 EventSource，浏览器与 node 测试走同一份解析代码，也能带游标与中止信号。
- **模型设置界面后置**：`/api/providers`、`/api/model-selection` 仍在旧代码中，本阶段只保留 `config.yaml`/环境变量 + `--config`/`--model`；界面缺口写明，不做假按钮。
- **撤销按钮只在最新提交旁**：由后端校验 HEAD 与工作区，界面不重复实现这套判断。
- **目录对话框按服务器路径**：不做浏览器文件上传式的目录授权——应用运行在本机，路径即事实；相对路径按服务端工作目录解析。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 前端 API 与状态 | `npx vitest run src/api/workspace.test.ts src/state/workspace.test.ts` | 15 项通过 |
| 前端整体 | `npx vitest run` | 164 项通过（首跑偶发 EPERM，见下） |
| 构建与类型 | `npm run build` | 通过 |
| 浏览器走查 | `node e2e/walkthrough-v04-workspace.mjs`（真实后端 + Chromium） | 10 步全部通过，无 console 错误 |
| 完整后端回归 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --junitxml=data/test-tmp/v04-4-full.xml` | 2105 项通过，0 失败 0 错误 0 跳过（490 秒） |

走查覆盖：经真实接口准备作品（新建 + 两次提交）→ 打开目录 → 章节与三份设定 → 只读阅读 → 改动与历史 → 撤销最新提交（内容回退）→ 搜索 → 外部新增章节刷新可见 → 对话框新建作品（含 `.git` 与基线提交）→ 对话栏。截图与结果归档在 `dev-notes/v0.4/artifacts/walkthrough-v04-4/`。

本机沙箱下 vitest 偶发对系统临时目录 `ssr\*` 的 EPERM（未重现于第二次运行）；把 `TMP`/`TEMP`/`TMPDIR` 指到仓库内 `data/test-tmp/` 可稳定。这是环境现象，不是代码缺陷。

## 偏差与未完成

- **真实模型未取证**：浏览器走查不调用模型；「一句写第一章」在浏览器里经真实模型完成一轮，仍需用真实凭据单独验证。
- **没有 token 级流式**：回复整条显示。
- **模型设置界面未迁入**：连接厂商、选择模型仍靠 `config.yaml` 或环境变量。
- **响应式未做**：固定三列，窄屏未折叠。
- **桌面壳未适配**：`desktop/` 仍按旧参数启动服务。
- 停止按钮在事件流断开时依赖下一轮 `run_finished` 事件；界面已处理断开错误，但未做自动重连。
