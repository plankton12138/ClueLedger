# Phase 19：常驻对话侧栏与显式创作周期

> 状态：**主体交付，代码审查缺陷已修复；真模型 A13 仍待验收**。
> 2026-10-16 的真实接口修复、回归证据与边界见 [代码审查修复记录](phase-19-review-fixes.md)。
> 计划：[plans/phase-19-chat-workbench-plan.md](plans/phase-19-chat-workbench-plan.md)。
> 本文是构建笔记，按子阶段追加；「Agent 设计经验」专节按 AGENTS.md 四透镜提炼。

## 19.1 页面壳与可交互原型

日期：2026-10-16。交付「七页常驻侧栏 + 可控模拟完整流程」第一阶段里程碑。

### 交付内容

- **全局壳常驻对话栏**（`frontend/src/App.tsx`）：七页共用，切页不销毁会话；
  桌面默认 380px、可拖动（320–560px，宽度持久化）、可收起（顶栏开关，
  开合状态持久化）；窄屏（<lg）以覆盖面板经顶栏按钮打开，关闭后返回原页面。
- **对话组件**（`frontend/src/components/chat/`）：消息列表（流式文本、引用来源、
  实际模型、失败/停止状态、「有新回复」跳转）、输入框（Enter 发送 /
  Shift+Enter 换行 / 输入法组合期不发送 / 停止回复 / 逐会话草稿）、
  引用条（当前章、故事对象、创作台选中文本一次性摘录——未保存明确标注）、
  四类任务卡（任务建议 / 执行任务 / 待审阅候选 / 已接受结果）、
  安全 Markdown（禁原始 HTML，按钮只来自结构化 payload）。
- **可控模拟器**（`chat/simulator.ts`）：`?chatMock=1` 或开发模式启用；
  演示逐段回复、停止（服务端取消、迟到分片不出现）、故障注入（三要素错误）、
  任务建议 → 显式开始 → 执行推进 → 候选待审阅 → 接受 → Commit 回读；
  项目隔离、发送幂等、修订校验、取消后迟到输出不发布。
- **创作台与概览调整**：次要说明改为可折叠面板（`CollapsiblePanel`），
  对话栏常驻时默认收拢，保留正文主要空间；创作台编辑器选中文本可
  「引用到对话」。
- **契约先行**：`chat/types.ts` 落实 §6 的 Conversation/Message/Turn/Event/
  TaskDraft 形状与 SSE 事件信封（序号仅在会话内递增）；`chat/store.ts`
  按 19.2 不变式设计（跨项目隔离、分片去重、停止后迟到无效、逐会话草稿、
  轮次模型定格）。
- **走查用例**：`frontend/e2e/v2-chat.spec.ts`（可执行场景清单 + 截图），
  覆盖 12 个场景：七页常驻、切页草稿保留、收起重开、窄屏面板、完整创作流程、
  建议不自动开工、缺项禁用开始、停止保留、故障三要素、重试关联、
  输入法不误发送、旧后端能力提示。

### 验证

- 前端单元 298 项通过（新增 17 项：模拟器 11 + 状态契约 6；`--no-file-parallelism`
  顺序执行，规避本机 Temp 写入限制导致的并发缓存 flaky）；
- `tsc --noEmit`、生产构建通过；
- E2E 全量 15 项通过：`v2-chat` 5 项新旅程 + 既有 `v2-models` 4 项 /
  `v2-workbench` 6 项无回归；截图位于 `frontend/test-results/v2-chat-*/`。

### 已知限制（19.2 起处理）

- 「打开审阅」跳转修订页为占位（19.5 接真实候选详情）；
- 模拟器不代表真实模型行为：流式分片、超时与取消语义以 19.3 真实服务为准。

### 走查反馈与修正（2026-10-16，用户走查后）

- **对话位置**：初版常驻侧栏在创作台形成「目录｜正文｜次要面板｜对话」四列，
  违反计划 §3.1「不硬挤多列」。修正为：对话栏常驻时创作台次要面板（场景事实/
  偏好/恢复）收拢进左列并默认折叠，右列不再渲染，网格回到两列——对话紧贴正文。
- 修掉一条自己引入的 CSS 缺陷：`aside:last-of-type { display:none }` 在右列改为
  条件渲染后误伤唯一左列（章节目录与任务表单消失、正文被挤进 245px 第一列）。
  条件渲染后该规则既多余又有害，已删除。
