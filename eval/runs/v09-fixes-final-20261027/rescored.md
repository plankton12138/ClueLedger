# 线簿上下文评测

运行：`context-20261027-155606-719731`；模型：`deepseek-flash`。

计划 16 次，执行 16 次；结果：{'pass': 16, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v09_latest_correction | current | 1 | pass | 9.375 | 2 | 0 |
| v09_latest_correction | no_compaction | 1 | pass | 2.328 | 1 | 0 |
| v09_rejected_plan | current | 1 | pass | 4.609 | 2 | 0 |
| v09_rejected_plan | no_compaction | 1 | pass | 1.5 | 1 | 0 |
| v09_actual_progress | current | 1 | pass | 21.547 | 3 | 2 |
| v09_actual_progress | no_compaction | 1 | pass | 5.282 | 2 | 2 |
| v09_future_reveal | current | 1 | pass | 7.907 | 3 | 1 |
| v09_future_reveal | no_compaction | 1 | pass | 2.906 | 2 | 1 |
| v09_pending_question | current | 1 | pass | 8.203 | 2 | 0 |
| v09_pending_question | no_compaction | 1 | pass | 2.125 | 1 | 0 |
| v09_multi_compaction | current | 1 | pass | 61.407 | 12 | 4 |
| v09_multi_compaction | no_compaction | 1 | pass | 36.532 | 6 | 0 |
| v09_multi_source_module | current | 1 | pass | 63.782 | 16 | 19 |
| v09_multi_source_module | no_compaction | 1 | pass | 28.75 | 7 | 10 |
| v09_overflow_and_stop | current | 1 | pass | 25.875 | 5 | 4 |
| v09_overflow_and_stop | no_compaction | 1 | pass | 22.125 | 8 | 9 |

## v09_latest_correction--current--1

证据目录：`evidence/v09_latest_correction--current--1`。


## v09_latest_correction--no_compaction--1

证据目录：`evidence/v09_latest_correction--no_compaction--1`。


## v09_rejected_plan--current--1

证据目录：`evidence/v09_rejected_plan--current--1`。


## v09_rejected_plan--no_compaction--1

证据目录：`evidence/v09_rejected_plan--no_compaction--1`。


## v09_actual_progress--current--1

证据目录：`evidence/v09_actual_progress--current--1`。


## v09_actual_progress--no_compaction--1

证据目录：`evidence/v09_actual_progress--no_compaction--1`。


## v09_future_reveal--current--1

证据目录：`evidence/v09_future_reveal--current--1`。


## v09_future_reveal--no_compaction--1

证据目录：`evidence/v09_future_reveal--no_compaction--1`。


## v09_pending_question--current--1

证据目录：`evidence/v09_pending_question--current--1`。


## v09_pending_question--no_compaction--1

证据目录：`evidence/v09_pending_question--no_compaction--1`。


## v09_multi_compaction--current--1

证据目录：`evidence/v09_multi_compaction--current--1`。


## v09_multi_compaction--no_compaction--1

证据目录：`evidence/v09_multi_compaction--no_compaction--1`。


## v09_multi_source_module--current--1

证据目录：`evidence/v09_multi_source_module--current--1`。


## v09_multi_source_module--no_compaction--1

证据目录：`evidence/v09_multi_source_module--no_compaction--1`。


## v09_overflow_and_stop--current--1

证据目录：`evidence/v09_overflow_and_stop--current--1`。


## v09_overflow_and_stop--no_compaction--1

证据目录：`evidence/v09_overflow_and_stop--no_compaction--1`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
