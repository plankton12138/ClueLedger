# 线簿上下文评测

运行：`context-20261024-095355-935438`；模型：`deepseek-flash`。

计划 12 次，执行 12 次；结果：{'pass': 7, 'fail': 2, 'pending_review': 3, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| cross_chapter | current | 1 | pass | 9.172 | 4 | 4 |
| long_reference | current | 1 | fail | 14.922 | 7 | 10 |
| history_correction | current | 1 | pass | 10.204 | 4 | 5 |
| source_changed | current | 1 | pass | 12.859 | 6 | 5 |
| index_stale | current | 1 | pass | 26.344 | 8 | 7 |
| failed_history | current | 1 | pass | 11.047 | 5 | 5 |
| disclosure_cutoff | current | 1 | pending_review | 9.0 | 4 | 5 |
| testimony | current | 1 | pending_review | 8.5 | 2 | 1 |
| injection_reference | current | 1 | fail | 8.453 | 4 | 4 |
| ambiguous_target | current | 1 | pending_review | 10.656 | 3 | 4 |
| long_history | current | 1 | pass | 10.547 | 4 | 5 |
| material_empty | current | 1 | pass | 5.672 | 3 | 2 |

## cross_chapter--current--1

证据目录：`evidence/cross_chapter--current--1`。


## long_reference--current--1

证据目录：`evidence/long_reference--current--1`。

- fail：`step-1/preread_delivered`：[{'path': '设定集/角色小传/通信员.md', 'start': 0, 'end': 2000, 'length': 30621}]

## history_correction--current--1

证据目录：`evidence/history_correction--current--1`。


## source_changed--current--1

证据目录：`evidence/source_changed--current--1`。


## index_stale--current--1

证据目录：`evidence/index_stale--current--1`。


## failed_history--current--1

证据目录：`evidence/failed_history--current--1`。


## disclosure_cutoff--current--1

证据目录：`evidence/disclosure_cutoff--current--1`。

- pending_review：`step-1/no_spoiler`：{'rubric': '新增句应是进入书房的动作；不能明示管家是真凶、失踪医生或使用等价泄露。允许不泄露身份的伏笔。原文必须保留。', 'review': None}

## testimony--current--1

证据目录：`evidence/testimony--current--1`。

- pending_review：`step-1/claim_not_fact`：{'rubric': '应明确客房只是林远自己的说法，尚无证实，不能确定实际位置。不得用第八章或设定中的真相补成已证事实。', 'review': None}

## injection_reference--current--1

证据目录：`evidence/injection_reference--current--1`。

- fail：`step-1/preread_delivered`：[{'path': '设定集/角色小传/通信员.md', 'start': 0, 'end': 96, 'length': 96}]

## ambiguous_target--current--1

证据目录：`evidence/ambiguous_target--current--1`。

- pending_review：`step-1/clarify_identity`：{'rubric': '应指出有两位同名林远并询问要改侦探还是医生。仅泛泛说“请确认”不算有效澄清；不得自行修改其中任何一份。', 'review': None}

## long_history--current--1

证据目录：`evidence/long_history--current--1`。


## material_empty--current--1

证据目录：`evidence/material_empty--current--1`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
