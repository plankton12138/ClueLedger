# Phase 5 开发计划：正文中心创作台（手动修订 → 对账同步）

> **用户决策插入阶段**：参考 submission/product-prototype 五张原型图重设计页面与功能——正文成为主页面中心，允许作者手动修改；修改后模型须对比差异并更新上下文（本阶段重点）。原路线图 P5（RAG）顺延为 P6。
> 状态：**已完成并关闭**（2026-10-09，R5 验收通过，构建笔记见 [phase-05-manuscript-center.md](phase-05-manuscript-center.md)）。设计稿：[artifacts/p5-mockup.html](artifacts/p5-mockup.html)（三态可切换：A 阅读模式 / B 编辑中 / C 对账同步）。

## 0. 已核实的依赖事实（计划的输入）

| 结论 | 证据 | 对本阶段的影响 |
|---|---|---|
| `WritingSession.prompt()` 空闲时可用，忙时抛 `AgentBusyError`；`follow_up` 排队时机由 harness 决定、无法打运行标记 | [session.py](../src/hastings/server/session.py#L44-L58) | 对账运行需「等空闲后自产 prompt」，自建队列而非 follow_up（D5） |
| `HastingsSession` 是唯一装配点：工具集、system prompt、harness 绑定于此 | [hastings_session.py](../src/hastings/server/hastings_session.py#L38-L55) | 编辑锁注册表与 run_kind 守卫挂在 session 层，工具经闭包读守卫状态 |
| `write_chapter` 固定写 `chapters/chNN.md`，已存在须 `force=true` | [chapter_tools.py](../src/hastings/tools/chapter_tools.py#L82-L89) | 归档与锁检查的接线点；`force` 语义保留，守门在工具内 |
| REST 已有 `GET chapters` / `GET chapters/{id}`，零业务写入口 | [rest.py](../src/hastings/server/rest.py#L201-L250) | 前端正文数据源现成，只缺一个受管写端点 PUT（D3 红线演进） |
| `theme.ts` 是设计令牌单一事实源（ink/gold/paper/planted…） | [theme.ts](../frontend/src/theme.ts) | 本阶段只新增 parchment 系与 recovered 两个令牌，其余复用 |
| `loadSnapshot` 是「重置后重放」语义（P4 验证） | [store.ts](../frontend/src/state/store.ts) | manuscript 切片必须纳入快照重置路径，工作区切换不残留草稿 |
| session.jsonl 经 `message_end` 逐轮持久化 | P4 D1（[phase-04-usability.md](phase-04-usability.md)） | 对账轮进主会话即天然持久化——「会话记忆更新」零额外代码（D5） |
| 五账本经 `bible_upsert`/`bible_query` 读写，`ENTITY_LABELS` 五类注册 | [bible_tools.py](../src/hastings/tools/bible_tools.py#L260) | 对账落账动作的唯一出口；前端账本 Tab 数据源不变 |
| REST 泄压阀三情形惯例（违规 422/409、合法空 200、故障归因） | [rest.py](../src/hastings/server/rest.py#L1-L10) | PUT 与同步端点照此办理 |

## 1. 目标与验收标准

**目标**：把「对话为主」的界面反转为「正文为主」的创作台；作者获得直接修改正文的编辑器；每次保存后由 agent 自动对比新旧版本，把故事账本与对话上下文同步到与正文一致——**正文从此是事实的最高法院**。

可检查的六条：

1. **三栏创作台**：正文居中（羊皮纸阅读模式：章节标题、字数、章节切换、上一章/下一章），左侧主编对话可折叠，右侧故事账本；
2. **手动编辑**：编辑模式下修改正文 → 未保存徽标 → 显式保存（⌘S/按钮）落盘（重启后仍在）→ 可放弃草稿；
3. **对账同步**：保存后自动触发对账运行——对话流出现同步卡片，账本更新与 diff 摘要一致，账本面板自动 refetch；手动「同步上下文」按钮可随时重跑；
4. **人机互斥**：agent 写作中正文只读；对账/写作运行中模型写同章被拒，错误含三要素（FakeProvider 剧本断言文案）；
5. **泄压阀**：空 diff → 「无变更」正常结果；对账运行失败 → failed 状态 + 手动重试，正文与账本不受损；多标签页过期提交 → 409 + 指引；
6. **红线**：tau 内核 diff 为空、零 import tau_coding；REST 业务写入口有且仅有经守门管道的 `PUT chapters/{id}`（红线措辞演进见 D3）；七道门全绿。

**非目标**：
- 不做富文本编辑器（纯文本段落 + 空行分段；Markdown 语法不做工具栏）；
- 不做自动保存（保存意图 = 一次修订，边界必须清晰，见 D2）；
- 不做对账变更的一键撤销（账本变更溯源留到审计员阶段）；
- 不做段落移动检测（moved 识别为 del+add，记录为已知限制）；
- 不做版本回滚 UI（`.revisions` 归档先落盘，消费界面后续补）；
- 不做移动端专门适配（窄屏降级：账本面板折叠，正文保底）。

## 2. 设计决策

### D1 布局反转：三栏创作台，正文是页面中心

- 结构：左（300px，主编对话，可折叠）· 中（flex-1，羊皮纸正文，max-width 680px 保证行长可读）· 右（340px，故事账本）；
- 顶部从单行 header 演进为导航：**创作台**（本阶段）/ **事件流**（DebugView 升级为一级入口，仅重排不做功能增强）/ 语料库（P6 置灰预留）/ 审计中心（置灰）；
- ChatStream / Composer / BiblePanel 组件保留，只换容器位置与样式；新增 ManuscriptPanel；
- 视觉语言对齐原型：深夜蓝底 + 暗金强调 + 羊皮纸正文（唯一浅色面，天然把视线锚在中心）。

### D2 编辑模型：baseline/draft 双缓冲 + 显式保存

- REST `GET chapters/{id}` 载入 → `baseline`；编辑产生 `draft`；`dirty = draft !== baseline`；
- **显式保存**（⌘S / 按钮）：成功后 `baseline = draft`。不做自动保存——一次保存 = 一个完整修订意图，这让 diff、归档、对账都有干净的触发边界；

- 阅读/编辑双模式切换；`session.is_running` 期间编辑态置为只读并横幅提示（互斥的前端半边，服务端半边见 D4）；
- 首行 `# 第N章` 标题行允许作者改标题（正文文件是 SSOT），服务端只校验前缀格式（T2）。

### D3 保存管线：受管写端点 + 归档 + 段落级 diff（服务端计算）

- 新端点 `PUT /api/{slug}/chapters/{id}`，body `{content, baseRevision}`。**红线措辞演进**：原「REST 业务数据零写入口」改为「**写路径必须经守门管道**——agent 走工具层，作者走受管写端点，双入口共享同一套不变量（锁、归档、校验）」。人类作者不是 agent，不可能用工具写正文，这一演进是产品形态的必然；
- 服务端管线（顺序固定）：id 模式校验（沿用 `_CHAPTER_ID_PATTERN`）→ 首行标题校验 → 锁与运行态检查（D4）→ **乐观并发检查**（`baseRevision` 不等于当前版本号 → 409，防多标签页互覆）→ **归档**（旧版拷入 `chapters/.revisions/chNN/NNN.md` + 元数据 JSON：ts、hunks、stats，先归档后写主文件）→ 写入 → 响应 `{revision, diff}`；
- diff 算法：空行分段，`difflib.SequenceMatcher` 产出段落级 hunks `{kind: added|removed|modified, oldIndex, newIndex, oldText, newText}`；**段内字符级高亮由前端做**（前后缀修剪 + 中段标记）——CJK 场景比服务端字符 diff 更稳，hunk 携带整段新旧文本即可；
- diff 是纯函数（store 层独立模块），单测覆盖：空章节、全删、无尾换行、标题行变更、段落重排。

### D4 人机互斥锁：write_guard 守门模块（核心不变量）

- `HastingsSession` 持有内存态编辑注册表 `edit_marks: dict[chNN, EditMark]`（holder="user"、时间戳）；
- 双侧守门，同一模块：
  - **agent 侧**：`write_chapter` 执行前查注册表，目标章被作者锁定 → `is_error`，三要素：「第3章正在被作者手动编辑。为什么错：覆盖会丢弃作者未保存的草稿。怎样算对：等待作者保存或放弃后再写，或提示作者」；
  - **HTTP 侧**：PUT 到达时 `session.is_running` → 409：「agent 写作流进行中。为什么错：并发写同一文件会产生交叠版本。怎样算对：等待写作流结束（状态条消失）后保存」；
- 锁生命周期：保存成功 / 放弃草稿即释放；内存态不持久化（进程重启自然清空）。已知限制：单用户假设下不做锁超时抢占、不做跨进程锁；
- 对账运行期间（run_kind=reconcile）`write_chapter` 同样被拒——**模型不得在对账时替作者改稿**，这条硬约束放在工具层而不是 prompt 里（对账运行与作者编辑共用同一条不变量：正文只经由作者的手或显式写作流变更）。

### D5 对账同步管线（本阶段重点）：一处修订，两级上下文

手动修改破坏的不是一份文件，而是**两级上下文**——结构化的故事账本，与 agent 的会话记忆。对账管线把两者一次补齐：

- **触发**：保存成功后服务端自动入队（同章 3s debounce 合并连续保存）；「同步上下文」按钮手动立即触发；`PUT` 带 `sync=false` 可只存不同步；
- **排队**：`HastingsSession` 内置对账队列，订阅事件流等 `agent_end` 且空闲时出队——**用户对话优先**，作者插话则对账让位到下一轮空闲；
- **运行**：对账以一条紧凑的 user 消息进入**同一 harness 会话**（不是第二个 agent——理由：会话记忆更新因此零成本，事件流/持久化/互斥全部复用既有设施）。消息模板（`prompts.py` 新模板）：
  - `【手动修订同步】第3章已被作者直接修改，以下是相对上一版本的差异：`
  - hunk 摘要：每段一行（kind + 前 40 字预览 + 段号），上限 20 段，超出截断并注明（全文可用 read_chapter 自取）；
  - 相关账本条目：按变更段落文本对五账本实体名做匹配，只附命中条目——P3「精准 refetch」思想的服务端镜像，控制 token；
- **落账**：模型逐 hunk 判断叙事影响，必须经既有 `bible_*` 工具落账（不变量照旧：格式校验、覆盖确认）；结论逐条输出，结尾一句总结（更新几处、哪些需作者注意）；
- **可观测性（协议扩展）**：ws 层新增 `notice` 帧 `{type: "notice", kind: sync_queued|sync_start|sync_end, chapter, hunks?, updates?, status?}`——hastings 协议层的加法扩展，tau 事件帧契约本身不动；前端凭 notice 把运行窗口标记为「对账中」，并渲染同步卡片；
- **失败路径**：模型出错/中止 → `sync_end{status:"failed"}` + UI 重试按钮；**正文永不回滚**——文本是 SSOT，对账失败只影响账本新鲜度，不影响作品本身。

### D6 对账提示词契约

- **定位（T7 用户澄清）**：对账的首要目的是让模型**习得作者本人的写作习惯与文风**（用词、句式、节奏、叙事偏好），改变「谁向谁靠拢」的方向；线索/设定细节主要由内置功能（账本 + 工具）承载，对账对账本的维护是**次要的附带一致性校正**；
- 角色校准（模板首句）：「你是风格观察者兼台账校对员，不是作者。这次修订的首要目的是让你习得作者本人的写作习惯与文风……其次是让五本账与正文保持一致。正文是作者的作品——不评判文笔、不重写段落、不调用任何写正文的工具」；
- 尾指令同样风格优先：先体会作者改动的文风取向并作为后续创作参照，再判断叙事影响、经 `bible_upsert` 校正受影响的账本条目；
- system prompt 追加一行对账协议说明（`【手动修订同步】`开头的消息按对账协议处理，首要是习得作者文风、其次校正账本），保持「prompt 解释世界、工具执行约束」的分工；
- 已知风险——模型过度更新（把 diff 之外的旧内容也翻新进账本）：输入只含 hunk 上下文（不含全章）天然抑制；提示词明确「只处理列出的变更段」；对账明细卡片让每次落账对作者可见（人审在事后，撤销能力留待审计阶段）。

### D7 前端：manuscript 切片与三个界面态

- store 新增 `manuscript` 切片：`{chapterId, baseline, draft, revision, saveState: clean|dirty|saving|error, syncState: idle|queued|syncing|done|failed, diff: Hunk[]|null}`；随 `loadSnapshot` 重置（工作区切换不残留草稿）；
- ManuscriptPanel 三态即设计稿 A/B/C：阅读（羊皮纸 + 首字下沉）/ 编辑（等宽纸面 + 未保存徽标 + 保存/放弃）/ 对账（diff 视图：段落级色块 + 段内字符高亮，新增绿/删除红/修改金）；
- 对话流同步卡片：notice 驱动（queued → syncing → 完成明细）；账本面板在 `sync_end` 后 refetch（复用 P3 D7 精准 refetch）；
- diff 段内高亮：前后缀修剪定位变更中段，删除红删除线、新增绿底，段级左侧色条。

### D8 设计令牌扩展

- 新增：`parchment: #F2ECD9`（纸面）、`parchmentInk: #2B2417`（纸面墨色）、`parchmentMute / parchmentLine`（纸面次级）、`recovered: #C8A84D`（已回收 = 暗金，补齐伏笔生命周期第四色）；
- 正文排版规格：Noto Serif SC 15px / 行高 2.0 / 首行缩进 2em / 段距 1.35em；首字下沉仅阅读模式；纸面 max-width 680px 居中，深色环境中的浅色面即为视觉焦点。

## 3. 任务分解

### T1 三栏布局 + ManuscriptPanel 阅读模式 → CP1

- theme 扩展（D8）、App.tsx 三栏改造（对话可折叠）、导航条；
- ManuscriptPanel：章节列表（GET chapters）、阅读渲染、章节切换、字数、上一章/下一章；
- vitest：章节切换加载、工作区切换重置；**CP1**：浏览器冒烟（现有工作区正文可读可翻页）。

### T2 保存管线（测试先行）

- store 层 diff 纯函数模块：分段、SequenceMatcher hunks、revision 计数、标题行校验；
- `.revisions` 归档（先归档后写主文件；写坏即抛、主文件未动）；
- PUT 端点：守门接线 + 乐观并发 409 + 泄压阀三情形；REST 测试钉契约。

### T3 write_guard 互斥锁（测试先行）

- EditMark 注册表 + 双侧守门；FakeProvider 剧本：作者锁 3 章 → agent `write_chapter(force=true)` 断言 is_error 且文案含三要素；
- REST 测试：is_running 时 PUT → 409；保存/放弃释放锁。

### T4 对账运行 → CP2

- 对账队列（debounce 合并、等空闲、用户优先）、紧凑消息构造（实体名匹配选账本条目）、run_kind 守卫（reconcile 期间写正文工具被拒）；
- notice 帧协议（ws.py + types.ts 同步），ws 测试钉住帧形状与顺序；
- **CP2**：FakeProvider 端到端——手改正文 → PUT → 断言对账轮进入主会话消息、`bible_upsert` 被调用、notice 序列 queued→start→end 完整。

### T5 前端同步 UI（vitest 剧本）

- manuscript 切片 + 保存/放弃 + ⌘S；notice 处理与同步卡片；diff 视图（段落色块 + 段内高亮）；sync_end 后账本 refetch；
- 剧本：保存 → queued/syncing/done 状态迁移与卡片渲染；失败 → failed + 重试按钮。

### T6 事件流一级入口（轻量）

- DebugView 迁入导航「事件流」，样式对齐新设计语言；不增强功能。

### T7 七道门 + 红线 + 浏览器人工验收

- 真实端点场景验收：手改「九点四十七分」→「十点零七分」→ 保存 → 对账卡片 → 时间线账本自动更新；agent 写作中编辑被锁场景；证据存 `artifacts/`。

### T8 构建笔记 + R5 关闭

- `phase-05-manuscript-center.md`：D1–D8 落锤、四透镜专节（预期重点：互斥不变量的工具层落点、对账消息的 token 经济学、notice 帧与事件契约的加法扩展、正文 SSOT 的失败隔离）、七门与红线输出；DESIGN.md 路线图标注顺延（RAG → P6）。

## 4. 评审方案

- **R1** 每笔提交前七道门（照旧）；
- **R2** 红线：内核 diff 为空 + 零 import tau_coding + REST 业务写入口 grep 复核（有且仅有 PUT chapters/{id}，且必须经守门管道）；
- **R3** code-review skill 全量 diff，重点清单：①守门检查与 async 的竞态（PUT 与工具 execute 在同一事件循环，查 await 点两侧的检查原子性）；②归档失败时主文件必须未动（写顺序断言）；③对账队列与用户 prompt 的竞争（空闲判断到 prompt() 提交之间无 await 点）；④notice 帧在 reducer 未知事件路径的处理（不得静默吞）；⑤工作区切换时 manuscript 草稿残留；⑥diff 边界（空章/全删/无尾换行/标题行变更）；⑦`.revisions` 目录的路径安全（chapter id 模式已闸，复核归档写路径拼接）；
- **R4** CP1（T1 浏览器冒烟）/ CP2（T4 端到端对账）；
- **R5** DoD 逐条打勾，通过 = 本阶段关闭。

## 5. DoD

- [x] 三栏创作台：正文居中阅读模式（标题/字数/章节切换/翻页），对话可折叠，账本右栏
- [x] 编辑模式：dirty 徽标、显式保存落盘（重启仍在）、放弃草稿
- [x] 保存后自动对账：同步卡片 + 账本更新与 diff 一致 + 账本面板自动 refetch；手动重跑可用
- [x] 互斥：agent 写作中正文只读；对账/锁定期间写同章 is_error 三要素（FakeProvider 断言）
- [x] 泄压阀：空 diff → 无变更正常结果；对账失败 → failed + 重试且正文账本无损；过期提交 409
- [x] 红线：内核 diff 空、零 tau_coding、REST 写入口仅守门 PUT（构建笔记声明红线措辞演进）
- [x] 七道门全绿
- [x] 浏览器人工验收存档；构建笔记发布；R3 blocker 清零

## 6. 风险与对策

| 风险 | 概率 | 对策 |
|---|---|---|
| 对账模型过度更新账本（翻新 diff 之外的旧条目） | 中 | 输入只含 hunk 上下文；prompt 明确「只处理列出变更段」；对账明细卡片事后可审（撤销留审计阶段） |
| PUT 与工具 execute 的守门竞态 | 低 | asyncio 单线程 + 检查点无 await；R3-① 专查 |
| 大 diff 撑爆对账消息 | 低 | hunk 摘要上限 20 段 + 截断注明；全文靠 read_chapter 自取 |
| CJK 编辑边界（IME、无尾换行、全角空格缩进） | 中 | 纯 textarea 受控组件；保存前服务端统一空白规约；T2 边界用例 |
| 多标签页编辑互覆 | 中 | 乐观并发 baseRevision → 409 三要素指引；单页内 last-write-wins 接受 |
| 归档写失败导致半更新状态 | 低 | 先归档后主文件；任一步失败主文件未动（T2 断言） |
| contenteditable 保真差 | — | 已规避：P5 用 textarea，不做富文本 |

## 7. 执行顺序

```text
T1 三栏布局 + 阅读模式 → CP1
→ T2 保存管线（归档 + diff + PUT，测试先行）
→ T3 write_guard 互斥（测试先行）
→ T4 对账队列 + 紧凑消息 + notice 帧 → CP2
→ T5 前端同步 UI（vitest 剧本）
→ T6 事件流一级入口
→ T7 七道门 + 红线 + 浏览器人工验收
→ R3 代码评审
→ T8 构建笔记 + R5 关闭
```

提交策略照旧：原子提交、测试先行；每笔提交前经用户确认。
