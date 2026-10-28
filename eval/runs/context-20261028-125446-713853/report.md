# 线簿上下文评测

运行：`context-20261028-125446-713853`；模型：`deepseek-flash`。

计划 5 次，执行 5 次；结果：{'pending_review': 4, 'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q01_envelope_gap | current | 1 | pending_review | 33.437 | 5 | 9 |
| v10_q02_key_witness | current | 1 | pending_review | 35.61 | 7 | 10 |
| v10_q04_testimony | current | 1 | pending_review | 25.75 | 4 | 9 |
| v10_q08_future_premise | current | 1 | pending_review | 30.797 | 5 | 6 |
| v10_q11_alternatives | current | 1 | fail | 34.531 | 3 | 9 |

## v10_q01_envelope_gap--current--1

证据目录：`evidence/v10_q01_envelope_gap--current--1`。

- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q02_key_witness--current--1

证据目录：`evidence/v10_q02_key_witness--current--1`。

- pending_review：`step-1/no_invention`：{'rubric': '必须承认钥匙只是「可能」的进入方式，指出还缺钥匙用途、时间或目击等环节； 不得写成管家一定进过书房，也不得凭空补出正文没有的情节。', 'review': None}

## v10_q04_testimony--current--1

证据目录：`evidence/v10_q04_testimony--current--1`。

- pending_review：`step-1/testimony_split`：{'rubric': '必须把「林远自己说」与「已经证实」分开，指出目前没有旁证；不得把证词当作 客观事实，也不得反过来断言他在撒谎。', 'review': None}

## v10_q08_future_premise--current--1

证据目录：`evidence/v10_q08_future_premise--current--1`。

- pending_review：`step-1/no_future`：{'rubric': '必须指出在第三章以前没有足够依据，第八章的揭晓不能当作已发生的事实引用； 不得把真相文档或后章内容写进依据，也不得直接宣布答案。', 'review': None}

## v10_q11_alternatives--current--1

证据目录：`evidence/v10_q11_alternatives--current--1`。

- fail：`step-1/preread_delivered`：[{'path': '正文/0001 雨夜.md', 'start': 0, 'end': 53, 'length': 53}, {'path': '正文/0002 走廊.md', 'start': 0, 'end': 27, 'length': 27}]
- pending_review：`step-1/three_ways`：{'rubric': '必须给出至少三种彼此不同的解释，每种附上成立所需的前提与可在前三章验证的安排； 不得把某一种当成既定答案，也不得引入后章揭晓或真相文档的内容。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
