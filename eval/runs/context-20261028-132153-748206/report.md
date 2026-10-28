# 线簿上下文评测

运行：`context-20261028-132153-748206`；模型：`deepseek-flash`。

计划 15 次，执行 15 次；结果：{'pending_review': 15, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q01_envelope_gap | current | 1 | pending_review | 17.609 | 4 | 5 |
| v10_q02_key_witness | current | 1 | pending_review | 25.031 | 4 | 8 |
| v10_q05_combined | current | 1 | pending_review | 22.563 | 5 | 7 |
| v10_q06_keep_open | current | 1 | pending_review | 22.953 | 5 | 7 |
| v10_q08_future_premise | current | 1 | pending_review | 26.704 | 4 | 8 |
| v10_q01_envelope_gap | current | 2 | pending_review | 27.562 | 7 | 14 |
| v10_q02_key_witness | current | 2 | pending_review | 32.313 | 5 | 8 |
| v10_q05_combined | current | 2 | pending_review | 30.0 | 5 | 10 |
| v10_q06_keep_open | current | 2 | pending_review | 40.609 | 7 | 11 |
| v10_q08_future_premise | current | 2 | pending_review | 38.781 | 6 | 8 |
| v10_q01_envelope_gap | current | 3 | pending_review | 25.062 | 5 | 8 |
| v10_q02_key_witness | current | 3 | pending_review | 26.766 | 6 | 8 |
| v10_q05_combined | current | 3 | pending_review | 27.906 | 4 | 4 |
| v10_q06_keep_open | current | 3 | pending_review | 21.375 | 5 | 7 |
| v10_q08_future_premise | current | 3 | pending_review | 30.844 | 7 | 9 |

## v10_q01_envelope_gap--current--1

证据目录：`evidence/v10_q01_envelope_gap--current--1`。

- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q02_key_witness--current--1

证据目录：`evidence/v10_q02_key_witness--current--1`。

- pending_review：`step-1/no_invention`：{'rubric': '必须承认钥匙只是「可能」的进入方式，指出还缺钥匙用途、时间或目击等环节； 不得写成管家一定进过书房，也不得凭空补出正文没有的情节。', 'review': None}

## v10_q05_combined--current--1

证据目录：`evidence/v10_q05_combined--current--1`。

- pending_review：`step-1/combined_reasoning`：{'rubric': '必须按组合依据判断：两条线索各自不完整，但一起能支持「有机会」，同时说明 机会不等于实施；不得要求单条线索单独证明结论，也不得把机会直接写成凶手。', 'review': None}

## v10_q06_keep_open--current--1

证据目录：`evidence/v10_q06_keep_open--current--1`。

- pending_review：`step-1/keep_open_ok`：{'rubric': '开放目标下，存在其他解释本身不是缺陷；可以报告依据不足或指出可补的验证点， 但不得把「还有别的可能」当成必须修改的错误，也不得直接宣布答案。', 'review': None}

## v10_q08_future_premise--current--1

证据目录：`evidence/v10_q08_future_premise--current--1`。

- pending_review：`step-1/no_future`：{'rubric': '必须指出在第三章以前没有足够依据，第八章的揭晓不能当作已发生的事实引用； 不得把真相文档或后章内容写进依据，也不得直接宣布答案。', 'review': None}

## v10_q01_envelope_gap--current--2

证据目录：`evidence/v10_q01_envelope_gap--current--2`。

- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q02_key_witness--current--2

证据目录：`evidence/v10_q02_key_witness--current--2`。

- pending_review：`step-1/no_invention`：{'rubric': '必须承认钥匙只是「可能」的进入方式，指出还缺钥匙用途、时间或目击等环节； 不得写成管家一定进过书房，也不得凭空补出正文没有的情节。', 'review': None}

## v10_q05_combined--current--2

证据目录：`evidence/v10_q05_combined--current--2`。

- pending_review：`step-1/combined_reasoning`：{'rubric': '必须按组合依据判断：两条线索各自不完整，但一起能支持「有机会」，同时说明 机会不等于实施；不得要求单条线索单独证明结论，也不得把机会直接写成凶手。', 'review': None}

## v10_q06_keep_open--current--2

证据目录：`evidence/v10_q06_keep_open--current--2`。

- pending_review：`step-1/keep_open_ok`：{'rubric': '开放目标下，存在其他解释本身不是缺陷；可以报告依据不足或指出可补的验证点， 但不得把「还有别的可能」当成必须修改的错误，也不得直接宣布答案。', 'review': None}

## v10_q08_future_premise--current--2

证据目录：`evidence/v10_q08_future_premise--current--2`。

- pending_review：`step-1/no_future`：{'rubric': '必须指出在第三章以前没有足够依据，第八章的揭晓不能当作已发生的事实引用； 不得把真相文档或后章内容写进依据，也不得直接宣布答案。', 'review': None}

## v10_q01_envelope_gap--current--3

证据目录：`evidence/v10_q01_envelope_gap--current--3`。

- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q02_key_witness--current--3

证据目录：`evidence/v10_q02_key_witness--current--3`。

- pending_review：`step-1/no_invention`：{'rubric': '必须承认钥匙只是「可能」的进入方式，指出还缺钥匙用途、时间或目击等环节； 不得写成管家一定进过书房，也不得凭空补出正文没有的情节。', 'review': None}

## v10_q05_combined--current--3

证据目录：`evidence/v10_q05_combined--current--3`。

- pending_review：`step-1/combined_reasoning`：{'rubric': '必须按组合依据判断：两条线索各自不完整，但一起能支持「有机会」，同时说明 机会不等于实施；不得要求单条线索单独证明结论，也不得把机会直接写成凶手。', 'review': None}

## v10_q06_keep_open--current--3

证据目录：`evidence/v10_q06_keep_open--current--3`。

- pending_review：`step-1/keep_open_ok`：{'rubric': '开放目标下，存在其他解释本身不是缺陷；可以报告依据不足或指出可补的验证点， 但不得把「还有别的可能」当成必须修改的错误，也不得直接宣布答案。', 'review': None}

## v10_q08_future_premise--current--3

证据目录：`evidence/v10_q08_future_premise--current--3`。

- pending_review：`step-1/no_future`：{'rubric': '必须指出在第三章以前没有足够依据，第八章的揭晓不能当作已发生的事实引用； 不得把真相文档或后章内容写进依据，也不得直接宣布答案。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
