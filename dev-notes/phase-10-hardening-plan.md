# Phase 10 开发计划：工具层加固（弱模型压测驱动）

> **立项动机**：2026-10-11 本地弱模型（qwen3:8b）对照压测（[eval/runs/2026-10-11-local/report-local-qwen3.md](../eval/runs/2026-10-11-local/report-local-qwen3.md)）把「模型自觉」从「系统机制」里剥离出来：DeepSeek 走查零违规的 intake 协议与正文落盘，本质是强模型在替软协议兜底。两个硬门缺口（正文直出零拦截 L1、intake 抢跑建账 L2）导致 harness 臂 0 章成稿；外加 context 管理缺陷（L7 状态注入 CLI 链路不刷新）与协议缝隙（L5 选项块）。本阶段全部收口。
> **追加（同日）L9 长篇上下文零兜底**：策略是「不压缩」，短篇靠状态外置 + 快照注入 + hunk 上限成立；但每章正文全文固化在 assistant 的 write_chapter tool_call args 里（工具结果只回摘要行），中文每章 3000 字 ≈ 1500–2000 token，50+ 章仅正文 args 即 75k–100k token，128k 窗口必爆，本地 32k 窗口 10 章上下即险——全项目无估算、无阈值、无压缩（D8 收口）。
> **优先级**（报告结论）：L1/L2 最优先（工具层硬门）→ L7（刷新点共享）→ L3（F1 示例，优先级上调为刚需）→ L8（driver 可观测）→ L5（选项协议）→ L4（F2 复现项）。
> 状态：**已立项（2026-10-11）**——计划已评审待执行。

## 0. 已核实的依赖事实（计划的输入）

