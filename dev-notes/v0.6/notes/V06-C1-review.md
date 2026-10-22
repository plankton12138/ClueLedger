# V06-C1 只读检查

> 实施日期：2026-10-22。计划见[上下文与文件操作开发计划](../context-and-file-operations-plan.md)。
> 本阶段做「检查报告、只读工具集、显式检查与改完检查」，整体交付与版本升级在 C2。

## 目标

需要理解剧情的检查有明确入口、有原文依据、有覆盖说明：作者说「检查第三章」或点
「检查本次改动」时，只读地核对实际落盘的内容；作者说「改完检查」时，修改完成后在同一轮
进入只读检查。普通修改不隐含开启检查。

## 实际改动

| 位置 | 改动 |
|---|---|
| `workspace/review.py`（新增） | `ReviewDraft` / `ReviewReport` / `ReviewStore`、`validate_report`、`publish_report`、`target_digests`、`REVIEW_INSTRUCTIONS` |
| `workspace/agent.py` | `build_tools(mode="review")` 只给目录、搜索、读取与 `save_review_report`；写模式新增 `request_review`（记录作者明确要求的写后检查） |
| `workspace/service.py` | `accept(..., purpose="review", review_paths=...)`；检查轮次用只读工具集；修改任务里登记了检查要求时，完成修改后进入只读检查阶段；没有报告时记 `review_finished: incomplete` 并把整轮标为部分完成 |
| `workspace/state.py` | `AcceptedTurn.review_paths`，写入 `turn_started`；旧日志按无目标处理 |
| `workspace/api.py` | 请求模型加 `review_paths` 与用途互斥校验；新增 `GET /{wid}/reviews` |
| `frontend/src/api/workspace.ts` | 检查报告与两类事件类型；`fetchReviews`；`TurnInput` 增加 `review_paths` |
| `frontend/src/state/chatMessages.ts` | `ChatTurn.review`；`review_finished` 归并到本轮 |
| `frontend/src/components/chat/ReviewReport.tsx`（新增） | 报告卡片：状态、问题、确定程度、证据与建议；未完成时列出未覆盖项 |
| `frontend/src/components/chat/ChatTurnView.tsx` | 结果卡新增「检查本次改动」，报告显示在本轮末尾 |
| `frontend/src/components/workspace/ConversationPanel.tsx` | 只对仍然存在的改动文件发起检查 |

## 接口与数据影响

- 请求新增 `purpose="review"` 与 `review_paths`（1–20 个受管文件）；与索引目标互斥。
- 新事件：`review_requested`、`review_finished`（含报告摘要与证据）。
- `GET /{wid}/reviews?path=`：历史报告与 `freshness`（`valid` / `stale` / `source_missing`）。
- 新错误码：`REVIEW_SCOPE_INVALID`、`REVIEW_EVIDENCE_INVALID`、`REVIEW_SOURCE_STALE_CONFLICT`、
  `REVIEW_WRITE_FAILED`。
- 报告保存在作品绑定状态目录的 `reviews.json`，最多 50 份，不参与作品 Git。

## 取舍

- **检查实际落盘的内容**：目标版本在发布时复核；证据必须来自本轮真正读过的文件，
  引文与区间一致。
- **没读完不算通过**：`no_findings` 只有在目标全文与必需参考都完整提供时才允许；
  否则用 `incomplete` 并列出未覆盖项。
- **检查不改作品**：检查工具集没有写工具，也不准备 Git 基线；检查失败不撤销已经落盘的修改，
  只把整轮标为部分完成。
- **回看不调模型**：报告过期只按当前指纹标记，不自动重跑。
- **入口由作者决定**：只有显式检查请求或写后检查登记会进入检查；打开文件、刷新、保存都不启动。

## 验证证据

- `uv run pytest tests/workspace/test_review.py -q`：14 项通过。覆盖状态与问题不一致、引文与区间
  不一致、证据来自未读文件、未读全不能报无问题、发布后 freshness 变化、缓存损坏、检查工具集
  不含写工具、`save_review_report` 发布、`request_review` 的授权引文校验、接口回看与用途校验、
  报告序列化往返。
- `tests/workspace/test_chat.py`：新增两条端到端用例。
  - `test_review_turn_publishes_report`：`purpose=review` 只调用读取与保存报告，发布后接口可回看，
    作品指纹不变。
  - `test_write_then_check_enters_readonly_phase`：一轮内先读、登记范围、登记检查、写入，再进入
    只读检查并发布 `no_findings`；整轮完成，修改保留。
- 前端：`npm test` 102 项通过，`npm run build` 通过；新增 3 项（报告归并、未完成说明、检查请求体）。

## 偏差与未完成项

- 检查阶段与修改阶段共用 `max_turns` 预算，没有精确扣减已用回合；计划里「消费本轮剩余回合
  预算」只做到「同一轮、同一模型连接、保留停止」。
- 「检查失败时整轮部分完成」已实现，但没有针对跨文件修改失败与 Git 记录状态的专门用例。
- 真实模型的检查质量（误报、漏报、把回忆当事实）未验收，只验证了规则与接线。
- 整体交付、版本升级与运行说明仍缺（C2）。
