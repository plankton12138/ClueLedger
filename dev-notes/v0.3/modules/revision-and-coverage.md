# 意图三分、覆盖台账、预算与修订任务

> 状态：**as-built**（V03-7.1–7.7，2026-10-19）。证据：[7.1](../notes/v03-7.1-intent-and-authorization.md)–[7.7](../notes/v03-7.7-impact-and-related-undo.md)、[8.7 真模型](../notes/v03-8.7-real-model.md)。
> 实际包：`application/services/{intent_service,revision_work_service,revision_task_service,revision_publish_service,revision_links}.py`、`domain/revision/*`、`interfaces/api/revision.py`。

## 1. 职责

一次「整体调整」的三个层次：作者意图（讨论/候选/执行）→ 逐章覆盖与内部工作版本 → 有预算上限的执行与统一发布。所有层次都可回读，不由模型自述证明。

## 2. 意图三分（7.1）

- `POST /conversations/{id}/intents`：只认作者消息或按钮授权；讨论无写权限；同范围不重复审批。
- 假设性讨论（"假如凶手是姐姐"）不修改正式内容；执行需要有明确范围。

## 3. 覆盖台账（7.2）

- 逐章读取登记（`summary`/`full`）与结论：`confirmed_unchanged` / `changed` / `blocked`（+ 未读）。
- **摘要读取不算全文检查**；`complete` 只在全书下过结论后为真（8.7 实测：两章 `changed` → 2/2 complete）。
- `GET /revision-work` 回读覆盖与全局上下文。

## 4. 预算与修订任务（7.3）

- `POST /revision-tasks` 开任务：`call_budget` 只能缩小默认 12；同作品同时只允许一个进行中任务（不同 request_id 409）。
- 预算口径（任务视图）：`budget.call_limit / calls_used / remaining / repair_rounds / max_repair_rounds(2) / exhausted`。
- **开任务不调用模型**（`calls_used=0`，Job 数不变，8.7 S3.1 实测）；到上限落 `waiting/budget_exhausted`，不新建隐含任务绕预算。
- 停止终态 `cancelled`；继续追加预算不清零已用次数与修正轮次。

## 5. 统一发布与撤销（7.4/7.7）

- 执行期间新建副本也阻止冲突发布；稳定版不半套更新。
- 发布前保护副本与基础重验；撤销是新操作记录，覆盖关联账本并保护后续独立修改（三方检查）。

## 6. 图谱与时间线（7.5/7.6）

- 手法图只投影已有正式文字与关系，缺口照实列出，不新造步骤对象、不模型补图。
- 时间线真实/证词分轨；未知时间不排序；叙事位置只来自获知事件。

## 7. 未验与留白

- **模型驱动的修订全循环未端到端跑**（8.7 留白）：读章 → 改稿 → 覆盖推进 → 暂存 → 发布。各分项有领域/HTTP 用例，但整体链路未以真模型跑通；这是 0.3 完整验收的核心未验项。
- 逐章审批流的底层服务（review/audit/rewrite）保留未删，入口 410（见 [api-matrix](api-matrix-and-retirement.md)）。
