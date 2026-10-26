# 线簿上下文评测

运行：`context-20261026-150147-944287`；模型：`deepseek-flash`。

计划 6 次，执行 6 次；结果：{'fail': 2, 'pass': 4, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v08_module_autocreate | current | 1 | fail | 91.734 | 7 | 14 |
| v08_module_update | current | 1 | pass | 35.219 | 9 | 20 |
| v08_module_discuss | current | 1 | pass | 23.828 | 5 | 12 |
| v08_module_autocreate | current | 2 | fail | 117.344 | 8 | 17 |
| v08_module_update | current | 2 | pass | 24.281 | 11 | 21 |
| v08_module_discuss | current | 2 | pass | 19.938 | 4 | 9 |

## v08_module_autocreate--current--1

证据目录：`evidence/v08_module_autocreate--current--1`。

- fail：`step-1/no_method`：{'value': '作案手法', 'modules': 2}

## v08_module_update--current--1

证据目录：`evidence/v08_module_update--current--1`。


## v08_module_discuss--current--1

证据目录：`evidence/v08_module_discuss--current--1`。


## v08_module_autocreate--current--2

证据目录：`evidence/v08_module_autocreate--current--2`。

- fail：`step-1/no_method`：{'value': '作案手法', 'modules': 1}
- fail：`step-1/self_organized`：{'verdict': 'fail', 'reviewer': 'Codex', 'reason': '只应用了人物小传一份模块，聚集缘由没有保存。模型分开准备两份变更集，应用第一份后本轮只读；再次准备被拒绝，最终回复承认第二份未保存。未满足同轮完成至少两份实际内容的要求。', 'evidence': 'evidence/v08_module_autocreate--current--2/step-1.json：prepare_module_changes 11205/11208；module_changes_applied 11527 只有人物小传；prepare_module_changes 11644 在收尾后重试；assistant_message 12358 明示聚集缘由未保存，assembly 仅一份绑定。'}

## v08_module_update--current--2

证据目录：`evidence/v08_module_update--current--2`。


## v08_module_discuss--current--2

证据目录：`evidence/v08_module_discuss--current--2`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