- 可访问性：顶栏开关与侧栏关闭按钮曾同名「收起对话栏」，改为「显示/隐藏对话栏」
  与「收起对话栏」，避免两个控件同名。
- 回归守卫：新增 e2e「创作台布局：对话栏紧贴正文，次要面板收拢左列」，
  以盒模型断言（次要面板在正文左侧、对话左边缘贴住正文列右边缘、正文宽度
  大于目录列两倍），避免布局再次退化。

## 19.2 前端状态与公开契约

日期：2026-10-16。交付「同一套组件切换模拟服务与真实 API」契约层。

### 交付内容

- **契约层 `frontend/src/api/chat.ts`**（契约事实源，计划 §7 落点）：
  消息/轮次/事件/任务建议/TaskDraft 的数据形状，SSE 事件信封与序号规则；
  `ChatService` 接口（模拟与真实实现同一接口）；`parseSseFrames` 纯函数
  （空行分帧、`data:` 载荷、跨分片残留、非法帧跳过）；`probeChatCapability`
  区分「缺少端点（404/405 → 重启后端指引）」与「网络不可达」；
  `createHttpChatService` 覆盖 §6.2 端点，错误信封复用 `V2Error` 四要素。
- **模块落点对齐计划 §7**：19.1 的 `components/chat/{types,simulator,store}.ts`
  迁为契约 `api/chat.ts`、模拟器 `api/chatSimulator.ts`、状态 `state/chat.ts`；
  组件层只留视图（`components/chat/*.tsx`、`refs.ts`）。模拟器与真实客户端同层同接口，
  仍是开发/`?chatMock=1` 入口，界面常驻「模拟模式」标识。
- **状态层 `frontend/src/state/chat.ts`**：
  - 规范化索引 `views[conversationId] = { messages, turns, abandoned, cursor,
    activeTurnId, pending, loading, error, connection }`——每个会话独立游标与
    活动轮次；流式消息只进对话视图，**不写入正文编辑缓冲**；
  - 纯 reducer `applyChatEvent(view, envelope)`：序号去重、非运行态轮次的
    迟到分片丢弃且不推进游标、终态先冲刷正文再落状态、不修改入参；
  - 服务选择 `selectChatService`：模拟入口 → 模拟服务；否则能力探测 →
    真实 API 或 unsupported（引导重启，不显示假空列表）；支持依赖注入以便测试；
  - 发送结果未知：网络故障不当失败——保留幂等键与草稿，`retryUnknownSend`
    用同一请求重发（服务端幂等，不重复调用模型）；有错误码的拒绝则直接提示；
  - 断线只改连接状态，不把模型执行标记成失败；重连成功以服务端为准补偿
    （`reloadMessages`，保留仍在流式的本地占位）；
  - 逐会话草稿落浏览器存储（`hastings.chat.drafts`，容错解析）。
- **界面状态**：会话探测中 / 会话读取失败（可重试横幅，与「后端缺端点」区分）/
  空历史 / 发送结果未知 / 回复断线，各有明确文案与动作。
- **测试**：`src/api/chat.test.ts` 13 项（端点与载荷、错误信封、能力探测三态、
  SSE 解析与游标续传、关闭后不再重连）；`src/state/chat.test.ts` 15 项
  （reducer 纯度与不变式、会话隔离、草稿持久化、发送未知与幂等重试、
  服务选择、重连补偿、轮次模型定格）。

### 与既有模块的关系

- 聊天客户端不新增写端口：候选接受复用既有审阅规则，任务取消/重试复用
  `/agent-jobs` 领域接口；`V2Error` 与请求辅助从 `api/v2.ts` 复用而非复制，
  避免错误信封语义分叉。
- 19.3 需要服务端提供的接口清单（前端已按此冻结）：`GET/POST /conversations`、
  `GET /conversations/{cid}/messages`、`POST /conversations/{cid}/turns`、
  `GET /conversations/{cid}/events?after=N`（SSE）、turns `cancel`/`retry`、
  项目级 `POST/PATCH /task-drafts`、`POST /task-drafts/{did}/start`、
  `POST /conversations/{cid}/messages/{mid}/accept`（映射回既有 Job 接受规则）。

### 验证

- 前端单元 320 项通过（新增 28 项：API 契约 13 + 状态契约 15）；
- `tsc --noEmit`、生产构建通过；
- E2E 全量 16 项通过（`v2-chat` 6 项含布局回归 + `v2-models` 4 + `v2-workbench` 6）。

### 已知限制

- `acceptCandidate` 的服务端映射端点由 19.5 实现；当前 e2e 走模拟器，
  真实链路未验证（19.3 起接通）。
