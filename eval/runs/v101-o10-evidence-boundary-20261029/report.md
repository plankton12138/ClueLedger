# 线簿上下文评测

运行：`context-20261029-234113-340443`；模型：`deepseek-flash`。

计划 3 次，执行 3 次；结果：{'pending_review': 2, 'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v101_o10 | current | 1 | pending_review | 78.984 | 14 | 13 |
| v101_o10 | current | 2 | pending_review | 68.062 | 14 | 14 |
| v101_o10 | current | 3 | fail | 130.718 | 14 | 16 |

## v101_o10--current--1

证据目录：`evidence/v101_o10--current--1`。

- pending_review：`step-1/goal`：{'rubric': '前文真实改写且保留线索与真相；需要失效的时间推断不能宣称继续有效，范围外正文不被改动。', 'review': None}

## v101_o10--current--2

证据目录：`evidence/v101_o10--current--2`。

- pending_review：`step-1/goal`：{'rubric': '前文真实改写且保留线索与真相；需要失效的时间推断不能宣称继续有效，范围外正文不被改动。', 'review': None}

## v101_o10--current--3

证据目录：`evidence/v101_o10--current--3`。

- fail：`step-1/terminal`：{'seq': 19453, 'at': '2026-10-29T23:45:53.512+08:00', 'event_version': 1, 'event_id': 'c_e2612fcaf290:19453', 'conversation_id': 'c_e2612fcaf290', 'type': 'run_finished', 'turn_id': 't_499b372d', 'operation_id': 'op_6d2217587c3b', 'generation': 1, 'status': 'needs_input', 'recording': 'no_change', 'changed_paths': [], 'commit': None, 'note': ''}
- fail：`step-1/required_changes`：['正文/0001 雨夜.md']
- pending_review：`step-1/goal`：{'rubric': '前文真实改写且保留线索与真相；需要失效的时间推断不能宣称继续有效，范围外正文不被改动。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
