# Phase 5 构建笔记：正文中心创作台（手动修订 → 对账同步）

> 对应 [phase-05-manuscript-center-plan.md](phase-05-manuscript-center-plan.md)，2026-10-09 完成并关闭。
> 结论：**R3 评审 7 项全数修复，R5 验收通过，P5 正式关闭**；原路线图 P5–P7（RAG/审计/评测）顺延为 P6–P8。
> 用户决策背景：参考 submission 五张产品原型，把「对话为主」的界面反转为「正文为主」的创作台——作者可直接改正文，
> 改完由 agent 对比差异并同步上下文。**正文从此是事实的最高法院**。
> 关键词：三栏创作台、正文 SSOT、write_guard、对账同步、notice 帧

## 1. 交付内容

提交：`78078c0`（P5 计划 + 三态创作台设计稿，D1–D8 决策 + T1–T8 / R1–R5）→ `938adba`（T1 三栏创作台 + `ManuscriptPanel` 阅读模式 → **CP1**）→ `221b2cb`（T2 受管写端点 `PUT chapters/{id}`：段落级 diff + `.revisions` 归档 + 乐观并发 409 + 泄压阀三情形）→ `4534f3f`（T3 `write_guard`：`EditMark` 注册表 + agent/HTTP 双侧守门）→ `327c3a4`（T4 对账管线：debounce/等空闲/用户优先 + `run_kind` 守卫 + `notice` 帧 → **CP2**）→ `e13da51`（T5 前端同步 UI：编辑器 + diff 视图 + 同步卡片 + 手动重跑）→ `3df1b01`（T6 事件流入口迁入导航，仅重排不增强）→ `bdc5105`（T7 验收存档 [artifacts/p5-acceptance.md](artifacts/p5-acceptance.md)）→ `f2a0a4b`（对账定位澄清：首要目的＝习得作者文风，账本校正降为次要）→ `d440041`（R3 修复×7，见 §4 评审记录）。

## 2. 关键决策（D1–D8 落锤）

### D1 布局反转：三栏创作台，正文是页面中心

左（主编对话，可折叠）· 中（羊皮纸正文，max-width 680px 保行长）· 右（故事账本）。
深夜蓝底 + 暗金强调 + 唯一浅色面（羊皮纸）——视觉上把视线锚在正文，是对「对话为主」的物理反转。
ChatStream/Composer/BiblePanel 只换容器位置与样式，新增 `ManuscriptPanel` 一个组件。

### D2 编辑模型：baseline/draft 双缓冲 + 显式保存

`GET chapters/{id}` → `baseline`；编辑产生 `draft`；`dirty = draft !== baseline`。
**不做自动保存**——一次保存 = 一个完整修订意图，让 diff、归档、对账都有干净的触发边界。
R3-② 修正：保存成功后 baseline 取**本次提交的文本**（而非当前 draft），保存往返期 textarea 置只读，
否则窗口内的新输入会被晚到的响应追认为已落盘。

### D3 保存管线：受管写端点 + 归档 + 段落级 diff（服务端计算）

`PUT /api/{slug}/chapters/{id}`，body `{content, baseRevision}`。**红线措辞演进**：原「REST 业务数据零写入口」
改为「**写路径必须经守门管道**」——agent 走工具层、作者走受管写端点，双入口共享同一套不变量（锁、归档、校验）。
人类作者不可能用工具写正文，这条演进是产品形态的必然。

管线顺序固定：id 模式校验 → 首行标题校验 → 锁与运行态检查（D4）→ 乐观并发检查（`baseRevision` 不符 → 409）
→ **归档**（旧版落 `.revisions/chNN/NNN.md` + 元数据 JSON）→ **原子写入** → 响应 `{revision, diff}`。
diff 用 `difflib.SequenceMatcher` 产出段落级 hunks；**段内字符高亮由前端做**（前后缀修剪 + 中段标记），
CJK 场景比服务端字符 diff 更稳。

### D4 人机互斥锁：write_guard 守门模块（核心不变量）

