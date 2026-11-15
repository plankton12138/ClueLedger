# P2：统一模型操作与恢复

本文件保留分批实施和验证记录；最新实现、已关闭事项及验收结果以[收尾记录](P7-implementation-and-acceptance.md)为准。
日期：2026-10-29。状态：**已完成。写作轮次、压缩与连接测试都在同一张操作表里，可用同一套接口读取、暂停、继续与插话（连接测试不支持插话）**。

## 1. 本阶段做了什么

| 计划项 | 落地 |
|---|---|
| 现有轮次接入统一操作视图，复用事件来源 | `workspace/operations.py` 只存控制面（状态、代次、指令、收据）；执行记录仍是会话事件。操作接口按 `attempt_started` 把事件标回代次，没有第二份执行日志 |
| 区分真实文本增量、工具进度、完整候选与提交结果 | 沿用既有事件：`assistant_block_delta`（真实增量）、`tool_started/finished`（进度）、`assistant_message`（完整公开文本）、`version_finished` + 操作收据（提交结果）。暂停后晚到的增量与完整文本都不再发布 |
| 暂停撤销代次资格、继续新代次、插话原子受理、等待作者回答、失败保留草稿 | `TurnService.pause_operation / continue_operation / interject_operation`；资格检查落在两处：`ChatStreamBridge` 的文本发布与 `WriteSession.prepare` 的写权限 |
| 压缩、写后核对、模块、灵感与连接模型测试使用同一控制契约 | 写后核对属于同一轮的阶段；压缩由 `CompactionRunner` 把状态映射进操作表（`completed` / `failed`；`noop` 用 `phase=noop` 承载），插话要求进入摘要提示；连接测试改为 `202 + operation_id`，可暂停与继续 |
| 统一前端控件，窄屏对话收起也能暂停 | 对话面板运行中给「插话 / 暂停」，暂停后按操作快照的能力给「继续」；轮次的旧停止路由已删除，压缩的旧停止路由是同语义的薄别名 |

### 压缩与连接测试的接入

- **压缩**：受理时登记 `purpose="compaction"` 的操作，`turn_id` 为空；事件按 `compaction_operation_id` 归属（模型请求的身份仍是事件自己的 `operation_id`）。压缩记录带 `request_operation_id`，结果接口按 `kind=compaction` 的收据返回。暂停与插话在锁内完成「撤销上一代 → 登记新代次 → 启动」；重启后转为 `paused` 且 `resumable=false`，只补记中断的摘要请求，不重新调用模型。
- **连接测试**：`ProbeRunner` 在应用级操作表受理 `purpose="probe"` 的操作，`owner="models"`、`connection_id/conversation_id` 为空。测试输入（含草稿 Key）只存在内存，不写操作文件；结束写入 `kind=probe` 的收据，通过与否是两条独立事实。取消记为暂停，不伪造失败或通过；`interject` 对该用途明确拒绝 `OPERATION_STATE_CONFLICT`。


## 2. 关键取舍

**操作是控制面，不复制执行记录。** 操作文件只放「谁在跑、跑到第几代、能不能写、收到了什么收据」。事件仍是唯一执行记录，接口读取时按代次标注；这样暂停与事件回看不会互相打架。

**暂停先撤销资格，再取消远端请求。** 顺序反过来的话，取消之前的一小段时间里工具结果仍会落到文件上。资格是内存里的集合，持久事实是操作文件里的 `generation`：重启之后没有进程在跑，旧代次自然失效。

**继续是新的一代，不是恢复原连接。** 历史截止在真正启动时才算，上一代刚写下的回复会进入新代次的上下文；发给模型的是应用补的说明（「继续完成上一条要求」），不冒充作者原话。

**插话写成作者的话，但不进历史。** 插话事件带作者原文，后续轮次回放能看到；这一代自己则通过提示接收同一段文字，若同时进历史就会重复一遍。为此插话事件在上一代收尾之后、新代次启动之前写入。

