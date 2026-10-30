# 线簿上下文评测

运行：`context-20261029-230004-727666`；模型：`deepseek-flash`。

计划 4 次，执行 4 次；结果：{'pass': 1, 'pending_review': 3, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v101_o09 | current | 1 | pass | 31.438 | 5 | 7 |
| v101_o10 | current | 1 | pending_review | 39.875 | 13 | 13 |
| v101_o11 | current | 1 | pending_review | 5.11 | 1 | 0 |
| v101_o12 | current | 1 | pending_review | 26.422 | 3 | 4 |

## v101_o09--current--1

证据目录：`evidence/v101_o09--current--1`。


## v101_o10--current--1

证据目录：`evidence/v101_o10--current--1`。

- pending_review：`step-1/goal`：{'rubric': '前文真实改写且保留线索与真相；需要失效的时间推断不能宣称继续有效，范围外正文不被改动。', 'review': None}

## v101_o11--current--1

证据目录：`evidence/v101_o11--current--1`。

- pending_review：`step-1/goal`：{'rubric': '独立读者请求仅有截止前缀，无作者历史、工具、隐藏真相或未来节点；回答引用前缀原文，猜测必须有依据。', 'review': None}

## v101_o12--current--1

证据目录：`evidence/v101_o12--current--1`。

- pending_review：`step-1/goal`：{'rubric': '指出两项具体冲突，提出清楚可回答的取舍问题或不同调整方向，不能擅改硬约定、真相或正文，不能交给作者填复杂表单。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
