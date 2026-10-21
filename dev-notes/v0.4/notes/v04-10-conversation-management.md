# V04-10 会话管理：阶段记录

日期：2026-10-21。范围：会话生命周期、连续讨论、任务归属与恢复、草稿与列表。计划见[会话管理开发计划](../conversation-management-plan.md)，已实现行为见[会话与草稿](../modules/conversations.md)。

## 1. 做了什么

### A 会话生命周期

- `ConversationStore` 用 `create_with_first_turn()` 取代「先建空会话再发送」：空提示直接拒绝，不留登记、不留日志；首轮日志（`seq 0` 元信息 + `seq 1` 的 `turn_started`）整份原子发布，之后才更新索引。
- 索引写失败不影响已经受理的提示：`conversations()` 读取时按 `*.jsonl` 重建缺项并尝试写回；旧日志没有元信息行时按首条提示补标题，不重写旧记录。
- 请求键去重落在日志里（不在进程内存）：同键同内容返回原会话/原轮次，同键不同内容或换会话返回 409 `REQUEST_KEY_CONFLICT`。超时重试与重启后的重复提交都不会生成第二个会话。
- 接口改为「首次发送即建会话」：`POST /{wid}/conversations` 要求 `text` 与 `request_key`，返回 202；旧的空 body 请求现在是 422。

### B 连续讨论

- 新增 `workspace/conversation_context.py`：`replay_public_messages()` 只回放 `turn_started.text` 与 `assistant_message.text`；`execution_facts()` 把每轮实际状态、改动路径与记录状态写成一段应用说明。
- 受理时记下历史游标（`TurnInfo.history_seq`），恢复范围止于该游标；当前提示由 `harness.prompt()` 追加一次，不会重复。
- 执行事实进系统提示而非冒充作者消息；`run_finished` 与模型自述冲突时以执行事实为准。
- 工具往返、思考签名与工具结果正文不跨轮回放：避免拼出残缺工具历史，也避免跨供应商复用签名。涉及原文时模型重新 `read_file`。

### C 任务归属与恢复

- `TurnService` 拆出受理锁与执行锁：受理锁覆盖「查重 → 忙碌判断 → 写初始记录 → 占用」，`busy` 改为「有活动轮次」，不再等后台任务拿到执行锁。
- `ActiveTurn` 记录会话、轮次、状态（accepted/running/stop_requested）与已固定模型的公开信息；列表与消息快照都带它。
- `stop()` 覆盖两个阶段：还没建 harness 的轮次直接写「已停止」终态并清空占用；正在跑的传取消。停止后不会留下「轮次不存在」。
- `mark_interrupted()` 改为按轮次配对：任何有 `turn_started` 没有 `run_finished` 的轮次都补中断终态，崩在工具阶段同样识别；重复启动不会补第二条。
- 收尾只释放自己那一轮，不清除后来任务的状态。

### D 草稿与列表

- 新增 `ConversationUiStore`（`conversation-ui.json`）：`recent_id` + 各会话草稿 + 内容散列版本。`PATCH /{wid}/conversation-ui` 只合并指定条目，`If-Match` 版本不符返回 409 `CONVERSATION_UI_CONFLICT`。
- 前端新增 `state/conversations.ts`：按会话索引的事件、运行状态、活动轮次与草稿；订阅按会话键与代次隔离，旧订阅的收尾不改别的会话。
- 切会话先切视图再读内容：慢响应回来只更新它自己的会话，不抢回选择；切换前把旧会话草稿按旧键保存。
- 首次受理后才清除「仍然等于提交文本」的草稿；受理期间新输入的字保留。
- 结果卡按 `run_finished` 挂在各自轮次后面，取代全局 `lastResult`；列表显示运行中、「改名」与「最近活动排序」。
- `state/workspace.ts` 只剩作品本身（目录、文件、Git、抽屉），一轮结束通过回调回读目录与 Git。

## 2. 取舍

- **不回放工具历史**：日志本来就只存工具定位与结果摘要，硬拼会造出没有对应工具结果的请求。代价是模型对旧轮次只能看到「做了什么、改了哪些文件」，需要原文时自己读。
- **执行事实进系统提示**：不伪装成用户消息，也不引入第三种角色；冲突时以应用记录为准。
- **受理即占用**：比「执行才占用」更严格，换来的是两次几乎同时的发送不会都通过检查。
- **界面状态由界面写**：服务端不替界面记「上次看的会话」，避免与草稿写入互相覆盖；重启时按最近活动回退到一个正式会话。
- **没有浏览器缓存恢复**：草稿以服务端保存为准，保存失败保留内存文字并提示，不做 localStorage 版本合并。

## 3. 接口影响

| 变更 | 调用者 |
|---|---|
| `POST /{wid}/conversations` 改为建会话 + 受理首轮（202，要求 `text`/`request_key`） | 前端 `createConversationWithText`、验证脚本 |
| `GET /{wid}/conversations` 从数组改为 `{items, recent_id, active_turn, revision}` | 前端 `listConversations`、测试准备器 |
| `GET .../messages` 增加 `active_turn` 与 `revision` | 前端 `conversationMessages` |
| 新增 `PATCH /{wid}/conversations/{cid}`（改名） | 前端「改名」 |
| 新增 `GET/PATCH /{wid}/conversation-ui` | 前端草稿与最近会话 |
| `POST .../turns` 的 `request_key` 变为必填 | 前端发送、验证脚本 |
| `POST /{wid}/turns/{tid}/stop` 改为异步并覆盖受理阶段 | 前端停止、测试 |

旧的 `POST conversations {}` 不再创建空记录；旧磁盘记录保持可读，不批量迁移或删除。

## 4. 验证

- 后端：`uv run pytest tests/workspace -q` → 261 项通过（62.9s）；`uv run ruff check src/hastings tests/workspace`、`uv run mypy src/hastings` 通过。
- 前端：`npx vitest run --no-file-parallelism` → 52 项通过（6 个文件）；`npx tsc --noEmit`、`npm run build` 通过。
- 新增用例：
  - `test_conversations.py`（19 项）：元信息与首轮一起发布、同键去重与冲突、索引自愈、旧日志可读、未完成轮次配对、界面草稿版本与合并、上下文回放与执行事实。
  - `test_chat.py`（22 项，重写）：首次发送只建一个会话、空提示不留记录、同键不同内容冲突、索引丢失可恢复、日志写失败不留半条会话、第二轮真的带上前一轮提示与回复、新会话不带其他会话历史、共享作品文件、忙碌拒绝且不留空会话、受理阶段可停止、重启中断按轮次识别且不重复、事件按会话隔离、改名不被自动标题覆盖、草稿与最近会话接口。
  - 前端 `state/conversations.test.ts`（15 项）：临时新状态、首次发送、受理失败保留草稿、受理期间新输入不被清、切换保存旧草稿、慢响应不抢回视图、切走不停止任务、停止只发请求、草稿版本与冲突、改名。
- 真实链路：见[验证材料](../artifacts/v04-10-verification.md)。

## 5. 未做

- 浏览器内逐屏走查（会话切换、草稿恢复、执行中切换）：界面由构建、状态与组件测试覆盖，真实链路在接口层取证。
- 真实供应商只覆盖 DeepSeek。
- 长会话压缩、会话分支、跨会话检索不在本阶段。
