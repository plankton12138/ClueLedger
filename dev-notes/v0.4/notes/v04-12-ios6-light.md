# V04-12 界面浅色化（iOS 6）：阶段记录

> 日期：2026-10-21。范围：写作台三栏、目录、对话栏、稿纸、空态与提示条接回 iOS 6 浅色设计系统，删除深色内联色与不再使用的遗留样式。
> 计划入口：[写作台界面开发计划](../workspace-ui-plan.md)。现状行为已整理进[写作台界面](../modules/desk.md)。视觉证据：[截图](../artifacts/v04-12-ui/)。

## 1. 目标

0.4 新壳层绕开了 `theme.ts` 与 `index.css` 的拟物浅色系统，手写深色内联值，导致金属顶栏下面接三块扁平深蓝。本阶段把界面统一回浅色拟物：目录用 iOS 6 分组列表、对话栏用浅色面板与信息气泡、中区用织物底、稿纸保持米白。

## 2. 实际改动

| 文件 | 改动 |
|---|---|
| `frontend/src/index.css` | 重写为当前结构所需的精简样式表：保留顶栏、按钮、分段控件、稿纸、把手；新增 `.toc-pane/.toc-head/.toc-row/.toc-foot`（分组列表）与 `.chat-shell/.chat-head/.chat-bar/.chat-scroll/.chat-msg/.chat-bubble/.chat-tool/.chat-error/.chat-card/.chat-composer/.chat-input`（浅色对话栏）；删除 0.3 遗留的导航条、面板、徽章、指标卡、英雄区、影响路径、进度条、差异、开关、状态栏、`page-grid/studio-grid`、旧 `chat-*` 与浮层类 |
| `frontend/src/components/workspace/WorkspaceShell.tsx` | 目录栏改 `.toc-pane`，搜索框与重命名用 `.well-input`，章节与设定行用 `.toc-row`（选中 `.on` 蓝色高光）；中区改 `.workbench` 织物底；空态与提示条改用语义令牌；对话开关关闭时加 `.dim`（暗色按压态） |
| `frontend/src/components/workspace/ConversationPanel.tsx` | 根节点改 `.chat-shell`；头部 `.chat-head`、模型行 `.chat-bar`、消息区 `.chat-scroll`；作者提示（`turn_started`）改为右侧绿色信息气泡，Agent 回复改为左侧白色气泡；工具行 `.chat-tool`（失败 `.bad`）、错误 `.chat-error`、结果卡 `.chat-card`（完成 `.result` 绿底，其余 `.warn` 琥珀底） |
| `frontend/src/components/workspace/Paper.tsx` | 标题、路径、字节数与空态改用 `fg-strong / sub`，不再用白色文字 |
| `frontend/src/components/models/ModelSelector.tsx` | 连接下拉改 `.well-input`，无连接时用 `.btn-gray`，「管理」改为蓝色文字入口 |
| `frontend/src/components/ErrorBoundary.tsx` | 旧暗色别名（`overdue / paper`）改为 `red / fg` 语义令牌 |
| `frontend/tailwind.config.js` | 删除「旧暗色令牌兼容映射」整段；`line` 的最后两处用法已改回 `panel-edge`，别名不再有引用 |
| `frontend/e2e/walkthrough-v04-12-ui.mjs` | 新增视觉走查脚本：准备作品后截取阅读、收起对话栏、拖宽目录栏、改动抽屉、模型设置五屏 |

## 3. 接口与数据影响

- 后端接口、请求与响应结构无改动。
- 主题令牌 `src/theme.ts` 未改：浅色令牌本来就在，本阶段只是让界面真正用上它们。
- 无新增状态与存储。

## 4. 实现取舍

- **目录用「白行 + 蓝色选中」**：iOS 6 分组表是白底单元格配蓝色高光，选中行文字转白；没有采用 iPad Mail 那种深色侧栏，因为顶栏已经是深色金属，再加深色侧栏会回到上一版的割裂感。
- **作者气泡用绿色**：蓝色留给按钮与选中态，作者消息用 iOS 6 信息的绿色渐变，避免「蓝色控件 + 蓝色气泡」混在一起。
- **结果卡按状态着色**：完成用绿底，部分完成/失败/停止/中断统一用琥珀底并在卡片内写明状态文字，不靠颜色单独表达结论。
- **气泡不做小尾巴**：渐变气泡加描边已经能区分方向，尾巴需要与渐变、边框同时对齐，收益低；本阶段省略。
- **删除而非保留旧样式**：`index.css` 里 0.3 的类全部没有引用（逐类 grep 确认），保留只会让下次改样式时选错类。删除后 `tailwind.config.js` 的旧别名也随之失去意义，一并移除。

## 5. 验证证据

| 项目 | 命令 | 结果 |
|---|---|---|
| 状态单测 | `cd frontend && npx vitest run`（临时目录指向仓库内） | 8 个文件、65 项通过 |
| 类型与构建 | `cd frontend && npm run build` | `tsc --noEmit` 与 `vite build` 通过 |
| 浏览器主链路 | `cd frontend && npx playwright test` | 10 项通过（真服务 + Chromium） |
| 视觉走查 | `cd frontend && node e2e/walkthrough-v04-12-ui.mjs` | 五屏截图，见[视觉证据](../artifacts/v04-12-ui/)：阅读、收起对话栏、拖宽目录栏 + 设定、改动抽屉、模型设置 |

走查结论：三栏均为浅色；目录白色单元格与蓝色选中清晰；对话开关开（蓝）与关（暗）可区分；稿纸米白、横线随内容滚动；抽屉与模型设置对话框原本就是浅色，样式清理后未受影响。

## 6. 偏差与未完成

- 计划提到「可选红色笔记本边线」，本阶段没有加：现有纸面已经能读出稿纸质感，加边线会与横线争夺注意力。
- 计划要求「复用 `chat-*` 类」，实际按当前面板结构重写了一套更小的类集合：0.3 的 `chat-*` 面向带引用条、意图条、任务卡的面板，直接复用会带进大量没有对应 DOM 的规则。
- 稿纸仍限制在 `max-w-3xl`：收起对话栏后两侧留白较大，是为保持阅读行宽；如需更宽的稿纸另开一次调整。
- 未做暗色模式切换：浅色是唯一主题，切换能力不在本版范围。
