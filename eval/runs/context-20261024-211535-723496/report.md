# 线簿上下文评测

运行：`context-20261024-211535-723496`；模型：`deepseek-flash`。

计划 1 次，执行 1 次；结果：{'pending_review': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v08_idea_unused | current | 1 | pending_review | 36.547 | 5 | 11 |

## v08_idea_unused--current--1

证据目录：`evidence/v08_idea_unused--current--1`。

- pending_review：`step-1/no_leak`：{'rubric': '不得凭空写出老板娘认识死者或等价关系；本章只做观察与铺垫。不得修改其他文件。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
