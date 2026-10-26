# 线簿上下文评测

运行：`context-20261026-145943-002513`；模型：`deepseek-flash`。

计划 2 次，执行 2 次；结果：{'pending_review': 2, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v08_module_update | current | 1 | pending_review | 31.828 | 9 | 14 |
| v08_module_update | current | 2 | pending_review | 27.063 | 9 | 18 |

## v08_module_update--current--1

证据目录：`evidence/v08_module_update--current--1`。

- pending_review：`step-1/local_update`：{'rubric': '应读取并修改已装的「人物设定」，只改医生这一条；保留管家内容与未涉及信息，不新建重复模块，不改其他模块。', 'review': None}

## v08_module_update--current--2

证据目录：`evidence/v08_module_update--current--2`。

- pending_review：`step-1/local_update`：{'rubric': '应读取并修改已装的「人物设定」，只改医生这一条；保留管家内容与未涉及信息，不新建重复模块，不改其他模块。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
