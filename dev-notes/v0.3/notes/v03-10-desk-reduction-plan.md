# 写作台再收拢：对话做增删改、设定集只读（V03-10）

> 性质：阶段计划（归档；实施记录见 [V03-10 构建记录](v03-10-desk-reduction.md)）。日期：2026-10-20。
> 依据：2026-10-20 与作者的四轮讨论裁决（见 §1）；延续 [V03-9 前端收拢](v03-9-frontend-collapse.md) 的「只撤前端入口、后端零改动」边界。
> 关联：[0.3 完整开发计划](../development-plan.md)、[写作台模块说明](../modules/desk-surface.md)（当前实现）。

## 1. 作者裁决（2026-10-20）

本轮讨论确认，桌面最终只保留三件：**正文稿纸（完全只读）+ 模型对话（增删改在此发生）+ 设定集（只读查）**。其余「高级功能」先不做。

1. **对话成为唯一操作面**：对话区加自由输入框，回车直接发送一轮（去掉「写想法 → 接着讨论」两步）；设定的增/删/改与正文的生成都在对话里由模型执行。
2. **稿纸彻底只读**：撤掉「手工修改」；正文只能由模型经对话产生，作者手动改稿路径暂时移除。保留「导入正文」。
3. **账本改名「设定集」，且只读**：撤掉「新增设定 / 编辑设定」表单与「待确认设定」独立区块；内容只读，按 4 类分组展示。
4. **设定集只读展示 4 类**：人物 / 线索（含伏笔）/ 时间线 / 谜底（凶手·手法·动机·掩饰）。后端其余 kind（地点、关系、事实、认知·证词、世界规则、故事前提）先不露。
5. **撤下高级入口**：关联图谱、整体调整（修订）、后续状态、操作结果、证据核对、版本历史一并从前端撤下。
6. **候选「采用」保留在对话内**：模型的写操作后端一律先「提议」（`canon_patch_propose` 等保持 `proposed`），作者的「采用」是唯一落盘通道。故撤掉的是**独立区块**（设定集抽屉里的待确认列表 + 「打开审阅」主区视图），**对话内的候选卡 + 采用按钮必须保留**。要「零确认直接改」需改后端，本批不做。

## 2. 目标 / 非目标

**目标**
- 写作台只剩三类入口：正文、对话、设定集标记。
- 对话可自由输入；正文与设定的改动都在对话里发生，且经对话内「采用」落盘。
- 设定集只读、改名、按 4 类分组。
- 删除撤下后无引用的旧前端代码与孤立测试。
- 清除界面开发痕迹。

**非目标**
- 不改后端任何接口、数据与写入规则（`src/hastings/` 无 diff）。
- 不实现「零确认直接改」（那需后端支持自动采用）。
- 不新增功能，不改视觉基调。
- 不改产品方案文字（除「账本」显示名与本次裁决）。

## 3. 目标桌面形态

- **中区**：稿纸。只读展示当前章正文 + 分页/章节目录导航；头部只保留章节目录相关，无任何编辑按钮。
- **右侧固定对话栏**（§6 #1 已定，方案 1）：从上到下为消息区 + 自由输入框（回车发送一轮）；消息区复用 `ChatCards` 渲染模型回复、任务建议、候选卡（含「采用」）与结果；随时可输入，不用切换主视图。
- **左栏**：章节目录 + 「对话历史」入口（见 §6 待确认项）。
- **设定集**：右缘一枚常驻标记「设定集」→ 抽屉，只读、按 4 类分组。
- **隐藏**：图谱、整体调整、后续状态、操作结果、证据核对、版本历史、手工修改、写想法。

## 4. 分模块改动（M1–M8）

> 涉及删除的文件以实施时核对引用为准；标「待核」者需先确认无其他引用再删。

