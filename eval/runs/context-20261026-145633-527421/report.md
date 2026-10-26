# 线簿上下文评测

运行：`context-20261026-145633-527421`；模型：`deepseek-flash`。

计划 6 次，执行 1 次；结果：{'fail': 1, 'not_run': 5}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v08_module_autocreate | current | 1 | fail | 89.656 | 6 | 11 |

未运行：v08_module_update--current--1、v08_module_discuss--current--1、v08_module_autocreate--current--2、v08_module_update--current--2、v08_module_discuss--current--2。

## v08_module_autocreate--current--1

证据目录：`evidence/v08_module_autocreate--current--1`。

- fail：`step-1/terminal`：{'seq': 8524, 'at': '2026-10-26T14:58:03.363+08:00', 'event_version': 1, 'event_id': 'c_53711da768ac:8524', 'conversation_id': 'c_53711da768ac', 'type': 'run_finished', 'turn_id': 't_638614fb', 'status': 'failed', 'recording': 'no_change', 'changed_paths': [], 'commit': None, 'note': ''}
- fail：`step-1/has_suspects`：{'value': '嫌疑人', 'modules': 0}
- fail：`step-1/has_victim`：{'value': '死者', 'modules': 0}
- fail：`step-1/has_culprit`：{'value': '真凶', 'modules': 0}
- pending_review：`step-1/self_organized`：{'rubric': '应在同一轮内主动建立并保存至少两份有实际内容的本作品模块，覆盖人物小传与聚集、调查前提；不把讨论或清单当成完成，也不写具体作案手法、调查过程或小说正文。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
