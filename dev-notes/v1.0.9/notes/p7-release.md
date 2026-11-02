# v1.0.9 发布验收（P7）

日期：2026-11-02。状态：**进行中**。本文件只记录实际执行过的检查；未执行的项目保持“未执行”，不以计划代替结论。

## 1. 范围

P7 覆盖四类证据：确定性矩阵 A01—A40b、Aqua 与响应式 V01—V10、在线模型 O01—O09、性能观察；最后统一版本为 1.0.9。旧格式与旧数据不在验收范围。

## 2. 确定性矩阵

矩阵中的能力多数已由 P1—P6 的测试覆盖。P7 只补投影层缺口，并按下表核对现有用例，不重复实现同一断言。

| 编号 | 证据 |
|---|---|
| A01 | `test_story_store` 的格式 4 round-trip；`test_chapter_writing` 建章后重新读取 |
| A02 | `test_chapter_writing::test_save_requires_book_intent`（空意图只报一个缺口） |
| A03 | `test_chapter_writing::test_write_policy_requires_book_intent`、`test_save_requires_book_intent` |
| A04 | `test_agent_chapter_gate`（Agent 写入与人工同一策略） |
| A05 | `test_creative_assist::test_assist_stale_base_and_unavailable_model` |
| A06 | `test_writing_api::test_writing_flow_through_http` |
| A07 | `test_story_joint_commit::test_decided_joint_recovers_each_write_failure`、`test_recovery_after_second_prose_file_fails` |
| A08 | `test_chapter_writing::test_chapter_creation_creates_prose_and_is_idempotent`、`test_save_checks_digest_and_retry` |
| A09 | 前端 `writing.test.ts` 冲突草稿；后端摘要冲突 |
| A10 | `test_writing_api::test_unplanned_prose_is_readonly_in_workbench` |
| A10b | `test_chapter_writing::test_confirm_candidate_creates_ledger_and_undo`、`test_confirm_rejects_stale_digest_and_duplicate` |
| A11 | `test_chapter_writing::test_save_relocates_binding_and_reports_invalidation` |
| A12 | `test_chapter_writing::test_binding_recovers_after_quote_is_restored`（P7 新增恢复段） |
| A13 | `test_chapter_writing::test_one_move_keeps_each_occurrence_span`（P7 新增） |
| A14 | `test_story_bindings::test_overlapping_repeat_counts_as_ambiguous`；确认入账对重复原句直接拒绝 |
| A15 | `test_story_bindings::test_emoji_and_crlf_use_code_points` |
| A16 | `test_creative_assist::test_index_job_merges_candidates`、`test_stop_preserves_candidate_state` |
| A17 | `test_creative_assist::test_index_failure_keeps_prose_saved` |
| A18 | `test_creative_assist::test_index_job_merges_candidates`；确认前不进入 `information_moves` |
| A19 | `test_story_joint_commit` 的依赖冲突与对齐操作；正文不被静默修改 |
| A20 | `test_story_joint_commit::test_joint_commit_retry_and_undo` |
| A21 | `test_story_joint_commit::test_create_chapter_and_undo_removes_only_created_file`、`test_undo_refuses_later_edit_to_same_file` |
| A22 | 在线 O09 与人工走查（角色谎言不进入普通冲突） |
| A23 | `notes/creative-input-inventory.md` 清单 + 页面模型入口检查 |
| A24 | 同一清单的控制字段排除项 |
| A25 | `test_creative_assist::test_assist_stale_base_and_unavailable_model`；前端禁用文案 |
| A26 | `test_creative_assist::test_global_assist_works_without_workspace` |
| A27 | 在线 O04；前端候选旁置 |
| A28 | `test_creative_assist::test_prose_candidate_adoption_and_stale_rejection` |
| A29 | `test_creative_assist::test_stop_preserves_candidate_state`、`test_recovery_marks_interrupted_generation_and_index` |
| A30 | 本版无“下一步建议”按钮；见第 7 节偏差 |
| A31 | 前端 `replaceAll` / `insertAtCursor` 单测；保存仍走普通写入 |
| A32 | `test_story_bindings::test_range_that_does_not_match_quote_is_rejected`、`test_model_without_range_uses_unique_quote` |
| A33 | `writing-workbench` 断言：无构思 / 线索入口 |
| A34 | 前端路由回落；`writing-page` 单测 |
| A35 | `writing-workbench` 1440 / 1024 / 760 三档 |
| A36 | `writing.test.ts` 合成输入；浏览器 fill / 撤销 |
| A37 | 前端保存失败与重试路径 |
| A38 | `writing.test.ts` 切章、冲突、旧响应不覆盖 |
| A39 | `writing-workbench` 索引与对话切换 |
| A40 | `BookPlanView` 的全书信息线总览 |
| A40b | `writing-workbench` 空章反馈：出现、可忽略、继续输入 |

