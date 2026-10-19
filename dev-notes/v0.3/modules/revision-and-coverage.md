# 整体修订、覆盖与预算

整体修订把作者的明确范围转换为一项可回查任务：记录实际读取的章节，限制模型调用，先暂存改动，检查完成后统一发布。讨论不直接获得正文写权限。

以下路径省略公共前缀 `/api/v2` 与所属作品段 `/projects/{project_id}`。

## 意图与覆盖

`POST /conversations/{id}/intents` 区分讨论、候选与执行，只认作者消息或按钮授权。同范围不重复要求审批；假设性讨论不修改正式内容。

覆盖台账记录章节来源版本、读取种类、区间与结论。状态为 `unread`（未读）、`read`（已读未确认）、`checked_unchanged`（全文检查后无需修改）、`changed`（已修改）、`blocked`（受阻，须有原因）。只读摘要或部分正文不能确认无需修改。

`GET /revision-work` 回读台账；`complete` 只在声明范围内所有章节均为“无需修改”或“已修改”时为真，受阻不算完成。

## 预算与任务

`POST /revision-tasks` 创建任务，创建本身不调用模型。同作品同时只允许一个进行中任务。默认调用上限为 12，启动时只能缩小；继续可以追加预算，但不清零已用次数。

任务保存 `call_limit`、`calls_used`、剩余预算及修正轮次，修正上限为两轮。预算不足先持久化为 `waiting/budget_exhausted`，再返回停止原因；停止状态为 `cancelled`。

预算不是所有模型调用的全局额度：独立试写 `POST /drafts` 不计整体任务预算。

## 执行与暂存

`POST /revision-tasks/{task_id}/run` 由作者显式请求，按章顺序执行：

1. 先消费一次预算，再登记全文读取。
2. 生成改写候选，提示词带该章现有正文。
3. 按任务基础版本暂存章节，并更新覆盖结论。

预算不足停在下一章调用之前，保留已有暂存与覆盖。模型失败时已消费的调用不退回，错误上报，任务保持 `running`。正式版本前进后返回 `REVISION_BASE_MOVED_CONFLICT`，不继续使用旧基础。

同一 `request_id` 的已完成执行记录可重放原摘要，继续执行应使用新的请求编号。章节按序执行，没有并行子任务。

## 发布与撤销

`POST /revision-tasks/{task_id}/publish` 在副本保护、基础版本、覆盖和预算状态核对后，把正文与账本放进同一次提交。暂存期间不改正式稿。只有因预算不足而等待的任务被预算门禁阻止；恰好用满预算但已完成覆盖的任务允许发布。

操作影响从实际版本差异回读；关联撤销保护后续独立编辑，具体见[修改与撤销](editing-and-undo.md)。

## 当前限制

- 前端任务卡尚无 `/run` 按钮，执行接口与驱动可用。
- 修正轮次有领域和记账实现，但执行循环尚无 `start_repair` 触发点，不能称为已贯通两轮修正。
- 已确认无需修改（`checked_unchanged`）的章不在默认执行范围，不重复消耗预算；要重改某章需在面板显式勾选。
- 一章一次模型调用；多章并行与子任务合并记账未实现。

## 代码位置

`domain/revision/`；`application/services/{intent_service,revision_work_service,revision_task_service,revision_execution_service,revision_publish_service,revision_links}.py`；`interfaces/api/revision.py`。图谱见[独立模块](graphs.md)。
