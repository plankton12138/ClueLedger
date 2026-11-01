# 1.0.6 构思总览与正文一致性（定向实现完成）

> 2026-11-01 后续修复：明确写作请求不再因为「构思页可见」或句中出现「根据构思」而被受理为 `design`；作者回答「关闭构思页，直接重写这两个文件」时也会重新判定用途。修复记录见本文「用途判定修复」。

运行版本 **1.0.6**。本版把构思推进为全书大纲，并让正文与构思之间形成可追踪、可确认的双向联动；同时取消「本章线索」独立面板，正文中的已登记线索直接显示下划线。

## 已实施

### 构思总览

- 新增节点正文契约摘要与绑定状态：`aligned / outline_ahead / prose_ahead / diverged / unchecked / waived / outline_only / unlinked`。
- 新增 `GET /story/variants/{variant_id}/sync` 只读接口，返回节点、章节、计数和机械冲突。
- 构思页增加「全书写入状态」条，可点击定位待同步、冲突、待写节点。
- 时间轴节点增加章节标签和状态点；目录章节增加同步状态点。

### 正文线索内联

- 新增 `GET /clue-marks?chapter_path=...`，只从已提交账本证据生成标注。
- 正文中的已登记线索句子直接下划线；点击打开左栏「全书线索」详情，右侧对话保持。
- 稿纸工具栏增加「线索标注」开关。
- 删除「本章线索」面板及未使用的章节线索详情组件；正文和构思左栏统一为「目录 / 全书线索」。
- 正文摘要变化时隐藏旧偏移，并提示「正文已修改，线索标注暂不可用」。

### 双向一致性

- `ProseBinding` 增加 `contract_digest`、`waived_digest`、`waived_note`；契约按节点投影，不使用整份方案修订号。
- 结构变更影响已绑定正文时，提交返回 `STORY_SYNC_CONFIRM_REQUIRED`，附带冲突摘要和选项。
- 新增 `SyncDecision`：作者确认后选择 `follow_outline / follow_prose / defer`；提交时重算冲突摘要，过期决定返回 `STORY_SYNC_CONFLICT_STALE`。
- `follow_prose` 自动更新绑定契约；`defer` 自动记录已确认差异；`follow_outline` 仍需模型提交正文补丁或候选正文。
- 新增 `read_story_sync` 只读工具；`update_story`、`propose_story_structure`、`propose_story_scene` 支持 `sync` 参数。
- 候选核对把缺失线索呈现句改为 blocking finding；`present:` 通过结论必须引用候选原文。
- 写作上下文增加「本轮必须披露」区块，明确呈现句必须原样出现。

### 正文归属补丁（2026-11-01）

- 节点状态新增 `unlinked`：存在有正文但尚未归属的章节时，未绑定节点显示「待核对」，不再显示「未写」。
- `bind_prose` 只需提交 `node_id`、`path`、`quote`、`observation`；偏移、正文摘要和契约摘要由服务端按当前正文计算，引文必须唯一出现。
- 构思从正文反建或章节写入后，提示词要求模型为对应节点提交 `bind_prose`，不再要求模型自己算偏移。
- 目录里的未归属章节说明改为「这一章有正文，还没有归属到构思节点」。

### 段落级锚点（2026-11-01）

- `ProseBinding` 增加 `span_digest` 和 `source`；同步状态按引文所在区间比较，不再因同章其他段落修改而误报。
- 章节新增 `partial` 状态：已有锚点、但仍有正文没有归属时显示「部分归属」；未绑定节点在该章存在未归属正文时显示「待核对」。
- 新增 `GET /story/variants/{variant_id}/prose-marks`，返回本章锚点的节点、区间、引文、来源与状态。
- 正文页增加「构思出处」条；点击节点芯片定位并高亮对应原文。
- 多节点候选会为能原样找到呈现句的每个节点分别建立锚点；找不到的节点仍需模型显式提交 `bind_prose`。
- 线索下划线优先用账本证据；账本没有本章贡献时，回退到锚点区间内的线索用途匹配，避免把同一节点的多条线索同时标到所有段落。

