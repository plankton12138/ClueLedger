# 线簿上下文评测

运行：`context-20261029-232906-556601`；模型：`deepseek-flash`。

计划 3 次，执行 3 次；结果：{'pending_review': 3, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v101_o11 | current | 1 | pending_review | 5.468 | 1 | 0 |
| v101_o11 | current | 2 | pending_review | 3.657 | 1 | 0 |
| v101_o11 | current | 3 | pending_review | 3.5 | 1 | 0 |

## v101_o11--current--1

证据目录：`evidence/v101_o11--current--1`。

- pending_review：`step-1/goal`：{'rubric': '独立读者请求仅有截止前缀，无作者历史、工具、隐藏真相或未来节点；回答引用前缀原文，猜测必须有依据。', 'review': None}

## v101_o11--current--2

证据目录：`evidence/v101_o11--current--2`。

- pending_review：`step-1/goal`：{'rubric': '独立读者请求仅有截止前缀，无作者历史、工具、隐藏真相或未来节点；回答引用前缀原文，猜测必须有依据。', 'review': None}

## v101_o11--current--3

证据目录：`evidence/v101_o11--current--3`。

- pending_review：`step-1/goal`：{'rubric': '独立读者请求仅有截止前缀，无作者历史、工具、隐藏真相或未来节点；回答引用前缀原文，猜测必须有依据。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
