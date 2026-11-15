# U0 基线：统计口径与原始数据能力

日期：2026-10-27。阶段：U0（现状基线、契约与原始数据能力确认）。状态：**已完成**。

本记录回答三个问题：现有事件里到底有什么可用的用量数据、统计口径按什么换算、
新增的对外字段长什么样。实现代码只到「拿到用量之后怎么解释」，估算、聚合与压缩
留给后续阶段。

## 1. 结论

- 四类协议里三类能拿到可靠用量，`google-generative-ai` 一条都没有——它不影响写作，
  但会让「累计用量」长期显示部分缺失，界面不能把它显示成零。
- 现有事件足够支撑累计统计，不需要开启原始输入捕获，也不需要改动 `tau_ai` 便携核心。
- 事件按「请求」而不是按「字段」记录缺失，所以分项层面只能如实转述，
  不能声称「供应商没报这一项」。
- 换算规则已冻结并有用例固定（`tests/workspace/test_conversation_usage.py`，14 项）。

## 2. 四类协议的用量可用性

适配层在 `src/tau_ai/`，各自把供应商字段归一化成同一份 `Usage`。核对结果：

| 协议 | 解析位置 | 可靠字段 | 已知缺口 |
|---|---|---|---|
| openai-completions | `openai_compatible.py` `_parse_chunk_usage` | 未缓存输入、输出、缓存读、缓存写、推理分项 | 需要请求带 `stream_options.include_usage`；兼容服务不给 usage 时整条为空；`prompt_tokens_details` 缺失时无法区分「无缓存」与「未上报缓存」 |
| openai-responses | `openai_compatible.py` `_usage_from_responses_event` | 同上，总量取供应商 `total_tokens` | 只读终态事件的 `response.usage`；中途断开没有终态事件就没有用量 |
| anthropic-messages | `anthropic.py` `_usage_from_message_start`、`_apply_message_delta_usage` | 输入（本身不含缓存）、输出、缓存读、缓存写、1 小时缓存写、思考分项 | 输入与缓存的合并要由统计层完成；`message_delta` 可能只覆盖部分字段 |
| google-generative-ai | `google.py` | 无 | 适配器没有解析 `usageMetadata`；`Usage` 恒为空，事件里 `usage` 为 `null` |

两条对后续阶段有约束力的事实：

1. **三类协议的 `input` 不是同一个东西。** OpenAI 两类的 `input` 已经扣掉缓存读写，
   Anthropic 的 `input` 本来就不含缓存。界面上要显示的是「这次请求让模型看了多少」，
   所以统计层负责统一加上缓存，而不是把差异留给前端。
2. **推理已经在输出里。** 两类协议都把推理算进 `completion_tokens` / `output_tokens`，
   适配层另存一份 `reasoning` 只是分项。累计时再叠加会虚增消耗。

## 3. 冻结的统计口径

三条规则写进 `workspace/conversation_usage.py` 的文档串，后续阶段不再各自解释：

- **输入包含缓存。** 完整输入 = 未缓存输入 + 缓存读取 + 缓存写入。缓存只影响计费，
  它照样占窗口；命中缓存的请求不该看起来几乎没消耗。
- **推理不重复计入。** 输出字段直接使用，不再叠加推理分项。
- **零不等于上报。** 一次真实请求必然有输入。整份用量全为零说明适配层没解析到字段，
  按未上报处理；把默认零当数据会让累计值看起来精确，实际是猜的。

补充规则：适配层只填了输入与输出时，`total_tokens` 仍是默认零。这种情况下用已知部分
重算总量，而不是把零写进累计。

分项只报了一部分时，已知项照常累计，未知项保持空值。事件层不做「这一项供应商没报」
的推断：`model_request_finished.usage` 是序列化结果，字段集合信息在此丢失，
强行为它补一套判据只会造出无法验证的结论。

## 4. 数据来源

### 跨轮

`state.py` 的会话事件日志（JSONL 为真相，SQLite 只是可重建索引）是唯一来源。
`conversation_context.replay_public_messages` 只回放两类事件：`turn_started.text`
与 `assistant_message.text`；`execution_facts` 把每轮的实际结果（完成、失败、停止、
是否记录历史、改了哪些文件、提问与回答）整理成系统提示里的一段说明。

### 轮内

本轮内存里的真实协议消息，不是事件。三个工具事件的字段只够定位：

