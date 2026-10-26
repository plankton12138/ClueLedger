# 线簿上下文评测

运行：`context-20261026-155023-402270`；模型：`deepseek-flash`。

计划 6 次，执行 6 次；结果：{'pass': 5, 'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v08_module_autocreate | current | 1 | pass | 178.281 | 9 | 13 |
| v08_module_update | current | 1 | pass | 23.766 | 8 | 13 |
| v08_module_discuss | current | 1 | pass | 21.75 | 3 | 9 |
| v08_module_autocreate | current | 2 | fail | 111.859 | 6 | 13 |
| v08_module_update | current | 2 | pass | 24.938 | 10 | 19 |
| v08_module_discuss | current | 2 | pass | 19.344 | 3 | 7 |

## v08_module_autocreate--current--1

证据目录：`evidence/v08_module_autocreate--current--1`。


## v08_module_update--current--1

证据目录：`evidence/v08_module_update--current--1`。


## v08_module_discuss--current--1

证据目录：`evidence/v08_module_discuss--current--1`。


## v08_module_autocreate--current--2

证据目录：`evidence/v08_module_autocreate--current--2`。

- fail：`step-1/self_organized`：{'verdict': 'fail', 'reviewer': 'Codex', 'reason': '按本次运行冻结的旧契约要求至少两份模块，实际将人物和聚集原因合并为一份完整模块，因此此项保留失败。作者随后明确按内容完整性验收、允许合并，已在新运行使用新契约验证，不改写本次旧契约结果。该次未修改旧目录。', 'evidence': 'evidence/v08_module_autocreate--current--2/case.json 的 self_organized 仍要求至少两份；step-1.json 的 module_changes_applied 10575 与 assembly 仅一份绑定，完整正文在 modules 中；run_finished 11239 为 completed，diffs 为空。'}

## v08_module_update--current--2

证据目录：`evidence/v08_module_update--current--2`。


## v08_module_discuss--current--2

证据目录：`evidence/v08_module_discuss--current--2`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
