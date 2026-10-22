# V06-B2 范围与自然语言闭环

> 实施日期：2026-10-22。计划见[上下文与文件操作开发计划](../context-and-file-operations-plan.md)。
> 本阶段把范围写进会话事件、补上界面展示与承接规则，并补语义边界用例；写后检查（C1）
> 仍不在本阶段。

## 目标

作者能看到这一轮到底被允许改什么、依据哪句话；「参考」不被当成修改授权；「继续」
必须能指出承接的是哪条旧要求；固定范围不会被悄悄扩大。

## 实际改动

| 位置 | 改动 |
|---|---|
| `workspace/agent.py` | `set_task_scope` / `extend_task_scope` 成功后写 `task_scope_set` / `task_scope_extended` 事件（版本、模式、任务理解、授权原话、承接位置、允许项、禁止项、可追加类别） |
| `workspace/task_scope.py` | `carried_from_seq` 校验：必须指向本轮可见的作者消息，且不能与授权语句是同一条（否则「继续」没有真正承接） |
| `workspace/context.py` | 提示词补充：参考不产生修改权限；「继续」要能定位承接对象，找不到先澄清 |
| `frontend/src/api/workspace.ts` | `TaskScope` / `TaskGrant` 类型与两类范围事件 |
| `frontend/src/state/chatMessages.ts` | `ChatTurn.scope`；范围事件按版本前进，迟到旧版本不覆盖 |
| `frontend/src/components/chat/TaskScopeDetails.tsx`（新增） | 每轮范围详情：任务、授权原话、承接位置、允许项、禁止项、可追加类别 |
| `frontend/src/components/chat/ChatTurnView.tsx` | 在材料详情下展示范围 |

## 接口与数据影响

- 新事件：`task_scope_set`、`task_scope_extended`，字段与工具返回一致。
- 事件只记录登记内容，不记录执行结果：写入是否成功由 `tool_finished` 与
  `run_finished` 说明，避免两处状态互相矛盾。
- `ChatTurn.scope` 只保留最新版本；回读与实时接收走同一个归并函数。

## 取舍

- **范围写进事件而不是只留在内存**：作者切回历史轮次仍能看到当时允许改什么；
  内存里的 `ScopeController` 仍然只服务当前轮。
- **版本只前进**：乱序或迟到的事件不能把范围退回旧版本。
- **承接要指认对象**：只写「继续」不算授权；必须给出被承接的作者消息位置，
  且不能与授权语句是同一句。
- **确定性边界**：应用能证明操作没有超出已记录范围，不能证明范围解释符合作者意图。
  本阶段的用例只验证规则与接线；「只改某章」「同步相关章节」这类自然语言理解仍需
  真实模型验收（见偏差）。

## 验证证据

- `uv run pytest tests/workspace/test_task_scope.py -q`：21 项通过。新增覆盖两个范围工具
  写出的事件内容、承接位置的三种情况（同一条、不存在、正确承接）。
- `tests/workspace/test_chat.py`：新增两条端到端用例。
  - `test_reference_does_not_authorize_writing`：作者只把文件当参考时，写入被
    `TASK_SCOPE_REQUIRED_CONFLICT` 拒绝，正文指纹不变，事件里没有范围登记。
  - `test_scope_is_recorded_and_writes_follow_it`：登记后写入成功，`task_scope_set`
    带任务理解、授权原话、允许与禁止项。
- 前端：`npm test` 通过。新增 3 项归并用例：版本前进、只读范围、未登记时为空。

## 偏差与未完成项

- **真实模型语义验收未做**：计划要求用独立作品跑「只改第三章」「同步相关章节」「继续
  缺少承接对象」等真实模型任务，本阶段只完成受控 provider 与规则层验证。这类用例需要
  实际模型与人工判读，未在本次交付中执行。
- 范围详情只读展示，没有「按范围撤回某条追加」的界面操作；范围只能重新登记。
- 写后检查（C1）与整体交付（C2）仍缺；包版本仍为 `0.5.0`。
