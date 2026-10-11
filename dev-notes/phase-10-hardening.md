# Phase 10 构建笔记：工具层加固与交互/上下文增强

> 对应计划：[phase-10-hardening-plan.md](phase-10-hardening-plan.md)
> 周期：2026-10-11。任务 T1–T14 全部交付，验收十五条逐条核对（见 §4）。
> 关键词：弱模型压测、硬约束下沉工具层、prose_guard、intake 阶段门、回合规速、刷新点共享、两档压缩、token 分层计数、确认交互卡、多维度选项块、多会话切换。

## 1. 交付内容

立项动机是 [本地弱模型对照压测](../eval/runs/2026-10-11-local/report-local-qwen3.md) 把「模型自觉」从「系统机制」里剥离出来——强模型替软协议兜的底，在弱模型上显形为真实缺口（L1–L9）；本阶段把这批缺口收成工具层硬约束，并同批并入作者补充要素 A1–A5。

按层交付（不回填提交罗列，逻辑分组）：

- **工具层硬门**：`story_state.py` 新增 intake 写账检查纯函数 → `bible_tools.py` / `method_tools.py` 统一前置检查（D1）；`manuscript/prose_guard.py`（D2）、`manuscript/advance_guard.py`（D3）、`manuscript/guards.py` 装配函数（一处接线 CLI/eval/Web 三端）；`bible_tools.py` 的 `_EXAMPLES` + `AgentTool.parameters` description 双通道冗余（D4）。
- **上下文管理**：新建 `context_manager.py`——估算器 + 两档压缩 + `count_context_tokens` 分层计数 + 泄压阀（D8/D11）；`settings.py` 增 `context_window_tokens`；`session_persist` / CLI / driver / `HastingsSession` 刷新点统一走 `prompts.refresh_system_prompt`（D6）。
- **协议与可观测**：`prompts.py` 的 WORKFLOW_SECTION 补正文交付明示、确认标记、多维度选项块、few-shot 反例（D2/D5/D9/D10）；`eval/driver.py` 0 章成稿 stderr 三要素告警 + `eval/README.md` 本地模型跑法（D7）。
- **应用层交互**：`session_store.py` 多会话存储（D13）+ REST 5 端点；前端 `ConfirmCard.tsx`/`confirm.ts`（D9）、`options.ts` 多组解析（D10）、`contextUsage.ts` 占用展示（D11）、`SessionSelector.tsx` 会话选择器（D13）、对话头压缩按钮（D12）。
- **事件契约新增**（不新增帧结构，只加具名事件）：`token_usage`、`session_compact`。

**红线核对**：`git diff src/tau_ai src/tau_agent` 内容为空（仅 `provider_events.py` 的 LF/CRLF 行尾噪声，无正文差异）；全部新能力走 tau 现有公开接口（事件订阅 / `config.before_tool_call` / `harness.steer` / `harness.replace_messages` / `JsonlSessionStorage`）。A3 未引第三方 tokenizer，按「依赖最小化」用 provider usage 锚定 + 中文 2 字符/token 兜底（计划 D11 的默认分支）。

## 2. 关键决策（D1–D13 落地与偏离）

