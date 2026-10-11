# Phase 10 开发计划：工具层加固与交互/上下文增强（弱模型压测驱动）

> **立项动机**：2026-10-11 本地弱模型（qwen3:8b）对照压测（[eval/runs/2026-10-11-local/report-local-qwen3.md](../eval/runs/2026-10-11-local/report-local-qwen3.md)）把「模型自觉」从「系统机制」里剥离出来：DeepSeek 走查零违规的 intake 协议与正文落盘，本质是强模型在替软协议兜底。两个硬门缺口（正文直出零拦截 L1、intake 抢跑建账 L2）导致 harness 臂 0 章成稿；外加 context 管理缺陷（L7 状态注入 CLI 链路不刷新）与协议缝隙（L5 选项块）。本阶段全部收口。
> **追加（同日）L9 长篇上下文零兜底**：策略是「不压缩」，短篇靠状态外置 + 快照注入 + hunk 上限成立；但每章正文全文固化在 assistant 的 write_chapter tool_call args 里（工具结果只回摘要行），中文每章 3000 字 ≈ 1500–2000 token，50+ 章仅正文 args 即 75k–100k token，128k 窗口必爆，本地 32k 窗口 10 章上下即险——全项目无估算、无阈值、无压缩（D8 收口）。
> **追加（同日）作者补充要素 A1–A5**：在压测缺口（L1–L9）之外，本阶段并入五项应用层要素——A1 作者确认的结构化交互卡、A2 多维度选项块解析、A3 token 计数（估算升级为计数）、A4 上下文压缩手动触发、A5 多会话并存与切换。口径为「并入本阶段」（对应 D9–D13 / T10–T14）：A3/A4 与 D8 共享同一计数与压缩原语，A5 复用 tau 现成会话存储、不碰内核。
> **优先级**（报告结论）：L1/L2 最优先（工具层硬门）→ L7（刷新点共享）→ L3（F1 示例，优先级上调为刚需）→ L8（driver 可观测）→ L5（选项协议）→ L4（F2 复现项）；A1–A5（作者补充要素）排在 D8 之后同批收口（A2 建在 D5 之上，A5 复用 tau 现成会话存储）。
> 状态：**已收口（2026-10-11）**——T1–T14 全部交付，十五条验收核对与复跑见构建笔记 [phase-10-hardening.md](phase-10-hardening.md)。本计划自此转为只读历史记录。

## 0. 已核实的依赖事实（计划的输入）

