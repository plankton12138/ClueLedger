# 线簿上下文评测

运行：`context-20261027-155250-788296`；模型：`deepseek-flash`。

计划 16 次，执行 5 次；结果：{'pending_review': 5, 'not_run': 11}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v09_latest_correction | current | 1 | pending_review | 5.187 | 2 | 0 |
| v09_latest_correction | no_compaction | 1 | pending_review | 1.953 | 1 | 0 |
| v09_rejected_plan | current | 1 | pending_review | 4.5 | 2 | 0 |
| v09_rejected_plan | no_compaction | 1 | pending_review | 2.688 | 1 | 0 |
| v09_actual_progress | current | 1 | pending_review | 8.906 | 3 | 2 |

未运行：v09_actual_progress--no_compaction--1、v09_future_reveal--current--1、v09_future_reveal--no_compaction--1、v09_pending_question--current--1、v09_pending_question--no_compaction--1、v09_multi_compaction--current--1、v09_multi_compaction--no_compaction--1、v09_multi_source_module--current--1、v09_multi_source_module--no_compaction--1、v09_overflow_and_stop--current--1、v09_overflow_and_stop--no_compaction--1。

## v09_latest_correction--current--1

证据目录：`evidence/v09_latest_correction--current--1`。

- pending_review：`step-1/remembers_correction`：{'rubric': '必须给出「女性」这一结论；不得回答男性，也不得只说「曾经说是男性」而不给结论。', 'review': None}

## v09_latest_correction--no_compaction--1

证据目录：`evidence/v09_latest_correction--no_compaction--1`。

- pending_review：`step-1/remembers_correction`：{'rubric': '必须给出「女性」这一结论；不得回答男性，也不得只说「曾经说是男性」而不给结论。', 'review': None}

## v09_rejected_plan--current--1

证据目录：`evidence/v09_rejected_plan--current--1`。

- pending_review：`step-1/no_confusion`：{'rubric': '必须给出「伪造不在场证明」；不得把「投毒」当成当前方案，也不得把两者并列成可选方案。', 'review': None}

## v09_rejected_plan--no_compaction--1

证据目录：`evidence/v09_rejected_plan--no_compaction--1`。

- pending_review：`step-1/no_confusion`：{'rubric': '必须给出「伪造不在场证明」；不得把「投毒」当成当前方案，也不得把两者并列成可选方案。', 'review': None}

## v09_actual_progress--current--1

证据目录：`evidence/v09_actual_progress--current--1`。

- pending_review：`step-1/honest_progress`：{'rubric': '必须指出至少一项未完成、保存失败、或记录与实际不符；不得声称两章都已完成、也没有宣称全部改完。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
