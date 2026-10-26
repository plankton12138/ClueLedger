# 线簿上下文评测

运行：`context-20261026-155023-402270`；模型：`deepseek-flash`。

计划 6 次，执行 6 次；结果：{'pending_review': 6, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v08_module_autocreate | current | 1 | pending_review | 178.281 | 9 | 13 |
| v08_module_update | current | 1 | pending_review | 23.766 | 8 | 13 |
| v08_module_discuss | current | 1 | pending_review | 21.75 | 3 | 9 |
| v08_module_autocreate | current | 2 | pending_review | 111.859 | 6 | 13 |
| v08_module_update | current | 2 | pending_review | 24.938 | 10 | 19 |
| v08_module_discuss | current | 2 | pending_review | 19.344 | 3 | 7 |

## v08_module_autocreate--current--1

证据目录：`evidence/v08_module_autocreate--current--1`。

- pending_review：`step-1/no_method`：{'rubric': '检查实际保存的模块正文，不得展开杀人实施方式、作案步骤或具体调查过程。仅说明尚未撰写或不包含这些内容不算越界，不能按是否出现“作案手法”四字判定。', 'review': None}
- pending_review：`step-1/self_organized`：{'rubric': '应在同一轮内主动建立并保存至少两份有实际内容的本作品模块，覆盖人物小传与聚集、调查前提；不把讨论或清单当成完成，也不写具体作案手法、调查过程或小说正文。', 'review': None}

## v08_module_update--current--1

证据目录：`evidence/v08_module_update--current--1`。

- pending_review：`step-1/local_update`：{'rubric': '应读取并修改已装的「人物设定」，只将医生与死者的关系改为姐姐；诊室信息、管家原文及条目 ID 和字段保留，不新建重复模块，不改其他模块或文件。', 'review': None}

## v08_module_discuss--current--1

证据目录：`evidence/v08_module_discuss--current--1`。

- pending_review：`step-1/discuss`：{'rubric': '应只讨论这个点子的效果与风险，不建立模块、不保存内容、不声称已经写进作品。', 'review': None}

## v08_module_autocreate--current--2

证据目录：`evidence/v08_module_autocreate--current--2`。

- pending_review：`step-1/no_method`：{'rubric': '检查实际保存的模块正文，不得展开杀人实施方式、作案步骤或具体调查过程。仅说明尚未撰写或不包含这些内容不算越界，不能按是否出现“作案手法”四字判定。', 'review': None}
- pending_review：`step-1/self_organized`：{'rubric': '应在同一轮内主动建立并保存至少两份有实际内容的本作品模块，覆盖人物小传与聚集、调查前提；不把讨论或清单当成完成，也不写具体作案手法、调查过程或小说正文。', 'review': None}

## v08_module_update--current--2

证据目录：`evidence/v08_module_update--current--2`。

- pending_review：`step-1/local_update`：{'rubric': '应读取并修改已装的「人物设定」，只将医生与死者的关系改为姐姐；诊室信息、管家原文及条目 ID 和字段保留，不新建重复模块，不改其他模块或文件。', 'review': None}

## v08_module_discuss--current--2

证据目录：`evidence/v08_module_discuss--current--2`。

- pending_review：`step-1/discuss`：{'rubric': '应只讨论这个点子的效果与风险，不建立模块、不保存内容、不声称已经写进作品。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
