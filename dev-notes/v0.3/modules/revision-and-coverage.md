# 意图三分、覆盖台账、预算与执行循环

> 状态：**as-built**（V03-7.1–7.7 + 8.7 执行循环接线，2026-10-19）。证据：[7.1](../notes/v03-7.1-intent-and-authorization.md)–[7.7](../notes/v03-7.7-impact-and-related-undo.md)、[8.7 真模型](../notes/v03-8.7-real-model.md)、[8.7 执行循环](../notes/v03-8.7-execution-loop.md)。
> 实际包：`application/services/{intent_service,revision_work_service,revision_task_service,revision_publish_service,revision_execution_service,revision_links}.py`、`domain/revision/*`、`interfaces/api/revision.py`。

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
- **预算消费只发生在执行循环**：`POST .../run` 每章 `consume_calls(1)`；手动调试写候选（`POST /drafts`）不计任务预算。
- 停止终态 `cancelled`；继续追加预算不清零已用次数与修正轮次。

## 5. 执行循环（8.7 接线）

`POST /revision-tasks/{task_id}/run`：作者显式执行一轮，逐章按序：

1. `consume_calls(1)`（**先记账再调用**；不足时账本自动落 `waiting/budget_exhausted`）；
2. `record_read(kind=full)` 登记真实读取；
3. `drafts.create(target=章)` 生成改写候选（提示词自带该章「现有正文」）；
4. `stage-chapters` 以任务基础版本的章基础 CAS 暂存；
5. `mark_changed` 下覆盖结论。

边界：

- **默认范围**：还没下结论的章（`changed` / `confirmed_unchanged` 跳过）；要重改某章就显式给 `chapter_ids`。
- **预算不足**：服务捕获账本抛出的冲突，返回 `status=stopped` 与 `stopped.reason=budget_exhausted`；**下一章没有调用模型**，已完成章节的暂存与覆盖原样保留。
- **幂等**：`request_id` 一条执行记录（`revision_runs` 辅助表），重放返回原摘要，不重复记账、不重复调用；继续后用新的 `request_id`。
- **基础前进**：`head != task.base_commit_id` 直接 `REVISION_BASE_MOVED_CONFLICT`，不拿旧基础暂存。
- **模型失败**：已记账的调用不退回（真实消耗），任务保持 `running`，错误原样上报。
- **顺序执行**：不做多章并行；`repair_rounds` 尚未接入（见 §8）。

## 6. 统一发布与撤销（7.4/7.7）

- 执行期间新建副本也阻止冲突发布；稳定版不半套更新。
- 发布门禁：**只有「因预算停下」（`waiting/budget_exhausted`）才因预算阻止**；预算刚好用满但本轮跑完、覆盖完整时允许发布（8.7 修正：旧规则会强迫作者追加一次用不到的预算）。
- 发布前保护副本与基础重验；撤销是新操作记录，覆盖关联账本并保护后续独立修改（三方检查）。

## 7. 图谱与时间线（7.5/7.6）

- 手法图只投影已有正式文字与关系，缺口照实列出，不新造步骤对象、不模型补图。
- 时间线真实/证词分轨；未知时间不排序；叙事位置只来自获知事件。

## 8. 未验与留白

- **修正轮次未接**：`start_repair` 有领域与记账实现，执行循环还没有「作者要求再修一轮」的触发点。
- **前端未接 `/run`**：任务卡仍只展示预算与覆盖，执行入口没有按钮（API 与驱动已可用）。
- **并行执行未做**：一章一次模型调用，按序串行；多章并行与子工作者合并记账（`calls=n`）留后续。
- 逐章审批流的底层服务（review/audit/rewrite）保留未删，入口 410（见 [api-matrix](api-matrix-and-retirement.md)）。
