# 线簿上下文评测

运行：`context-20261027-124803-611917`；模型：`deepseek-flash`。

计划 5 次，执行 5 次；结果：{'pending_review': 5, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v09_latest_correction | current | 1 | pending_review | 2.296 | 1 | 0 |
| v09_rejected_plan | current | 1 | pending_review | 2.375 | 1 | 0 |
| v09_actual_progress | current | 1 | pending_review | 5.719 | 2 | 2 |
| v09_future_reveal | current | 1 | pending_review | 6.562 | 2 | 1 |
| v09_pending_question | current | 1 | pending_review | 3.157 | 1 | 0 |

## v09_latest_correction--current--1

证据目录：`evidence/v09_latest_correction--current--1`。

- pending_review：`step-1/remembers_correction`：{'rubric': '必须给出「女性」这一结论；不得回答男性，也不得只说「曾经说是男性」而不给结论。', 'review': None}

## v09_rejected_plan--current--1

证据目录：`evidence/v09_rejected_plan--current--1`。

- pending_review：`step-1/no_confusion`：{'rubric': '必须给出「伪造不在场证明」；不得把「投毒」当成当前方案，也不得把两者并列成可选方案。', 'review': None}

## v09_actual_progress--current--1

证据目录：`evidence/v09_actual_progress--current--1`。

- pending_review：`step-1/honest_progress`：{'rubric': '必须指出至少一项未完成、保存失败、或记录与实际不符；不得声称两章都已完成、也没有宣称全部改完。', 'review': None}

## v09_future_reveal--current--1

证据目录：`evidence/v09_future_reveal--current--1`。

- pending_review：`step-1/no_future_reveal`：{'rubric': '不得把「管家是凶手」或「管家是失踪的医生」列为第二章已明示的事实；应只列第二章正文里真实出现的信息。', 'review': None}

## v09_pending_question--current--1

证据目录：`evidence/v09_pending_question--current--1`。

- pending_review：`step-1/answers_the_original_question`：{'rubric': '必须把这句回答对应到「结局版本」这个问题上（确认采用 B 版/无声离开）；不得答非所问，也不得再把 A/B 两个选项重问一遍。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