- **D1 intake 写账门**：拦数据面（`bible_upsert`/`method_lock`/`method_unlock` 在 stage=intake 拒绝，只读工具放行）、放控制面（`story_stage advance` 不拦，留痕可审计）。旧工作区（story.yaml 缺失）不拦——`load_story_doc` 兜底读出的 intake 不代表真实状态，遵循「不强制迁移旧数据」。检查函数落在 `story_state.py` 保持工具层薄。
- **D2 正文直出泄压阀**：三层防线全部落地——prompt 明示（软）/ `ProseLeakGuard` steering 纠偏 + stderr 告警（半硬，阈值 600 字与标题正则做成模块常量）/ CLI·driver 空稿观测（`warn_empty_chapters`）。检测只在 `MessageEndEvent`（流式中间态无语义），同回合至多一次。
- **D3 回合规速**：`StageAdvanceGuard` 以 `AgentStartEvent` 重置计数、`before_tool_call` 拦截单回合第二次 advance，rollback 不限次。选进程内计数器而非时间戳阈值——语义精确（=本回合），不随模型快慢漂移。
- **D4 错误消息最小示例**：覆盖压测实际命中的三类（name 空 / alibi day 类型 / requirements 类型），不做全类型覆盖。schema description 与错误消息双通道冗余对弱模型是刚需。
- **D5 选项协议收敛**：协议侧加「一次只问一个决策点」+ 反例提示；前端 `parseOptions` 加 `malformed` 派生态（单块解析零放宽——连续性约束是防误判底线）。不新增 WS 事件（派生自同一文本流）。
- **D6 刷新点共享**：`refresh_system_prompt(harness, workspace)` 成单一事实源，`HastingsSession._refresh_system` 改为委托，`run_repl` / `run_harness_case` 每轮 prompt 前调用。
- **D7 driver 可观测**：0 章成稿 stderr 三要素告警但不改退出码（结果不是异常，报告要证据）；README 补 num_ctx 派生 / 占位 key / timeout 跑法。
- **D8 两档压缩**：蓝图从 tau_coding 参考层移植重写（不可 import）。第一档确定性剥离 `write_chapter` 正文 args → 占位符（`model_copy` 重建、tool_call_id 配对原样保留）；第二档 LLM 摘要兜底；持久化走 session 归档重写。中文换算按 2 字符/token（宁高估早压）。
- **D9 确认交互卡**：`CONFIRM_MARKER` 协议 + `parseConfirm` 宽容解析 + `ConfirmCard` 定式指令发送。**硬约束不搬家**——确认卡是 UI 便利层，推进仍由 `story_stage` 工具把门（迁移边校验 + D3 回合规速）。
- **D10 多维度选项块**：`parseOptions` 升级为 `groups: {dimension, options}[]`，无维度块向后兼容单组；任一块解析失败则整条按原文渲染 + malformed 提示（宁可全不渲染，不给半成按钮误导）；渲染侧多组时本地暂存选中、一次性合并提交。
- **D11 token 分层计数**：`count_context_tokens` 三档（provider usage 锚定 → 尾部计数 → 字符兜底），压缩触发/前端展示/事件留痕共用这一个函数，不再各算各的；`token_usage` 帧刷新对话头占用。
- **D12 压缩手动触发**：`compact_context(harness, workspace, *, manual)` 抽为同一原语，自动=超阈值、手动=跳过阈值；CLI `/compact` + REST `POST /session/compact` + Web 按钮；无可剥离 → 「无需压缩」正常返回（合法空结果）。
- **D13 多会话并存**：`stories/<slug>/sessions/<id>.jsonl` + `sessions.json` 索引，旧单 `session.jsonl` 首访迁移为首会话；REST 5 端点；前端选择器切换复用 `setSlug` 重连 + 快照重放（正文面板不清）；CLI `/sessions` `/session` `/new`；原 reset 收编为「新建会话」（旧会话保留可回看，不再只能归档找回）。running → 409；删除活跃自动切换。
- **偏离/实施选择**：压缩触发与手动路径共用同一持久化分支（D8/D12 一致，未新增分支）；会话 id 形态 `session-<stamp>-<4hex>` 与压缩归档名区分，避免索引扫描误收归档文件。

## 3. Agent 设计经验（四透镜）

本阶段是「软协议失效 → 硬约束下沉」的一次集中补课，四透镜的形态从**输出侧**（P9 面向用户/进程）转回了**工具与上下文**（面向模型），但判据不变。

### 3.1 错误消息是喂给模型的 API——双通道冗余对弱模型是刚需

D4 把「schema description」与「参数校验错误消息」当**两条独立 API**同步供给：模型首轮看到参数的 description（含最小正确示例），违规后收到错误消息（同样带示例）。强模型任一条即可自我修正，弱模型需要两条冗余才收敛——压测里 name 空、alibi day 类型错正是只靠一条通道无法纠偏的实例。教训：**「字段级定位原样透传」不够，还要在错误路径的终点附上「怎样算对」的最小可运行样例**，让纠偏从「猜格式」变成「照抄」。

### 3.2 硬约束放工具层，prompt 只负责解释世界——本阶段把这条线补完到全部写路径

P8 立了「门是硬约束（工具层）、推进是协议（prompt）」的哲学，但 intake 写账与写作推进两处仍是软协议，弱模型一击即穿。D1/D3 把这两处下沉：

- intake 写账 → 工具层 `execute` 前置检查（一处改动六实体类型生效）；
- 单回合推进限次 → `before_tool_call` 计数拦截（拒绝即纠偏，错误消息的逃生门保证「作者明确要求连推」的合法路径畅通）。

对照 D9/D10 是**反例**：确认卡与选项块都是纯 UI 便利层，硬约束一点没上浮——确认卡点击照样只是发一条定式文本，推进仍由 `story_stage` 工具裁决；选项块格式漂移只在解析层降级为原文。**同样的「协议软」在两处的差异**：软协议若保护的是**数据正确性**就必须有工具层硬门兜底；若只是**交互便利**则允许纯前端宽容处理。

### 3.3 泄压阀——三情形在纠偏消息与「无需压缩」空结果中的体现

| 情形 | D2 正文直出纠偏 | D12 手动压缩 |
|---|---|---|
| 违规 | 真·正文直出 → steering 注入含逃生门的纠偏消息 | （不适用：压缩无违规输入） |
| 合法但空结果 | 「如为讨论性文字，请忽略本提醒」——防道歉-重试循环 | 未超 keep-window → 「无需压缩」正常返回 + 指明下一步 |
| 环境故障 | stderr 三要素告警，写作流不中断 | 摘要 provider 失败 → 不压缩不重试、stderr 告警、下轮再触发 |