| 结论 | 证据 | 对本阶段的影响 |
|---|---|---|
| `before_tool_call` 返回 `(False, reason)` 时，loop 生成 `_error_result(reason)` 作为工具结果喂回模型 | [loop.py](../src/tau_agent/loop.py#L298-L303) | L4 单回合 advance 限次的拒绝通道现成，错误消息即纠偏消息 |
| `harness.steer(content)` 公开存在，steering 在工具回合边界消费、纯文本收尾时留队等下一轮 | [harness.py](../src/tau_agent/harness.py#L121-L126) | L1 正文直出检测器的注入通道现成；纯文本收尾场景提醒延迟生效可接受 |
| `AgentStartEvent` 每次 `prompt()` 触发一次，`harness.subscribe` 支持多消费者 fan-out | [harness.py](../src/tau_agent/harness.py#L147-L153)、events_store 先例 | L4 回合计数器：AgentStartEvent 重置 + 事件订阅，零内核改动 |
| write_chapter 阶段门先例：`stage not in _WRITING_STAGES` → 三要素 ToolInputError | [chapter_tools.py](../src/hastings/tools/chapter_tools.py#L81-L122) | L2 intake 写账门照抄同款模式（写路径统一前置检查） |
| `_refresh_system` 的刷新语义只在 HastingsSession.prompt 入口，CLI `run_repl`（cli.py:217）与 eval driver 直连 `harness.prompt` | [hastings_session.py](../src/hastings/server/hastings_session.py#L189-L220) vs [cli.py](../src/hastings/cli.py#L217)、driver.py:74 | L7 提取共享函数，三个调用点统一 |
| 前端 `parseOptions` 严格解析（非选项行/键不连续即整块放弃、返回原文），测试 12 例全绿；取最后一个标记块 | [options.ts](../frontend/src/components/options.ts#L55-L78) | L5 单块不放宽解析（连续性约束是防误判底线），只加「格式异常」可观测提示；D10 在其上做多块分组解析 |
| bible_upsert 各实体类型共享 execute 闭包，参数校验集中在 `_require` 系 | [bible_tools.py](../src/hastings/tools/bible_tools.py) | L2 门与 L3 示例文案各一处改动全类型生效 |
| `build_harness` 是 CLI/eval 装配点，已挂 `make_event_persister` 订阅 | [cli.py](../src/hastings/cli.py#L109-L128) | L1 检测器、L4 守卫同款装配，一处接线两端生效 |
| `harness.replace_messages(...)` 是内核公开接口（tau_coding/session.py 三处调用先例） | [harness.py](../src/tau_agent/harness.py)、tau_coding/session.py:1060 | L9 压缩的内存消息替换通道现成，零内核改动 |
| 完整压缩蓝图在 tau_coding 参考层：估算器（provider usage 锚定 + 字符 fallback）、摘要提示词、keep-recent 预算（20k）、阈值=窗口−reserve（16k） | [context_window.py](../src/tau_coding/context_window.py)（不参与构建，须移植不能 import） | D8 照搬骨架，摘要模板与 token 换算按小说场景重做 |
| 持久层 `CompactionEntry` + `_apply_compaction` 重放支持压缩留痕；但 Hastings 的 session_persist 用 `JsonlSessionStorage` 线性 append（仅 MessageEntry） | [memory.py](../src/tau_agent/session/memory.py#L100-L106)、session_persist.py | D8 持久化适配点：压缩后归档旧 session.jsonl + 重写（P9 reset 的 archive 手法） |
| `Usage` 挂在 assistant 消息上（`AgentMessage.usage`：input/output/cache/total），由 tau_ai 各 provider 填充 | [messages.py](../src/tau_agent/messages.py#L46-L56)、messages.py:169 | A3 token 计数：provider usage 锚定源现成 |
| 「开新会话」reset 先例：`archive_session_file` 归档 + `registry.rebuild` 空会话 + `session_reset` 事件；前端 ChatHeader 已有入口与 AlertDialog 确认 | [rest.py](../src/hastings/server/rest.py#L1049-L1083)、[session_persist.py](../src/hastings/session_persist.py#L57-L74)、[ChatHeader.tsx](../frontend/src/components/ChatHeader.tsx) | A4/A5 复用同款归档/重装配/确认模式；A5 把 reset 收编为「新建会话」 |
| 选项块渲染就地出按钮（`onSelectOption` → App 走 WS 发送），无独立交互卡组件 | [ChatStream.tsx](../frontend/src/components/ChatStream.tsx#L173-L193) | A1 确认卡照此模式加 ConfirmCard；A2 多组按钮同址扩展 |

## 1. 目标与验收标准

**目标**：把弱模型压测暴露的两个硬门缺口补成工具层硬约束，修复 context 管理缺陷，收敛协议缝隙——「门是硬约束（工具层），推进是协议（prompt）」的 P8 哲学补完到全部写路径；并落地作者补充要素 A1–A5（确认交互卡、多维度选项块、token 计数、压缩手动触发、多会话切换），把「协议软、工具硬」的分工延伸到交互与上下文管理两条线。

可检查的十五条：

1. **intake 写账门（L2）**：stage=intake 时 `bible_upsert` / `method_lock` / `method_unlock` 被拒，错误三要素含逃生门（先 story_stage 推进）；story.yaml 不存在的旧工作区不拦（不强制迁移旧数据）；
2. **正文直出泄压阀（L1）**：writing/revising 阶段 assistant 长文本（>600 字）含章节标题特征时，steering 注入纠偏提醒（同回合至多一次）；WORKFLOW_SECTION 明示「正文唯一交付通道是 write_chapter」；CLI/driver 转录含告警行；
3. **回合规速（L4/F2）**：单次 prompt（一次 AgentStartEvent 到 AgentEndEvent）内第二次 `story_stage advance` 被 before_tool_call 拒绝，理由三要素；
4. **错误消息示例（L3/F1）**：outline 的 name 空/类型错、method 的 alibi_claims.day 等高频 schema 错误，错误消息按实体类型附最小正确示例；
5. **刷新点共享（L7）**：`refresh_system_prompt(harness, workspace)` 单一事实源，HastingsSession / run_repl / eval driver 三点调用；CLI 4 轮对话中每轮 system 含最新阶段与账本计数（测试断言）；
6. **选项协议收敛（L5）**：WORKFLOW_SECTION 候选协议补「一次只问一个决策点；多维度逐块输出」+ few-shot 强化；前端解析失败但标记存在时消息卡显示「候选方案格式异常」提示（不吞内容）；
7. **driver 可观测（L8）**：成稿合并 0 章时 stderr 三要素告警非静默成功；eval/README 补本地模型跑法（num_ctx 派生、占位 key、timeout）；
8. **上下文压缩（L9）**：估算器 + 两档压缩（确定性正文剥离为主力、LLM 摘要为兜底）落地；`context_window_tokens` 进 settings；压缩触发时 keep-window 外的 write_chapter args 被占位符替换且 tool_call_id 配对不变；压缩后 session.jsonl 归档重写；摘要 provider 失败时 stderr 三要素告警不断流（测试覆盖四情形）；
9. **复跑验证**：L1/L2 修复后换新 slug 复跑 C1 双臂（本地 qwen3-32k），harness 臂成稿 ≥1 章、无 intake 抢跑，补 CP2 盲评产物；
10. **作者确认交互卡（A1/D9）**：需要作者显式确认才继续的场合（当前明确场合=bible_drafting 建账完成后推进阶段前），agent 在消息尾部输出确认块，前端渲染为「确认 / 暂不」结构化卡片，点击发送定式指令；格式异常原样展示并提示。硬约束仍在 story_stage 工具层，不改（协议软、工具硬）；
11. **多维度选项块（A2/D10）**：协议允许一条消息内多个带维度名的选项块（`【选项：<维度>】`），前端解析为多组按钮（每组独立选择、一次性提交），无维度名单块向后兼容；任一块格式异常则整条按原文渲染 + 「格式异常」提示（承接 D5）；
12. **token 计数（A3/D11）**：`count_context_tokens` 分层计数（provider usage 锚定 → tokenizer 计数 → 中文按 tokenizer、缺失时字符兜底）；每轮 usage 进 events.jsonl（`token_usage`）+ 前端显示上下文占用/窗口比；`context_window_tokens` 进 settings；计数是压缩触发（D8）与窗口管理的唯一数据源；
13. **压缩手动触发（A4/D12）**：CLI `/compact` + Web 对话头按钮 + REST `POST /api/{slug}/session/compact`，调用 D8 同一压缩原语（绕过阈值）；无可压缩空间 → 「无需压缩」正常返回（泄压阀三情形中的合法空结果）；留痕 `session_compact` 事件；
14. **多会话并存与切换（A5/D13）**：每工作区多会话文件 + `sessions.json` 索引，REST/前端/CLI 支持列表/新建/切换/删除/重命名；切换复用现有快照重放路径；现存单 `session.jsonl` 迁移为首会话；现有 reset 收编为「新建会话」；running 拒绝切换/删除；
15. **红线**：tau 内核 diff 为空（before_tool_call/steer/replace_messages/事件、A5 的会话存储均为现有公开接口）、七道质量门全绿、测试先行（每项先写失败测试）、构建笔记含四透镜专节。

**非目标**：

- 不改迁移表拓扑（F3 的 writing→bible_drafting 回退边另议：WORKFLOW_SECTION 已明确「账本修改任何阶段可用」，写作期改设定走 bible_upsert 直改，回退边属产品决策不在本阶段）；
- 不做输出侧正文的内容级检测（标题正则 + 长度阈值即止，不上 LLM 判定）；
- 不做 CompactionEntry 树形持久化迁移（session.jsonl 归档重写已满足「可找回 + 可恢复」，树形会话不在 Hastings 范围）；
- 会话为并列平铺（A5 只做列表/切换/新建/删除/重命名），不做会话分支或消息级树形回滚；
- 不做逐消息 token 账本（A3 只记每轮 usage 与当前上下文占用，不做增量累加审计）；
- 不做多步表单式确认卡（A1 一次确认一个决策点）；
- 不动 tau_ai / tau_agent / tau_coding。

> 说明：原「不做作者确认交互卡 / 不做多维度选项块解析 / 不做 token 精确计数」三条已由作者补充要素 A1–A3 上调为本阶段范围（见 D9–D11），从非目标移出。

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
  - 加一句「一次只问一个决策点：需要作者在多个维度做选择时，选影响最大的一个先问，其余留待后续轮次」——与 intake 协议「每轮只问 1–2 个关键问题」对齐，从源头**减少**多维度混块（多块不再是格式错误，而是合法兜底，见 D10）；
  - few-shot 样例扩充一个「格式变体即失败」的反例提示：「标题前缀（如 `### 【选项】`）、维度名在选项行中穿插（维度名只能作块标题 `【选项：<维度>】`）、全角冒号替代 —— 都会使作者端无法渲染按钮，严格按样例格式」。
- **前端侧**（options.ts + ChatStream）：`parseOptions` 返回值增加 `malformed: boolean`（标记存在但解析失败，正文照旧返回原文）——**单块内**解析逻辑零放宽（连续性约束是防误判底线），多块解析升级见 D10；ChatStream 对 malformed 消息在尾部显示一条灰色提示「候选方案格式异常，已按原文展示」。测试：L5 压测实录片段（`### 【选项】` 变体）作 malformed 用例。
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

### D9 作者确认交互卡（A1）：软协议 + 工具层硬门不变

- **协议侧**（prompts.py）：新增确认标记 `CONFIRM_MARKER = "【确认】"`，与 `OPTIONS_MARKER` 同族；只在「需要作者显式确认才继续」的场合输出（当前明确场合：bible_drafting 建账完成后推进阶段前；其余阶段留待后续扩展）。格式：

  ```
  【确认】
  待确认：<一句话，确认什么>
  确认后：<会发生什么：推进到 X 阶段 / 落盘哪些条目>
  暂不：<不确认时你会做什么>
  ```

- **前端**（新 `confirm.ts` + ChatStream）：`parseConfirm(text)` → 尾部确认块解析为 `{body, confirm, malformed}`；`confirm` 命中时渲染 ConfirmCard（说明行 + 「确认」「暂不」两键），点击发送定式指令（确认 → 「我确认，请继续推进」；暂不 → 「暂不确认，请说明还需补充什么」）；格式异常 → 原样展示 + 灰字「确认卡格式异常」提示（复用 D5 malformed 模式）。
- **硬约束不搬家**：确认卡是 UI 便利层；推进仍由 story_stage 工具把门（迁移边校验 + D3 回合规速）。协议软、工具硬的分工不变——确认卡不新增工具层门。
- **ws 契约**：不新增事件类型（同 D5，派生态）。
- **不做**：不做多步表单式确认卡（一次确认一个决策点，见非目标）。

### D10 多维度选项块（A2）：宽解析、协议默认仍单点

- **现状**：`parseOptions` 取 `lastIndexOf(OPTIONS_MARKER)` 只解析最后一个块，模型中出多块时前面块留在正文不渲染（[options.ts](../frontend/src/components/options.ts#L55-L78)）。
- **协议侧**（prompts.py）：允许带维度名的块 `【选项：<维度>】`，说明「一次只在必要时给出多维度的候选，每维度一块」；D5 的「一次只问一个决策点」仍为默认建议，多块是弱模型给出多维度时的合法兜底而非鼓励路径。
- **前端**（options.ts）：`parseOptions` 升级为返回 `groups: Array<{dimension: string | null, options: ChatOption[]}>`——分块解析（按标记切段），各块键各自从 A 起；无维度名单块 → 单组 `dimension=null`，向后兼容；**任一块解析失败 → 整条按原文渲染 + malformed 提示**（与单块语义一致：宁可全不渲染，不给半成按钮误导）。
- **渲染**（ChatStream）：多组时每组一个维度小标题 + 按钮行，组内单选、本地暂存选中，出现「提交选择」按钮一次性发送合并回复（`关于「<维度>」我选 X：<方案名>；关于「<维度2>」我选 Y：…`）；单组沿用即时发送（`我选 X：<方案名>`，零交互变更）。
- **不做**：不做跨维度自由文本混排（选项按钮 + 提交即止）。

### D11 token 计数（A3）：分层计数、唯一数据源

- **计数函数** `count_context_tokens(system, messages, tools)`（放 `context_manager.py`，是 D8 估算器的升级形态）分层：
  1. **provider usage 锚定**：最近一次成功 assistant 响应的 `Usage`（input+output+cache，[messages.py](../src/tau_agent/messages.py#L46-L56)）是精确值，作为「已发送上下文 + 本轮输出」基准；
  2. **尾部/增量 tokenizer 计数**：锚点之后追加的消息按本地 tokenizer 计（中文按实际 tokenizer，不再用字符换算）；
  3. **字符兜底**：tokenizer 不可用 → 回退 D8 换算（中文 2 字符/token，宁高估早压）。
- **唯一数据源**：压缩触发（D8）、前端占用展示、事件留痕都读这一个函数，不再各算各的。
- **落账与展示**：每轮 assistant 结束把 `usage` 写进 events.jsonl（`token_usage` 事件：input/output/total/cache）；前端对话头显示「上下文 X / 窗口 Y（Z%）」；`context_window_tokens` 进 settings（D8 已列，与 D6 刷新点共享每轮入口）。
- **依赖决策**：tokenizer 引入与「依赖最小化」冲突——**默认不引第三方 tokenizer，优先 provider usage 锚定 + 字符兜底**；若 pre-flight 精确计数确有刚需再评估轻量实现，作为 T12 的实施决策点记入构建笔记。
- **不做**：不建逐消息 token 账本（见非目标）。

### D12 压缩手动触发（A4）：同一原语，绕过阈值

- **抽函数** `compact_context(harness, workspace, *, manual: bool)`——D8 的实现即此函数：自动 = 超阈值触发，手动 = 直接调用（跳过阈值判断，走同一第一档剥离 + 第二档摘要 + 归档重写）。
- **入口**：CLI `/compact` 命令；Web 对话头「压缩上下文」按钮（AlertDialog 说明后果，复用「新会话」同款确认）；REST `POST /api/{slug}/session/compact`（异步，返回压缩统计）；
- **边界**：running → 409（文案对齐 reset）；无可剥离内容或未超 keep-window → 返回「无需压缩」正常结束（**泄压阀三情形中的「合法但空结果」**：不报错、指明下一步）；
- **留痕**：events.jsonl `session_compact`（reason=manual|auto、剥离章数/摘要字数、压缩前后计数）；
- **共用**：与自动触发共用实现与持久化（session.jsonl 归档重写同 D8），手动路径不新增持久化分支。

### D13 多会话并存与切换（A5）：平铺会话，收编 reset

- **存储**：`stories/<slug>/sessions/<session_id>.jsonl` + `sessions.json` 索引（id / 标题 / 创建时间 / 最后活跃 / 消息数 / active_id）；**迁移**：现存单 `session.jsonl` 首次访问时归位为首会话（`sessions.json` 缺失即视为单会话旧格式），不强制用户迁移。
- **REST**：`GET /api/{slug}/sessions`（列表）、`POST /api/{slug}/sessions`（新建，可带标题）、`POST /api/{slug}/sessions/{id}/activate`（切换）、`DELETE /api/{slug}/sessions/{id}`、`PATCH /api/{slug}/sessions/{id}`（重命名）。
- **前端**：对话头会话选择器（列表 + 新建 + 重命名 + 删除，删除走 AlertDialog）；切换 → 重连 WS 快照重放（复用现有 `setSlug` 重连与 `loadSnapshot`，正文面板不清）。
- **CLI**：`/sessions`（列出）/ `/session <id>`（切换）/ `/new`（新建）。
- **与 reset 关系**：现有「新会话」reset 收编为「新建会话」——旧会话保留在列表可回看，不再是「只能归档找回」。
- **边界**：running → 拒绝切换/删除/新建（409）；删除活跃会话 → 自动切到默认或新建空会话。
- **与 D8/D12 关系**：压缩与自动触发只作用于**当前活跃会话**的文件；新建会话是作者主动回收上下文的另一条路径（旧会话保留可回看，天然承接长文场景）。
- **不做**：不做会话分支/树（见非目标）。

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
| T10 | D9 作者确认交互卡（A1） | `CONFIRM_MARKER` 协议 + confirm.ts 解析 + ConfirmCard 组件 + 测试（确认/暂不指令 / 格式异常原样展示 / 非确认点不渲染） | — |
| T11 | D10 多维度选项块（A2） | prompts 协议补维度块 + options.ts 多组解析 + ChatStream 分组多选提交 + 测试（多块/单块兼容/任一块坏则整条 malformed） | T6（建在其 options.ts malformed 之上） |
| T12 | D11 token 计数（A3） | `count_context_tokens` 分层（usage 锚定 / tokenizer / 字符兜底）+ `token_usage` 事件 + 前端占用展示 + settings 窗口项 + 测试 | T8（共享 context_manager） |
| T13 | D12 压缩手动触发（A4） | `compact_context(manual)` 抽函数 + CLI `/compact` + REST `/session/compact` + Web 按钮 + `session_compact` 事件 + 测试（无需压缩空返回 / running 409） | T8、T12 |
| T14 | D13 多会话并存切换（A5） | sessions/ 存储 + `sessions.json` 索引 + REST（列表/新建/切换/删除/重命名）+ 前端选择器 + CLI + 旧 session.jsonl 迁移 + 测试 | T8（压缩作用于活跃会话文件） |

每个 T 先写失败测试再实现（项目约定）；T1/T3-T7 互相独立可并行，T2 先行（T3/T8 依赖其共享入口），T9 收口。补充要素 T10/T11 独立可并行（T11 建在 T6 之上），T12→T13→T14 顺序（共享 D8 原语）；T9 复跑在 T1–T8 后即可进行，T10–T14 同批收口。

## 4. 红线与收尾

- tau 内核 diff 为空（本阶段全部走 hastings 层：before_tool_call 配置、steer、replace_messages、事件订阅均为现有公开接口；压缩蓝图从 tau_coding 参考层移植重写，不 import）；
- A5 同样零内核改动：多会话复用 tau 现成 `JsonlSessionStorage`（每会话一个 jsonl + hastings 侧 `sessions.json` 索引），切换复用 `replace_messages` 与前端快照重放，不碰内核；
- 依赖红线：A3 默认不引第三方 tokenizer（provider usage 锚定 + 字符兜底）；若 pre-flight 精确计数必须引依赖，先过「依赖最小化」评审并在构建笔记记录动机；
- 七道质量门全绿（pytest / ruff check / ruff format / mypy / 前端 tsc / vitest / build）；
- DESIGN.md §8 路线表补 Phase 10 行（含 A1–A5 交互/上下文要素）；构建笔记 `phase-10-hardening.md` 含四透镜专节（重点：②硬约束放工具层的补完、③泄压阀三情形在纠偏消息与「无需压缩」空结果中的体现、①错误消息双通道冗余）；
- 工作区遗留的 ollama WIP 改动不触碰（与 D7 的占位 key 说明无冲突——README 记录的是 driver 直连跑法）。
