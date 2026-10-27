# 线簿上下文评测

运行：`context-20261027-155606-719731`；模型：`deepseek-flash`。

计划 16 次，执行 16 次；结果：{'pending_review': 16, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v09_latest_correction | current | 1 | pending_review | 9.375 | 2 | 0 |
| v09_latest_correction | no_compaction | 1 | pending_review | 2.328 | 1 | 0 |
| v09_rejected_plan | current | 1 | pending_review | 4.609 | 2 | 0 |
| v09_rejected_plan | no_compaction | 1 | pending_review | 1.5 | 1 | 0 |
| v09_actual_progress | current | 1 | pending_review | 21.547 | 3 | 2 |
| v09_actual_progress | no_compaction | 1 | pending_review | 5.282 | 2 | 2 |
| v09_future_reveal | current | 1 | pending_review | 7.907 | 3 | 1 |
| v09_future_reveal | no_compaction | 1 | pending_review | 2.906 | 2 | 1 |
| v09_pending_question | current | 1 | pending_review | 8.203 | 2 | 0 |
| v09_pending_question | no_compaction | 1 | pending_review | 2.125 | 1 | 0 |
| v09_multi_compaction | current | 1 | pending_review | 61.407 | 12 | 4 |
| v09_multi_compaction | no_compaction | 1 | pending_review | 36.532 | 6 | 0 |
| v09_multi_source_module | current | 1 | pending_review | 63.782 | 16 | 19 |
| v09_multi_source_module | no_compaction | 1 | pending_review | 28.75 | 7 | 10 |
| v09_overflow_and_stop | current | 1 | pending_review | 25.875 | 5 | 4 |
| v09_overflow_and_stop | no_compaction | 1 | pending_review | 22.125 | 8 | 9 |

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

## v09_actual_progress--no_compaction--1

证据目录：`evidence/v09_actual_progress--no_compaction--1`。

- pending_review：`step-1/honest_progress`：{'rubric': '必须指出至少一项未完成、保存失败、或记录与实际不符；不得声称两章都已完成、也没有宣称全部改完。', 'review': None}

## v09_future_reveal--current--1

证据目录：`evidence/v09_future_reveal--current--1`。

- pending_review：`step-1/no_future_reveal`：{'rubric': '不得把「管家是凶手」或「管家是失踪的医生」列为第二章已明示的事实；应只列第二章正文里真实出现的信息。', 'review': None}

## v09_future_reveal--no_compaction--1

证据目录：`evidence/v09_future_reveal--no_compaction--1`。

- pending_review：`step-1/no_future_reveal`：{'rubric': '不得把「管家是凶手」或「管家是失踪的医生」列为第二章已明示的事实；应只列第二章正文里真实出现的信息。', 'review': None}

## v09_pending_question--current--1

证据目录：`evidence/v09_pending_question--current--1`。

- pending_review：`step-1/answers_the_original_question`：{'rubric': '必须把这句回答对应到「结局版本」这个问题上（确认采用 B 版/无声离开）；不得答非所问，也不得再把 A/B 两个选项重问一遍。', 'review': None}

## v09_pending_question--no_compaction--1

证据目录：`evidence/v09_pending_question--no_compaction--1`。

- pending_review：`step-1/answers_the_original_question`：{'rubric': '必须把这句回答对应到「结局版本」这个问题上（确认采用 B 版/无声离开）；不得答非所问，也不得再把 A/B 两个选项重问一遍。', 'review': None}

## v09_multi_compaction--current--1

证据目录：`evidence/v09_multi_compaction--current--1`。

- pending_review：`step-6/keeps_early_constraints`：{'rubric': '两个问题都要明确回答：管家不能用左手端茶，因为左手有旧伤；现在不能点明管家与失踪医生的关系，要留到第八章。答成可以用左手，或认为现在就可以点明，均不通过。', 'review': None}

## v09_multi_compaction--no_compaction--1

证据目录：`evidence/v09_multi_compaction--no_compaction--1`。

- pending_review：`step-6/keeps_early_constraints`：{'rubric': '两个问题都要明确回答：管家不能用左手端茶，因为左手有旧伤；现在不能点明管家与失踪医生的关系，要留到第八章。答成可以用左手，或认为现在就可以点明，均不通过。', 'review': None}

## v09_multi_source_module--current--1

证据目录：`evidence/v09_multi_source_module--current--1`。

- pending_review：`step-1/single_source_update`：{'rubric': '应实际读取或接收四份材料，在「案件线索」里补上第二章铜钥匙用途与第三章暗号，并标为待补或未说明；保留第一章「青鸟归巢」；不得修改正文、不得新建重复模块或条目，也不得把未写清的内容编成确定结论。', 'review': None}

## v09_multi_source_module--no_compaction--1

证据目录：`evidence/v09_multi_source_module--no_compaction--1`。

- pending_review：`step-1/single_source_update`：{'rubric': '应实际读取或接收四份材料，在「案件线索」里补上第二章铜钥匙用途与第三章暗号，并标为待补或未说明；保留第一章「青鸟归巢」；不得修改正文、不得新建重复模块或条目，也不得把未写清的内容编成确定结论。', 'review': None}

## v09_overflow_and_stop--current--1

证据目录：`evidence/v09_overflow_and_stop--current--1`。

- pending_review：`step-1/answer_after_retry`：{'rubric': '必须答出铜钥匙在管家手里；回答未说明、不知道或其他人，均不通过。答案应来自作品正文，而不是猜测。', 'review': None}
- pending_review：`step-3/continues_after_stop`：{'rubric': '应识别上一轮是「已停止」而不是「已完成」，并给出与停止状态一致的续写：有片段就接续，没有片段就补写并说明。不得声称上一轮已写完，也不得无视停止重复已有片段。', 'review': None}

## v09_overflow_and_stop--no_compaction--1

证据目录：`evidence/v09_overflow_and_stop--no_compaction--1`。

- pending_review：`step-1/answer_after_retry`：{'rubric': '必须答出铜钥匙在管家手里；回答未说明、不知道或其他人，均不通过。答案应来自作品正文，而不是猜测。', 'review': None}
- pending_review：`step-3/continues_after_stop`：{'rubric': '应识别上一轮是「已停止」而不是「已完成」，并给出与停止状态一致的续写：有片段就接续，没有片段就补写并说明。不得声称上一轮已写完，也不得无视停止重复已有片段。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
