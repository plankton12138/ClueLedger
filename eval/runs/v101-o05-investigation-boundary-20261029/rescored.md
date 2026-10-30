# 线簿上下文评测

运行：`context-20261029-234316-308336`；模型：`deepseek-flash`。

计划 3 次，执行 3 次；结果：{'pass': 2, 'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v101_o05 | current | 1 | pass | 58.093 | 3 | 4 |
| v101_o05 | current | 2 | pass | 84.594 | 4 | 11 |
| v101_o05 | current | 3 | fail | 56.297 | 4 | 6 |

## v101_o05--current--1

证据目录：`evidence/v101_o05--current--1`。


## v101_o05--current--2

证据目录：`evidence/v101_o05--current--2`。


## v101_o05--current--3

证据目录：`evidence/v101_o05--current--3`。

- fail：`step-1/goal`：{'verdict': 'fail', 'reviewer': 'Codex 独立证据审阅（非作者体验）', 'reason': '承认同款靴无法定人，却仍把不明人物脚印称为与管家在房间的证词矛盾；汇合结论超出已取得证据。', 'evidence': 'eval/runs/v101-o05-investigation-boundary-20261029/evidence/v101_o05--current--3/step-1.json：events、details、before/after、diffs、story、operations'}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