### 视觉

- 顶栏模式切换改为「正文 / 构思 / 模块库」图标式分段控件，使用深蓝选中态。
- 左栏标签、构思状态条、节点卡片、目录状态点与正文线索样式按 1.0.6 预览调整。
- 节点卡的动作文字默认显示两行；超过时提供「展开 / 收起」，不再只能看省略号。
- 对话气泡样式保持不变。

## 主要文件

- `src/hastings/workspace/story_sync.py`
- `src/hastings/workspace/clue_marks.py`
- `src/hastings/workspace/story_models.py`
- `src/hastings/workspace/story_store.py`
- `src/hastings/workspace/story_api.py`
- `src/hastings/workspace/story_tools.py`
- `src/hastings/workspace/story_candidate.py`
- `src/hastings/workspace/story_review.py`
- `src/hastings/workspace/story_prose_marks.py`
- `src/hastings/workspace/story_context.py`
- `src/hastings/prompts/authoring.py`
- `src/hastings/prompts/story.py`
- `src/hastings/prompts/tools.py`
- `frontend/src/api/story.ts`
- `frontend/src/api/clueMarks.ts`
- `frontend/src/state/story.ts`
- `frontend/src/components/story/StorySyncStrip.tsx`
- `frontend/src/components/story/StoryMysteryView.tsx`
- `frontend/src/components/story/StorySurface.tsx`
- `frontend/src/components/workspace/ClueMarks.ts`
- `frontend/src/components/workspace/Paper.tsx`
- `frontend/src/components/workspace/WorkspaceToc.tsx`
- `frontend/src/components/workspace/WritingLayout.tsx`
- `frontend/src/index.css`

## 验证

- 后端：新增同步读模型、同步闸门、候选同步决定、线索标注测试；全量 `tests/workspace` 回归 1408 项通过，唯一失败是版本断言，已同步到 1.0.6 并单独复验通过。
- 前端：33 个测试文件、347 项单测通过；`tsc --noEmit` 与 `npm run build` 通过。
- 浏览器（8010，1440×900）：构思总览状态条、节点章节/状态、目录状态点、正文线索下划线、点击打开左栏线索、标注开关、无「本章线索」tab；控制台无 error。
- P1 补丁：新增 span 区间状态、partial 覆盖、prose-marks、多线索锚点匹配与多节点候选测试；相关后端同步/候选/工具/投影 79 项、线索标注 5 项，前端同步与线索标注 5 项通过，`tsc` 与 `npm run build` 通过。

## 用途判定修复

- 服务端 `_explicit_prose_request` 不再把「根据构思」当作阻止写作的词；改为按正文目标判断。
- 识别「前两章」「这两个文件」「第一章的正文」「重写」等说法的正文意图；只有当前阅读位置是正文时，「这两个文件」才算正文目标。
- 构思页开着时，前端对明确写作请求发送 `purpose: "write"`，不再只按页面可见性发送 `design`。
- 问题回答沿用原请求材料时，服务端也会重新识别作者回答；「已关闭构思页，直接重写」不会再停留在设计用途。
- 回归：新增服务端用途判定与候选工具测试，前端新增 `taskPurpose` 测试及构思页显式写作发送测试；相关后端 133 项、前端 33 个文件 347 项通过。

## 已知边界

- 多节点候选只会为能在正文里原样找到呈现句的节点自动绑定；其余节点仍需模型显式提交 `bind_prose`。
- 线索下划线仍优先使用账本证据；没有账本贡献时才按锚点匹配，多线索段落匹配不到具体线索时不强行标注。
- 语义冲突仍由候选核对和模型核对承担；没有后台全书自动审计。
- 外部编辑正文只能通过摘要/引文失配标记「待同步」，不会自动调用模型。
- 多个节点共用一个新章节时，只有能找到唯一呈现句的部分会自动绑定；其余仍需模型显式提交 `bind_prose`。
- `follow_outline` 的正文改写仍需要模型在同一候选或后续写正文中提交正文补丁。
- 旧数据不迁移；没有 `contract_digest` 的旧绑定显示为「未核对」。
