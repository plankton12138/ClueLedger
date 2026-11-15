# V03-10 写作台再收拢：对话做增删改、设定集只读（2026-10-20 实施）

> 阶段开发记录；范围、待办与测试结果均对应记录时点。当前行为见[写作台模块说明](../modules/desk-surface.md)，剩余缺口见[运行与验收状态](../release.md)。计划与设计示例见[再收拢计划](v03-10-desk-reduction-plan.md)。

## 目标

按 2026-10-20 六项裁决把写作台再收拢为「对话做增删改」的形态：桌面只剩正文稿纸（完全只读）、模型对话（自由输入，增删改在此发生）、设定集（只读查，按 4 类分组）。撤掉手工修改与独立候选审阅，撤下图谱、整体调整、后续状态、操作结果、证据核对、版本历史等高级入口，删除撤下后无引用的前端代码，清除界面开发痕迹。后端零改动，撤下的都是前端入口。

## 实际改动（M1–M8）

- **M1 对话成为唯一操作面**：讨论区加自由输入框（`ConversationComposer`，回车发送一轮、Shift+Enter 换行、组合输入期间不提交），复用 chat store 的 `setDraft`/`send`，不复制编排；左栏「想法与讨论」整块移除，改为可切换的「对话历史」会话列表。删 `desk/DeskComposer.tsx`、`api/entries.ts`(+test)。
- **M2 稿纸彻底只读 + 撤手工修改**：正文 textarea 恒 `readOnly`，撤「手工修改 / 修改中 / 完成修改 / 暂存返回 / 放弃」头部控件与「历史」「证据核对」入口。删 `desk/useEditSession.ts`、`api/editSessions.ts`(+test)。保留 `ImportDialog`、`api/imports.ts`。
- **M3 设定集只读 + 改名 + 4 类分组**：`LedgerPanel` 重写为只读组件，按人物 / 线索 / 时间线 / 谜底 4 类分组（`kind` 用后端值，标签用类内中文名），其余 kind 不渲染；显示名「账本」→「设定集」（内部名 `ledger` / `/ledger` 不动）。删 `desk/LedgerEditor.tsx`；`api/ledger.ts` 只留 `fetchLedger`。
- **M4 撤高级右栏标记与面板**：删图谱组件（`GraphOverview` / `GraphPanel` / `GraphCanvas` / `GraphDetail` / `GraphDirectory` / `GraphViewTabs` / `TimelinePanel` / `useGraphData`）与 `api/graphs.ts`、`state/graphLayout.ts`、`graphDirectory.ts`、`graphLabels.ts`；删修订（`RevisionPanel` / `api/revision.ts`）、后续状态（`OperationFollowups` / `api/followups.ts`、`api/impact.ts`）、证据核对（`EvidenceCheck`）、版本历史（`ChapterHistory` / `api/history.ts`）及各自测试。右栏标记只剩「设定集」一枚；`state/desk.ts` 的 `MainView` 去 graph/history/evidence/revision 变体。
- **M5 撤候选独立审阅**：删 `chat/ChatCandidateReview.tsx`；`CandidateCard` 去 `onReview`（只留「接受候选并提交」）；`TaskExecutionCard` 的 `onView` 改为可选；撤「打开审阅」主区视图与导航。对话内候选卡 + 采用（唯一落盘通道）保留。
- **M7 清理孤立代码**：撤下后续状态 / 操作结果面板后，操作结果缓存（`operations` / `applyOperationResult` / `lastOperationId` / `setLastOperation`）失去消费方，一并移除；删 `api/desk.ts`(+test)。
- **M6 布局与左栏收拢（四列）**：左栏（章节目录 + 对话历史）｜中区稿纸｜右侧固定对话栏（消息 + 输入框）｜右缘标记条。对话不再占用主区；`MainView` 收窄为 `{ kind: "manuscript"; chapterId } | null`。中屏及以下（<1024）左栏、对话栏、右缘标记折叠为抽屉（正文头部「目录 / 对话 / 侧栏」）。
- **M8 测试 / 走查收敛**：重写 `DeskShell.test.tsx`（只读稿纸、固定对话栏、设定集标记、无开发痕迹）；`state/desk.test.ts` 改判；`e2e/v2-ledger.spec.ts` 重写为只读 4 类走查；新增 `e2e/walkthrough-v03-10.mjs`；文档同步见 §7。

## 接口与数据影响

- 后端零改动：`src/hastings/` 无 diff；路由、410 拒启面、存储与事件流不动。
- 前端删除大量组件与 api 模块；`state/desk.ts` 收窄 `MainView`；`state/deskStore.ts` 只留 `setProject` / `navigate` / `saveReadingPosition`。
- localStorage：无废止键需清理（遍历确认无遗留读取代码，不做迁移）。

## 关键取舍

- 「操作结果缓存」（operations / applyOperationResult / lastOperationId）在操作结果面板撤下后成为孤立子系统，随 M7 一并移除，不做保留。
- `api/identities.ts` 的 `OperationEnvelope` 失去生产消费方，但作为与后端 `identities.py` 配对的 wire 契约模块保留，避免删掉契约测试。
- 四列布局以可读性定断点，不把三列按比例缩小：≥1024 四列，<1024 主区优先、左右入抽屉。

## Agent 设计经验

本批次只撤前端入口，后端不动，无 Agent 设计改动。一处与 Agent 协作链路相关的取舍：撤掉「写想法 → 接着讨论」两步后，作者意图经对话自由输入直接进入一轮，模型的写操作仍一律先「提议」、作者的「采用」是唯一落盘通道——把「模型提议 + 作者决定」这条链路收敛到单一操作面，避免多处入口各自表述。

## 验证证据

- 前端 `npm run build`（`tsc --noEmit` + `vite build`）通过；bundle 由 593kB 降到 362kB；`npm test` **147 passed（18 文件）**。
- 四窗口走查 `frontend/e2e/walkthrough-v03-10.mjs` **38/38 通过**，控制台无 error；截图归档 `artifacts/visual-walkthrough-10/`。正文区宽度：1440 → 730px、1280 → 570px、1024 → 314px、390 → 372px；正文字号 18px / 行高 32px / 行宽 40.6em，四组对比度 ≥ 4.5。
- e2e Playwright：`v2-ledger.spec.ts`（只读设定集走查）通过；整体 4 passed / 1 failed，唯一失败为 `v2-models` 的 Ollama 下载流程，为既有失败，与本批次改动路径无关。
- 后端全量 `uv run pytest` **1995 passed**（本批次未动后端）。

## 偏差与未完成项

- 候选链路的浏览器级验证仍受限：写作台 e2e 使用的定速假模型只返回固定文本、不产生 task_draft / candidate；对话输入框已可用，但候选卡渲染与采用进攻链路改由组件层与模拟器服务级单测覆盖，浏览器级完整链路仍需真实模型。已在 [release.md §5](../release.md) 登记。
- 窄屏（<1024）右侧固定对话栏折叠为「对话」抽屉；右缘标记折叠为「侧栏」入口。
- 真实浏览器 200% 缩放与中文输入法仍以等价视口、合成组合事件代理（沿用既有口径）。
- 后端能力（手工修改副本、候选采用、图谱、整体调整等）保留但前端未接通，属已声明的产品取舍，不是缺口。

## 下一步

无（本文件对应批次收口）。