- **agent 侧**：`write_chapter` 执行前查 `EditMark` 注册表，目标章被作者锁定 → `is_error` + 三要素；
- **HTTP 侧**：PUT 到达时 `session.is_running` → 409 + 三要素；
- 对账运行期间（`run_kind=reconcile`）`write_chapter` 同样被拒——**模型不得在对账时替作者改稿**，
  这条硬约束放在工具层（不是 prompt）：正文只经由作者的手或显式写作流变更。

R3-① 修正：锁生命周期必须跟随「UI 编辑态」而非组件实例——切章节/切工作区/退出编辑态都必须释放，
否则服务端 `EditMark` 留在原章，agent 对该章被长期拒绝。落点抽成纯函数 `state/editLock.ts`。

### D5 对账同步管线：一处修订，两级上下文

手动修改破坏的是**结构化故事账本**与 **agent 会话记忆**两级上下文，对账一次补齐：

- 保存成功自动入队（同章 3s debounce 合并）；「同步上下文」按钮手动立即触发；
- **对账以一条紧凑 user 消息进入同一 harness 会话**（不是第二个 agent——会话记忆更新因此零成本，
  事件流/持久化/互斥全部复用既有设施）；等 `agent_end` 且空闲时出队，**用户对话优先**；
- `notice` 帧 `{kind: sync_queued|sync_start|sync_end, chapter, hunks?, updates?, status?}`——
  hastings 协议层的**加法扩展**，tau 事件帧契约本身不动；
- 失败路径：模型出错/中止 → `sync_end{status:"failed"}` + UI 重试；**正文永不回滚**——文本是 SSOT。

R3-⑤/⑥ 修正：泵让位后需「等闲重试」（`agent_end` 可能在泵被调度前投递，此后无事件 → 队列永久停滞）；
`resync` 需先取消同章 debounce（否则同一修订被对账两遍）。

### D6 对账提示词契约（T7 用户澄清后的定位）

对账首要目的是让模型**习得作者本人的写作习惯与文风**（用词、句式、节奏、叙事偏好），
改变「谁向谁靠拢」的方向；账本校正降为**次要的附带一致性校正**。角色校准为「风格观察者兼台账校对员，不是作者」，
尾指令同样风格优先。已知风险（模型过度更新账本）用三重抑制：输入只含 hunk 上下文、prompt 限定「只处理列出的变更段」、
对账明细卡片事后可审（撤销留审计阶段）。

### D7 前端：manuscript 切片与三个界面态

store 新增 `manuscript` 切片；三态即设计稿 A/B/C：阅读（首字下沉）/ 编辑（textarea + dirty 徽标）/ 对账（diff 色块 + 段内高亮）。
同步卡片随快照重放清空；账本面板在 `sync_end` 后 refetch（复用 P3 精准 refetch）。
manuscript **不随 `loadSnapshot` 重置**（CP1 修正）——重连/重放是同一工作区的状态更替，正文与未保存草稿必须保留；
重置点唯一在 `setSlug`（换工作区）。

### D8 设计令牌扩展

新增 `parchment`（纸面）/`parchmentInk`（纸面墨色）/`parchmentMute`/`parchmentLine`/`recovered`（已回收=暗金，
补齐伏笔生命周期第四色）。正文排版：Noto Serif SC 15px / 行高 2.0 / 首行缩进 2em / 段距 1.35em。

## 3. Agent 设计经验（四透镜）

### 3.1 错误消息是喂给模型的 API——互斥拒绝的三要素

`write_guard` 拒绝 `write_chapter` 时的消息不是「操作被禁止」，而是三要素：「**哪里错**（第3章正在被作者手动编辑）、
**为什么**（覆盖会丢弃作者未保存的草稿）、**怎样算对**（等待作者保存或放弃后再写，或提示作者）」。
HTTP 侧同一配方换读者：409 给的是「等待写作流结束（状态条消失）后保存」。FakeProvider 剧本断言文案含三要素——
把「拒绝」变成「引导模型走向合法路径」，模型才不会陷入对抗式重试。

### 3.2 硬约束放工具层，prompt 只负责解释世界

P5 最有价值的一条经验：「对账期间模型不得改稿」这条约束**没有写进 prompt 求配合**，而是挂在工具层——
`run_kind` 守卫在 `write_chapter` 执行前拦截。同理作者草稿锁、路径安全、标题前缀格式全部在工具/端点层强制。
prompt（D6 契约）只解释世界：对账消息为何开头、首要是习得文风而非改稿。**能靠结构保证的，绝不靠模型自觉。**

