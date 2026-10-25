# 线簿上下文评测

运行：`context-20261025-211828-108744`；模型：`deepseek-flash`。

计划 4 次，执行 4 次；结果：{'pass': 3, 'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| overview_mixed_module | current | 1 | pass | 19.328 | 4 | 3 |
| overview_negation_and_ambiguity | current | 1 | pass | 20.375 | 4 | 4 |
| overview_testimony_and_future | current | 1 | fail | 16.828 | 10 | 7 |
| overview_empty_discovery | current | 1 | pass | 3.219 | 3 | 2 |

## overview_mixed_module--current--1

证据目录：`evidence/overview_mixed_module--current--1`。


## overview_negation_and_ambiguity--current--1

证据目录：`evidence/overview_negation_and_ambiguity--current--1`。


## overview_testimony_and_future--current--1

证据目录：`evidence/overview_testimony_and_future--current--1`。

- fail：`step-1/observation_scope`：{'verdict': 'fail', 'reviewer': 'Codex 原文与公开派生结果复核', 'reason': '正文证词和截止章正确，但模块错误返回 empty，遗漏明确的人物林远和铜钥匙；已据此修订提取规则。', 'evidence': 'evidence/overview_testimony_and_future--current--1/step-1.json: derived, run_finished, diffs；所有作品 diff 为空。'}

## overview_empty_discovery--current--1

证据目录：`evidence/overview_empty_discovery--current--1`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
