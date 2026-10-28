# 线簿上下文评测

运行：`context-20261028-131927-641994`；模型：`deepseek-flash`。

计划 6 次，执行 6 次；结果：{'pass': 6, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q03_plan_not_text | current | 1 | pass | 21.062 | 5 | 7 |
| v10_q13_early_revision | current | 1 | pass | 18.891 | 5 | 7 |
| v10_q03_plan_not_text | current | 2 | pass | 37.734 | 7 | 10 |
| v10_q13_early_revision | current | 2 | pass | 16.812 | 6 | 6 |
| v10_q03_plan_not_text | current | 3 | pass | 20.437 | 4 | 5 |
| v10_q13_early_revision | current | 3 | pass | 13.438 | 5 | 7 |

## v10_q03_plan_not_text--current--1

证据目录：`evidence/v10_q03_plan_not_text--current--1`。


## v10_q13_early_revision--current--1

证据目录：`evidence/v10_q13_early_revision--current--1`。


## v10_q03_plan_not_text--current--2

证据目录：`evidence/v10_q03_plan_not_text--current--2`。


## v10_q13_early_revision--current--2

证据目录：`evidence/v10_q13_early_revision--current--2`。


## v10_q03_plan_not_text--current--3

证据目录：`evidence/v10_q03_plan_not_text--current--3`。


## v10_q13_early_revision--current--3

证据目录：`evidence/v10_q13_early_revision--current--3`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
