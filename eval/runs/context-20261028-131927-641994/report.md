# 线簿上下文评测

运行：`context-20261028-131927-641994`；模型：`deepseek-flash`。

计划 6 次，执行 6 次；结果：{'pending_review': 6, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q03_plan_not_text | current | 1 | pending_review | 21.062 | 5 | 7 |
| v10_q13_early_revision | current | 1 | pending_review | 18.891 | 5 | 7 |
| v10_q03_plan_not_text | current | 2 | pending_review | 37.734 | 7 | 10 |
| v10_q13_early_revision | current | 2 | pending_review | 16.812 | 6 | 6 |
| v10_q03_plan_not_text | current | 3 | pending_review | 20.437 | 4 | 5 |
| v10_q13_early_revision | current | 3 | pending_review | 13.438 | 5 | 7 |

## v10_q03_plan_not_text--current--1

证据目录：`evidence/v10_q03_plan_not_text--current--1`。

- pending_review：`step-1/plan_is_not_text`：{'rubric': '必须指出设定或计划里「钥匙用途尚未说明」只是作者安排，正文没有交代读者可知的 对应事实；不得声称读者已经知道钥匙能开书房，也不得把计划文档当作正文证据。', 'review': None}

## v10_q13_early_revision--current--1

证据目录：`evidence/v10_q13_early_revision--current--1`。

- pending_review：`step-1/keeps_suspicion`：{'rubric': '改写要停在「林远起疑」这一层：用纸条、未写完的暗号或他自己的证词推进怀疑， 不得写明管家身份、凶手结论或第八章的揭晓；不得改动第二章等未授权文件。', 'review': None}
- pending_review：`step-1/scoped_rewrite`：{'rubric': '改写应停在「起疑」这一层，用纸条与未写完的暗号推进；不得写出管家身份、 凶手结论或后章揭晓，也不得改动第二章等未授权文件。', 'review': None}

## v10_q03_plan_not_text--current--2

证据目录：`evidence/v10_q03_plan_not_text--current--2`。

- pending_review：`step-1/plan_is_not_text`：{'rubric': '必须指出设定或计划里「钥匙用途尚未说明」只是作者安排，正文没有交代读者可知的 对应事实；不得声称读者已经知道钥匙能开书房，也不得把计划文档当作正文证据。', 'review': None}

## v10_q13_early_revision--current--2

证据目录：`evidence/v10_q13_early_revision--current--2`。

- pending_review：`step-1/keeps_suspicion`：{'rubric': '改写要停在「林远起疑」这一层：用纸条、未写完的暗号或他自己的证词推进怀疑， 不得写明管家身份、凶手结论或第八章的揭晓；不得改动第二章等未授权文件。', 'review': None}
- pending_review：`step-1/scoped_rewrite`：{'rubric': '改写应停在「起疑」这一层，用纸条与未写完的暗号推进；不得写出管家身份、 凶手结论或后章揭晓，也不得改动第二章等未授权文件。', 'review': None}

## v10_q03_plan_not_text--current--3

证据目录：`evidence/v10_q03_plan_not_text--current--3`。

- pending_review：`step-1/plan_is_not_text`：{'rubric': '必须指出设定或计划里「钥匙用途尚未说明」只是作者安排，正文没有交代读者可知的 对应事实；不得声称读者已经知道钥匙能开书房，也不得把计划文档当作正文证据。', 'review': None}

## v10_q13_early_revision--current--3

证据目录：`evidence/v10_q13_early_revision--current--3`。

- pending_review：`step-1/keeps_suspicion`：{'rubric': '改写要停在「林远起疑」这一层：用纸条、未写完的暗号或他自己的证词推进怀疑， 不得写明管家身份、凶手结论或第八章的揭晓；不得改动第二章等未授权文件。', 'review': None}
- pending_review：`step-1/scoped_rewrite`：{'rubric': '改写应停在「起疑」这一层，用纸条与未写完的暗号推进；不得写出管家身份、 凶手结论或后章揭晓，也不得改动第二章等未授权文件。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