- 会话分页只按首屏加载，未实现滚动续页。

### 评审记录

- 待 19.3 真实服务接通后补评审。

## 19.3 V2 对话存储与真实流式服务

日期：2026-10-16。交付「服务端会话事实源 + 纯对话流式执行」；前端 19.2 的
`createHttpChatService` 从此有了真实对端。

### 交付内容

- **对话存储**（`infrastructure/conversations/store.py`，T19.3b）：
  Memory / SQLite 双后端，共享基类把状态机写一次——`open_turn` 原子创建
  用户消息与轮次（同会话活动轮次 → `CHAT_CONVERSATION_BUSY`；同键不同输入 →
  `CHAT_IDEMPOTENCY_CONFLICT`；同键同输入幂等重放）；`append_delta` /
  `settle_turn` 只在运行态生效，终态后迟到分片一律丢弃；事件表
  `(conversation_id, sequence)` 保证序号仅在会话内递增。SQLite 与
  Repository/Job 共用 DB 文件、独立连接与 `IF NOT EXISTS` 建表，不触碰
  Repository schema 版本；对话是项目辅助数据，不产生 Revision。
  修复一处 ID 碰撞：同一微秒创建多个会话时 `conv_{hash}` 完全相同，ID 改为
  带随机分量。
- **ChatRunner 端口与适配器**（`application/ports/chat.py` +
  `infrastructure/conversations/runner.py`，T19.3c）：`tools` 恒为空——聊天
  拿不到任何工具，自然没有写端口（四透镜②）；`TextDeltaEvent` 逐段透传，
  `ThinkingDelta` 不下发（不显示内部推理文本）；取消用同步判的
  `TurnCancellationToken`（Provider 协议要求），取消返回部分文本而非报错。
  失败分类是适配器的主要职责：空文本 / Provider 错误事件 / 连接失败 /
  首段超时 / 总时长超时各有稳定 code（`CHAT_EMPTY_COMPLETION` 等六个新码）；
  首段预算与总预算取较小者作为阶段一上限，谁先到期按谁分类——两个超时态
  文案与对策不同。
- **会话应用服务**（`application/services/conversation_service.py`，T19.3d）：
  编排「发送 → 排队 → 执行 → 终态」；模型解析失败也走轮次终态（排队中的
  请求得到明确失败，不悬挂）；全局 `asyncio.Semaphore(max_concurrent)` 有界
  并发，排队轮次停在 queued；取消先置 token 再收束 stopped，部分回复保留；
  重试创建 `retry_of` 链接的新轮次，不覆盖旧回复，非终态轮次拒绝重试；
  `mark_interrupted` 在服务启动时把遗留排队/运行轮次标为中断（幂等），不
  自动重发。服务对象上没有任何 commit/submit/approve 入口（结构断言有测试）。
- **模型接入**（`server/v2_agents.py`）：`ConfiguredV2Runner` 抽出
  `_resolve_profile_model` 供 worker 与聊天共用；`chat_model()` 未配置时抛
  `CHAT_MODEL_NOT_CONFIGURED`；`chat_runner()` 按回环地址放宽预算（本地
  first_token 300s / 总 1800s，云端 60s / 600s）。凭证失效保留选择、不跨
  厂商回退；ProviderBook 由 `app.py` 既有接线注入，设置页切换即时生效。
- **REST/SSE 端点**（`interfaces/api/conversations.py`，T19.3e）：§6.2 的
  `GET/POST /conversations`、`GET /messages`、`POST /turns`、`cancel`、
  `retry`、`GET /events?after=N`。SSE 快照（`tail=0`，返回积压后关闭）与
  长连流式（默认）同一端点；心跳是注释帧，只证明连接存活；归属校验先行，
  跨项目/不存在一律 404 三要素信封。V2Runtime 装配
  `SqliteConversationStore + ConversationService`，路由挂入 `create_v2_router`，
  lifespan 启动时 `mark_interrupted`、退出时 `shutdown`。

### 与既有模块的关系

- 不新建写端口：候选接受（19.5）、任务取消/重试仍走 `/agent-jobs`；聊天
  服务对象经测试断言不存在 commit/submit/approve/accept 入口。
- `ConfiguredV2Runner.adapter()` 行为保持不变（重构仅抽公共解析）；
  worker 与聊天共享 ProviderBook，但失败语义独立（WORKER_* vs CHAT_*）。