**重启后不提供「继续」。** 继续要用受理时冻结的请求身份（阅读位置、参考指纹、装配快照），这部分只在进程内存里。重启恢复出来的操作标为 `needs_attention` 且 `resumable=false`，界面因此不显示「继续」按钮，不让人点了才发现接不上。

**`run_finished` 不再等于整件事结束。** 前端收到整轮终态后读一次操作快照：操作还在跑（继续或插话开了新代次）就接着接收，否则才结束「进行中」。页面状态由快照与版本驱动，不从助手的最后一句话推断。

**旧停止路由只保留同语义别名。** 轮次的旧停止路由已经删除；压缩的 `/compactions/{id}/stop` 仍在，供现有压缩卡片使用，它直接走同一暂停语义，不存在第二套状态机。压缩卡片改读统一操作后再删除。

**压缩沿用自身状态，只把事实映射进操作表。** 「没有可压缩内容」是合法结论，不是失败；统一状态里用 `completed + phase=noop` 表示，界面仍能给确定结论。压缩没有轮次，所以事件用独立的 `compaction_operation_id` 归属，不与模型请求的 `operation_id` 混用。重启后没有冻结的摘要请求可以承接，因此标为不可继续的暂停（`resumable=false`），继续按钮不出现。

**连接测试不持久化输入。** 草稿里可能有刚填写的 Key，写进操作文件就把凭据落到了状态目录。继续依赖同一进程里的输入，重启后明确拒绝继续；取消只记暂停，不写「通过」或「失败」的收据。

## 3. 代码位置

| 文件 | 职责 |
|---|---|
| `src/hastings/workspace/operations.py` | 操作对象、代次资格、控制收据、重启恢复；按操作分文件持久化 |
| `src/hastings/workspace/service.py` | 受理时登记操作；每次尝试记录 `attempt_started`；暂停／继续／插话；文本与写入的资格检查 |
| `src/hastings/workspace/chat_stream.py` | 发布前检查代次：失效代次只封口已接受的内容，不拿完整文本覆盖 |
| `src/hastings/workspace/operation_api.py` | `/api/operations` 的快照、列表、事件、流、依据、结果与三项控制；按用途分派到轮次服务、压缩运行器或连接测试 |
| `src/hastings/workspace/compaction_ops.py` | 压缩运行器：受理、暂停、继续、插话与重启恢复；状态映射进统一操作表，执行对象只在内存 |
| `src/hastings/server/probe_ops.py` | 连接测试运行器：受理、执行、暂停、继续；测试输入只存在内存，结果写 `kind=probe` 收据 |
| `src/hastings/server/model_api.py` | 连接测试路由改为 `202 + operation_id`；响应带配置版本与连接标识 |
| `frontend/src/api/models.ts` | 测试受理与跟进：`awaitProbe` 轮询操作快照，结束后从 probe 收据还原两条事实 |
| `frontend/src/components/models/ConnectionForm.tsx` | 测试中显示进度并可暂停；暂停明确报告「没有采用这次结果」 |
| `frontend/src/api/operations.ts` | 操作请求与类型；结果读取（收据） |
| `frontend/src/state/operations.ts` | 事件归并：代次隔离、游标去重、暂停后的迟到输出 |
| `frontend/src/state/conversations.ts` | 暂停／继续／插话；终态后按快照决定是否继续跟随 |
| `tests/workspace/test_operations.py` | 10 个用例：持久化、幂等建、代次判定、收据、视图、重启恢复、损坏文件 |
| `tests/workspace/test_operation_control.py` | 8 个用例：暂停后的迟到文本、首 token 前暂停、幂等暂停、继续的历史与代次、插话、状态与代次校验、重启拒绝继续、写入资格 |
| `tests/workspace/test_operation_api.py` | 9 个用例：快照只读、暂停／继续、插话、事件标注与流、依据与结果、按会话取最近操作、404、控制键冲突、压缩并入统一接口 |
| `tests/workspace/test_compaction_api.py` | 5 个用例：受理、记录、幂等键、noop、失败保留投影、404；改为经统一操作表后复跑 |
| `tests/workspace/test_model_api.py` | 连接测试改为 202 + 操作轮询，并覆盖暂停、继续、插话被拒与取消不伪造结果 |
| `tests/workspace/test_context_boundaries.py` | 重启恢复用例改为预置操作文件：转暂停、不可继续、补记中断请求且不重跑 |
| `frontend/src/api/models.test.ts` | 测试受理返回操作标识、按操作读取两条事实、暂停不冒充结果 |
| `frontend/src/state/operations.test.ts` | 6 个用例：拼接、去重、暂停、跨代、旧代迟到、缺快照 |

