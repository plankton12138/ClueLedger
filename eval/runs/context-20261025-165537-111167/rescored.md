# 线簿上下文评测

运行：`context-20261025-165537-111167`；模型：`deepseek-flash`。

计划 5 次，执行 5 次；结果：{'pass': 5, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| i06_expand_three_directions | current | 1 | pass | 42.687 | 5 | 17 |
| i06_expand_asks_for_write | current | 1 | pass | 10.14 | 3 | 4 |
| i06_expand_followup | current | 1 | pass | 60.031 | 10 | 27 |
| i06_forbid_idea_tools | current | 1 | pass | 22.953 | 3 | 12 |
| v08_idea_apply | current | 1 | pass | 27.328 | 7 | 13 |

## i06_expand_three_directions--current--1

证据目录：`evidence/i06_expand_three_directions--current--1`。


## i06_expand_asks_for_write--current--1

证据目录：`evidence/i06_expand_asks_for_write--current--1`。


## i06_expand_followup--current--1

证据目录：`evidence/i06_expand_followup--current--1`。


## i06_forbid_idea_tools--current--1

证据目录：`evidence/i06_forbid_idea_tools--current--1`。


## v08_idea_apply--current--1

证据目录：`evidence/v08_idea_apply--current--1`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
