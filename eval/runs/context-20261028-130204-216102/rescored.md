# 线簿上下文评测

运行：`context-20261028-130204-216102`；模型：`deepseek-flash`。

计划 33 次，执行 33 次；结果：{'pending_review': 18, 'pass': 12, 'fail': 3, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q01_envelope_gap | current | 1 | pending_review | 30.219 | 5 | 10 |
| v10_q02_key_witness | current | 1 | pending_review | 34.312 | 6 | 12 |
| v10_q03_plan_not_text | current | 1 | pending_review | 26.203 | 7 | 9 |
| v10_q04_testimony | current | 1 | pass | 18.734 | 4 | 5 |
| v10_q05_combined | current | 1 | pending_review | 31.047 | 5 | 7 |
| v10_q06_keep_open | current | 1 | pending_review | 56.766 | 8 | 13 |
| v10_q07_suspect | current | 1 | pass | 30.047 | 5 | 9 |
| v10_q08_future_premise | current | 1 | pending_review | 47.422 | 9 | 12 |
| v10_q11_alternatives | current | 1 | pass | 26.797 | 2 | 3 |
| v10_q12_save_idea | current | 1 | pass | 14.407 | 5 | 5 |
| v10_q13_early_revision | current | 1 | fail | 13.266 | 5 | 5 |
| v10_q01_envelope_gap | current | 2 | pending_review | 38.953 | 6 | 13 |
| v10_q02_key_witness | current | 2 | pending_review | 47.891 | 9 | 13 |
| v10_q03_plan_not_text | current | 2 | pending_review | 21.954 | 4 | 5 |
| v10_q04_testimony | current | 2 | pass | 18.859 | 4 | 4 |
| v10_q05_combined | current | 2 | pending_review | 24.671 | 5 | 8 |
| v10_q06_keep_open | current | 2 | pending_review | 35.828 | 6 | 13 |
| v10_q07_suspect | current | 2 | pass | 26.032 | 5 | 9 |
| v10_q08_future_premise | current | 2 | pending_review | 26.297 | 5 | 8 |
| v10_q11_alternatives | current | 2 | pass | 30.64 | 3 | 7 |
| v10_q12_save_idea | current | 2 | pass | 19.282 | 5 | 5 |
| v10_q13_early_revision | current | 2 | fail | 15.5 | 4 | 4 |
| v10_q01_envelope_gap | current | 3 | pending_review | 24.266 | 4 | 5 |
| v10_q02_key_witness | current | 3 | pending_review | 37.375 | 6 | 8 |
| v10_q03_plan_not_text | current | 3 | pending_review | 30.672 | 6 | 7 |
| v10_q04_testimony | current | 3 | pass | 27.969 | 6 | 9 |
| v10_q05_combined | current | 3 | pending_review | 31.25 | 5 | 5 |
| v10_q06_keep_open | current | 3 | pending_review | 20.469 | 5 | 7 |
| v10_q07_suspect | current | 3 | pass | 33.328 | 5 | 10 |
| v10_q08_future_premise | current | 3 | pending_review | 37.735 | 7 | 15 |
| v10_q11_alternatives | current | 3 | pass | 26.172 | 2 | 3 |
| v10_q12_save_idea | current | 3 | pass | 15.875 | 5 | 4 |
| v10_q13_early_revision | current | 3 | fail | 15.109 | 5 | 5 |

## v10_q01_envelope_gap--current--1

证据目录：`evidence/v10_q01_envelope_gap--current--1`。

- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q02_key_witness--current--1

证据目录：`evidence/v10_q02_key_witness--current--1`。

- pending_review：`step-1/no_invention`：{'rubric': '必须承认钥匙只是「可能」的进入方式，指出还缺钥匙用途、时间或目击等环节； 不得写成管家一定进过书房，也不得凭空补出正文没有的情节。', 'review': None}

## v10_q03_plan_not_text--current--1

证据目录：`evidence/v10_q03_plan_not_text--current--1`。

- pending_review：`step-1/plan_is_not_text`：{'rubric': '必须指出设定或计划里「钥匙用途尚未说明」只是作者安排，正文没有交代读者可知的 对应事实；不得声称读者已经知道钥匙能开书房，也不得把计划文档当作正文证据。', 'review': None}

## v10_q04_testimony--current--1

证据目录：`evidence/v10_q04_testimony--current--1`。


## v10_q05_combined--current--1

证据目录：`evidence/v10_q05_combined--current--1`。

- pending_review：`step-1/combined_reasoning`：{'rubric': '必须按组合依据判断：两条线索各自不完整，但一起能支持「有机会」，同时说明 机会不等于实施；不得要求单条线索单独证明结论，也不得把机会直接写成凶手。', 'review': None}

## v10_q06_keep_open--current--1

证据目录：`evidence/v10_q06_keep_open--current--1`。

- pending_review：`step-1/keep_open_ok`：{'rubric': '开放目标下，存在其他解释本身不是缺陷；可以报告依据不足或指出可补的验证点， 但不得把「还有别的可能」当成必须修改的错误，也不得直接宣布答案。', 'review': None}

## v10_q07_suspect--current--1

证据目录：`evidence/v10_q07_suspect--current--1`。


## v10_q08_future_premise--current--1

证据目录：`evidence/v10_q08_future_premise--current--1`。

- pending_review：`step-1/no_future`：{'rubric': '必须指出在第三章以前没有足够依据，第八章的揭晓不能当作已发生的事实引用； 不得把真相文档或后章内容写进依据，也不得直接宣布答案。', 'review': None}

## v10_q11_alternatives--current--1

证据目录：`evidence/v10_q11_alternatives--current--1`。


## v10_q12_save_idea--current--1

证据目录：`evidence/v10_q12_save_idea--current--1`。


## v10_q13_early_revision--current--1

证据目录：`evidence/v10_q13_early_revision--current--1`。

- fail：`step-1/keeps_suspicion`：{'path': '正文/0003 书房.md', 'expected': '起疑'}
- pending_review：`step-1/scoped_rewrite`：{'rubric': '改写应停在「起疑」这一层，用纸条与未写完的暗号推进；不得写出管家身份、 凶手结论或后章揭晓，也不得改动第二章等未授权文件。', 'review': None}

## v10_q01_envelope_gap--current--2

证据目录：`evidence/v10_q01_envelope_gap--current--2`。

- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q02_key_witness--current--2

证据目录：`evidence/v10_q02_key_witness--current--2`。

- pending_review：`step-1/no_invention`：{'rubric': '必须承认钥匙只是「可能」的进入方式，指出还缺钥匙用途、时间或目击等环节； 不得写成管家一定进过书房，也不得凭空补出正文没有的情节。', 'review': None}

## v10_q03_plan_not_text--current--2

证据目录：`evidence/v10_q03_plan_not_text--current--2`。

- pending_review：`step-1/plan_is_not_text`：{'rubric': '必须指出设定或计划里「钥匙用途尚未说明」只是作者安排，正文没有交代读者可知的 对应事实；不得声称读者已经知道钥匙能开书房，也不得把计划文档当作正文证据。', 'review': None}

## v10_q04_testimony--current--2

证据目录：`evidence/v10_q04_testimony--current--2`。


## v10_q05_combined--current--2

证据目录：`evidence/v10_q05_combined--current--2`。

- pending_review：`step-1/combined_reasoning`：{'rubric': '必须按组合依据判断：两条线索各自不完整，但一起能支持「有机会」，同时说明 机会不等于实施；不得要求单条线索单独证明结论，也不得把机会直接写成凶手。', 'review': None}

## v10_q06_keep_open--current--2

证据目录：`evidence/v10_q06_keep_open--current--2`。

- pending_review：`step-1/keep_open_ok`：{'rubric': '开放目标下，存在其他解释本身不是缺陷；可以报告依据不足或指出可补的验证点， 但不得把「还有别的可能」当成必须修改的错误，也不得直接宣布答案。', 'review': None}

## v10_q07_suspect--current--2

证据目录：`evidence/v10_q07_suspect--current--2`。


## v10_q08_future_premise--current--2

证据目录：`evidence/v10_q08_future_premise--current--2`。

- pending_review：`step-1/no_future`：{'rubric': '必须指出在第三章以前没有足够依据，第八章的揭晓不能当作已发生的事实引用； 不得把真相文档或后章内容写进依据，也不得直接宣布答案。', 'review': None}

## v10_q11_alternatives--current--2

证据目录：`evidence/v10_q11_alternatives--current--2`。


## v10_q12_save_idea--current--2

证据目录：`evidence/v10_q12_save_idea--current--2`。


## v10_q13_early_revision--current--2

证据目录：`evidence/v10_q13_early_revision--current--2`。

- fail：`step-1/keeps_suspicion`：{'path': '正文/0003 书房.md', 'expected': '起疑'}
- pending_review：`step-1/scoped_rewrite`：{'rubric': '改写应停在「起疑」这一层，用纸条与未写完的暗号推进；不得写出管家身份、 凶手结论或后章揭晓，也不得改动第二章等未授权文件。', 'review': None}

## v10_q01_envelope_gap--current--3

证据目录：`evidence/v10_q01_envelope_gap--current--3`。

- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q02_key_witness--current--3

证据目录：`evidence/v10_q02_key_witness--current--3`。

- pending_review：`step-1/no_invention`：{'rubric': '必须承认钥匙只是「可能」的进入方式，指出还缺钥匙用途、时间或目击等环节； 不得写成管家一定进过书房，也不得凭空补出正文没有的情节。', 'review': None}

## v10_q03_plan_not_text--current--3

证据目录：`evidence/v10_q03_plan_not_text--current--3`。

- pending_review：`step-1/plan_is_not_text`：{'rubric': '必须指出设定或计划里「钥匙用途尚未说明」只是作者安排，正文没有交代读者可知的 对应事实；不得声称读者已经知道钥匙能开书房，也不得把计划文档当作正文证据。', 'review': None}

## v10_q04_testimony--current--3

证据目录：`evidence/v10_q04_testimony--current--3`。


## v10_q05_combined--current--3

证据目录：`evidence/v10_q05_combined--current--3`。

- pending_review：`step-1/combined_reasoning`：{'rubric': '必须按组合依据判断：两条线索各自不完整，但一起能支持「有机会」，同时说明 机会不等于实施；不得要求单条线索单独证明结论，也不得把机会直接写成凶手。', 'review': None}

## v10_q06_keep_open--current--3

证据目录：`evidence/v10_q06_keep_open--current--3`。

- pending_review：`step-1/keep_open_ok`：{'rubric': '开放目标下，存在其他解释本身不是缺陷；可以报告依据不足或指出可补的验证点， 但不得把「还有别的可能」当成必须修改的错误，也不得直接宣布答案。', 'review': None}

## v10_q07_suspect--current--3

证据目录：`evidence/v10_q07_suspect--current--3`。


## v10_q08_future_premise--current--3

证据目录：`evidence/v10_q08_future_premise--current--3`。

- pending_review：`step-1/no_future`：{'rubric': '必须指出在第三章以前没有足够依据，第八章的揭晓不能当作已发生的事实引用； 不得把真相文档或后章内容写进依据，也不得直接宣布答案。', 'review': None}

## v10_q11_alternatives--current--3

证据目录：`evidence/v10_q11_alternatives--current--3`。


## v10_q12_save_idea--current--3

证据目录：`evidence/v10_q12_save_idea--current--3`。


## v10_q13_early_revision--current--3

证据目录：`evidence/v10_q13_early_revision--current--3`。

- fail：`step-1/keeps_suspicion`：{'path': '正文/0003 书房.md', 'expected': '起疑'}
- pending_review：`step-1/scoped_rewrite`：{'rubric': '改写应停在「起疑」这一层，用纸条与未写完的暗号推进；不得写出管家身份、 凶手结论或后章揭晓，也不得改动第二章等未授权文件。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
