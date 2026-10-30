# 线簿上下文评测

运行：`context-20261029-231035-211145`；模型：`deepseek-flash`。

计划 9 次，执行 9 次；结果：{'pending_review': 9, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v101_o02 | current | 1 | pending_review | 124.704 | 4 | 6 |
| v101_o03 | current | 1 | pending_review | 74.328 | 3 | 4 |
| v101_o12 | current | 1 | pending_review | 25.438 | 3 | 4 |
| v101_o02 | current | 2 | pending_review | 72.937 | 3 | 4 |
| v101_o03 | current | 2 | pending_review | 43.656 | 3 | 3 |
| v101_o12 | current | 2 | pending_review | 24.047 | 4 | 5 |
| v101_o02 | current | 3 | pending_review | 72.641 | 3 | 4 |
| v101_o03 | current | 3 | pending_review | 86.656 | 2 | 2 |
| v101_o12 | current | 3 | pending_review | 31.125 | 4 | 5 |

## v101_o02--current--1

证据目录：`evidence/v101_o02--current--1`。

- pending_review：`step-1/goal`：{'rubric': '误导来自解释变化，保留真实湿手套与既定真相，回收有观察依据，不改正文。', 'review': None}

## v101_o03--current--1

证据目录：`evidence/v101_o03--current--1`。

- pending_review：`step-1/goal`：{'rubric': '证词不是作者真相，林砚早期不能知道搬尸事实；后期发现须有实际依据。', 'review': None}

## v101_o12--current--1

证据目录：`evidence/v101_o12--current--1`。

- pending_review：`step-1/goal`：{'rubric': '指出两项具体冲突，提出清楚可回答的取舍问题或不同调整方向，不能擅改硬约定、真相或正文，不能交给作者填复杂表单。', 'review': None}

## v101_o02--current--2

证据目录：`evidence/v101_o02--current--2`。

- pending_review：`step-1/goal`：{'rubric': '误导来自解释变化，保留真实湿手套与既定真相，回收有观察依据，不改正文。', 'review': None}

## v101_o03--current--2

证据目录：`evidence/v101_o03--current--2`。

- pending_review：`step-1/goal`：{'rubric': '证词不是作者真相，林砚早期不能知道搬尸事实；后期发现须有实际依据。', 'review': None}

## v101_o12--current--2

证据目录：`evidence/v101_o12--current--2`。

- pending_review：`step-1/goal`：{'rubric': '指出两项具体冲突，提出清楚可回答的取舍问题或不同调整方向，不能擅改硬约定、真相或正文，不能交给作者填复杂表单。', 'review': None}

## v101_o02--current--3

证据目录：`evidence/v101_o02--current--3`。

- pending_review：`step-1/goal`：{'rubric': '误导来自解释变化，保留真实湿手套与既定真相，回收有观察依据，不改正文。', 'review': None}

## v101_o03--current--3

证据目录：`evidence/v101_o03--current--3`。

- pending_review：`step-1/goal`：{'rubric': '证词不是作者真相，林砚早期不能知道搬尸事实；后期发现须有实际依据。', 'review': None}

## v101_o12--current--3

证据目录：`evidence/v101_o12--current--3`。

- pending_review：`step-1/goal`：{'rubric': '指出两项具体冲突，提出清楚可回答的取舍问题或不同调整方向，不能擅改硬约定、真相或正文，不能交给作者填复杂表单。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