- `tool_started.args` 只保留 `path`、`source`、`target`、`query`、`offset`、`limit`；
- `tool_finished` 只留 `summary` 摘录与 `result_chars`，全文进活动附件；
- 活动附件受脱敏、捕获开关与保留期影响，可能被清理。

所以轮内压缩的投影必须以本轮的消息对象为源，事件日志只能用来定位「当时读的是哪个文件」。
崩溃后沿用现有的中断处理，不承诺从日志自动续跑任意工具步骤。

### 不能作为统计来源

模型列表发现、连接测试、探针请求不归属任何会话；其他会话与其他作品的任务不混入；
未受理的输入框草稿与临时参考不计入当前上下文。

## 5. DTO 与接口字段

统计块的对外形状（`UsageTotals.as_dict()`），字段名与前端 `UsageTotals` 逐项对应：

| 字段 | 含义 | 空值含义 |
|---|---|---|
| `total_tokens` | 已知累计总量 | 没有任何可靠用量 |
| `input_tokens` | 含缓存读写的完整输入 | 未得到可靠数值 |
| `output_tokens` | 已含推理的输出 | 同上 |
| `cache_read_tokens` | 输入子项 | 同上 |
| `cache_write_tokens` | 输入子项 | 同上 |
| `finished_requests` | 已结束请求数，含失败与停止 | — |
| `requests_without_usage` | 其中没有可靠用量的条数 | — |
| `requests_in_progress` | 已发出、没有终态的条数 | — |
| `model_duration_ms` | 请求耗时之和 | 全部耗时未知 |

前端据此判定五种展示状态：`empty`（没有请求，显示零）、`pending`（只有进行中请求）、
`unknown`（有请求但没有可靠用量）、`partial`（已知累计中夹着缺失）、`known`。
判定逻辑在 `frontend/src/api/contextUsage.ts`，不依赖数字是否为零。

上下文估算块与压缩状态块分别在 U1 与 C1 定义，不在 U0 预设字段：
那两块的真实字段取决于估算方法与操作状态机，过早冻结只会改两次。

## 6. 新增错误码

沿用 `workspace/errors.py` 的「位置 + 现象 + 建议」格式与状态码推导规则：

| 错误码 | 位置 | 何时 | 状态码 |
|---|---|---|---|
| `MODEL_LIMITS_INVALID` | `model.limits` | 容量、回复预留或安全余量非法，或与实际回复上限冲突 | 400 |
| `CONTEXT_USAGE_NOT_FOUND` | `conversation.usage` | 会话不属于该作品或不存在 | 404 |
| `USAGE_EVENT_CONFLICT` | `conversation.usage` | 同一请求存在两个不同的终态用量 | 400 |
| `CONTEXT_COMPACTION_NOT_FOUND` | `conversation.compaction` | 整理操作不存在或不属于该会话 | 404 |
| `CONTEXT_COMPACTION_CONFLICT` | `conversation.compaction` | 来源在整理期间变化，或重复键指向不同请求 | 409 |
| `CONTEXT_COMPACTION_FAILED` | `conversation.compaction` | 摘要生成或校验失败，且剩余预算不足以继续 | 500 |
| `CONTEXT_REQUIRED_INPUT_TOO_LARGE` | `conversation.context` | 当前请求与必要原文超过输入预算 | 400 |

`USAGE_EVENT_CONFLICT` 是诊断，不阻塞原任务：保留先到的终态，另一条只作记录。

## 7. 本次验证

```text
uv run pytest tests/workspace/test_conversation_usage.py   → 14 passed
npx vitest run src/api/contextUsage.test.ts               → 5 passed
```

用例覆盖：三类协议的完整用量、缓存合并、推理不重复、缓存写入单列、整体缺失、
全零按未上报、部分字段缺失、陈旧的零总量重算、非整数脏值，以及五种展示状态。

## 8. 限制与依赖

- 未核对真实供应商返回：本节结论来自适配器解析逻辑，不是在线抓包。R1 的在线对照
  会用实际请求复核三类协议的字段可用性。
- `google-generative-ai` 的用量缺失是本版已知限制。修复它要改 `tau_ai` 适配器，
  与「不修改便携核心」的取舍冲突，本版只保证界面如实标记。
- 分项层面的缺失不可辨，前端的「部分未统计」按请求粒度说明，不逐项标注。
- U1 依赖本节的输入口径（含缓存）与 `total_tokens` 重算规则，估算方法另行选择。