- `V2Error` 错误信封复用既有 handler 链；`CHAT_*_NOT_FOUND` 经
  `_status_for` 自然映射 404。

### 验证

- 后端全量 `uv run pytest` 1309 项通过；新增 41 项：runner 契约 13 +
  服务行为 16（幂等/忙/取消竞态/失败分类/重启中断/隔离/重试/并发有界）+
  REST/SSE 12（含「聊天不产生 Commit/Job」与重启恢复）。
- `uv run ruff check src/hastings tests`、`uv run mypy` 通过。
- 真模型走查留待 19.6 统一取证（本地模型 + 国内 API）；当前验证用可控
  FakeProvider，不以模拟宣称真实接入。

### 已知限制

- SSE 由服务端轮询事件表实现（0.25s），不是进程内 pub/sub；单进程部署下
  足够，多进程需换通知机制（记录为 19.6 评审项）。
- 上下文历史按最近 20 条裁剪，未实现 §3.4 的摘要与 omissions 记录。
- `events?after=0` 每次重连全量补齐；会话事件量大后需要快照+增量（§6.3
  「日志裁剪后返回需要重新读取快照」尚未实现）。

### 评审记录

- 待 19.4/19.5 接通任务准备与创作周期后一并评审。

## 19.4 上下文引用与任务准备

日期：2026-10-16。交付「讨论可以变成可检查、可启动的任务，且只有显式路径
能进入创作」；前端 19.2 的 draft 契约（createDraft / adjust / abandon /
start）从此有真实对端。

### 交付内容

- **受限 TaskDraft 域模型**（`domain/conversation/task_drafts.py`）：
  类型/指令/目标/基础版本/上下文/缺失项/状态（draft→started/abandoned）+
  内部启动幂等键（不进前端 payload）。``missing_items`` 是纯函数：plan 只看
  模型；chapter_draft 要求 head、已接受节拍、章节目标；check/targeted_revision
  要求目标对象，修订还要求可靠基础版本——每条缺失项写明哪里缺、为什么、
  怎样继续，缺失为空是「开始」可用的充分条件。
- **TaskDraftStore**（`application/ports/task_drafts.py` +
  `infrastructure/conversations/drafts.py`）：Memory / SQLite 双后端；修订
  冲突（CAS）由服务在进程锁内裁决。``ConversationStore`` 增加
  ``append_message``（任务卡消息载体），对话域模型、事件序号规则不变。
- **TaskPrepService**（`application/services/task_prep_service.py`）：
  - 创建：显式表单或「从消息准备」（仅复制该消息的引用，**草稿不自动附加**
    任何上下文）；创建时定格 head 为基础版本；同步落 kind=task_draft 的消息
    与 `task.draft.created` 事件；
  - 调整：expected_revision CAS（防多标签覆盖），缺失项按当前项目视图重算，
    事件 `task.draft.updated`；已启动的 draft 拒绝调整（补充讨论不改执行中
    的目标，§4.2）；
  - 放弃：幂等；已启动的 draft 走任务取消而不是放弃建议；
  - 启动：缺失项非空 → `TASK_MISSING_ITEMS`（details 带全部缺失）；基础版本
    落后 head → `TASK_BASE_STALE`（不静默换新版本，§3.4）；修订不符 →
    `TASK_REVISION_CONFLICT`；同键重放返回同一 job_ref（不重复开工），不同
    键二次启动 → `TASK_DRAFT_ALREADY_STARTED`。建议本身无执行权限——
    创建/调整/放弃全程不触碰 starter（测试钉住）。
- **引用校验**（发送轮次挂点 `ref_validator`，实现在 V2Runtime）：chapter/
  artifact 引用必须存在于当前项目（跨项目/不存在 → `CHAT_REF_NOT_FOUND`）；
  未保存选文必须携带摘录。校验在装配层，应用服务只持挂点。
- **启动映射**（V2Runtime.`start_task_job`）：plan → `AgentWorkflows.plan`、
  chapter_draft → `app.draft`（目标章节必填）、check → `app.audit`（空目标
  自动圈定 story/narrative 对象）；与 `/agent-jobs` 同一授权与调度，
  启动后 `wake_workers`。targeted_revision 明确拒绝（`TASK_START_UNSUPPORTED`）：
  修订必须走 Change 用例，不静默降级。
- **REST 端点**（`interfaces/api/conversations.py`）：`POST/GET/PATCH
  /task-drafts`、`/abandon`、`/start`，形状与前端 19.2 契约逐字对应；
  项目隔离 404 信封与既有约定一致。

