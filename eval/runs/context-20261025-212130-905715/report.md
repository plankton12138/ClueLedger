# 线簿上下文评测

运行：`context-20261025-212130-905715`；模型：`deepseek-flash`。

计划 4 次，执行 4 次；结果：{'pending_review': 4, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| overview_mixed_module | current | 1 | pending_review | 15.125 | 5 | 5 |
| overview_negation_and_ambiguity | current | 1 | pending_review | 12.578 | 4 | 3 |
| overview_testimony_and_future | current | 1 | pending_review | 48.11 | 12 | 9 |
| overview_empty_discovery | current | 1 | pending_review | 3.047 | 3 | 2 |

## overview_mixed_module--current--1

证据目录：`evidence/overview_mixed_module--current--1`。

- pending_review：`step-1/mixed`：{'rubric': '查看 derived.overview，人物包含林远和𠮷且引用不同；铜钥匙是线索；姐弟关系有模块原文依据；没有虚构章节或正文已知状态。', 'review': None}

## overview_negation_and_ambiguity--current--1

证据目录：`evidence/overview_negation_and_ambiguity--current--1`。

- pending_review：`step-1/no_invented_relation`：{'rubric': '查看派生结果，两个林远不能合并；不得生成父子等肯定人物关系；可以没有任何关系边。否定和未采用假设不得转为事实。', 'review': None}

## overview_testimony_and_future--current--1

证据目录：`evidence/overview_testimony_and_future--current--1`。

- pending_review：`step-1/observation_scope`：{'rubric': '查看 story-index 与 overview，模块应识别人物林远和线索铜钥匙，不能因为含有计划便返回空发现。第1章林远的话保留证词或其他非客观模式；不得把‘没见过’写成已证实事实。第2章不在本轮章节范围。钥匙来自林远是作者未来计划，不能成为第1章正文观察。', 'review': None}

## overview_empty_discovery--current--1

证据目录：`evidence/overview_empty_discovery--current--1`。

- pending_review：`step-1/empty_not_invented`：{'rubric': 'derived.overview 对模块保存 empty 覆盖，没有凭空生成角色、人物关系或线索，作者内容没有变化。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