| 结论 | 证据 | 对本阶段的影响 |
|---|---|---|
| `before_tool_call` 返回 `(False, reason)` 时，loop 生成 `_error_result(reason)` 作为工具结果喂回模型 | [loop.py](../src/tau_agent/loop.py#L298-L303) | L4 单回合 advance 限次的拒绝通道现成，错误消息即纠偏消息 |
| `harness.steer(content)` 公开存在，steering 在工具回合边界消费、纯文本收尾时留队等下一轮 | [harness.py](../src/tau_agent/harness.py#L121-L126) | L1 正文直出检测器的注入通道现成；纯文本收尾场景提醒延迟生效可接受 |
| `AgentStartEvent` 每次 `prompt()` 触发一次，`harness.subscribe` 支持多消费者 fan-out | [harness.py](../src/tau_agent/harness.py#L147-L153)、events_store 先例 | L4 回合计数器：AgentStartEvent 重置 + 事件订阅，零内核改动 |
| write_chapter 阶段门先例：`stage not in _WRITING_STAGES` → 三要素 ToolInputError | [chapter_tools.py](../src/hastings/tools/chapter_tools.py#L81-L122) | L2 intake 写账门照抄同款模式（写路径统一前置检查） |
| `_refresh_system` 的刷新语义只在 HastingsSession.prompt 入口，CLI `run_repl`（cli.py:217）与 eval driver 直连 `harness.prompt` | [hastings_session.py](../src/hastings/server/hastings_session.py#L189-L220) vs [cli.py](../src/hastings/cli.py#L217)、driver.py:74 | L7 提取共享函数，三个调用点统一 |
| 前端 `parseOptions` 严格解析（非选项行/键不连续即整块放弃、返回原文），测试 12 例全绿 | [options.ts](../frontend/src/components/options.ts#L55-L78) | L5 不放宽解析（连续性约束是防误判底线），只加「格式异常」可观测提示 |
| bible_upsert 各实体类型共享 execute 闭包，参数校验集中在 `_require` 系 | [bible_tools.py](../src/hastings/tools/bible_tools.py) | L2 门与 L3 示例文案各一处改动全类型生效 |
| `build_harness` 是 CLI/eval 装配点，已挂 `make_event_persister` 订阅 | [cli.py](../src/hastings/cli.py#L109-L128) | L1 检测器、L4 守卫同款装配，一处接线两端生效 |
| `harness.replace_messages(...)` 是内核公开接口（tau_coding/session.py 三处调用先例） | [harness.py](../src/tau_agent/harness.py)、tau_coding/session.py:1060 | L9 压缩的内存消息替换通道现成，零内核改动 |
| 完整压缩蓝图在 tau_coding 参考层：估算器（provider usage 锚定 + 字符 fallback）、摘要提示词、keep-recent 预算（20k）、阈值=窗口−reserve（16k） | [context_window.py](../src/tau_coding/context_window.py)（不参与构建，须移植不能 import） | D8 照搬骨架，摘要模板与 token 换算按小说场景重做 |
| 持久层 `CompactionEntry` + `_apply_compaction` 重放支持压缩留痕；但 Hastings 的 session_persist 用 `JsonlSessionStorage` 线性 append（仅 MessageEntry） | [memory.py](../src/tau_agent/session/memory.py#L100-L106)、session_persist.py | D8 持久化适配点：压缩后归档旧 session.jsonl + 重写（P9 reset 的 archive 手法） |

## 1. 目标与验收标准

**目标**：把弱模型压测暴露的两个硬门缺口补成工具层硬约束，修复 context 管理缺陷，收敛协议缝隙——「门是硬约束（工具层），推进是协议（prompt）」的 P8 哲学补完到全部写路径。

可检查的十条：

1. **intake 写账门（L2）**：stage=intake 时 `bible_upsert` / `method_lock` / `method_unlock` 被拒，错误三要素含逃生门（先 story_stage 推进）；story.yaml 不存在的旧工作区不拦（不强制迁移旧数据）；
2. **正文直出泄压阀（L1）**：writing/revising 阶段 assistant 长文本（>600 字）含章节标题特征时，steering 注入纠偏提醒（同回合至多一次）；WORKFLOW_SECTION 明示「正文唯一交付通道是 write_chapter」；CLI/driver 转录含告警行；
3. **回合规速（L4/F2）**：单次 prompt（一次 AgentStartEvent 到 AgentEndEvent）内第二次 `story_stage advance` 被 before_tool_call 拒绝，理由三要素；
4. **错误消息示例（L3/F1）**：outline 的 name 空/类型错、method 的 alibi_claims.day 等高频 schema 错误，错误消息按实体类型附最小正确示例；
5. **刷新点共享（L7）**：`refresh_system_prompt(harness, workspace)` 单一事实源，HastingsSession / run_repl / eval driver 三点调用；CLI 4 轮对话中每轮 system 含最新阶段与账本计数（测试断言）；
6. **选项协议收敛（L5）**：WORKFLOW_SECTION 候选协议补「一次只问一个决策点；多维度逐块输出」+ few-shot 强化；前端解析失败但标记存在时消息卡显示「候选方案格式异常」提示（不吞内容）；
7. **driver 可观测（L8）**：成稿合并 0 章时 stderr 三要素告警非静默成功；eval/README 补本地模型跑法（num_ctx 派生、占位 key、timeout）；
8. **上下文压缩（L9）**：估算器 + 两档压缩（确定性正文剥离为主力、LLM 摘要为兜底）落地；`context_window_tokens` 进 settings；压缩触发时 keep-window 外的 write_chapter args 被占位符替换且 tool_call_id 配对不变；压缩后 session.jsonl 归档重写；摘要 provider 失败时 stderr 三要素告警不断流（测试覆盖四情形）；
9. **复跑验证**：L1/L2 修复后换新 slug 复跑 C1 双臂（本地 qwen3-32k），harness 臂成稿 ≥1 章、无 intake 抢跑，补 CP2 盲评产物；
10. **红线**：tau 内核 diff 为空（before_tool_call/steer/replace_messages/事件均为现有公开接口）、七道质量门全绿、测试先行（每项先写失败测试）、构建笔记含四透镜专节。

**非目标**：

- 不做作者确认的结构化交互卡（沿用 P8 非目标，Web 端确认卡移交后续阶段）；
- 不做多维度选项块的解析支持（协议侧收敛为单块，前端只加提示不放宽解析）；
- 不改迁移表拓扑（F3 的 writing→bible_drafting 回退边另议：WORKFLOW_SECTION 已明确「账本修改任何阶段可用」，写作期改设定走 bible_upsert 直改，回退边属产品决策不在本阶段）；
- 不做输出侧正文的内容级检测（标题正则 + 长度阈值即止，不上 LLM 判定）；
- 不做 token 精确计数（估算即触发条件，provider usage 有则锚定、无则字符 fallback）；
- 不做 CompactionEntry 树形持久化迁移（session.jsonl 归档重写已满足「可找回 + 可恢复」，树形会话不在 Hastings 范围）；
- 不动 tau_ai / tau_agent / tau_coding。

## 2. 设计决策

### D1 intake 写账门（L2）：数据面拦截、控制面放行

- **拦数据面**：`bible_upsert` / `method_lock` / `method_unlock` 在 stage=intake 时拒绝（timeline_check 等只读工具放行）。错误消息：
  > 「写入被拒：当前阶段是 intake。为什么错：intake 是构想接收阶段，设定未经作者确认不得落盘。怎样算对：先完成 intake 三段式（五维摘要→缺口清单→关键问题）并经作者确认，用 story_stage 推进到 bible_drafting 后再写入；若作者已明确要求开始建账，直接推进即可。」
- **放控制面**：`story_stage advance`（intake→bible_drafting）不加门——抢跑的实际危害是设定未经确认落盘，advance 本身零数据写入且留痕可审计；错误消息的逃生门保证「作者明确要求建账」的合法路径永远畅通，形成「控制面前置、数据面在后」的完整闭环。
- **旧工作区泄压阀**：story.yaml 不存在（P8 之前的工作区）不拦——`load_story_doc` 兜底读出的 intake 不代表真实状态，按项目「不强制迁移旧数据」原则放行；仅在 story.yaml 存在且显式 stage=intake 时拦。stderr 不告警（合法场景）。
- **实现位置**：`bible_tools.py` execute 闭包开头统一前置检查（一处改动六类型生效）；`method_tools.py` 的 lock/unlock 同款。检查函数放 `story_state.py`（纯函数：stage → bool + 拒绝文案），保持工具层薄。

### D2 正文直出泄压阀（L1）：三层防线，检测器走 steering

三层，每层独立可测：

1. **prompt 显式化**（软）：WORKFLOW_SECTION 的 writing/revising 节加一句「正文的唯一交付通道是 write_chapter 工具；在回复中直接输出章节正文视为未完成写作——作者端不会保存对话中的正文」。同时 intake 节已有禁止语不动。
2. **检测器 + steering 注入**（半硬）：新模块 `src/hastings/manuscript/prose_guard.py`——事件订阅者 `ProseLeakGuard(workspace, harness)`：
   - 触发：`MessageEndEvent`（assistant 纯文本消息）且 stage ∈ {writing, revising} 且非空白字符数 > 600 且章节标题正则命中（`^#{1,3}\s*(ch\d+|第[一二三四五六七八九十百\d]+章)` 多行匹配，或 `^#\s*《.+》`）；
   - 动作：`harness.steer(纠偏消息)`（模型继续工具回合时立即消费；纯文本收尾则留队下一轮生效）+ stderr 三要素告警（CLI 转录可见）；同回合只注入一次（标记位，AgentStartEvent 重置）；
   - 纠偏消息：「检测到你在回复中直接输出了章节正文。正文唯一交付通道是 write_chapter 工具，作者端不保存对话中的正文。请将刚才的内容（或按章纲重写）经 write_chapter 落盘；如刚才只是讨论性文字，请忽略本提醒并继续。」——泄压阀三情形中的「合法但易混淆」情形有逃生门，防道歉-重试循环（四透镜③）。
   - 阈值与正则是策略不是约束，做成模块常量便于测试调整；误报成本 = 一条可忽略的提醒，漏报成本 = 空稿——阈值取宽松值（600 字）偏向捕获。
3. **可观测信号**（观测）：CLI `run_repl` 与 driver 在 prompt 循环结束处检查「stage ∈ writing/revising 且 chapters/ 为空」时输出 stderr 提示行（不阻断退出码，复跑验收 T8 依赖此信号快速定位）。

- **装配**：`build_harness` 处订阅（与 make_event_persister 同款），一处接线 CLI/eval/Web 三端生效。注意 Web 端 HastingsSession 自建 harness，需在 `from_workspace` 同款挂载——两处装配点以 `attach_prose_guard(harness, workspace)` 辅助函数统一。
- **不做**：不在 MessageUpdateEvent 流式过程中判定（流式中间态无语义）；不用 LLM 判定内容（成本与确定性都不可接受）。

### D3 回合规速（L4/F2）：before_tool_call 计数

- 新类 `StageAdvanceGuard`（挂 `hastings/manuscript/` 同级或 tools 层）：
  - `AgentStartEvent` → 重置计数（事件订阅）；
  - `before_tool_call(call)` → `call.name == "story_stage"` 且 `arguments.action == "advance"` 且计数 ≥1 → 返回 `(False, 拒绝消息)`：「本回合已 advance 过一次。为什么错：一次推进应对应一次作者确认，连续推进会让作者失去确认点。怎样算对： bible_confirmed 等确认点应停留等待作者下一条消息；若作者确已要求连推，等作者回复后再推进。」；否则计数 +1 返回 `(True, None)`；
  - rollback 不限次（作者驱动推翻设定的合法高频操作）。
- **依赖事实**：loop 对 `blocked=True` 生成 `_error_result(reason)` 喂回模型（§0 表第 1 行），拒绝即纠偏。
- **验收校准**：A 组用例轮次 4 的「连续两次 advance」场景改写为回归测试（FakeProvider 剧本：第二次 advance 断言被拒文案）。
- **为什么不用 stage_history 时间戳**：进程内计数器语义精确（=本回合），时间戳阈值在快慢模型下都脆弱。

### D4 错误消息最小示例（L3/F1）

- bible_tools 的参数校验错误路径按实体类型附示例，`_EXAMPLES: dict[entity_type, str]`：
  - outline：`data.name 是章纲主键，取 chNN 形态（如 "ch01"），chapter 字段才是章号`；
  - method：`alibi_claims 的 day 是整数天数（如 1），t_start/t_end 是 "HH:MM" 字符串（如 "18:00"）`；
  - 通用 name 空：`name 是账本主键（如角色 "宫泽健一"、章纲 "ch01"）`。
- 同步在 AgentTool.parameters 的 `data` 字段 description 尾部追加一行示例（schema 是喂给模型的第一 API，错误消息是第二 API，双通道冗余对弱模型是刚需）。
- 优先覆盖压测实际命中的三类（name 空 / alibi day 类型 / requirements 类型），不求全类型示例全覆盖。

### D5 选项协议收敛（L5）

- **协议侧**（prompts.py WORKFLOW_SECTION）：
  - 加一句「一次只问一个决策点：需要作者在多个维度做选择时，选影响最大的一个先问，其余留待后续轮次」——与 intake 协议「每轮只问 1–2 个关键问题」对齐，从源头消灭多维度混块；
  - few-shot 样例扩充一个「格式变体即失败」的反例提示：「标题前缀（如 `### 【选项】`）、维度名穿插、全角冒号替代 —— 都会使作者端无法渲染按钮，严格按样例格式」。
- **前端侧**（options.ts + ChatStream）：`parseOptions` 返回值增加 `malformed: boolean`（标记存在但解析失败，正文照旧返回原文）——解析逻辑零放宽；ChatStream 对 malformed 消息在尾部显示一条灰色提示「候选方案格式异常，已按原文展示」。测试：L5 压测实录片段（`### 【选项】` 变体）作 malformed 用例。
- **ws 契约**：malformed 是前端派生态（同一条文本流），不新增事件类型——事件契约不变。

### D6 刷新点共享（L7）

- 提取 `refresh_system_prompt(harness, workspace) -> None`（放 `prompts.py`，读 settings + 重建 system + 赋值 `harness.config.system`）——单一事实源；
- 三个调用点：`HastingsSession._refresh_system`（改为委托）、`run_repl` 每行 prompt 前、`run_harness_case` 每轮 prompt 前；
- 回归测试：FakeProvider 剧本模拟两轮对话，轮间经工具建账，断言第二轮请求的 system 含更新后的计数（FakeProvider 捕获 system 的现成能力）；
- `_refresh_system` docstring 的「每轮刷新」语义说明同步更新为三端共享。

### D7 driver 可观测 + 本地模型跑法（L8）

- `run_harness_case` 成稿合并后：`chapters` 为空时 stderr 三要素告警（「哪里错：0 章成稿。为什么错：全程无 write_chapter 落盘或正文直出被泄压阀捕获。怎样算对：检查转录中 [tool] write_chapter 行与 prose_guard 告警」）；仍正常退出（0 章是结果不是异常，报告要的是证据）；
- eval/README.md 补「本地模型跑法」小节：派生 num_ctx 模型（`ollama create qwen3-32k -f Modelfile`）、进程级 env 覆盖（不改 .env）、占位 OPENAI_API_KEY、OPENAI_TIMEOUT_SECONDS、以及「弱模型压测定位软协议缺口」的方法论一句话。

### D8 上下文压缩（L9）：两档压缩，确定性剥离为主力

新模块 `src/hastings/context_manager.py`（估算 + 压缩执行 + 泄压阀），蓝图移植自 tau_coding/context_window.py（参考层不可 import，骨架照搬、场景重做）：

**估算器（触发条件）**
- `estimate_context_usage(system, messages, tools)`：provider usage 锚定（最近一次成功 assistant 的 usage.total_tokens）+ 尾部字符 fallback——照搬上游；
- **中文换算校准**：上游 `CHARS_PER_TOKEN=4` 按英文，中文实际约 1.5–2 字符/token，fallback 按 2 计（宁高估早压，不低估爆窗）；
- 阈值 = `context_window_tokens − reserve`（16k）；`context_window_tokens` 进 settings（SettingsPanel 模型配置已有，本地 32k / 云端 128k 缺省），与 D6 的 `refresh_system_prompt` 共享同一每轮入口——刷新即估算，一处接线三端生效。

**第一档：确定性正文剥离（主力，零 LLM 调用）**
- 超阈值时，keep-window（按 token 预算保留最近消息，缺省 20k）之外的 assistant 消息里，`write_chapter` 调用的 `content` 参数替换为占位符：「（第 N 章正文已落盘 chapters/chNN.md，需要时用 read_chapter 读取）」；
- 用 `model_copy` 重建 AssistantMessage（消息不可变），**tool_call_id 与调用结构原样保留**——OpenAI 兼容协议的 call/result 配对不破坏，provider 无感；
- 摘要不需要：正文在磁盘 + read_chapter 按需取——这正是状态外置设计的胜利，压缩后模型行为不退化（旧章不读不写，要读走工具）；
- 确定性、可单测（输入消息序列 → 输出消息序列的纯函数）。

**第二档：LLM 摘要（兜底，剥离后仍超阈值时）**
- 剥离后估算仍超 → 上游式全量压缩：provider 单独调一次摘要（模板换小说场景：作品名与阶段 / 已完成章节清单每章一句话 / 关键决策（作者确认过的设定变更、文风偏好）/ 进行中工作 / 作者最新指令）+ keep-recent 窗口；
- `harness.replace_messages([summary_user_message, *keep_recent])`（内核公开接口，§0 表已核实）。

**持久化与泄压阀**
- 压缩执行后：session.jsonl 归档（`archive_session_file`，P9 reset 手法）+ 以 replace 后的消息全量重写——重连恢复即压缩后形态；归档保留全量历史可找回；
- 摘要 provider 调用失败：**不压缩、不重试、stderr 三要素告警、写作流继续**——压缩是优化不是依赖，宁可下一轮再触发（四透镜③：环境故障明确归因，不进重试循环）；
- 压缩事件进 events.jsonl（可观测性是事件契约的一部分）：触发原因（估算值/阈值）、剥离章数或摘要字数、压缩前后估算。

**为什么两档**：第一档解决 L9 的主因（正文 args 占大头，50+ 章场景 90% 的膨胀来自它），确定性可测无额外成本；第二档兜对话本身超长的场景（高频 intake 问答、对账轮堆积）。主因不解只做全量摘要，会把「正文在历史里」这条冗余固化进摘要成本。

## 3. 任务分解（测试先行）

| # | 任务 | 交付 | 依赖 |
|---|---|---|---|
| T1 | D1 intake 写账门 | story_state 检查函数 + bible_tools/method_tools 接线 + 测试（intake 拒 / 推进后放行 / 旧工作区 story.yaml 缺失放行） | — |
| T2 | D6 刷新点共享 | `refresh_system_prompt` + 三调用点改造 + FakeProvider 两轮 system 断言测试 | — |
| T3 | D2 泄压阀检测器 | prose_guard.py + build_harness/HastingsSession 双装配 + 测试（触发/同回合一次/非 writing 不触发/短文本不触发/标题正则用例） | T2（装配点改造后接线更稳） |
| T4 | D3 回合规速 | StageAdvanceGuard + before_tool_call 接线 + FakeProvider 连续 advance 被拒测试 | — |
| T5 | D4 错误示例 | `_EXAMPLES` + schema description + 三类高频错误的消息断言测试 | — |
| T6 | D5 选项协议 | prompts 协议补文 + options.ts malformed + ChatStream 提示 + 前端测试（含压测实录变体用例） | — |
| T7 | D7 driver/README | 空产物告警 + eval/README 本地跑法小节 | — |
| T8 | D8 上下文压缩 | context_manager.py（估算器 + 两档压缩 + 泄压阀 + events 落账）+ settings 增项 + 刷新点接线 + session 归档重写 + 测试（纯函数剥离用例：keep-window 边界 / id 配对 / 中文换算；摘要失败泄压阀；压缩后 restore 恢复形态） | T2（共享刷新点入口） |
| T9 | 复跑验证 | 换新 slug 复跑 C1 双臂（qwen3-32k），核对：intake 零抢跑、成稿 ≥1 章、泄压阀告警与否均记录；CP2 盲评产物（或记录仍失败的新证据）；长篇压力用例（连续 write_chapter 剧本触发剥离并断言估算下降） | T1-T8 |

每个 T 先写失败测试再实现（项目约定）；T1/T3-T7 互相独立可并行，T2 先行（T3/T8 依赖其共享入口），T9 收口。

## 4. 红线与收尾

- tau 内核 diff 为空（本阶段全部走 hastings 层：before_tool_call 配置、steer、replace_messages、事件订阅均为现有公开接口；压缩蓝图从 tau_coding 参考层移植重写，不 import）；
- 七道质量门全绿（pytest / ruff check / ruff format / mypy / 前端 tsc / vitest / build）；
- DESIGN.md §8 路线表补 Phase 10 行；构建笔记 `phase-10-hardening.md` 含四透镜专节（重点：②硬约束放工具层的补完、③泄压阀三情形在纠偏消息设计中的体现、①错误消息双通道冗余）；
- 工作区遗留的 ollama WIP 改动不触碰（与 D7 的占位 key 说明无冲突——README 记录的是 driver 直连跑法）。