### 与计划 19.5 的边界

启动映射的最小闭环（校验+绑定 job_ref+幂等重放）提前在 19.4 落地，以满足
「可启动」退出条件；19.5 继续做：任务卡投影执行状态、候选接受入口、
「任务已建但卡片未关联」的重启恢复窗口、任务模型快照持久化、旧表单清理。

### 验证

- 后端全量 1347 项通过；新增 38 项：域/存储 12 + 服务 16 + REST 12
  （含「任意文本不能触发 Job」「建议无执行权限」「过期版本不能直接开始」
  「引用不能跨项目」四个计划点）。
- `ruff`、`mypy` 通过。

### 已知限制

- 缺失项的「具体解决入口」目前是文案+ fix 指引；跳转到审阅页/创作台的
  深链交互在 19.5 随任务卡一起做。
- 启动映射未持久化任务模型快照（19.5 交付项）；重启恢复窗口未处理
  （job 已建但绑定未写回时需人工检查）。
- 会话事件尚无裁剪与快照机制，`task.*` 事件随会话历史增长（与 19.3
  同一评审项）。

### 评审记录

- 待 19.5 接通任务卡投影与候选审阅后一并评审。

## 19.5 接入创作周期、任务卡与结果审阅

日期：2026-10-16。交付「任务卡只投影实际 Job、候选经显式接受成为正式内容、
模型绑定随任务定格」；对话里的候选从此能走完真实的 Job → Commit 链路。

### 交付内容

- **候选投影**（`application/services/task_projection.py`）：
  `ChatTaskProjector.sync()` 由 worker_loop 每轮驱动，扫描已启动 draft 对应
  Job 的状态——`waiting` 产生 `candidate.ready` 事件与候选消息（**一次且仅
  一次**，幂等以 `candidate_message_id` 为准）；`completed` /
  `terminal_failed` / `cancelled` 只落定 draft（`job_settled`），**取消的任务
  永远不产出候选**（A16：迟到输出无效）。候选摘要从 worker 输出提取
  （rationale / 按类型说明），不虚构内容。
- **候选接受**（`TaskPrepService.accept_candidate` + `POST
  /conversations/{cid}/messages/{mid}/accept`）：把候选消息映射回 job_ref，
  复用既有 `agent_workflows.accept` 的全部审阅规则（expected_head 校验、
  基础版本冲突、marker Commit 幂等）——服务不实现第二套审阅（§3.3：聊天
  文本不代替审批）。接受成功落 `result.accepted` 事件 + kind=result 的消息
  （job_ref/commit_id/target_ids/summary），候选消息置 `accepted: true`。
  幂等：已接受的候选重放直接返回既有结果消息；缺 expected_head →
  `CHAT_MISSING_BASE_VERSION`；非候选消息 → `CHAT_NOT_A_CANDIDATE`。
- **模型绑定快照**（§19.5「切模型不改变已启动任务」）：
  `ConfiguredV2Runner.model_binding()` 在启动时定格 `{profile, model, api}`
  进 Job payload 与 draft（`record_binding`）；`WorkerJobRunner.execute` 优先
  派发到 `run_with_events_bound`（按绑定解析来源）；绑定来源失效 →
  `JOB_MODEL_SOURCE_UNAVAILABLE`（明确失败，**不静默换厂商**）。旧路径
  （`/agent-jobs` 直发、无绑定）行为不变。
- **重启恢复窗口**（「任务已建但卡片未关联」）：JobStore 按
  (project, job_type, idempotency_key) 去重 + 指纹校验；同键重放启动返回
  同一 Job（集成测试：先经工作流建 Job，再用同键经 draft 启动 → 绑回同一
  job_id）。指纹不同 → 明确的输入冲突，不静默复用。
- **生命周期集成测试**（`tests/v2/server/test_task_lifecycle.py`，脚本化
  worker 不经真模型）：建议 → 显式启动（绑定进 payload）→ worker 执行 →
  waiting → 候选投影 → 显式接受（head 前进到结果 Commit）→ 幂等重放同
  Commit、仅一条结果消息。

### 与既有模块的关系

- 接受链路零新增写端口：`accept_candidate` 只做消息→Job 的映射，
  提交、版本冲突、marker 幂等全部在 `agent_workflows.accept`。
- 投影只读 JobStore；worker_loop 每轮一次 `sync()`，失败被吞但不影响
  任务调度（`suppress(HastingsError)`）。
