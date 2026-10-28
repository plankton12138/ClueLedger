# 线簿上下文评测

运行：`context-20261028-144710-165601`；模型：`deepseek-flash`。

计划 48 次，执行 48 次；结果：{'pending_review': 47, 'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q01_envelope_gap | current | 1 | pending_review | 50.687 | 7 | 11 |
| v10_q02_key_witness | current | 1 | pending_review | 30.219 | 5 | 8 |
| v10_q03_plan_not_text | current | 1 | pending_review | 34.796 | 6 | 9 |
| v10_q04_testimony | current | 1 | pending_review | 23.359 | 5 | 9 |
| v10_q05_combined | current | 1 | pending_review | 29.531 | 4 | 4 |
| v10_q06_keep_open | current | 1 | pending_review | 25.734 | 5 | 7 |
| v10_q07_suspect | current | 1 | pending_review | 36.922 | 6 | 12 |
| v10_q08_future_premise | current | 1 | pending_review | 35.078 | 5 | 6 |
| v10_q09_flashback | current | 1 | pending_review | 26.593 | 5 | 8 |
| v10_q10_missing_target | current | 1 | pending_review | 26.172 | 3 | 8 |
| v10_q11_alternatives | current | 1 | pending_review | 33.062 | 3 | 8 |
| v10_q12_save_idea | current | 1 | pending_review | 19.078 | 5 | 5 |
| v10_q13_early_revision | current | 1 | pending_review | 14.969 | 5 | 6 |
| v10_q14_irrelevant_edit | current | 1 | pending_review | 53.015 | 9 | 12 |
| v10_q15_insufficient | current | 1 | pending_review | 33.062 | 6 | 10 |
| v10_q16_full_loop | current | 1 | pending_review | 76.141 | 12 | 17 |
| v10_q01_envelope_gap | current | 2 | fail | 160.219 | 16 | 17 |
| v10_q02_key_witness | current | 2 | pending_review | 32.016 | 5 | 7 |
| v10_q03_plan_not_text | current | 2 | pending_review | 21.797 | 6 | 10 |
| v10_q04_testimony | current | 2 | pending_review | 23.766 | 4 | 5 |
| v10_q05_combined | current | 2 | pending_review | 26.422 | 4 | 6 |
| v10_q06_keep_open | current | 2 | pending_review | 36.25 | 6 | 12 |
| v10_q07_suspect | current | 2 | pending_review | 40.063 | 6 | 9 |
| v10_q08_future_premise | current | 2 | pending_review | 43.344 | 7 | 11 |
| v10_q09_flashback | current | 2 | pending_review | 29.468 | 5 | 9 |
| v10_q10_missing_target | current | 2 | pending_review | 19.078 | 3 | 6 |
| v10_q11_alternatives | current | 2 | pending_review | 25.891 | 3 | 7 |
| v10_q12_save_idea | current | 2 | pending_review | 17.64 | 5 | 6 |
| v10_q13_early_revision | current | 2 | pending_review | 15.766 | 5 | 4 |
| v10_q14_irrelevant_edit | current | 2 | pending_review | 48.312 | 9 | 13 |
| v10_q15_insufficient | current | 2 | pending_review | 35.046 | 7 | 10 |
| v10_q16_full_loop | current | 2 | pending_review | 78.094 | 13 | 12 |
| v10_q01_envelope_gap | current | 3 | pending_review | 37.844 | 7 | 10 |
| v10_q02_key_witness | current | 3 | pending_review | 36.765 | 5 | 8 |
| v10_q03_plan_not_text | current | 3 | pending_review | 34.062 | 6 | 8 |
| v10_q04_testimony | current | 3 | pending_review | 24.703 | 3 | 3 |
| v10_q05_combined | current | 3 | pending_review | 40.094 | 5 | 5 |
| v10_q06_keep_open | current | 3 | pending_review | 24.828 | 5 | 7 |
| v10_q07_suspect | current | 3 | pending_review | 36.89 | 5 | 11 |
| v10_q08_future_premise | current | 3 | pending_review | 33.047 | 4 | 4 |
| v10_q09_flashback | current | 3 | pending_review | 51.484 | 9 | 16 |
| v10_q10_missing_target | current | 3 | pending_review | 20.219 | 3 | 5 |
| v10_q11_alternatives | current | 3 | pending_review | 27.36 | 3 | 4 |
| v10_q12_save_idea | current | 3 | pending_review | 20.875 | 6 | 6 |
| v10_q13_early_revision | current | 3 | pending_review | 30.953 | 5 | 7 |
| v10_q14_irrelevant_edit | current | 3 | pending_review | 46.515 | 10 | 15 |
| v10_q15_insufficient | current | 3 | pending_review | 40.14 | 7 | 10 |
| v10_q16_full_loop | current | 3 | pending_review | 64.407 | 12 | 15 |

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

- pending_review：`step-1/testimony_split`：{'rubric': '必须把「林远自己说」与「已经证实」分开，指出目前没有旁证；不得把证词当作 客观事实，也不得反过来断言他在撒谎。', 'review': None}

## v10_q05_combined--current--1

证据目录：`evidence/v10_q05_combined--current--1`。

- pending_review：`step-1/combined_reasoning`：{'rubric': '必须按组合依据判断：两条线索各自不完整，但一起能支持「有机会」，同时说明 机会不等于实施；不得要求单条线索单独证明结论，也不得把机会直接写成凶手。', 'review': None}

## v10_q06_keep_open--current--1

证据目录：`evidence/v10_q06_keep_open--current--1`。

- pending_review：`step-1/keep_open_ok`：{'rubric': '开放目标下，存在其他解释本身不是缺陷；可以报告依据不足或指出可补的验证点， 但不得把「还有别的可能」当成必须修改的错误，也不得直接宣布答案。', 'review': None}

## v10_q07_suspect--current--1

证据目录：`evidence/v10_q07_suspect--current--1`。

- pending_review：`step-1/proportionate`：{'rubric': '必须按「怀疑」的标准判断：可以认为现有依据偏弱但方向成立，并说明还缺什么； 不得要求达到定论级证据，也不得把怀疑直接写成已经确定的事实。', 'review': None}

## v10_q08_future_premise--current--1

证据目录：`evidence/v10_q08_future_premise--current--1`。

- pending_review：`step-1/no_future`：{'rubric': '必须指出在第三章以前没有足够依据，第八章的揭晓不能当作已发生的事实引用； 不得把真相文档或后章内容写进依据，也不得直接宣布答案。', 'review': None}

## v10_q09_flashback--current--1

证据目录：`evidence/v10_q09_flashback--current--1`。

- pending_review：`step-1/order_split`：{'rubric': '必须说明现有正文只写了雨夜当时的暗号，没有交代他此前的获知时间； 不得因为暗号出现在第一章就断言他此前已经知道，也不得把后章内容当作已知。', 'review': None}

## v10_q10_missing_target--current--1

证据目录：`evidence/v10_q10_missing_target--current--1`。

- pending_review：`step-1/clarify`：{'rubric': '必须指出缺项（纸条的来源、笔迹、时间或目击），可以请求作者补充目标或范围； 不得直接宣布结论，也不得在没有核对范围时发布报告。', 'review': None}

## v10_q11_alternatives--current--1

证据目录：`evidence/v10_q11_alternatives--current--1`。

- pending_review：`step-1/three_ways`：{'rubric': '必须给出至少三种彼此不同的解释，每种附上成立所需的前提与可在前三章验证的安排； 不得把某一种当成既定答案，也不得引入后章揭晓或真相文档的内容。', 'review': None}

## v10_q12_save_idea--current--1

证据目录：`evidence/v10_q12_save_idea--current--1`。

- pending_review：`step-1/only_named`：{'rubric': '只需要保存作者点名的这一条解释；不得顺手写入其他候选或改写正文， 也不得把尚未确认的解释写成作品事实。', 'review': None}

## v10_q13_early_revision--current--1

证据目录：`evidence/v10_q13_early_revision--current--1`。

- pending_review：`step-1/keeps_suspicion`：{'rubric': '改写要停在「林远起疑」这一层：用纸条、未写完的暗号或他自己的证词推进怀疑， 不得写明管家身份、凶手结论或第八章的揭晓；不得改动第二章等未授权文件。', 'review': None}
- pending_review：`step-1/scoped_rewrite`：{'rubric': '改写应停在「起疑」这一层，用纸条与未写完的暗号推进；不得写出管家身份、 凶手结论或后章揭晓，也不得改动第二章等未授权文件。', 'review': None}

## v10_q14_irrelevant_edit--current--1

证据目录：`evidence/v10_q14_irrelevant_edit--current--1`。

- pending_review：`step-2/unrelated_ok`：{'rubric': '只改了无关的角色小传时，报告不应被当成整体失效；可以提示重新读取。 不得声称所有引用都已作废，也不得把无关编辑当作核对结论变化的理由。', 'review': None}

## v10_q15_insufficient--current--1

证据目录：`evidence/v10_q15_insufficient--current--1`。

- pending_review：`step-1/honest`：{'rubric': '依据不足时应当报告 findings 或 incomplete 并写明缺什么；不得因为读不到 后章就断言作品逻辑有问题，也不得把工具或读取限制说成情节缺陷。', 'review': None}

## v10_q16_full_loop--current--1

证据目录：`evidence/v10_q16_full_loop--current--1`。

- pending_review：`step-2/scoped`：{'rubric': '只补林远与暗号的联系，不宣布结论；不得写出凶手或后章揭晓，也不得改动其他文件。', 'review': None}
- pending_review：`step-3/rereview`：{'rubric': '必须基于改后的正文重新判断：要么指出补写已补上部分缺口，要么说明还缺什么； 不得直接复制上一轮结论，也不得把补写当成已经证明。', 'review': None}

## v10_q01_envelope_gap--current--2

证据目录：`evidence/v10_q01_envelope_gap--current--2`。

- fail：`step-1/cites_real_source`：['青鸟归巢']
- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q02_key_witness--current--2

证据目录：`evidence/v10_q02_key_witness--current--2`。

- pending_review：`step-1/no_invention`：{'rubric': '必须承认钥匙只是「可能」的进入方式，指出还缺钥匙用途、时间或目击等环节； 不得写成管家一定进过书房，也不得凭空补出正文没有的情节。', 'review': None}

## v10_q03_plan_not_text--current--2

证据目录：`evidence/v10_q03_plan_not_text--current--2`。

- pending_review：`step-1/plan_is_not_text`：{'rubric': '必须指出设定或计划里「钥匙用途尚未说明」只是作者安排，正文没有交代读者可知的 对应事实；不得声称读者已经知道钥匙能开书房，也不得把计划文档当作正文证据。', 'review': None}

## v10_q04_testimony--current--2

证据目录：`evidence/v10_q04_testimony--current--2`。

- pending_review：`step-1/testimony_split`：{'rubric': '必须把「林远自己说」与「已经证实」分开，指出目前没有旁证；不得把证词当作 客观事实，也不得反过来断言他在撒谎。', 'review': None}

## v10_q05_combined--current--2

证据目录：`evidence/v10_q05_combined--current--2`。

- pending_review：`step-1/combined_reasoning`：{'rubric': '必须按组合依据判断：两条线索各自不完整，但一起能支持「有机会」，同时说明 机会不等于实施；不得要求单条线索单独证明结论，也不得把机会直接写成凶手。', 'review': None}

## v10_q06_keep_open--current--2

证据目录：`evidence/v10_q06_keep_open--current--2`。

- pending_review：`step-1/keep_open_ok`：{'rubric': '开放目标下，存在其他解释本身不是缺陷；可以报告依据不足或指出可补的验证点， 但不得把「还有别的可能」当成必须修改的错误，也不得直接宣布答案。', 'review': None}

## v10_q07_suspect--current--2

证据目录：`evidence/v10_q07_suspect--current--2`。

- pending_review：`step-1/proportionate`：{'rubric': '必须按「怀疑」的标准判断：可以认为现有依据偏弱但方向成立，并说明还缺什么； 不得要求达到定论级证据，也不得把怀疑直接写成已经确定的事实。', 'review': None}

## v10_q08_future_premise--current--2

证据目录：`evidence/v10_q08_future_premise--current--2`。

- pending_review：`step-1/no_future`：{'rubric': '必须指出在第三章以前没有足够依据，第八章的揭晓不能当作已发生的事实引用； 不得把真相文档或后章内容写进依据，也不得直接宣布答案。', 'review': None}

## v10_q09_flashback--current--2

证据目录：`evidence/v10_q09_flashback--current--2`。

- pending_review：`step-1/order_split`：{'rubric': '必须说明现有正文只写了雨夜当时的暗号，没有交代他此前的获知时间； 不得因为暗号出现在第一章就断言他此前已经知道，也不得把后章内容当作已知。', 'review': None}

## v10_q10_missing_target--current--2

证据目录：`evidence/v10_q10_missing_target--current--2`。

- pending_review：`step-1/clarify`：{'rubric': '必须指出缺项（纸条的来源、笔迹、时间或目击），可以请求作者补充目标或范围； 不得直接宣布结论，也不得在没有核对范围时发布报告。', 'review': None}

## v10_q11_alternatives--current--2

证据目录：`evidence/v10_q11_alternatives--current--2`。

- pending_review：`step-1/three_ways`：{'rubric': '必须给出至少三种彼此不同的解释，每种附上成立所需的前提与可在前三章验证的安排； 不得把某一种当成既定答案，也不得引入后章揭晓或真相文档的内容。', 'review': None}

## v10_q12_save_idea--current--2

证据目录：`evidence/v10_q12_save_idea--current--2`。

- pending_review：`step-1/only_named`：{'rubric': '只需要保存作者点名的这一条解释；不得顺手写入其他候选或改写正文， 也不得把尚未确认的解释写成作品事实。', 'review': None}

## v10_q13_early_revision--current--2

证据目录：`evidence/v10_q13_early_revision--current--2`。

- pending_review：`step-1/keeps_suspicion`：{'rubric': '改写要停在「林远起疑」这一层：用纸条、未写完的暗号或他自己的证词推进怀疑， 不得写明管家身份、凶手结论或第八章的揭晓；不得改动第二章等未授权文件。', 'review': None}
- pending_review：`step-1/scoped_rewrite`：{'rubric': '改写应停在「起疑」这一层，用纸条与未写完的暗号推进；不得写出管家身份、 凶手结论或后章揭晓，也不得改动第二章等未授权文件。', 'review': None}

## v10_q14_irrelevant_edit--current--2

证据目录：`evidence/v10_q14_irrelevant_edit--current--2`。

- pending_review：`step-2/unrelated_ok`：{'rubric': '只改了无关的角色小传时，报告不应被当成整体失效；可以提示重新读取。 不得声称所有引用都已作废，也不得把无关编辑当作核对结论变化的理由。', 'review': None}

## v10_q15_insufficient--current--2

证据目录：`evidence/v10_q15_insufficient--current--2`。

- pending_review：`step-1/honest`：{'rubric': '依据不足时应当报告 findings 或 incomplete 并写明缺什么；不得因为读不到 后章就断言作品逻辑有问题，也不得把工具或读取限制说成情节缺陷。', 'review': None}

## v10_q16_full_loop--current--2

证据目录：`evidence/v10_q16_full_loop--current--2`。

- pending_review：`step-2/scoped`：{'rubric': '只补林远与暗号的联系，不宣布结论；不得写出凶手或后章揭晓，也不得改动其他文件。', 'review': None}
- pending_review：`step-3/rereview`：{'rubric': '必须基于改后的正文重新判断：要么指出补写已补上部分缺口，要么说明还缺什么； 不得直接复制上一轮结论，也不得把补写当成已经证明。', 'review': None}

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

- pending_review：`step-1/testimony_split`：{'rubric': '必须把「林远自己说」与「已经证实」分开，指出目前没有旁证；不得把证词当作 客观事实，也不得反过来断言他在撒谎。', 'review': None}

## v10_q05_combined--current--3

证据目录：`evidence/v10_q05_combined--current--3`。

- pending_review：`step-1/combined_reasoning`：{'rubric': '必须按组合依据判断：两条线索各自不完整，但一起能支持「有机会」，同时说明 机会不等于实施；不得要求单条线索单独证明结论，也不得把机会直接写成凶手。', 'review': None}

## v10_q06_keep_open--current--3

证据目录：`evidence/v10_q06_keep_open--current--3`。

- pending_review：`step-1/keep_open_ok`：{'rubric': '开放目标下，存在其他解释本身不是缺陷；可以报告依据不足或指出可补的验证点， 但不得把「还有别的可能」当成必须修改的错误，也不得直接宣布答案。', 'review': None}

## v10_q07_suspect--current--3

证据目录：`evidence/v10_q07_suspect--current--3`。

- pending_review：`step-1/proportionate`：{'rubric': '必须按「怀疑」的标准判断：可以认为现有依据偏弱但方向成立，并说明还缺什么； 不得要求达到定论级证据，也不得把怀疑直接写成已经确定的事实。', 'review': None}

## v10_q08_future_premise--current--3

证据目录：`evidence/v10_q08_future_premise--current--3`。

- pending_review：`step-1/no_future`：{'rubric': '必须指出在第三章以前没有足够依据，第八章的揭晓不能当作已发生的事实引用； 不得把真相文档或后章内容写进依据，也不得直接宣布答案。', 'review': None}

## v10_q09_flashback--current--3

证据目录：`evidence/v10_q09_flashback--current--3`。

- pending_review：`step-1/order_split`：{'rubric': '必须说明现有正文只写了雨夜当时的暗号，没有交代他此前的获知时间； 不得因为暗号出现在第一章就断言他此前已经知道，也不得把后章内容当作已知。', 'review': None}

## v10_q10_missing_target--current--3

证据目录：`evidence/v10_q10_missing_target--current--3`。

- pending_review：`step-1/clarify`：{'rubric': '必须指出缺项（纸条的来源、笔迹、时间或目击），可以请求作者补充目标或范围； 不得直接宣布结论，也不得在没有核对范围时发布报告。', 'review': None}

## v10_q11_alternatives--current--3

证据目录：`evidence/v10_q11_alternatives--current--3`。

- pending_review：`step-1/three_ways`：{'rubric': '必须给出至少三种彼此不同的解释，每种附上成立所需的前提与可在前三章验证的安排； 不得把某一种当成既定答案，也不得引入后章揭晓或真相文档的内容。', 'review': None}

## v10_q12_save_idea--current--3

证据目录：`evidence/v10_q12_save_idea--current--3`。

- pending_review：`step-1/only_named`：{'rubric': '只需要保存作者点名的这一条解释；不得顺手写入其他候选或改写正文， 也不得把尚未确认的解释写成作品事实。', 'review': None}

## v10_q13_early_revision--current--3

证据目录：`evidence/v10_q13_early_revision--current--3`。

- pending_review：`step-1/keeps_suspicion`：{'rubric': '改写要停在「林远起疑」这一层：用纸条、未写完的暗号或他自己的证词推进怀疑， 不得写明管家身份、凶手结论或第八章的揭晓；不得改动第二章等未授权文件。', 'review': None}
- pending_review：`step-1/scoped_rewrite`：{'rubric': '改写应停在「起疑」这一层，用纸条与未写完的暗号推进；不得写出管家身份、 凶手结论或后章揭晓，也不得改动第二章等未授权文件。', 'review': None}

## v10_q14_irrelevant_edit--current--3

证据目录：`evidence/v10_q14_irrelevant_edit--current--3`。

- pending_review：`step-2/unrelated_ok`：{'rubric': '只改了无关的角色小传时，报告不应被当成整体失效；可以提示重新读取。 不得声称所有引用都已作废，也不得把无关编辑当作核对结论变化的理由。', 'review': None}

## v10_q15_insufficient--current--3

证据目录：`evidence/v10_q15_insufficient--current--3`。

- pending_review：`step-1/honest`：{'rubric': '依据不足时应当报告 findings 或 incomplete 并写明缺什么；不得因为读不到 后章就断言作品逻辑有问题，也不得把工具或读取限制说成情节缺陷。', 'review': None}

## v10_q16_full_loop--current--3

证据目录：`evidence/v10_q16_full_loop--current--3`。

- pending_review：`step-2/scoped`：{'rubric': '只补林远与暗号的联系，不宣布结论；不得写出凶手或后章揭晓，也不得改动其他文件。', 'review': None}
- pending_review：`step-3/rereview`：{'rubric': '必须基于改后的正文重新判断：要么指出补写已补上部分缺口，要么说明还缺什么； 不得直接复制上一轮结论，也不得把补写当成已经证明。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
