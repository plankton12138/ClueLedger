# 线簿上下文评测

运行：`context-20261029-231035-211145`；模型：`deepseek-flash`。

计划 9 次，执行 9 次；结果：{'pass': 7, 'fail': 2, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v101_o02 | current | 1 | pass | 124.704 | 4 | 6 |
| v101_o03 | current | 1 | pass | 74.328 | 3 | 4 |
| v101_o12 | current | 1 | pass | 25.438 | 3 | 4 |
| v101_o02 | current | 2 | pass | 72.937 | 3 | 4 |
| v101_o03 | current | 2 | fail | 43.656 | 3 | 3 |
| v101_o12 | current | 2 | pass | 24.047 | 4 | 5 |
| v101_o02 | current | 3 | fail | 72.641 | 3 | 4 |
| v101_o03 | current | 3 | pass | 86.656 | 2 | 2 |
| v101_o12 | current | 3 | pass | 31.125 | 4 | 5 |

## v101_o02--current--1

证据目录：`evidence/v101_o02--current--1`。


## v101_o03--current--1

证据目录：`evidence/v101_o03--current--1`。


## v101_o12--current--1

证据目录：`evidence/v101_o12--current--1`。


## v101_o02--current--2

证据目录：`evidence/v101_o02--current--2`。


## v101_o03--current--2

证据目录：`evidence/v101_o03--current--2`。

- fail：`step-1/goal`：{'verdict': 'fail', 'reviewer': 'Codex 独立证据审阅（非作者体验）', 'reason': '首次询问一面声称21:30后未离房，一面承认到后廊发现尸体，林砚仍采信；误信缺少自洽依据。', 'evidence': 'eval/runs/v101-causal-contract-20261029/evidence/v101_o03--current--2/step-1.json：events、details、before/after、diffs、story、operations'}

## v101_o12--current--2

证据目录：`evidence/v101_o12--current--2`。


## v101_o02--current--3

证据目录：`evidence/v101_o02--current--3`。

- fail：`step-1/goal`：{'verdict': 'fail', 'reviewer': 'Codex 独立证据审阅（非作者体验）', 'reason': '误读表把房间与整栋房屋写成偷换；后文称进门时管家在屋内、门从内开足以封死刚回来，与前文自行推门矛盾，推断依据不足。', 'evidence': 'eval/runs/v101-causal-contract-20261029/evidence/v101_o02--current--3/step-1.json：events、details、before/after、diffs、story、operations'}

## v101_o03--current--3

证据目录：`evidence/v101_o03--current--3`。


## v101_o12--current--3

证据目录：`evidence/v101_o12--current--3`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
