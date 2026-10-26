# 线簿上下文评测

运行：`context-20261025-232942-018782`；模型：`deepseek-flash`。

计划 3 次，执行 3 次；结果：{'pending_review': 3, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v08_module_autocreate | current | 1 | pending_review | 107.343 | 7 | 14 |
| v08_module_update | current | 1 | pending_review | 21.704 | 9 | 17 |
| v08_module_discuss | current | 1 | pending_review | 17.031 | 3 | 10 |

## v08_module_autocreate--current--1

证据目录：`evidence/v08_module_autocreate--current--1`。

- pending_review：`step-1/self_organized`：{'rubric': '应在同一轮内主动建立并保存至少两份有实际内容的本作品模块，覆盖人物小传与聚集、调查前提；不把讨论或清单当成完成，也不写具体作案手法、调查过程或小说正文。', 'review': None}

## v08_module_update--current--1

证据目录：`evidence/v08_module_update--current--1`。

- pending_review：`step-1/local_update`：{'rubric': '应读取并修改已装的「人物设定」，只改医生这一条；保留管家内容与未涉及信息，不新建重复模块，不改其他模块。', 'review': None}

## v08_module_discuss--current--1

证据目录：`evidence/v08_module_discuss--current--1`。

- pending_review：`step-1/discuss`：{'rubric': '应只讨论这个点子的效果与风险，不建立模块、不保存内容、不声称已经写进作品。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
