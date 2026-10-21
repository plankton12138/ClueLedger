# V05-2 对话展示与流式回复

日期：2026-10-21。目标是让回复边生成边可读：增量落到会话记录、界面同一条回复持续更新，
思考与工具默认折叠，公开回复按 Markdown 阅读，并能复制、展开与回到最新。实施顺序按
[开发计划](../chat-experience-plan.md)分为 2A 事件与保存、2B 实时展示、2C 阅读体验、
2D 恢复与取证四段，本轮一次交付。

## 实际改动

新增事件与保存：

- 新增 `workspace/chat_stream.py`：把模型响应、内容块与增量翻译成会话事件，负责合批、
  用完整消息校对块、写正式公开文本，以及停止/失败时的封口。
- `ConversationStore` 增加批量追加与按游标追尾读取：一批事件同一把锁内分配连续 `seq`、
  一次写入；追尾只读新增的完整行，文件被截断时重新定位；高频增量不改写会话索引。
- `TurnService._consume` 改为「读任务 + 队列」：事件之间按约 100 毫秒或约 1,024 字符提交
  缓冲，空闲也提交，因此不必等下一个 token。工具过程按 `tool_call_id` 配对并记录耗时。
- 停止、厂商报错、输出上限各写入自己的终态；截断时整轮记为 `partial`。
- 重启修复按已保存增量重建未封口的内容块，补部分公开文本与响应终态，并保持幂等。
- `conversation_context.py` 仍只回放提示与正式公开文本；同一轮多次响应的公开文本合并成
  一条 assistant 消息，各回放一次。
- 事件流接口改为追尾读取，接口路径与格式不变。

界面：

- 新增 `state/chatMessages.ts`：把事件折成「轮次 → 条目」，实时接收与从头回放共用；
  缺批时要求回读，回读仍有缺口则标记记录不完整。
- `state/conversations.ts` 保存归并结果与游标，增加按 1、2、5 秒的有限重连与「重新连接」。
- 新增 `components/chat/`：`ChatMarkdown`、`ThinkingBlock`、`ToolActivity`、
  `AssistantMessageView`、`MessageActions`、`ExpandedReplyDialog`、`ChatTurnView`、
  `useChatScroll`。`ConversationPanel` 只负责装配。
- `WorkspaceEvent` 从松散可选字段收敛为按 `type` 区分的联合类型；旧日志的分支保留。

## 接口与数据影响

会话日志新增 `run_phase`、`assistant_started`、`assistant_block_started`、
`assistant_block_delta`、`assistant_block_finished`、`assistant_finished`，
`assistant_message` 增加 `message_id` 与 `completion`，`tool_started`/`tool_finished`
增加 `tool_call_id` 与 `duration_ms`。

读写接口不变：`GET /conversations/{cid}/messages?after=` 与 `GET /events?after=&follow=`。
旧日志没有 `message_id` 的公开回复按旧记录单独展示，以 `seq` 生成本地稳定标识；没有保存的
思考、耗时与增量不补造。存储版本号未变，无需迁移。

## 实现取舍

合批阈值取「时间或字数先到者」，是为了同时解决两种观感：字符量大的短突发不该拖到 100
毫秒，慢速输出也不该因为等不到下一段而停顿。代价是同一响应可能分成多批写入，因此读侧
必须按 `part` 去重与识别缺口，不能假设一批就是一段。

停止时把「厂商报错」与「作者停止」分开：取消后 harness 会补一条没有正文的收尾消息，
若按厂商失败处理，已写出的文字会被标成失败。

块与响应各自持有完整性：收到 `text_end` 的块是完整的，即使整次响应随后被停止。界面因此
在同一轮里可能同时看到「完整的思考块」与「未完整生成的回复」。

只有思考、没有公开文字的响应用无边框的过程样式展示，不画气泡，避免一轮里出现空壳。

宽表在对话栏内横向滚动，舒适阅读走展开视图；没有为窄栏重新排列表格。

## 验证与证据

后端（2026-10-21）：

- `uv run pytest tests/workspace -q`：295 通过。新增 `tests/workspace/test_chat_stream.py`
  18 项，覆盖增量在响应结束前可读、空闲定时提交、块顺序、缺开始事件、无增量端点、
  redacted 思考、最终文本校对、多次响应、同名工具配对、停止/厂商报错/供应商抛错/截断、
  批量 `seq`、追尾读取、SSE 增量先到、上下文不重复、重启修复与幂等。
- `uv run ruff check src/hastings tests/workspace`：通过。
- `uv run mypy src/hastings`：37 个源文件通过。

前端：

- `npm test -- --no-file-parallelism`：9 个文件、84 项通过；新增 `chatMessages` 13 项与
  会话状态用例的归并断言。
- `npm run build`：通过。
- `npm run test:e2e`：11 项通过（既有布局与文件流程，不含模型）。

真实供应商走查（DeepSeek `deepseek-flash`，独立测试作品，脚本
`frontend/e2e/walkthrough-v05-2-chat.mjs`）：

- 首段可见时整轮结果卡为 0，说明模型尚未结束；首段可见 48 字；
- 状态行出现「等待模型响应」「正在执行工具」；两次响应各带思考块，默认折叠；
- 一轮内出现 2 次工具执行（读取正文、列出目录）；最终回复含 Markdown 表格；
- 复制得到 922 字 Markdown 原文，含表格管道符、不含按钮文字；
- 展开视图显示同一张表，关闭后滚动位置 586 → 586 不变；
- 向上滚动出现「回到最新」，点击后回到底部；
- 第二轮结束后 2 条带正文的回复、1 个只有思考的过程块；
- 刷新后回复文字与实时一致。

截图与观察记录见[走查观察](../artifacts/v05-2-chat/observations.md)与同目录截图。

## 偏差与未完成项

- 浏览器层面的确定性流式用例未接入：需要可脚本化供应商在真实服务里产生增量，本轮没有
  为测试新增这类开关。流式的保存、终态、去重与修复由后端受控用例覆盖，界面由真实供应商
  走查与状态层用例覆盖。
- 断线重连在浏览器里只做了代码路径与状态层覆盖，没有用真实网络中断复现；后端用例验证了
  SSE 断开后按游标重连的增量语义。
- 供应商差异只在实际调用过的 DeepSeek 一个模型上取证；四种接入协议的适配差异由后端受控
  传输用例覆盖，不含真实供应商。
- 思考展示取决于模型是否返回思考文本；本次模型有返回，其他模型未验证。
- 预选项、方案卡、可视化组件、上下文压缩按计划仍不在本轮范围，界面没有放占位按钮。
