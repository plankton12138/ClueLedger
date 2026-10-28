# 线簿上下文评测

运行：`context-20261028-144710-165601`；模型：`deepseek-flash`。

计划 48 次，执行 48 次；结果：{'pass': 41, 'fail': 7, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q01_envelope_gap | current | 1 | pass | 50.687 | 7 | 11 |
| v10_q02_key_witness | current | 1 | pass | 30.219 | 5 | 8 |
| v10_q03_plan_not_text | current | 1 | pass | 34.796 | 6 | 9 |
| v10_q04_testimony | current | 1 | pass | 23.359 | 5 | 9 |
| v10_q05_combined | current | 1 | pass | 29.531 | 4 | 4 |
| v10_q06_keep_open | current | 1 | pass | 25.734 | 5 | 7 |
| v10_q07_suspect | current | 1 | pass | 36.922 | 6 | 12 |
| v10_q08_future_premise | current | 1 | pass | 35.078 | 5 | 6 |
| v10_q09_flashback | current | 1 | pass | 26.593 | 5 | 8 |
| v10_q10_missing_target | current | 1 | pass | 26.172 | 3 | 8 |
| v10_q11_alternatives | current | 1 | fail | 33.062 | 3 | 8 |
| v10_q12_save_idea | current | 1 | pass | 19.078 | 5 | 5 |
| v10_q13_early_revision | current | 1 | pass | 14.969 | 5 | 6 |
| v10_q14_irrelevant_edit | current | 1 | pass | 53.015 | 9 | 12 |
| v10_q15_insufficient | current | 1 | pass | 33.062 | 6 | 10 |
| v10_q16_full_loop | current | 1 | pass | 76.141 | 12 | 17 |
| v10_q01_envelope_gap | current | 2 | fail | 160.219 | 16 | 17 |
| v10_q02_key_witness | current | 2 | pass | 32.016 | 5 | 7 |
| v10_q03_plan_not_text | current | 2 | pass | 21.797 | 6 | 10 |
| v10_q04_testimony | current | 2 | fail | 23.766 | 4 | 5 |
| v10_q05_combined | current | 2 | pass | 26.422 | 4 | 6 |
| v10_q06_keep_open | current | 2 | pass | 36.25 | 6 | 12 |
| v10_q07_suspect | current | 2 | fail | 40.063 | 6 | 9 |
| v10_q08_future_premise | current | 2 | pass | 43.344 | 7 | 11 |
| v10_q09_flashback | current | 2 | pass | 29.468 | 5 | 9 |
| v10_q10_missing_target | current | 2 | pass | 19.078 | 3 | 6 |
| v10_q11_alternatives | current | 2 | fail | 25.891 | 3 | 7 |
| v10_q12_save_idea | current | 2 | pass | 17.64 | 5 | 6 |
| v10_q13_early_revision | current | 2 | pass | 15.766 | 5 | 4 |
| v10_q14_irrelevant_edit | current | 2 | pass | 48.312 | 9 | 13 |
| v10_q15_insufficient | current | 2 | pass | 35.046 | 7 | 10 |
| v10_q16_full_loop | current | 2 | pass | 78.094 | 13 | 12 |
| v10_q01_envelope_gap | current | 3 | pass | 37.844 | 7 | 10 |
| v10_q02_key_witness | current | 3 | pass | 36.765 | 5 | 8 |
| v10_q03_plan_not_text | current | 3 | pass | 34.062 | 6 | 8 |
| v10_q04_testimony | current | 3 | pass | 24.703 | 3 | 3 |
| v10_q05_combined | current | 3 | pass | 40.094 | 5 | 5 |
| v10_q06_keep_open | current | 3 | pass | 24.828 | 5 | 7 |
| v10_q07_suspect | current | 3 | fail | 36.89 | 5 | 11 |
| v10_q08_future_premise | current | 3 | pass | 33.047 | 4 | 4 |
| v10_q09_flashback | current | 3 | pass | 51.484 | 9 | 16 |
| v10_q10_missing_target | current | 3 | pass | 20.219 | 3 | 5 |
| v10_q11_alternatives | current | 3 | fail | 27.36 | 3 | 4 |
| v10_q12_save_idea | current | 3 | pass | 20.875 | 6 | 6 |
| v10_q13_early_revision | current | 3 | pass | 30.953 | 5 | 7 |
| v10_q14_irrelevant_edit | current | 3 | pass | 46.515 | 10 | 15 |
| v10_q15_insufficient | current | 3 | pass | 40.14 | 7 | 10 |
| v10_q16_full_loop | current | 3 | pass | 64.407 | 12 | 15 |

## v10_q01_envelope_gap--current--1

证据目录：`evidence/v10_q01_envelope_gap--current--1`。


## v10_q02_key_witness--current--1

证据目录：`evidence/v10_q02_key_witness--current--1`。


## v10_q03_plan_not_text--current--1

证据目录：`evidence/v10_q03_plan_not_text--current--1`。


## v10_q04_testimony--current--1

证据目录：`evidence/v10_q04_testimony--current--1`。


## v10_q05_combined--current--1

证据目录：`evidence/v10_q05_combined--current--1`。


## v10_q06_keep_open--current--1

证据目录：`evidence/v10_q06_keep_open--current--1`。


## v10_q07_suspect--current--1

证据目录：`evidence/v10_q07_suspect--current--1`。


## v10_q08_future_premise--current--1

证据目录：`evidence/v10_q08_future_premise--current--1`。


## v10_q09_flashback--current--1

证据目录：`evidence/v10_q09_flashback--current--1`。


## v10_q10_missing_target--current--1

证据目录：`evidence/v10_q10_missing_target--current--1`。


## v10_q11_alternatives--current--1

证据目录：`evidence/v10_q11_alternatives--current--1`。

- fail：`step-1/three_ways`：{'verdict': 'fail', 'reviewer': 'Codex 代理复核（非独立人工签收）', 'reason': '原请求没有给出评分侧的禁用来源条件；输出读取并使用真相和后章揭晓，按原评分标准记失败。', 'evidence': 'eval/runs/v10-final-20261028/evidence/v10_q11_alternatives--current--1/step-1.json'}

## v10_q12_save_idea--current--1

证据目录：`evidence/v10_q12_save_idea--current--1`。


## v10_q13_early_revision--current--1

证据目录：`evidence/v10_q13_early_revision--current--1`。


## v10_q14_irrelevant_edit--current--1

证据目录：`evidence/v10_q14_irrelevant_edit--current--1`。


## v10_q15_insufficient--current--1

证据目录：`evidence/v10_q15_insufficient--current--1`。


## v10_q16_full_loop--current--1

证据目录：`evidence/v10_q16_full_loop--current--1`。


## v10_q01_envelope_gap--current--2

证据目录：`evidence/v10_q01_envelope_gap--current--2`。

- fail：`step-1/cites_real_source`：['青鸟归巢']
- fail：`step-1/gap_named`：{'verdict': 'fail', 'reviewer': 'Codex 代理复核（非独立人工签收）', 'reason': '实际落库报告是模型用于试坐标的探针占位内容，后续完整报告因同键负载变化被拒；公开解释不能代替有效报告。', 'evidence': 'eval/runs/v10-final-20261028/evidence/v10_q01_envelope_gap--current--2/step-1.json'}

## v10_q02_key_witness--current--2

证据目录：`evidence/v10_q02_key_witness--current--2`。


## v10_q03_plan_not_text--current--2

证据目录：`evidence/v10_q03_plan_not_text--current--2`。


## v10_q04_testimony--current--2

证据目录：`evidence/v10_q04_testimony--current--2`。

- fail：`step-1/testimony_split`：{'verdict': 'fail', 'reviewer': 'Codex 代理复核（非独立人工签收）', 'reason': '将尚无人证实误分类为与目标相反的 supported 反证，混淆缺少支撑与相反事实。', 'evidence': 'eval/runs/v10-final-20261028/evidence/v10_q04_testimony--current--2/step-1.json'}

## v10_q05_combined--current--2

证据目录：`evidence/v10_q05_combined--current--2`。


## v10_q06_keep_open--current--2

证据目录：`evidence/v10_q06_keep_open--current--2`。


## v10_q07_suspect--current--2

证据目录：`evidence/v10_q07_suspect--current--2`。

- fail：`step-1/proportionate`：{'verdict': 'fail', 'reviewer': 'Codex 代理复核（非独立人工签收）', 'reason': '对怀疑目标提出过严的空间条件，并将无明确同一时点的天气差异列为反证，判断不与目标强度相称。', 'evidence': 'eval/runs/v10-final-20261028/evidence/v10_q07_suspect--current--2/step-1.json'}

## v10_q08_future_premise--current--2

证据目录：`evidence/v10_q08_future_premise--current--2`。


## v10_q09_flashback--current--2

证据目录：`evidence/v10_q09_flashback--current--2`。


## v10_q10_missing_target--current--2

证据目录：`evidence/v10_q10_missing_target--current--2`。


## v10_q11_alternatives--current--2

证据目录：`evidence/v10_q11_alternatives--current--2`。

- fail：`step-1/three_ways`：{'verdict': 'fail', 'reviewer': 'Codex 代理复核（非独立人工签收）', 'reason': '原请求没有给出评分侧的禁用来源条件；输出按第八章真相排列方案，按原评分标准记失败。', 'evidence': 'eval/runs/v10-final-20261028/evidence/v10_q11_alternatives--current--2/step-1.json'}

## v10_q12_save_idea--current--2

证据目录：`evidence/v10_q12_save_idea--current--2`。


## v10_q13_early_revision--current--2

证据目录：`evidence/v10_q13_early_revision--current--2`。


## v10_q14_irrelevant_edit--current--2

证据目录：`evidence/v10_q14_irrelevant_edit--current--2`。


## v10_q15_insufficient--current--2

证据目录：`evidence/v10_q15_insufficient--current--2`。


## v10_q16_full_loop--current--2

证据目录：`evidence/v10_q16_full_loop--current--2`。


## v10_q01_envelope_gap--current--3

证据目录：`evidence/v10_q01_envelope_gap--current--3`。


## v10_q02_key_witness--current--3

证据目录：`evidence/v10_q02_key_witness--current--3`。


## v10_q03_plan_not_text--current--3

证据目录：`evidence/v10_q03_plan_not_text--current--3`。


## v10_q04_testimony--current--3

证据目录：`evidence/v10_q04_testimony--current--3`。


## v10_q05_combined--current--3

证据目录：`evidence/v10_q05_combined--current--3`。


## v10_q06_keep_open--current--3

证据目录：`evidence/v10_q06_keep_open--current--3`。


## v10_q07_suspect--current--3

证据目录：`evidence/v10_q07_suspect--current--3`。

- fail：`step-1/proportionate`：{'verdict': 'fail', 'reviewer': 'Codex 代理复核（非独立人工签收）', 'reason': '忽略章标题与推门动作的叙述上下文，以没有跨门槛或停留描写否定起疑方向，要求过严。', 'evidence': 'eval/runs/v10-final-20261028/evidence/v10_q07_suspect--current--3/step-1.json'}

## v10_q08_future_premise--current--3

证据目录：`evidence/v10_q08_future_premise--current--3`。


## v10_q09_flashback--current--3

证据目录：`evidence/v10_q09_flashback--current--3`。


## v10_q10_missing_target--current--3

证据目录：`evidence/v10_q10_missing_target--current--3`。


## v10_q11_alternatives--current--3

证据目录：`evidence/v10_q11_alternatives--current--3`。

- fail：`step-1/three_ways`：{'verdict': 'fail', 'reviewer': 'Codex 代理复核（非独立人工签收）', 'reason': '原请求没有给出评分侧的禁用来源条件；输出引入后章真相及明示章号，按原评分标准记失败。', 'evidence': 'eval/runs/v10-final-20261028/evidence/v10_q11_alternatives--current--3/step-1.json'}

## v10_q12_save_idea--current--3

证据目录：`evidence/v10_q12_save_idea--current--3`。


## v10_q13_early_revision--current--3

证据目录：`evidence/v10_q13_early_revision--current--3`。


## v10_q14_irrelevant_edit--current--3

证据目录：`evidence/v10_q14_irrelevant_edit--current--3`。


## v10_q15_insufficient--current--3

证据目录：`evidence/v10_q15_insufficient--current--3`。


## v10_q16_full_loop--current--3

证据目录：`evidence/v10_q16_full_loop--current--3`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
