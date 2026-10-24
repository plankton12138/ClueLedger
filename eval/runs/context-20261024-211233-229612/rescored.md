# 线簿上下文评测

运行：`context-20261024-211233-229612`；模型：`deepseek-flash`。

计划 4 次，执行 4 次；结果：{'pass': 3, 'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v08_idea_discuss | current | 1 | pass | 20.0 | 4 | 11 |
| v08_idea_apply | current | 1 | pass | 31.438 | 5 | 14 |
| v08_idea_unused | current | 1 | fail | 30.797 | 6 | 12 |
| v08_module_reference | current | 1 | pass | 23.11 | 7 | 14 |

## v08_idea_discuss--current--1

证据目录：`evidence/v08_idea_discuss--current--1`。


## v08_idea_apply--current--1

证据目录：`evidence/v08_idea_apply--current--1`。


## v08_idea_unused--current--1

证据目录：`evidence/v08_idea_unused--current--1`。

- fail：`step-1/no_old_acquaintance`：{'path': '正文/0006 旧识.md', 'expected': '旧识'}
- pending_review：`step-1/no_leak`：{'rubric': '不得凭空写出老板娘认识死者或等价关系；本章只做观察与铺垫。不得修改其他文件。', 'review': None}

## v08_module_reference--current--1

证据目录：`evidence/v08_module_reference--current--1`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