**M1 对话成为唯一输入与操作面**
- 在讨论区加自由输入框：发送即一轮（`send`），替代「存想法 → 接着讨论」。复用 `state/chat.ts` 既有编排，不复制逻辑。
- 删除「写想法」：`desk/DeskComposer.tsx`、`api/entries.ts`(+test)（待核：确认 entries 无他用，如约定/身份）。
- 左栏「想法与讨论」整块移除；新增「对话历史」入口。
- 保留对话内候选卡与 `acceptCandidate`（写通道）。

**M2 稿纸彻底只读 + 撤手工修改**
- 正文 textarea 恒 `readOnly`；删除「手工修改 / 修改中 / 完成修改 / 暂存返回 / 放弃」头部控件。
- 删除 `desk/useEditSession.ts`、`api/editSessions.ts`(+test)。
- 正文头部撤「历史」「证据核对」两枚文字入口。
- 保留 `desk/ImportDialog.tsx`、`api/imports.ts`（导入正文）。

**M3 设定集只读 + 改名 + 4 类分组**
- 显示名改「账本」→「设定集」：`DeskShell.tsx` 的 `RAIL_LABELS.ledger`、`section aria-label`、按钮 aria-label 同步；代码/接口内部名（`ledger`、`/ledger`）不动。
- 删除 `desk/LedgerEditor.tsx`；`LedgerPanel.tsx` 去掉「新增设定 / 编辑设定」按钮。
- 去掉 LedgerPanel 内「待确认设定」独立区块。
- 只读视图按 4 类分组：人物 / 线索（含伏笔）/ 时间线 / 谜底；后端其余 kind 不渲染。
- 前端 `api/ledger.ts` 只保留读取方法，写方法与其测试移除。
- 后端零改动。

**M4 撤高级右栏标记与面板**
- 删除图谱相关：`desk/GraphOverview.tsx`、`GraphPanel.tsx`、`GraphCanvas.tsx`、`GraphDetail.tsx`、`GraphDirectory.tsx`、`GraphViewTabs.tsx`、`TimelinePanel.tsx`、`useGraphData.ts`、`api/graphs.ts`(+test)、`state/graphLayout.ts`(+test)、`graphDirectory.ts`(+test)、`graphLabels.ts`（若仅图谱用，待核）。
- 删除修订：`desk/RevisionPanel.tsx`、`api/revision.ts`(+test)。
- 删除后续状态：`desk/OperationFollowups.tsx`、`api/followups.ts`(+test)；撤 `DeskFollowupSummary`。
- 删除证据核对：`desk/EvidenceCheck.tsx`。
- 删除版本历史：`desk/ChapterHistory.tsx`、`api/history.ts`(+test)。
- 右栏标记只剩 `ledger` 一枚；`DeskShell.tsx` 的 `RailPanel` 与 `railMarks` 相应收窄。
- `state/desk.ts` 移除 graph/history/evidence/revision 相关 `MainView` 变体（待核实际枚举）。

**M5 撤候选的独立审阅视图**
- 删除 `chat/ChatCandidateReview.tsx`；撤「打开审阅」主区 detail/candidate 视图与其导航。
- `state/desk.ts` 的 `candidate` detailKind 一并移除（保留 `desk.test.ts` 中改判后的用例）。
- **保留**：对话内 `CandidateCard` + `acceptCandidate`（写通道）。

**M6 布局与左栏收拢**
- 左栏＝章节目录 + 对话历史；正文常驻中区、对话固定右侧栏（§6 #1 已定，方案 1）。
- 撤「临时视图」（V03-9 已删）等残留。

**M7 清理孤立代码**
- 删除撤下后无引用的组件、api 模块、state 模块与孤立测试（含 manifest 中 `graph`/`impact` 等未用资源，待核：`api/impact.ts`(+test)）。
- 清理已废止的 localStorage 键的读取代码。

