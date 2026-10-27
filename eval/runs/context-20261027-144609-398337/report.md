# 线簿上下文评测

运行：`context-20261027-144609-398337`；模型：`deepseek-flash`。

计划 1 次，执行 1 次；结果：{'pending_review': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v09_overflow_and_stop | current | 1 | pending_review | 20.297 | 5 | 4 |

## v09_overflow_and_stop--current--1

证据目录：`evidence/v09_overflow_and_stop--current--1`。

- pending_review：`step-1/answer_after_retry`：{'rubric': '必须答出铜钥匙在管家手里；回答未说明、不知道或其他人，均不通过。答案应来自作品正文，而不是猜测。', 'review': None}
- pending_review：`step-3/continues_after_stop`：{'rubric': '应识别上一轮是「已停止」而不是「已完成」，并给出与停止状态一致的续写：有片段就接续，没有片段就补写并说明。不得声称上一轮已写完，也不得无视停止重复已有片段。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