- 旧任务页 `/agent-jobs` 行为完全不变；聊天侧新增的绑定只在 draft 启动
  路径生效（同一授权与调度，不同入口不产生不同规则）。

### 验证

- 后端全量 1361 项通过；新增 14 项：投影 5 + 接受 5（幂等/非候选/归属/
  缺 head）+ 绑定 4（快照、绑定派发、失效不回退、legacy 无绑定路径）+
  生命周期集成 2。
- `ruff`、`mypy` 通过。

### 已知限制

- 执行卡状态（queued/running/waiting）的中间态推送依赖前端 task.linked
  重建 + candidate.ready 翻转；failed/cancelled 的执行卡终态文案在聊天侧
  仍以任务页为准（19.6 走查决定是否补 `task.execution.settled` 事件）。
- 「提取现有任务展示、清理重复表单」「点击候选打开主区预览（未保存稿
  保护）」属于前端改造，随 19.6 前端联调一起做。
- 多步骤周期（规划→起草连环）未实现自动衔接，仍按 §4.3 保留作者检查点。

### 评审记录

- 待 19.6 整体验收与真模型走查后一并评审。

## Agent 设计经验（分阶段记录；定稿见文末 19.6 节）

### ① 错误消息是喂给模型的 API

- 能力探测区分三态而非「加载失败」：缺少端点（给出重启后端的动作）、网络不可达
  （确认服务）、正常；后两态都可重试，第一态必须改环境。
- 「发送结果未知」独立成态：网络故障不谎报失败，明确说「可能已发出」并给出
  「用同一请求重试」的入口——把不确定性交还给作者，而不是假装成功或假装失败。
- TaskDraft 的缺失项逐条写「哪里缺、为什么、怎样继续」，并直接决定「开始」是否可用；
  缺项文案里出现的就是解决办法（先接受节拍、先保存正文），不是「参数不合法」。

### ② 硬约束放工具层，提示词只负责解释世界

- 聊天客户端不持有任何内容写端口：候选接受、任务取消与重试复用既有审阅与 Job
  领域接口；聊天文本永远不能代替审批。
- 「建议本身不创建 Job」由应用层与启动端点强制，「开始」按钮的前置条件只是它的
  界面投影；模型即使输出「已开始」也改变不了状态（§3.3）。
- 幂等键与序号由契约层固定：重发同键不重复计费、重复分片不重复追加，不依赖界面自觉。

### ③ 泄压阀：违规 / 空结果 / 环境故障三情形分开

- 类型上就分开：`V2Error`（输入违规、版本冲突）→ 直接提示并可改后再发；
  网络故障 → 结果未知（保留幂等键）；空历史 → 合法空态而不是错误。
- 轮次终态区分已停止 / 失败 / 已中断，三者都保留已收到的部分回复；
  「无正文结果」与「执行失败」不共用同一路径。

### ④ 可观测性是事件契约的一部分

- 每条消息带 `turn_id` 与实际模型（发送时定格），每个任务卡带 `job_ref`，
  已接受结果以 Commit 回读为准——身份从生成到生效都能追。
- 断线是连接状态而非执行失败；重连按序号续传并回读服务端，不重放模型调用。
- 事件序号只在会话内递增，与 Job、业务 Commit 游标分别管理，避免三种游标互相污染。

### 19.3 补充：取消、超时与失败分类的裁决权

- **存储层是唯一裁决者**：取消后迟到分片无效不是 runner 的自觉，是
  `append_delta` 在非运行态直接拒绝——竞态正确性放在有锁的临界区里，而不是
  分布在调用方的小心里。
- **超时分类要按「谁先到期」而不是「有没有见过文本」**：首段预算 90s、总预算
  0.05s 时，超时发生在首段等待内但语义是总超时。两个嵌套 `asyncio.timeout`
  抛的异常无法区分，解法是取较小预算做阶段一上限并记录它的语义。
- **模型解析失败也是轮次终态**：排队中的请求如果因为「未配置模型」悬挂，
  用户看到的是无限等待；把它变成 `turn.failed` 事件（三要素齐全），失败也是
  可观测性契约的一部分。
- **测试先行的回报**：先写的失败测试直接暴露了会话 ID 在同一微秒内碰撞的
  存储缺陷——`content_hash` 做身份够用于内容寻址，做实体 ID 必须有随机分量。

### 19.4 补充：建议与执行的边界

- **「建议无执行权限」用测试钉住而不是靠注释**：starter 是构造注入的调用
  记录桩，create/adjust/abandon 的测试全部断言 `starter.calls == []`——
  权限边界是可回归检查的行为，不是文档承诺。