关键手法仍是**三种出口在代码里长得不一样**：D2 的纠偏消息把「合法但易混淆」显式写进文案（否则模型会把讨论性文字也当违规去重写）；D12 把「无需压缩」和「压缩被拒（running 409）」分成两条路径，前者是 200 空结果、后者是拒绝，绝不混用。环境故障一律与「空结果」物理隔离：告警但不改退出码/不报错。

### 3.4 可观测性是事件契约的一部分——新能力的留痕先于新 UI

本阶段两个新事件（`token_usage`、`session_compact`）都不是 UI 驱动出来的，而是**先定为契约**：占用展示与压缩统计从同一事件流取数，前端只是消费者之一，审计/转录同样能读。`session_compact` 的 `reason=manual|auto` 字段把「手动触发」与「阈值自动」在留痕层分开——同一压缩原语、两种来源在证据里可区分。这与 P9 的事件契约一脉相承，也是「同一事件流服务多消费者」的又一次兑现。

## 4. 复跑验证与验收

**C1 双臂换新 slug 复跑（本地 qwen3-32k）**：见 [runs/2026-10-11-local2/report-rerun.md](../eval/runs/2026-10-11-local2/report-rerun.md)。核对项——intake 零抢跑、harness 臂成稿 ≥1 章、泄压阀告警与否均记录。相较 [首次本地压测](../eval/runs/2026-10-11-local/report-local-qwen3.md) 的 harness 臂 0 章成稿（L1 正文直出 + L2 intake 抢跑），本次为 L1/L2 硬门落地的直接验证。

**回归**：长篇压力用例（连续 `write_chapter` 剧本触发第一档剥离并断言估算下降）与四情形压缩泄压阀（成功/空/摘要失败/running）见 `tests/test_context_manager.py`；intake 门 / 回合规速 / 刷新点 / 错误示例 / 选项协议等分别见 `tests/test_story_state.py`、`test_advance_guard.py`、`test_prose_guard.py`、`test_prompts.py`、`test_cli.py`、`test_session_store.py`、前端 `confirm.test.ts`/`options.test.ts`/`rest.test.ts`/`SessionSelector.test.ts`。

**七道质量门**：pytest 738 passed；ruff check All passed；ruff format 192 files already formatted；mypy 83 files Success；前端 tsc + vite build 通过（主 chunk 600.27kB / gzip 189.57kB）；vitest 286 passed。

## 5. 评审记录

- 计划 §4 红线逐条：内核 diff 为空 ✅ / A5 零内核改动 ✅ / 不引第三方 tokenizer ✅ / 七道门全绿 ✅ / DESIGN §8 补行 ✅ / 本笔记含四透镜专节 ✅ / ollama WIP 不触碰 ✅。
- 计划 §1 验收十五条：T1–T14 的 DoD 在对应测试文件中有断言；复跑一条见 §4。

## 6. 踩坑

- **内部 helper 命名遮蔽**：新会话端点内部 `_summary` 与既有 provider 的 `_summary` 撞名，3 个 provider 测试 TypeError——重命名为 `_session_summary`/`_session_summaries`。教训：应用层函数名要带域前缀，REST 模块已很大，泛名易撞。
- **`session_file` 语义变更的连带面**：改 `session_file` 返回活跃会话文件后，压缩归档断言（`test_context_manager`）与导出路径需同步改为相对 `sessions/` 目录定位——语义变更要 grep 全部调用点，别只改主路径。
- **`restore_and_attach` 从「条件替换」变「无条件替换」**：切换会话必须清旧历史（空会话不得残留上一会话消息），返回值扩展为退订回调供 CLI 切换时重新接线。
- **前端删除 `resetSession` 的连锁**：ChatHeader 仍 import 已删除符号会直接 tsc 失败；改动共享 API 出口时要一次搜齐引用点（App/ChatHeader/测试）。

## 7. 留白

- **CP2 盲评产物**：复跑成稿已落盘，盲评按 [C-v2-scoring.md](../eval/intake/C-v2-scoring.md) 执行（本轮只采集证据，盲评评分未纳入本笔记）。
- **tokenizer 精确计数**：A3 默认不引依赖，尾部计数仍为字符换算；若后续 pre-flight 精确计数有刚需，作为独立评审再评估轻量实现。
- **会话树/分支**：A5 为并列平铺，不做会话分支或消息级树形回滚（计划非目标）。
- **确认卡场合**：当前只接 bible_drafting 建账完成后的推进点，其余阶段留待扩展。
- **ollama 本地模型接入 WIP**：工作区另有一批 ollama 相关改动（本地模型卡片/下载流/服务端探测），与本阶段无关，未触碰。