## 4. 验证

本阶段没有调用在线模型：全部用脚本化 provider 与受控取消。按作者要求，在线实测集中在实现完成之后。

| 检查 | 结果 |
|---|---|
| `pytest tests/workspace/test_compaction_api.py tests/workspace/test_operation_api.py tests/workspace/test_model_api.py tests/workspace/test_context_boundaries.py -q` | 44 passed |
| `pytest tests/workspace/test_operation_control.py -q`（补首 token 前暂停用例后） | 16 passed |
| `pytest -q`（全量） | **1401 passed**，392 秒，0 失败 0 错误 |
| `ruff check src/hastings tests` | 通过 |
| `mypy src/hastings` | 通过，111 个源文件 |
| `npx tsc --noEmit`（frontend） | 通过 |
| `npx vitest run` | 322 passed |

### 在线冒烟（真实模型）

`deepseek-flash`，一次短要求、两次请求：首个增量 1.08 秒；暂停后整轮 `stopped`；继续开第 2 代并 `completed`（8.3 秒）；第 1 代在收尾之后没有任何迟到增量。用量：输入 4754、输出 1451、合计 8125。完整事实与发现见[在线冒烟](../artifacts/P2-online-smoke.md)。

这次只用了两次短请求，没有跑完整在线评测；语义门槛按计划留到全部实现完成之后统一执行。

### 覆盖到的边界

| 情形 | 用例 |
|---|---|
| 暂停前到达的内容保留、取消后补来的完整文本不落盘 | `test_pause_records_paused_operation_and_stops_late_text` |
| 重复暂停只产生一次停止记录；已完成的操作不会被改成暂停 | `test_pause_is_idempotent_and_does_not_rewrite_finished_operation`、接口侧同名用例 |
| 继续是新代次，上一代输出进入上下文 | `test_continue_starts_new_generation_with_previous_output_as_history` |
| 插话作为作者的话进入下一代，事件标第 2 代 | `test_interject_adds_author_text_and_registers_next_generation` |
| 运行中不能「继续」；代次不符被拒 | `test_continue_requires_paused_state_and_matching_generation` |
| 重启后标为需要处理且不能继续 | `test_restarted_service_marks_operation_for_attention_and_refuses_continue` |
| 撤销资格后写工具拿不到基线 | `test_write_session_refuses_to_prepare_once_the_generation_is_revoked` |

## 5. 尚未完成（留给后续）

- **操作文件体积**：每次保存整份 JSON，`instruction_log / checkpoints` 随操作变长；清理策略与「查看改动」的分页一起在 P5／P7 定。
- **操作的历史列表**：目前只按会话取最近一条（刷新后恢复「继续」入口）。跨会话的操作总览没有做，避免在界面里多一个管理页。
- **压缩界面仍读旧视图**：`/compactions/{id}` 与 `/compactions/{id}/stop` 现在是同一状态事实的薄映射（停止等价于暂停），界面继续用它们；等压缩卡片改用 `/api/operations/{id}` 的同一控件后删除。
- **连接测试的设置页控件仍是局部实现**：当前只做「跟随快照 + 暂停」，继续由作者重新点一次测试；等设置页需要跨页面保留测试进度时再抽成共享控件。

## 6. 下一阶段条件

P3 需要的能力已经就位：操作快照给出任务、方案与代次；`context_refs` 记录受理时冻结的材料身份；`state_injected` 事件保留每一代实际注入的摘要。P3 要做的是把这些换成「按依赖闭包装配的必需内容」，并把实际发送内容与来源版本一起冻结。