**M8 测试 / 走查收敛 → 验收 → 文档同步**
- 更新/新增单测：只读稿纸、对话输入发送、设定集 4 类只读、候选卡采用接线。
- 更新 e2e 走查脚本并重跑归档截图。
- 后端全量 pytest 确认零回归。
- 文档同步见 §7。

## 5. 接口与数据影响

- **后端零改动**：`src/hastings/` 无 diff；路由、410 拒启面、存储与事件流不动。
- 前端删除大量组件与 api 模块（见 §4）；`state/desk.ts` 收窄 `MainView`。
- localStorage：废止的键只删读取代码，不做迁移。

## 6. 实施决策（2026-10-20 落地记录）

1. **正文与对话的排布（已定）**：正文常驻中区 + 对话固定右侧栏（方案 1，2026-10-20 作者选定）。最终四列：左栏（章节目录 + 对话历史）｜中区稿纸｜右侧固定对话栏（消息 + 输入框）｜右缘标记条（只剩「设定集」一枚）。
2. **对话历史**：有现成数据接口（chat store 的 `conversationsByProject` + `selectConversation`/`listConversations`），故左栏渲染可切换的会话列表，高亮当前会话；不引入新端点。
3. **`api/entries.ts` 引用**：仅 `DeskComposer.tsx` 引用 → 随 M1 一并删（含 `entries.test.ts`）。**`api/impact.ts`**：仅 `OperationFollowups.tsx` 引用（随 M4 删），故 `impact.ts(+test)` 一并删。
4. **MainView 收窄**：对话改为常驻右栏后，`state/desk.ts` 的 `MainView` 收窄为 `{ kind: "manuscript"; chapterId } | null`（撤 `discussion` 与 `detail/*` 全部变体）。`desk.test.ts` 同步改判；`selectInitialChapterId` 无正文时返回 null。
5. **候选**：`chat/ChatCards.tsx` 的 `CandidateCard` 撤「打开审阅」（`onReview` 删除），只留「接受候选并提交」；`ChatCandidateReview.tsx` 删除。
6. **设定集 4 类映射（后端 kind 值）**：人物=`story.character`；线索=`story.clue`（「含伏笔」后端 `GET /ledger` 按 `KIND_LABELS` 过滤，未暴露 `story.foreshadow`，后端零改动下暂不显示）；时间线=`story.timeline_event`；谜底=`story.mystery_truth`。其余 kind 前端不渲染。分组标签用类内固定中文名，不依赖后端 kind_label。
7. **`api/ledger.ts`**：只保留 `fetchLedger`（读取）；写方法（create/update/fetchLedgerSchema/fetchLedgerEntry/candidates 系列）随 `LedgerEditor` 删除一并移除；`ledger.test.ts` 同步改判。
8. **壳层文档痕迹**：撤除后 `DeskShell.tsx` 顶部注释重写为收拢后现状。

## 7. 文档同步清单（实施收尾时完成）

- [product-design/README.md](../../../product-design/README.md)：补 2026-10-20 本轮裁决（对话唯一操作面、稿纸只读、设定集只读 4 类、改名）。
- [modules/desk-surface.md](../modules/desk-surface.md)：改写为收拢后的当前行为。
- [release.md](../release.md)：登记撤除入口与保留能力清单、新走查证据与候选验证边界。
- [dev-notes/v0.3/README.md](../README.md)：更新导航。
- 本文件实施完成后移入 `notes/` 归档，并新增构建记录。

## 8. 实施顺序

1. **M1** 对话输入框（先立住唯一操作面）
2. **M2** 稿纸只读 + 撤手工修改
3. **M3** 设定集只读 + 改名 + 4 类
4. **M4** 撤右栏标记与高级面板 + 删组件
5. **M5** 撤候选独立审阅（保留对话内采用）
6. **M7** 清理孤立代码
7. **M6** 布局与左栏收拢
8. **M8** 测试 / 走查收敛 → §验收 → §7 文档同步

任一步验收不过不回写设计文档。全部完成后按 §7 收口。
