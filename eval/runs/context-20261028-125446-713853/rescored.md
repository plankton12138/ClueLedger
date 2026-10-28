# 线簿上下文评测

运行：`context-20261028-125446-713853`；模型：`deepseek-flash`。

计划 5 次，执行 5 次；结果：{'pass': 4, 'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q01_envelope_gap | current | 1 | pass | 33.437 | 5 | 9 |
| v10_q02_key_witness | current | 1 | pass | 35.61 | 7 | 10 |
| v10_q04_testimony | current | 1 | pass | 25.75 | 4 | 9 |
| v10_q08_future_premise | current | 1 | pass | 30.797 | 5 | 6 |
| v10_q11_alternatives | current | 1 | fail | 34.531 | 3 | 9 |

## v10_q01_envelope_gap--current--1

证据目录：`evidence/v10_q01_envelope_gap--current--1`。


## v10_q02_key_witness--current--1

证据目录：`evidence/v10_q02_key_witness--current--1`。


## v10_q04_testimony--current--1

证据目录：`evidence/v10_q04_testimony--current--1`。


## v10_q08_future_premise--current--1

证据目录：`evidence/v10_q08_future_premise--current--1`。


## v10_q11_alternatives--current--1

证据目录：`evidence/v10_q11_alternatives--current--1`。

- fail：`step-1/preread_delivered`：[{'path': '正文/0001 雨夜.md', 'start': 0, 'end': 53, 'length': 53}, {'path': '正文/0002 走廊.md', 'start': 0, 'end': 27, 'length': 27}]

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
