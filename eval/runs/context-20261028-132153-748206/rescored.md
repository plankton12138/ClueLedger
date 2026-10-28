# 线簿上下文评测

运行：`context-20261028-132153-748206`；模型：`deepseek-flash`。

计划 15 次，执行 15 次；结果：{'pass': 15, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q01_envelope_gap | current | 1 | pass | 17.609 | 4 | 5 |
| v10_q02_key_witness | current | 1 | pass | 25.031 | 4 | 8 |
| v10_q05_combined | current | 1 | pass | 22.563 | 5 | 7 |
| v10_q06_keep_open | current | 1 | pass | 22.953 | 5 | 7 |
| v10_q08_future_premise | current | 1 | pass | 26.704 | 4 | 8 |
| v10_q01_envelope_gap | current | 2 | pass | 27.562 | 7 | 14 |
| v10_q02_key_witness | current | 2 | pass | 32.313 | 5 | 8 |
| v10_q05_combined | current | 2 | pass | 30.0 | 5 | 10 |
| v10_q06_keep_open | current | 2 | pass | 40.609 | 7 | 11 |
| v10_q08_future_premise | current | 2 | pass | 38.781 | 6 | 8 |
| v10_q01_envelope_gap | current | 3 | pass | 25.062 | 5 | 8 |
| v10_q02_key_witness | current | 3 | pass | 26.766 | 6 | 8 |
| v10_q05_combined | current | 3 | pass | 27.906 | 4 | 4 |
| v10_q06_keep_open | current | 3 | pass | 21.375 | 5 | 7 |
| v10_q08_future_premise | current | 3 | pass | 30.844 | 7 | 9 |

## v10_q01_envelope_gap--current--1

证据目录：`evidence/v10_q01_envelope_gap--current--1`。


## v10_q02_key_witness--current--1

证据目录：`evidence/v10_q02_key_witness--current--1`。


## v10_q05_combined--current--1

证据目录：`evidence/v10_q05_combined--current--1`。


## v10_q06_keep_open--current--1

证据目录：`evidence/v10_q06_keep_open--current--1`。


## v10_q08_future_premise--current--1

证据目录：`evidence/v10_q08_future_premise--current--1`。


## v10_q01_envelope_gap--current--2

证据目录：`evidence/v10_q01_envelope_gap--current--2`。


## v10_q02_key_witness--current--2

证据目录：`evidence/v10_q02_key_witness--current--2`。


## v10_q05_combined--current--2

证据目录：`evidence/v10_q05_combined--current--2`。


## v10_q06_keep_open--current--2

证据目录：`evidence/v10_q06_keep_open--current--2`。


## v10_q08_future_premise--current--2

证据目录：`evidence/v10_q08_future_premise--current--2`。


## v10_q01_envelope_gap--current--3

证据目录：`evidence/v10_q01_envelope_gap--current--3`。


## v10_q02_key_witness--current--3

证据目录：`evidence/v10_q02_key_witness--current--3`。


## v10_q05_combined--current--3

证据目录：`evidence/v10_q05_combined--current--3`。


## v10_q06_keep_open--current--3

证据目录：`evidence/v10_q06_keep_open--current--3`。


## v10_q08_future_premise--current--3

证据目录：`evidence/v10_q08_future_premise--current--3`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