## 3. 浏览器与视觉

`e2e/writing-workbench.spec.ts` 与 `e2e/aqua-layout.spec.ts` 覆盖 V01—V10 的可自动判定部分：chrome 与稿纸分层、控件渐变与圆角、选中态、焦点环、减少动态效果、四档宽度与 150% 缩放、无横向滚动、索引与对话互斥、空正文唯一主操作。

对比度（V09）只核对默认与选中态的具体颜色值；悬停、按下、禁用、错误、焦点的逐项对比度仍需人工确认，不在本文件记为通过。

## 4. 在线模型

使用 `.env` 中配置的在线模型（`OPENAI_BASE_URL` / `HASTINGS_MODEL`），逐条顺序执行，每条自建隔离作品。用例见 `frontend/e2e/writing-online.spec.ts`。

| 编号 | 结果 | 实际观察 |
|---|---|---|
| O01 | 通过（4.6 s） | 空作品生成多个起点候选；意图输入保持为空；采用后才进入输入框 |
| O02 | 通过（9.8 s） | 方向建议给出文字，只有“复制建议”，正文仍为空 |
| O03 | 通过（14.3 s） | 起草候选旁置，正文仍为空 |
| O04 | 通过 | 起草期间手写，候选标记“生成后内容又变了”，作者文字保留 |
| O05 | 通过 | 已有正文续写只产生候选，正文未被覆盖 |
| O06 | 通过（7.8 s） | 保存蓝蜡段落后出现“正文新增”候选，候选带准确原句，未自动入账 |
| O07 | 通过（10.6 s） | 入账蓝蜡候选后把正文改成红蜡，索引进入“需要对齐” |
| O08 | 无入口 | 对话页没有“给我下一步建议”按钮；见第 7 节 |
| O09 | 未执行 | 本轮验收被中断，未取得读者视角回复的原文 |

O04 / O05 / O07 的结论来自单条复验：O04 首次失败是把 `stale` 误判为失败，O07 前两次失败分别是页边索引被对话栏挤掉、以及入账了与矛盾无关的候选；修正测试选择后通过，产品行为未因此放宽。

O06 首次失败暴露了真实缺陷（索引完成后页面不刷新），修复后单条通过。O09 未执行，不记通过。

## 5. 性能观察

样本：200 章、300 条信息线、300 个绑定，正文每章约 600 字。测量脚本 `artifacts/perf/profile_workbench.py`，重复 5 次取中位数。

| 场景 | 中位耗时 | 最小 / 最大 | 响应体 |
|---|---|---|---|
| 全书工作台 | 66.0 ms | 63.3 / 74.9 ms | 52.6 KB |
| 单章工作台 | 82.0 ms | 64.1 / 87.3 ms | 55.1 KB |

结论只限本机同环境：首屏为一次聚合读取，没有按信息线发独立请求；单章投影略慢于全书，因为要读取正文并计算每条绑定的锚点状态。浏览器稳定时间与内存未测量，不写“性能达标”。

## 6. 本次发现与修复

1. **空章提示被分析状态阻塞**：原实现要求语义分析结算后才显示提示，在线模型较慢时作者看不到反馈。改为只看已入账信息，不等分析。
2. **索引完成后页面不更新**：保存后只刷新一次工作台，分析在几秒后完成时页面仍停在“正在整理”。改为分析未结算时每 1.5 秒重读，结算或切章即停，最多 40 次。
3. **对齐判断缺少对照原句**：索引提示词只给已入账语句、不给原句，模型把改写后的细节当成新增。补充每条已入账信息的原句，并写明“同一细节说法不一致时用 alignment”。“需要对齐”只在入账了对应信息后才出现，这符合预期。

## 7. 偏差与未完成

- **O08「给我下一步建议」无入口**：对话页没有该按钮，产品方案里的方向建议在章节页（O02 覆盖）。要么删掉 O08，要么另立需求，不能记为通过。
- **O09 未执行**：读者视角用例已写好，但本轮验收在运行前被中断；恢复后只跑这一条。
- 页边索引的段落级像素对齐、专用编辑内核比较仍未执行。
- 版本号仍为 1.0.8，待本文件第 8 节全部完成后统一。

## 8. 清理与版本

（待补）
