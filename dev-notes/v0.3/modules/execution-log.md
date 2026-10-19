# 执行日志通道

> 状态：**as-built**（V03-1.6、8.4，2026-10-19）。证据：[1.6](../notes/v03-1.6-execution-log.md)、[8.4](../notes/v03-8.4-log-cursor-and-pressure.md)。
> 实际包：`domain/execution_log.py`、`infrastructure/execution_log.py`（Memory/SQLite 双后端）、`interfaces/api/execution_log.py`。

## 1. 职责

运行事件（执行日志）的独立事实源：任务/Agent/模型/读取/等待/失败/停止/提交类事件，与对话 SSE（消息投影）、业务 WS（版本变化）三通道分工，互不冒充。

## 2. 契约

- **append-once**：`(project_id, source_event_id)` 幂等；事件 ID 全局唯一（§M13），SQLite 以全局唯一约束去重。
- **作品级游标**：`sequence` 严格递增；`GET /execution-events?after_sequence&limit` 分页，`next_cursor=None` 表示取尽（V03-1.6 契约）。
- **SSE**：`GET /execution-events/stream`；`tail=0` 有限流排空当前积压后关闭（积压超过 page_size 连续取页不截断），`tail>=1` 持续流以注释帧心跳（只证明连接存活，不伪装进度）。
- **断线补齐**：历史分页与实时流共用同一游标与事件 ID 空间；重连 = 从上次游标继续，无重复无遗漏（8.4 双后端实测）。
- **性能目标**：`PERFORMANCE_TARGETS["execution_log"].page_size`；full 规模（12 作品×833 事件）分页 p95 **1.29ms**（目标 <1000ms）。

## 3. 关键决策

1. **心跳是注释帧**（`": heartbeat"`），不是 data 帧——客户端不会把它当执行进度。
2. **拒绝语义在生成器层可测**：8.4 起帧生成提为模块级 `event_stream`，路由与测试共用；本机 TestClient 会把响应整体缓冲，无限流走生成器层测试（环境约束已记录）。
3. **日志不改状态**：执行日志只读投影；停止/取消等动作仍走各自命令与服务。

## 4. 未验与留白

- **前端日志面板消费端未接**（1.6 留白，用户裁决后置）：接口与事件齐备，但没有前端读取方；A24 因此只算「部分」。
- `mark_interrupted` 不发射执行事件（恢复只标记状态，取证走 `GET /recovery`）；去向 8.7/8.8 销号或顺延。
- 心跳帧不带游标（客户端游标经 REST 分页建立）。
