# 线簿上下文评测

运行：`context-20261024-103553-713850`；模型：`deepseek-flash`。

计划 3 次，执行 3 次；结果：{'pass': 3, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| cross_chapter | current | 1 | pass | 6.469 | 4 | 4 |
| source_changed | current | 1 | pass | 12.89 | 7 | 6 |
| material_empty | current | 1 | pass | 5.875 | 3 | 2 |

## cross_chapter--current--1

证据目录：`evidence/cross_chapter--current--1`。


## source_changed--current--1

证据目录：`evidence/source_changed--current--1`。


## material_empty--current--1

证据目录：`evidence/material_empty--current--1`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
