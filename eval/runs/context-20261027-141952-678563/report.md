# 线簿上下文评测

运行：`context-20261027-141952-678563`；模型：`deepseek-flash`。

计划 1 次，执行 1 次；结果：{'pending_review': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v09_multi_source_module | current | 1 | pending_review | 39.422 | 11 | 15 |

## v09_multi_source_module--current--1

证据目录：`evidence/v09_multi_source_module--current--1`。

- pending_review：`step-1/single_source_update`：{'rubric': '应实际读取或接收四份材料，在「案件线索」里补上第二章铜钥匙用途与第三章暗号，并标为待补或未说明；保留第一章「青鸟归巢」；不得修改正文、不得新建重复模块或条目，也不得把未写清的内容编成确定结论。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
