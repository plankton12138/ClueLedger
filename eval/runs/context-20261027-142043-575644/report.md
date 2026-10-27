# 线簿上下文评测

运行：`context-20261027-142043-575644`；模型：`deepseek-flash`。

计划 1 次，执行 1 次；结果：{'pending_review': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v09_multi_compaction | current | 1 | pending_review | 49.0 | 8 | 3 |

## v09_multi_compaction--current--1

证据目录：`evidence/v09_multi_compaction--current--1`。

- pending_review：`step-6/keeps_early_constraints`：{'rubric': '两个问题都要明确回答：管家不能用左手端茶，因为左手有旧伤；现在不能点明管家与失踪医生的关系，要留到第八章。答成可以用左手，或认为现在就可以点明，均不通过。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
