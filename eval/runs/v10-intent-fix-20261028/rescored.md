# 线簿上下文评测

运行：`context-20261028-151704-790216`；模型：`deepseek-flash`。

计划 9 次，执行 9 次；结果：{'pass': 8, 'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q01_envelope_gap | current | 1 | pass | 32.438 | 4 | 9 |
| v10_q04_testimony | current | 1 | pass | 44.469 | 6 | 9 |
| v10_q07_suspect | current | 1 | pass | 34.578 | 4 | 7 |
| v10_q01_envelope_gap | current | 2 | pass | 21.406 | 4 | 5 |
| v10_q04_testimony | current | 2 | pass | 18.797 | 3 | 3 |
| v10_q07_suspect | current | 2 | pass | 37.0 | 5 | 7 |
| v10_q01_envelope_gap | current | 3 | pass | 24.391 | 5 | 7 |
| v10_q04_testimony | current | 3 | pass | 22.172 | 4 | 4 |
| v10_q07_suspect | current | 3 | fail | 52.812 | 8 | 12 |

## v10_q01_envelope_gap--current--1

证据目录：`evidence/v10_q01_envelope_gap--current--1`。


## v10_q04_testimony--current--1

证据目录：`evidence/v10_q04_testimony--current--1`。


## v10_q07_suspect--current--1

证据目录：`evidence/v10_q07_suspect--current--1`。


## v10_q01_envelope_gap--current--2

证据目录：`evidence/v10_q01_envelope_gap--current--2`。


## v10_q04_testimony--current--2

证据目录：`evidence/v10_q04_testimony--current--2`。


## v10_q07_suspect--current--2

证据目录：`evidence/v10_q07_suspect--current--2`。


## v10_q01_envelope_gap--current--3

证据目录：`evidence/v10_q01_envelope_gap--current--3`。


## v10_q04_testimony--current--3

证据目录：`evidence/v10_q04_testimony--current--3`。


## v10_q07_suspect--current--3

证据目录：`evidence/v10_q07_suspect--current--3`。

- fail：`step-1/proportionate`：{'verdict': 'fail', 'reviewer': 'Codex 代理复核（非独立人工签收）', 'reason': '忽略章标题与推门动作的叙述上下文，以没有跨门槛或停留描写否定起疑方向，要求过严。', 'evidence': 'eval/runs/v10-intent-fix-20261028/evidence/v10_q07_suspect--current--3/step-1.json'}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
