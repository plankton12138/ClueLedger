# V04-11 布局交互：阶段记录

> 日期：2026-10-21。范围：对话栏显隐、目录栏与对话栏宽度拖拽、稿纸横线随内容滚动。视觉统一（iOS 6 浅色化）属 V04-12，未在本阶段实施。
> 计划入口：[写作台界面开发计划](../workspace-ui-plan.md)。现状行为已整理进[写作台界面](../modules/desk.md)。

## 1. 目标

修掉三个界面缺陷：对话栏无法收起、三栏宽度写死、稿纸横线不随内容滚动。不改后端，不改对话与会话行为。

## 2. 实际改动

| 文件 | 改动 |
|---|---|
| `frontend/src/state/layout.ts` | 新增布局偏好状态：目录栏宽、对话栏宽、对话栏显隐；`parseLayout` 负责解析与钳制，读写浏览器本地存储（键 `hastings.workspace.layout`），存储不可用或内容损坏时回默认值 |
| `frontend/src/state/layout.test.ts` | 新增：钳制取整、缺省/损坏回退、显隐保留、写入内容、写入失败不抛错 |
| `frontend/src/components/workspace/ResizeHandle.tsx` | 新增通用把手：pointer 事件拖动、`invert` 处理右侧栏方向、双击复位；另加键盘微调（方向键 16px、Shift 48px、Home 复位）与 separator 语义 |
| `frontend/src/components/workspace/WorkspaceShell.tsx` | 三栏由写死 grid 改为 flex + 宽度来自 layout store；栏间插入把手；顶栏加「对话」开关；无作品时不再渲染空白右栏 |
| `frontend/src/components/workspace/Paper.tsx` | 横线承载层从滚动容器拆到内层 `.paper-lined` |
| `frontend/src/components/workspace/ConversationPanel.tsx` | 去掉根节点左边框（改由把手分隔），其余行为不变 |
| `frontend/src/index.css` | `.paper-sheet` 只留纸底与投影；新增 `.paper-lined`；`.chat-resize` 改为布局内的 `.resize-handle` |
| `frontend/e2e/workspace.spec.ts` | 新增四项：对话栏显隐与刷新保持、目录栏拖拽与复位、对话栏拖拽与上限、横线随滚动；用例顺序调整，新建作品的用例移到最后 |

## 3. 接口与数据影响

- 后端接口、请求与响应结构无改动。
- 新增一份浏览器本地偏好（三栏宽度与对话栏显隐），只影响本机显示，不进入作品目录与 Git。
- 宽度区间：目录 180–400（默认 260），对话 280–560（默认 380）；越界写入按边界存。

## 4. 实现取舍

- **显隐用条件渲染**：收起时不渲染对话面板，中区直接接过宽度；会话状态在 `state/conversations.ts`，不受影响。没有做「收成窄边条」的中间态。
- **拖动即写入**：拖动过程中每帧写 store 并落本地存储，宽度变化只引起壳层一次重渲染，不需要拖拽暂存、松手提交的额外状态。
- **横线按装饰处理**：线格周期 32px 与段落行高一致，段落区能对齐；标题、列表高度不是行高倍数，跨过它们后不保证对齐，本阶段不为此统一所有 Markdown 元素行高。
- **键盘可达**：把手除了拖动还支持方向键与 Home，避免只能靠指针调整宽度。

## 5. 验证证据

| 项目 | 命令 | 结果 |
|---|---|---|
| 状态单测 | `cd frontend && npx vitest run` | 8 个文件、65 项通过（含新增 layout 用例） |
| 类型与构建 | `cd frontend && npm run build` | `tsc --noEmit` 与 `vite build` 通过 |
| 浏览器主链路 | `cd frontend && npx playwright test` | 10 项通过（原 6 项 + 新增 4 项，真服务 + Chromium） |

沙箱内跑 vitest 会因临时目录写入权限出现 EPERM（与本阶段改动无关），重跑时关闭沙箱即恢复；`frontend/test-results` 累积过多时会被批量删除保护拦下，改用 `--output` 指向新目录。

## 6. 偏差与未完成

- 计划的把手示例只含拖动与双击，实现时补了键盘操作与 ARIA 语义。
- 计划把 `.chat-resize` 直接改造复用，实际改名为 `.resize-handle`，避免与 0.3 遗留的绝对定位用法混淆；`chat-overlay` 等其它遗留类留给 V04-12 清理。
- 视觉仍是深色底：本阶段只做交互，iOS 6 浅色化与对比度核对属 V04-12。
- 窄窗口下没有响应式重排，仍靠手动收对话栏或缩目录栏腾空间。
