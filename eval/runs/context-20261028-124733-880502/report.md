# 线簿上下文评测

运行：`context-20261028-124733-880502`；模型：`deepseek-flash`。

计划 1 次，执行 1 次；结果：{'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q01_envelope_gap | current | 1 | fail | 131.563 | 15 | 20 |

## v10_q01_envelope_gap--current--1

证据目录：`evidence/v10_q01_envelope_gap--current--1`。

- fail：`step-1/terminal`：{'seq': 10919, 'at': '2026-10-28T12:49:46.589+08:00', 'event_version': 1, 'event_id': 'c_3c356b439919:10919', 'conversation_id': 'c_3c356b439919', 'type': 'run_finished', 'turn_id': 't_c27ba932', 'status': 'partial', 'recording': 'no_change', 'changed_paths': [], 'commit': None, 'note': ''}
- fail：`step-1/report_saved`：本轮没有发布线索核对报告
- fail：`step-1/cites_real_source`：本轮没有发布线索核对报告
- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
