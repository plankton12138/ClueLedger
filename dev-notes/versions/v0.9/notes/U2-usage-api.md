# U2 请求记录、累计统计与读取接口

日期：2026-10-27。阶段：U2（请求记录、累计统计与读取接口）。状态：**已完成**。

这一阶段解决的是「账怎么记、怎么读」：每次真实请求留下估算与身份，会话累计按请求去重，
界面通过一个只读接口拿结果。预算拦截与压缩触发不在本阶段。

## 1. 交付

| 位置 | 内容 |
|---|---|
| `workspace/conversation_usage.py` | `summarize_events()`：按 `operation_id` 去重累计，冲突终态留诊断 |
| `workspace/model_activity.py` | `model_request_started` 增加 `request_estimate`（分项、方法、指纹、预算、容量） |
| `workspace/service.py` | 构造记录器时带上模型身份、受理时冻结的容量与装配摘要 |
| `workspace/api.py` | `GET /{workspace_id}/conversations/{conversation_id}/usage` |
| `frontend/src/api/` | `fetchConversationUsage`、`ConversationUsageView` 类型与契约测试 |

## 2. 累计按什么去重

以 `operation_id` 为单位，一个请求只算一次。事件日志会被重放——断线重连、客户端带旧游标
回读、`run_finished` 之后重新连接——把重放当成新请求，累计值会凭空翻倍，而且翻倍得
很像真话。

三种边界：

- **只有开始事件**：计入「进行中」。请求确实发出去了，只是还没有终态用量。
  这类不计入已完成数，也不假装它没有发生。
- **只有终态、开始事件被裁剪**：仍然计入。少记一次消耗比漏一次更容易误导作者判断成本。
- **同一请求两个不同终态**：保留先到的一条，后到的写进 `diagnostics`。先到的那条对应
  真正完成的响应，后到的多半是重放或竞态。诊断不参与累计，也不阻塞原任务。

反过来，同一个终态重复推送两次（内容相同）不算冲突，只算一次。

## 3. 读取接口

`GET /api/file-workspaces/{workspace_id}/conversations/{conversation_id}/usage`

返回累计值、请求计数、`through_seq` 与 `diagnostics`。三条约束：

- 不读活动附件：附件受保留期影响，可能已被清理，统计不能依赖它。
- 不产生模型调用、不写会话事件：打开一次浮层不应该改变任何数据。
- 不存在的会话返回 404（`CONVERSATION_NOT_FOUND`），而不是一份全零统计。

`through_seq` 是这次统计覆盖到的事件序号。客户端拿它判断本地视图是否落后：一个慢的
GET 回来得比 SSE 更新更晚时，不能把更新的数字覆盖回去。

## 4. 请求记录里的估算

`model_request_started` 新增 `request_estimate`，与既有的 `request_size`（字符数）并列：

```text
total_tokens / system_tokens / tool_tokens / message_tokens / protocol_tokens
method / fingerprint / budget
context_window_tokens / input_limit_tokens / limits_revision
```

字符数保留，是因为它对不上模型窗口——「这轮装了 12 万字符」无法回答「还装得下吗」。
两个数各有用途，不互相替代。

`budget` 直接来自受理时冻结的容量：`unknown` 表示窗口未知，此时不能显示百分比，也不能
据此触发自动整理。容量在受理时固定，中途改模型配置只影响下一轮。

`fingerprint` 包含模型身份、系统提示、工具序列化、消息序列化、装配摘要与估算方法。
同一指纹下的估算可以互相比较；换了任何一项，旧值只能当历史看。

## 5. 一个容易误判的行为

会话里存在「已受理但没有结束事件」的轮次时，**第一次请求这个作品的接口**会触发既有的
中断收尾，补一条 `run_finished`。这是服务重启后的恢复逻辑，和用量读取无关，但它会让
「读取不写事件」的用例在没有终态的会话上失败。

写测试时要先补齐终态，否则断言的是恢复逻辑而不是读取行为。真实使用中这条补充只在
服务重启后发生一次。

## 6. 验证

```text
uv run pytest tests/workspace/test_conversation_usage.py   → 24 passed
uv run pytest tests/workspace/test_usage_api.py            → 5 passed
uv run pytest tests/workspace/test_activity.py             → 21 passed
uv run pytest test_chat + 上述文件                          → 91 passed
npx vitest run src/api/contextUsage.test.ts                → 6 passed
npx tsc --noEmit                                           → 通过
```

覆盖：两次请求相加、终态重放不翻倍、冲突终态留诊断、只有开始事件、只有终态、全部缺失、
部分缺失、耗时未知、非 `model_request_*` 事件被忽略、坏 usage、空会话、未知会话 404、
读取不写事件、有容量与无容量两种请求记录。

## 7. 限制与依赖

- **没有预算拦截**：估算已经写进事件，但发出请求前不检查、不拒绝。拦截与自动整理
  属于 C1/C2；本阶段只是把数据准备好。
- **SSE 未扩展**：`context_usage_updated` 等事件留给 U3。当前界面要靠 GET 刷新。
- **没有真实供应商的估算校准**：R1 才能拿实测反推偏差。
- **累计依赖事件日志**：`model_request_finished` 被裁剪或损坏时无法重建，此时只有
  `diagnostics` 与缺失计数。目前没有为统计单独保存副本，这是有意选择——两套记录会分叉。
