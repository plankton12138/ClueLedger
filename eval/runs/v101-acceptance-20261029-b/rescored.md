# 线簿上下文评测

运行：`context-20261029-224914-661915`；模型：`deepseek-flash`。

计划 24 次，执行 24 次；结果：{'pass': 12, 'pending_review': 10, 'fail': 2, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v101_o01 | current | 2 | pass | 120.281 | 9 | 9 |
| v101_o02 | current | 2 | pending_review | 47.015 | 3 | 4 |
| v101_o03 | current | 2 | pending_review | 58.422 | 3 | 6 |
| v101_o04 | current | 2 | pass | 36.437 | 3 | 5 |
| v101_o05 | current | 2 | pending_review | 57.016 | 3 | 8 |
| v101_o06 | current | 2 | pass | 40.781 | 11 | 9 |
| v101_o07 | current | 2 | pass | 72.125 | 12 | 16 |
| v101_o08 | current | 2 | pass | 37.782 | 7 | 5 |
| v101_o09 | current | 2 | pass | 59.578 | 4 | 4 |
| v101_o10 | current | 2 | pending_review | 37.704 | 7 | 7 |
| v101_o11 | current | 2 | pending_review | 3.406 | 1 | 0 |
| v101_o12 | current | 2 | fail | 27.422 | 4 | 5 |
| v101_o01 | current | 3 | pass | 123.812 | 6 | 6 |
| v101_o02 | current | 3 | pending_review | 50.25 | 4 | 5 |
| v101_o03 | current | 3 | pending_review | 49.906 | 4 | 6 |
| v101_o04 | current | 3 | pass | 28.907 | 3 | 4 |
| v101_o05 | current | 3 | pending_review | 55.953 | 4 | 7 |
| v101_o06 | current | 3 | pass | 32.078 | 11 | 9 |
| v101_o07 | current | 3 | pass | 65.422 | 10 | 16 |
| v101_o08 | current | 3 | pass | 41.781 | 7 | 6 |
| v101_o09 | current | 3 | pass | 65.906 | 5 | 6 |
| v101_o10 | current | 3 | pending_review | 37.844 | 8 | 10 |
| v101_o11 | current | 3 | pending_review | 3.578 | 1 | 0 |
| v101_o12 | current | 3 | fail | 28.234 | 4 | 6 |

## v101_o01--current--2

证据目录：`evidence/v101_o01--current--2`。


## v101_o02--current--2

证据目录：`evidence/v101_o02--current--2`。

- pending_review：`step-1/goal`：{'rubric': '误导来自解释变化，保留真实湿手套与既定真相，回收有观察依据，不改正文。', 'review': None}

## v101_o03--current--2

证据目录：`evidence/v101_o03--current--2`。

- pending_review：`step-1/goal`：{'rubric': '证词不是作者真相，林砚早期不能知道搬尸事实；后期发现须有实际依据。', 'review': None}

## v101_o04--current--2

证据目录：`evidence/v101_o04--current--2`。


## v101_o05--current--2

证据目录：`evidence/v101_o05--current--2`。

- pending_review：`step-1/goal`：{'rubric': '两条调查均提供不可省略的证据和发现过程，汇合结论依据充分，不能直接读到作者真相。', 'review': None}

## v101_o06--current--2

证据目录：`evidence/v101_o06--current--2`。


## v101_o07--current--2

证据目录：`evidence/v101_o07--current--2`。


## v101_o08--current--2

证据目录：`evidence/v101_o08--current--2`。


## v101_o09--current--2

证据目录：`evidence/v101_o09--current--2`。


## v101_o10--current--2

证据目录：`evidence/v101_o10--current--2`。

- pending_review：`step-1/goal`：{'rubric': '前文真实改写且保留线索与真相；需要失效的时间推断不能宣称继续有效，范围外正文不被改动。', 'review': None}

## v101_o11--current--2

证据目录：`evidence/v101_o11--current--2`。

- pending_review：`step-1/goal`：{'rubric': '独立读者请求仅有截止前缀，无作者历史、工具、隐藏真相或未来节点；回答引用前缀原文，猜测必须有依据。', 'review': None}

## v101_o12--current--2

证据目录：`evidence/v101_o12--current--2`。

- fail：`step-1/terminal`：{'seq': 3074, 'at': '2026-10-29T22:59:21.533+08:00', 'event_version': 1, 'event_id': 'c_5ee82245aced:3074', 'conversation_id': 'c_5ee82245aced', 'type': 'run_finished', 'turn_id': 't_79ea5bfc', 'operation_id': 'op_b5ed36b83ea9', 'generation': 1, 'status': 'needs_input', 'recording': 'no_change', 'changed_paths': [], 'commit': None, 'note': ''}
- pending_review：`step-1/goal`：{'rubric': '指出两项具体冲突，提出清楚可回答的取舍问题或不同调整方向，不能擅改硬约定、真相或正文，不能交给作者填复杂表单。', 'review': None}

## v101_o01--current--3

证据目录：`evidence/v101_o01--current--3`。


## v101_o02--current--3

证据目录：`evidence/v101_o02--current--3`。

- pending_review：`step-1/goal`：{'rubric': '误导来自解释变化，保留真实湿手套与既定真相，回收有观察依据，不改正文。', 'review': None}

## v101_o03--current--3

证据目录：`evidence/v101_o03--current--3`。

- pending_review：`step-1/goal`：{'rubric': '证词不是作者真相，林砚早期不能知道搬尸事实；后期发现须有实际依据。', 'review': None}

## v101_o04--current--3

证据目录：`evidence/v101_o04--current--3`。


## v101_o05--current--3

证据目录：`evidence/v101_o05--current--3`。

- pending_review：`step-1/goal`：{'rubric': '两条调查均提供不可省略的证据和发现过程，汇合结论依据充分，不能直接读到作者真相。', 'review': None}

## v101_o06--current--3

证据目录：`evidence/v101_o06--current--3`。


## v101_o07--current--3

证据目录：`evidence/v101_o07--current--3`。


## v101_o08--current--3

证据目录：`evidence/v101_o08--current--3`。


## v101_o09--current--3

证据目录：`evidence/v101_o09--current--3`。


## v101_o10--current--3

证据目录：`evidence/v101_o10--current--3`。

- pending_review：`step-1/goal`：{'rubric': '前文真实改写且保留线索与真相；需要失效的时间推断不能宣称继续有效，范围外正文不被改动。', 'review': None}

## v101_o11--current--3

证据目录：`evidence/v101_o11--current--3`。

- pending_review：`step-1/goal`：{'rubric': '独立读者请求仅有截止前缀，无作者历史、工具、隐藏真相或未来节点；回答引用前缀原文，猜测必须有依据。', 'review': None}

## v101_o12--current--3

证据目录：`evidence/v101_o12--current--3`。

- fail：`step-1/terminal`：{'seq': 3481, 'at': '2026-10-29T23:09:13.796+08:00', 'event_version': 1, 'event_id': 'c_23fe3fc6ac8a:3481', 'conversation_id': 'c_23fe3fc6ac8a', 'type': 'run_finished', 'turn_id': 't_4b55c099', 'operation_id': 'op_5e71a6dbc451', 'generation': 1, 'status': 'needs_input', 'recording': 'no_change', 'changed_paths': [], 'commit': None, 'note': ''}
- pending_review：`step-1/goal`：{'rubric': '指出两项具体冲突，提出清楚可回答的取舍问题或不同调整方向，不能擅改硬约定、真相或正文，不能交给作者填复杂表单。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