### 3.3 泄压阀：三情形在对账与保存各有落点

| 情形 | P5 落点 | 行为 |
|---|---|---|
| 违规（参数/输入不合法） | 标题前缀格式错；过期 `baseRevision`；对账期写正文；作者锁章 | 422/409 三要素；工具 `is_error` + 修正指引 |
| 合法但空结果 | 空 diff 保存；无可对账 hunk；新工作区无章节 | **正常返回**（空 diff 是合法 no-op，不归档不 bump 不打扰） |
| 环境故障 | 归档/写盘 `OSError`；对账运行异常 | 主文件保持原样 + stderr 三要素；`sync_end{failed}` + 重试按钮 |

关键区分：空 diff 不是错误（否则模型/用户会陷入「为什么什么都没发生」的困惑），故障不污染作品
（归档先于写入 + 原子替换，任一步失败主文件都原样）。

### 3.4 可观测性是事件契约的一部分——`notice` 是加法扩展，契约外事件不静默吞

- `notice` 帧是对账进度的**显式契约**：queued→start→end 三段驱动前端同步卡片与徽标；
- 前端对**契约外事件**（未知 event type / 未知 notice kind）一律经 `reportUnhandled` 上报进
  `unhandledEventTypes`（R3-④），跨连接保留——协议演进出的新 kind 不会被静默吞掉，调试视图留着证据；
- `ToolResultMessage.details` 结构化数据仍留给前端/审计（同一事件流服务两类读者：人看卡片、机器看 details）。

## 4. 评审记录（R3 独立子代理全量 diff）

范围 `bff623b..f2a0a4b`（51 文件，8 新增模块）。计划 §4 七项清单逐条查验：①守门/async 竞态 ✅；
②归档失败主文件未动 ✅；③队列竞争 ✅；④notice 未知路径 → 见 #4；⑤切工作区草稿残留 ✅（但残留服务端锁 → #1）；
⑥diff 边界 ✅；⑦`.revisions` 路径安全 ✅。无 blocker，产出 7 项非阻塞发现，**用户选择全部修复**（`d440041`）：

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| ① | 中 | 编辑态切换章节/工作区时不释放服务端 `EditMark` → agent 对该章长期被拒 | 新建 `state/editLock.ts` 纯函数，`ManuscriptPanel` effect 随 (slug, chapterId, mode) 释放；7 vitest 用例 |
| ② | 中 | 保存成功后 `applySaved` 以当前 draft 为 baseline → 保存窗口内新输入被追认为已落盘 | baseline 取提交快照；保存往返期 textarea `readOnly`；新增回归用例 |
| ③ | 中 | 章节列表拉取失败经 `chapterFailed` 的 id 竞态守卫被整条吞掉（未选章时 id 为 null） | 新增 `setManuscriptError` 不经守卫；新增用例 |
| ④ | 中 | 契约外 `notice.kind` 在 `nextSyncState` 默认分支静默吞 | 返回 null → 走 `reportUnhandled`，进 `unhandledEventTypes`；新增用例 |
| ⑤ | 中 | 对账泵让位后仅等下一次 `agent_end` 起泵，而该事件可能早于泵调度 → 队列永久停滞 | 新增 `_retry_when_idle` 轮询等闲重踢；新增时序用例 |
| ⑥ | 中 | `resync` 不清同章 debounce → 同一修订被对账两遍 | `resync` 先 `cancel` 定时器并清 `_pending`；新增用例 |
| ⑦ | 低 | 主文件 `write_text` 直接覆盖会先 truncate，中途 OSError 留半截；500 文案归因失实 | 改 `tmp + replace` 原子替换；500 文案说明「归档先于写入、覆盖为原子替换」；新增用例 |

## 5. 踩过的坑

1. **交接总结与代码库实际状态不符**：上一会话交接声称 T2–T7 已完成，但 git 与代码库只有 T1 一笔提交，
   T2–T8 实际未开始。**教训**：接手时以 `git log` + 代码实况为准，交接总结只作线索——这是本阶段最大的流程教训。
