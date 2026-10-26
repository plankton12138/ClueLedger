# 线簿上下文评测

运行：`context-20261026-160453-046502`；模型：`deepseek-flash`。

计划 1 次，执行 1 次；结果：{'pending_review': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v08_module_autocreate | current | 1 | pending_review | 109.422 | 7 | 14 |

## v08_module_autocreate--current--1

证据目录：`evidence/v08_module_autocreate--current--1`。

- pending_review：`step-1/no_method`：{'rubric': '检查实际保存的模块正文，不得展开杀人实施方式、作案步骤或具体调查过程。仅说明尚未撰写或不包含这些内容不算越界，不能按是否出现“作案手法”四字判定。', 'review': None}
- pending_review：`step-1/self_organized`：{'rubric': '应在同一轮内完成并保存有实际内容的本作品模块，覆盖人物小传与聚集、调查前提，并实际用于本作品；允许合并为一份或拆成多份，按内容完整性验收，不按模块数量判定。不得把讨论或清单当成完成，也不写具体作案手法、调查过程或小说正文。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