- **缺失项是「开始」的充分条件而不是提示**：服务端 `TASK_MISSING_ITEMS`
  拦截与前端按钮禁用共用同一份缺失数据（`missing` 随 draft 走），两侧不
  各写一套判断，就不会出现「按钮能点但服务端拒绝」或反过来。
- **过期引用分两层**：发送时的引用校验（对象存在于当前项目）拦「引用不能
  跨项目」；启动时的基础版本校验（base_version == head）拦「过期版本直接
  开始」。前者错误可以重选，后者必须重建任务——两种错、两种恢复路径，
  不共用一个错误码。
- **空项目上的 plan 是合法路径**：`missing_items` 对 plan 不要求 head，
  空项目也能规划——「没有内容」和「不能执行」是两个概念，泄压阀允许前者。

### 19.5 补充：投影与绑定的身份链

- **投影的幂等锚点是消息 id 而不是时间窗**：`candidate_message_id` 落在
  draft 上，sync() 扫多遍也只产出一条候选——「幂等」靠状态字段，不靠
  「最近 N 秒内已投影过」这类易碎判断。
- **模型绑定进 payload 而不是进程状态**：Job 自带绑定快照，执行/重试/重启
  恢复读的都是同一份事实；若把绑定放在 runner 的进程变量里，两个并发 Job
  就会互相污染模型选择。
- **恢复窗口的钥匙是指纹一致的幂等键**：同键不同输入是明确冲突（测试里
  先真实复现了这一点），同键同输入才找回同一 Job——「找回」不能放宽成
  「按键找回来就用」。
- **接受入口薄到只剩映射**：chat 侧 accept 只做「消息 → job_ref → 既有
  accept」，审阅规则、版本冲突、幂等全部复用——第二套审阅实现就是第二套
  真相，必然漂移。

## 19.6 整体验收、真模型走查与文档收口

日期：2026-10-16。

### 自动化验收证据

- 后端：`uv run pytest` 1361 项通过；`ruff check`、`mypy` 干净。
- 前端：`npm test` 320 项通过；`npm run build` 通过；`npm run test:e2e`
  **16/16 通过**（v2-chat 7 + v2-models 5 + v2-workbench 4）。
  修正一处随 19.3 失效的用例前提：「旧后端能力提示」改用路由拦截模拟
  404（e2e 服务器已带对话端点，无法再用真实旧后端复现）。
- 环境坑复现：Playwright 清理 `test-results` 触发 safe-delete 批量守卫
  （>50 文件），跑 e2e 前需按文件清空——已记入 19.1 节，本次再次确认。

### 真模型走查（本地 Ollama qwen3.5:4b）

入口 `eval/v2/chat_driver.py`（进程内 V2 应用 + 真实 Provider，HTTP + SSE
全链路）；报告与结论：[eval/runs/2026-10-16-phase19-chat/README.md](../eval/runs/2026-10-16-phase19-chat/README.md)。

- **通过 7/8**：流式多轮（50 个真实增量分片）、只讨论不写入（Commit/Job
  数不变）、多轮上下文、停止后文本冻结（迟到分片无效）、SSE 游标补齐不重复、
  建议不自动开工、显式启动与模型绑定快照进 Job。
- **未通过 1 项（如实标记）**：planner 候选投影——qwen3.5:4b 两轮 Schema
  纠正后仍无法产出合规 `story_proposal`（`WORKER_PROVIDER_INTERRUPTED` →
  `terminal_failed`，三要素完整）。**失败路径本身验证了泄压阀设计**：明确
  失败、无假候选、无静默换厂商。投影与接受链路由
  `test_task_lifecycle.py`（脚本化 worker 全闭环）与
  `test_closed_loop.py`（FakeProvider 回放）确定性覆盖。
- **qwen3:8b 复跑（同日）**：同为 7/8，但失败模式不同——8b 能产出 Schema
  合规的建议，只是 `parent_id` 引用了快照中不存在的对象（幻觉）；系统给了
  一次纠正重试后仍幻觉，按 violation 终止。这补上了 4b 没走到的路径：
  **Schema 合规但事实不合规 → 一次纠正回路 → 仍不合规按违规终止**，纠错
  回路真实存在且只重试一次。报告：[eval/runs/2026-10-16-phase19-chat-qwen3-8b/README.md](../eval/runs/2026-10-16-phase19-chat-qwen3-8b/README.md)。