2. **TestClient 每请求独立事件循环**：PUT 循环上创建的 debounce 定时任务随请求结束被连带取消，`sync_end` 永不到达。
   修法是 `with client:`（长驻 portal，HTTP 与 WS 复用同一循环，对齐 uvicorn 生产拓扑）。
3. **对账泵「等闲重试」**（R3-⑤）：`agent_end` 在 harness 置闲**前**投递，泵任务被延后调度时仍见 `is_running=True` → return，
   此后无任何事件 → 队列永久停滞。会话协议没有「运行结束」信号，只能轮询等闲。这是一类**纯事件驱动会漏掉的边界**。
4. **实体名匹配剧本**：对账消息按变更段文本对五账本实体名做匹配——测试用真实实体名而非占位串，否则匹配恒空、测不到位。
5. **陈旧 dev server 伪装成业务报错**（验收发现）：运行中的进程早于 T2/T5 启动，缺新路由 → `PUT edit-mark` 返回 405（静态托管兜底）。
   教训：涉及后端契约新增的阶段验收，**重启服务 + 核对 openapi 路由**应列入验收清单前置步骤。

## 6. 验证输出存档（R3 修复后复跑，全绿）

七道门全绿：pytest 337 通过、vitest 99 通过（8 文件）、ruff / format / mypy（67 文件零豁免）/ tsc+build 全过。红线（`git diff` + grep 复核）零违例：tau_agent / tau_ai 与上游逐字节一致（diff 0）；hastings 对 tau_coding 零 import（仅 ws.py / cli.py 2 处 docstring 提及设计出处）；正文写端点有且仅有 `PUT /api/{slug}/chapters/{id}`（守门管线，另计 POST workspaces 资源创建、PUT/DELETE edit-mark 锁，非内容）；stories/ 运行数据不入库（git status 仅 ?? .vscode/）。

## 7. DoD 对照（计划 §5）

| DoD | 证据 |
|---|---|
| 三栏创作台：正文居中阅读模式 | [p5-acceptance.md §1](artifacts/p5-acceptance.md)：三栏快照 + 羊皮纸渲染（891 字） |
| 编辑模式：dirty 徽标、显式保存落盘、放弃草稿 | §2：Ctrl+S 保存成功 + §3 归档证据；放弃走 vitest/store 剧本 |
| 保存后自动对账 + 手动重跑 | §3：卡片 `对账中… → ✓ 已同步`，账本 `t_end 00:50 → 01:07`；重跑走 ws/socket 单测 |
| 互斥：写作中只读 + is_error 三要素 | §4：真实写作流中「编辑」页签 disabled；服务端 409 由 T3 单测钉住 |
| 泄压阀：空 diff / 失败重试 / 过期 409 | §5：pytest（rest/ws/reconcile）+ vitest（store/socket）剧本钉住 |
| 红线 | 本笔记 §6（含 D3 措辞演进声明） |
| 七道门全绿 | 本笔记 §6 |
| 验收存档 + 构建笔记 + R3 blocker 清零 | p5-acceptance.md + 本笔记 + §4 七项修复 |

R5 验收（2026-10-09）：计划 §5 八条全部通过（证据见上表与 [p5-acceptance.md](artifacts/p5-acceptance.md)），
七道门复跑全绿（本笔记 §6），红线四项零违例，R3 七项修复复跑无回归。**P5 正式关闭，可进入 P6（RAG 语料库）**；
开工时先写 `phase-06-rag-plan.md`，核实 Chroma + BGE-M3 在 Windows 上的安装性。

## 8. 已知留白（转入后续阶段）

- **无版本回滚 UI**：`.revisions` 归档已先落盘，消费界面（历史对比/回滚）留待后续；
- **段落移动识别为 del+add**：不做 moved 检测，大段重排会产生较多 hunk（记录为已知限制）；
- **对账账本变更无一键撤销**：明细卡片事后可审，撤销能力留审计员阶段；
- **无段落移动 / 无自动保存**：设计取舍（D2 显式保存边界）；
- **单用户锁假设**：`EditMark` 内存态不持久化，不做锁超时抢占、不做跨进程锁。