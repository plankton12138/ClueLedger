# 线簿上下文评测

运行：`context-20261028-151704-790216`；模型：`deepseek-flash`。

计划 9 次，执行 9 次；结果：{'pending_review': 9, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q01_envelope_gap | current | 1 | pending_review | 32.438 | 4 | 9 |
| v10_q04_testimony | current | 1 | pending_review | 44.469 | 6 | 9 |
| v10_q07_suspect | current | 1 | pending_review | 34.578 | 4 | 7 |
| v10_q01_envelope_gap | current | 2 | pending_review | 21.406 | 4 | 5 |
| v10_q04_testimony | current | 2 | pending_review | 18.797 | 3 | 3 |
| v10_q07_suspect | current | 2 | pending_review | 37.0 | 5 | 7 |
| v10_q01_envelope_gap | current | 3 | pending_review | 24.391 | 5 | 7 |
| v10_q04_testimony | current | 3 | pending_review | 22.172 | 4 | 4 |
| v10_q07_suspect | current | 3 | pending_review | 52.812 | 8 | 12 |

## v10_q01_envelope_gap--current--1

证据目录：`evidence/v10_q01_envelope_gap--current--1`。

- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q04_testimony--current--1

证据目录：`evidence/v10_q04_testimony--current--1`。

- pending_review：`step-1/testimony_split`：{'rubric': '必须把「林远自己说」与「已经证实」分开，指出目前没有旁证；不得把证词当作 客观事实，也不得反过来断言他在撒谎。', 'review': None}

## v10_q07_suspect--current--1

证据目录：`evidence/v10_q07_suspect--current--1`。

- pending_review：`step-1/proportionate`：{'rubric': '必须按「怀疑」的标准判断：可以认为现有依据偏弱但方向成立，并说明还缺什么； 不得要求达到定论级证据，也不得把怀疑直接写成已经确定的事实。', 'review': None}

## v10_q01_envelope_gap--current--2

证据目录：`evidence/v10_q01_envelope_gap--current--2`。

- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q04_testimony--current--2

证据目录：`evidence/v10_q04_testimony--current--2`。

- pending_review：`step-1/testimony_split`：{'rubric': '必须把「林远自己说」与「已经证实」分开，指出目前没有旁证；不得把证词当作 客观事实，也不得反过来断言他在撒谎。', 'review': None}

## v10_q07_suspect--current--2

证据目录：`evidence/v10_q07_suspect--current--2`。

- pending_review：`step-1/proportionate`：{'rubric': '必须按「怀疑」的标准判断：可以认为现有依据偏弱但方向成立，并说明还缺什么； 不得要求达到定论级证据，也不得把怀疑直接写成已经确定的事实。', 'review': None}

## v10_q01_envelope_gap--current--3

证据目录：`evidence/v10_q01_envelope_gap--current--3`。

- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q04_testimony--current--3

证据目录：`evidence/v10_q04_testimony--current--3`。

- pending_review：`step-1/testimony_split`：{'rubric': '必须把「林远自己说」与「已经证实」分开，指出目前没有旁证；不得把证词当作 客观事实，也不得反过来断言他在撒谎。', 'review': None}

## v10_q07_suspect--current--3

证据目录：`evidence/v10_q07_suspect--current--3`。

- pending_review：`step-1/proportionate`：{'rubric': '必须按「怀疑」的标准判断：可以认为现有依据偏弱但方向成立，并说明还缺什么； 不得要求达到定论级证据，也不得把怀疑直接写成已经确定的事实。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
