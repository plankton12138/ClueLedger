# 线簿上下文评测

运行：`context-20261028-132947-165160`；模型：`deepseek-flash`。

计划 3 次，执行 3 次；结果：{'fail': 3, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v10_q01_envelope_gap | presentation_refs | 1 | fail | 87.485 | 12 | 16 |
| v10_q02_key_witness | presentation_refs | 1 | fail | 147.14 | 22 | 30 |
| v10_q06_keep_open | presentation_refs | 1 | fail | 121.235 | 20 | 29 |

## v10_q01_envelope_gap--presentation_refs--1

证据目录：`evidence/v10_q01_envelope_gap--presentation_refs--1`。

- fail：`step-1/terminal`：{'seq': 8178, 'at': '2026-10-28T13:31:15.996+08:00', 'event_version': 1, 'event_id': 'c_6975113297a1:8178', 'conversation_id': 'c_6975113297a1', 'type': 'run_finished', 'turn_id': 't_311e6694', 'status': 'partial', 'recording': 'no_change', 'changed_paths': [], 'commit': None, 'note': ''}
- fail：`step-1/report_saved`：本轮没有发布线索核对报告
- fail：`step-1/cites_real_source`：本轮没有发布线索核对报告
- pending_review：`step-1/gap_named`：{'rubric': '必须指出「知道暗号内容」与「提前接触过原件」之间的前提缺口（还可能从别处听到、 有人转述等），并给出可验证的补写方向；不得直接断言林远是凶手，也不得把设定里的 作者计划或真相文档当作读者已经看到的正文。', 'review': None}

## v10_q02_key_witness--presentation_refs--1

证据目录：`evidence/v10_q02_key_witness--presentation_refs--1`。

- fail：`step-1/terminal`：{'seq': 17210, 'at': '2026-10-28T13:33:44.326+08:00', 'event_version': 1, 'event_id': 'c_dc4e07d03f80:17210', 'conversation_id': 'c_dc4e07d03f80', 'type': 'run_finished', 'turn_id': 't_357d1520', 'status': 'partial', 'recording': 'no_change', 'changed_paths': [], 'commit': None, 'note': ''}
- fail：`step-1/report_saved`：本轮没有发布线索核对报告
- fail：`step-1/cites_key`：本轮没有发布线索核对报告
- pending_review：`step-1/no_invention`：{'rubric': '必须承认钥匙只是「可能」的进入方式，指出还缺钥匙用途、时间或目击等环节； 不得写成管家一定进过书房，也不得凭空补出正文没有的情节。', 'review': None}

## v10_q06_keep_open--presentation_refs--1

证据目录：`evidence/v10_q06_keep_open--presentation_refs--1`。

- fail：`step-1/terminal`：{'seq': 12937, 'at': '2026-10-28T13:35:47.300+08:00', 'event_version': 1, 'event_id': 'c_42a3ea41d5bf:12937', 'conversation_id': 'c_42a3ea41d5bf', 'type': 'run_finished', 'turn_id': 't_da614abd', 'status': 'partial', 'recording': 'no_change', 'changed_paths': [], 'commit': None, 'note': ''}
- pending_review：`step-1/keep_open_ok`：{'rubric': '开放目标下，存在其他解释本身不是缺陷；可以报告依据不足或指出可补的验证点， 但不得把「还有别的可能」当成必须修改的错误，也不得直接宣布答案。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