- 国内 API 无可用凭证，标记为未验项；接入后重跑驱动即可补齐。
- 走查中发现并修复两处驱动缺陷（非产品缺陷）：ASGITransport 不触发
  lifespan，worker 循环需显式启动；思考型模型首段正文可能长期不出现，
  「0 字停止」是合法终态，判据应为文本冻结。

### 文档收口

- `dev-notes/v2/DESIGN.md` 新增 §15「对话侧栏与显式创作周期 as-built」
  （模块事实源表 + 两条状态流边界 + 验收入口）；
- `src/hastings/README.md`、`eval/README.md`、`eval/v2/README.md` 导航更新；
- `frontend/README.md` 新增对话侧栏使用说明与交互约定。

## Agent 设计经验（19.1–19.6 四透镜定稿）

### ① 错误消息是喂给模型的 API

- 能力探测三态（缺端点/网络不可达/正常）各有动作指引；「发送结果未知」
  独立成态并把不确定性交还作者。
- 缺失项文案里写的就是解决办法（先接受节拍、先保存正文），缺失为空是
  「开始」可用的充分条件——前端按钮与服务端拦截共用同一份数据。
- 真模型走查证明失败三要素在链路末端依然成立：4B 模型规划失败时，作者
  在任务页看到的是「哪里错、为什么、怎样恢复」，不是一句「出错了」。

### ② 硬约束放工具层，提示词只负责解释世界

- 聊天 runner `tools` 恒空：没有工具就没有写路径，不需要用提示词求模型
  「不要写」。
- 「建议不创建 Job」由启动端点强制；模型在对话里说「已开始」改变不了
  任何状态——真模型走查 A03/A05 在 4B 模型上同样成立。
- 幂等键、序号、绑定快照全部在契约与存储层：JobStore 同键同指纹去重使
  「重启恢复窗口」成为一个可以测试的性质而不是口头承诺。

### ③ 泄压阀：违规 / 空结果 / 环境故障三情形分开

- 轮次终态四分（完成/停止/失败/中断），失败再按授权/连接/超时/空文本
  细分；「0 字停止」是合法终态——思考型本地模型让这一情形从假设变成
  走查必现。
- 任务失败的三个来源（缺失项拦截 / 基础版本过期 / Provider 失效）各自
  有稳定 code 与不同的恢复动作；4B 模型规划失败走的是环境故障路径，
  明确保留 Context 等待重试，而不是静默换一个模型再试。

### ④ 可观测性是事件契约的一部分

- 每条消息带 turn_id 与实际模型；每个任务卡带 job_ref；候选以 `waiting`
  状态为准、结果以 Commit 回读为准——走查脚本全程只依赖事件与 REST
  快照即可断言所有场景，证明事件流本身就是可审计的执行历史。
- 投影幂等锚在 draft 的 `candidate_message_id` 上：sync 扫多少遍也只有
  一条候选——「事件恰好一次」靠状态字段，不靠时间窗。
- ASGITransport 不触发 lifespan 的教训：依赖「框架会替我启动后台循环」
  的假设在测试与生产拓扑分叉时静默失效——后台循环的启动要显式、可注入、
  可观测。

### 评审记录

- 2026-10-16：作者要求提交并继续；按文件级切分提交（17/18 快照 → 19 全量
  → 测试清理），App.tsx/v2.py 携带跨阶段叠加改动已在提交信息中说明。
- 2026-10-16：作者要求用 qwen3:8b 复跑走查；结论已补入两份报告与 19.6 节，
  A13 仍为未闭环项但纠错回路路径已获得真实模型证据。
- 19.6 验收矩阵：A01–A05、A06（前半）、A07、A09（e2e 覆盖切页/草稿）、
  A13（脚本化闭环）、A16（投影层）、A17（绑定）、A19（e2e 输入法/窄屏）、
  A20（路由拦截）有直接证据；A06 长回复、A08 重启恢复、A10 跨项目晚到、
  A11 引用版本、A14 未保存稿保护、A15 部分接受由单元/集成测试覆盖，
  真模型端到端留待更强模型复跑。

### 已知限制（后续阶段处理）

- 真模型 A13（创作候选投影→接受）未在本地 4B 模型上打通，云端 API 未验证；
- 执行卡终态、候选主区预览和未保存稿保护已在代码审查修复中交付；旧任务表单仍保留兼容入口；
- SSE 为轮询事件表实现（单进程足够）；会话事件无裁剪与快照机制；
- 上下文按最近 20 条裁剪，无摘要与 omissions 记录。
