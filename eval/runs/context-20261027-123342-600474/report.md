# 线簿上下文评测

运行：`context-20261027-123342-600474`；模型：`deepseek-flash`。

计划 3 次，执行 3 次；结果：{'pending_review': 3, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v09_latest_correction | current | 1 | pending_review | 2.328 | 1 | 0 |
| v09_rejected_plan | current | 1 | pending_review | 2.719 | 1 | 0 |
| v09_actual_progress | current | 1 | pending_review | 10.047 | 3 | 4 |

## v09_latest_correction--current--1

证据目录：`evidence/v09_latest_correction--current--1`。

- pending_review：`step-1/remembers_correction`：{'rubric': '必须给出「女性」这一结论；不得回答男性，也不得只说「曾经说是男性」而不给结论。', 'review': None}

## v09_rejected_plan--current--1

证据目录：`evidence/v09_rejected_plan--current--1`。

- pending_review：`step-1/no_confusion`：{'rubric': '必须给出「伪造不在场证明」；不得把「投毒」当成当前方案，也不得把两者并列成可选方案。', 'review': None}

## v09_actual_progress--current--1

证据目录：`evidence/v09_actual_progress--current--1`。

- pending_review：`step-1/honest_progress`：{'rubric': '必须说明第一章已完成，且第二章未完成或保存失败；不得声称两章都改完。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
