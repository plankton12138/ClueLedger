# P2：统一模型操作与恢复

日期：2026-10-29。状态：**写作轮次已全部接入统一操作（可见、可暂停、可继续、可插话）；压缩与连接测试尚未并入同一张操作表**。

## 1. 本阶段做了什么

| 计划项 | 落地 |
|---|---|
| 现有轮次接入统一操作视图，复用事件来源 | `workspace/operations.py` 只存控制面（状态、代次、指令、收据）；执行记录仍是会话事件。操作接口按 `attempt_started` 把事件标回代次，没有第二份执行日志 |
| 区分真实文本增量、工具进度、完整候选与提交结果 | 沿用既有事件：`assistant_block_delta`（真实增量）、`tool_started/finished`（进度）、`assistant_message`（完整公开文本）、`version_finished` + 操作收据（提交结果）。暂停后晚到的增量与完整文本都不再发布 |
| 暂停撤销代次资格、继续新代次、插话原子受理、等待作者回答、失败保留草稿 | `TurnService.pause_operation / continue_operation / interject_operation`；资格检查落在两处：`ChatStreamBridge` 的文本发布与 `WriteSession.prepare` 的写权限 |
| 压缩、写后核对、模块、灵感与连接模型测试使用同一控制契约 | **部分完成。** 写后核对属于同一轮的阶段，已经在同一操作下；压缩仍走 `CompactionRunner` 自己的状态，连接测试仍一次性返回。两者待并入，见第 5 节 |
| 统一前端控件，窄屏对话收起也能暂停 | 对话面板运行中给「插话 / 暂停」，暂停后按操作快照的能力给「继续」；`stop()` 已删除，`/turns/{id}/stop` 仅作为资料页仍在使用时的薄别名 |

## 2. 关键取舍

**操作是控制面，不复制执行记录。** 操作文件只放「谁在跑、跑到第几代、能不能写、收到了什么收据」。事件仍是唯一执行记录，接口读取时按代次标注；这样暂停与事件回看不会互相打架。

**暂停先撤销资格，再取消远端请求。** 顺序反过来的话，取消之前的一小段时间里工具结果仍会落到文件上。资格是内存里的集合，持久事实是操作文件里的 `generation`：重启之后没有进程在跑，旧代次自然失效。

**继续是新的一代，不是恢复原连接。** 历史截止在真正启动时才算，上一代刚写下的回复会进入新代次的上下文；发给模型的是应用补的说明（「继续完成上一条要求」），不冒充作者原话。

**插话写成作者的话，但不进历史。** 插话事件带作者原文，后续轮次回放能看到；这一代自己则通过提示接收同一段文字，若同时进历史就会重复一遍。为此插话事件在上一代收尾之后、新代次启动之前写入。

**重启后不提供「继续」。** 继续要用受理时冻结的请求身份（阅读位置、参考指纹、装配快照），这部分只在进程内存里。重启恢复出来的操作标为 `needs_attention` 且 `resumable=false`，界面因此不显示「继续」按钮，不让人点了才发现接不上。

**`run_finished` 不再等于整件事结束。** 前端收到整轮终态后读一次操作快照：操作还在跑（继续或插话开了新代次）就接着接收，否则才结束「进行中」。页面状态由快照与版本驱动，不从助手的最后一句话推断。

**旧停止路由保留为薄别名。** 资料页仍在调用 `/turns/{id}/stop`；它现在直接走同一暂停语义，不存在第二套状态机。等资料页在 P5 退出时一并删除。

## 3. 代码位置

| 文件 | 职责 |
|---|---|
| `src/hastings/workspace/operations.py` | 操作对象、代次资格、控制收据、重启恢复；按操作分文件持久化 |
| `src/hastings/workspace/service.py` | 受理时登记操作；每次尝试记录 `attempt_started`；暂停／继续／插话；文本与写入的资格检查 |
| `src/hastings/workspace/chat_stream.py` | 发布前检查代次：失效代次只封口已接受的内容，不拿完整文本覆盖 |
| `src/hastings/workspace/operation_api.py` | `/api/operations` 的快照、列表、事件、流、依据、结果与三项控制 |
| `frontend/src/api/operations.ts` | 操作请求与类型 |
| `frontend/src/state/operations.ts` | 事件归并：代次隔离、游标去重、暂停后的迟到输出 |
| `frontend/src/state/conversations.ts` | 暂停／继续／插话；终态后按快照决定是否继续跟随 |
| `tests/workspace/test_operations.py` | 10 个用例：持久化、幂等建、代次判定、收据、视图、重启恢复、损坏文件 |
| `tests/workspace/test_operation_control.py` | 7 个用例：暂停后的迟到文本、幂等暂停、继续的历史与代次、插话、状态与代次校验、重启拒绝继续、写入资格 |
| `tests/workspace/test_operation_api.py` | 8 个用例：快照只读、暂停／继续、插话、事件标注与流、依据与结果、按会话取最近操作、404、控制键冲突 |
| `frontend/src/state/operations.test.ts` | 6 个用例：拼接、去重、暂停、跨代、旧代迟到、缺快照 |

## 4. 验证

本阶段没有调用在线模型：全部用脚本化 provider 与受控取消。按作者要求，在线实测集中在实现完成之后。

| 检查 | 结果 |
|---|---|
| `uv run pytest tests/workspace/test_operations.py tests/workspace/test_operation_control.py tests/workspace/test_operation_api.py -q` | 25 passed |
| `uv run pytest tests/workspace/test_chat.py tests/workspace/test_module_context_and_stop.py -q` | 54 passed（暂停语义替换旧停止后复跑） |
| `uv run pytest -q`（全量） | **1390 passed**，427 秒，0 失败 0 错误 |
| `uv run ruff check src/hastings tests` | 通过 |
| `uv run mypy` | 通过，106 个源文件 |
| `npx tsc --noEmit`（frontend） | 通过 |
| `npx vitest run --no-file-parallelism` | 328 passed |
| `npm run build` | 通过 |
| `npx playwright test shared-interactions.spec.ts --project=workspace` | 5 passed（含「暂停点击立即反馈」） |

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

- **压缩与连接测试并入操作表**：控制契约与接口已就绪，缺的是适配层——压缩需要把 `CompactionRunner` 的状态映射为操作，连接测试需要把一次性返回改成 `202 + operation_id`。两者目前仍各自可暂停，但不在这张表里，界面也还没共用控件。
- **结构提交的代次检查**：本轮只覆盖正文写入（`WriteSession.prepare`）。`StoryStore.commit_change` 还没有接入，等 P4 的结构工具真正落地时补 D08 的「提交决定前后」用例。
- **操作文件体积**：每次保存整份 JSON，`instruction_log / checkpoints` 随操作变长；清理策略与「查看改动」的分页一起在 P5 定。
- **操作的历史列表**：目前只按会话取最近一条（刷新后恢复「继续」入口）。跨会话的操作总览没有做，避免在界面里多一个管理页。
- **`/turns/{id}/stop` 未删除**：资料页还有调用方，P5 随资料页一起退出。

## 6. 下一阶段条件

P3 需要的能力已经就位：操作快照给出任务、方案与代次；`context_refs` 记录受理时冻结的材料身份；`state_injected` 事件保留每一代实际注入的摘要。P3 要做的是把这些换成「按依赖闭包装配的必需内容」，并把实际发送内容与来源版本一起冻结。
