# 线簿上下文评测

运行：`context-20261026-133756-343476`；模型：`deepseek-flash`。

计划 1 次，执行 1 次；结果：{'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v08_module_update | current | 1 | fail | 98.047 | 11 | 21 |

## v08_module_update--current--1

证据目录：`evidence/v08_module_update--current--1`。

- fail：`step-1/allowed_final_changes`：['正文/0008 揭晓.md', '设定集/真相.md', '设定集/角色小传/管家.md']
- fail：`step-1/trajectory_scope`：[{'seq': 12581, 'at': '2026-10-26T13:39:25.643+08:00', 'event_version': 1, 'event_id': 'c_2aea45b66570:12581', 'conversation_id': 'c_2aea45b66570', 'type': 'file_changed', 'turn_id': 't_d503b85b', 'parent_operation_id': 'd9a3330cf8fc4ec7b507e8504f6a511d', 'tool_call_id': 'call_00_u10IXJYfpdSwxsvneVjE8046', 'action': 'update', 'path': '设定集/真相.md', 'before_digest': '8cd15fb7e3be160fcf6423824c013023f9e09a9f78534d129c4f3f37b21130ae', 'after_digest': '88c51f423e141488d1acc09a857653a2df8506444ac60ff05fdf174466d9d1b0', 'changed': True, 'start': 6, 'end': 32, 'size': 89}, {'seq': 12582, 'at': '2026-10-26T13:39:25.667+08:00', 'event_version': 1, 'event_id': 'c_2aea45b66570:12582', 'conversation_id': 'c_2aea45b66570', 'type': 'file_changed', 'turn_id': 't_d503b85b', 'parent_operation_id': 'd9a3330cf8fc4ec7b507e8504f6a511d', 'tool_call_id': 'call_01_r55rrWx9G31zYx3eftXi5165', 'action': 'update', 'path': '正文/0008 揭晓.md', 'before_digest': 'd52fc550ab935fa627757cb671290de6ac72c385a35787f21bf84a02ad8a427e', 'after_digest': '881acc164b67f500fa452b099d09f2824a2c01ba22293cd2198c4afea87108d3', 'changed': True, 'start': 6, 'end': 27, 'size': 74}, {'seq': 12583, 'at': '2026-10-26T13:39:25.691+08:00', 'event_version': 1, 'event_id': 'c_2aea45b66570:12583', 'conversation_id': 'c_2aea45b66570', 'type': 'file_changed', 'turn_id': 't_d503b85b', 'parent_operation_id': 'd9a3330cf8fc4ec7b507e8504f6a511d', 'tool_call_id': 'call_02_g26XhghJ0sWpLolYyjr08365', 'action': 'update', 'path': '设定集/角色小传/管家.md', 'before_digest': '8daa63e8d0a22044826b25c63fa8cf197c617dc7d253529d16e7ca639da0adc4', 'after_digest': '70f45a6bd535cfad446f43f73a0b7b2a73dcbc31aa5354acaa1a25e2ce6beec9', 'changed': True, 'start': 21, 'end': 40, 'size': 107}]
- fail：`step-1/decision_scope`：[{'seq': 12587, 'at': '2026-10-26T13:39:25.447+08:00', 'event_version': 1, 'event_id': 'c_2aea45b66570:12587', 'conversation_id': 'c_2aea45b66570', 'type': 'tool_started', 'turn_id': 't_d503b85b', 'tool_call_id': 'call_00_u10IXJYfpdSwxsvneVjE8046', 'tool': 'edit_file', 'args': {'path': '设定集/真相.md'}, 'input_detail': 'a1c1d7d480644a1fa2971da1a2c6064c', 'parent_operation_id': 'd9a3330cf8fc4ec7b507e8504f6a511d'}, {'seq': 12590, 'at': '2026-10-26T13:39:25.643+08:00', 'event_version': 1, 'event_id': 'c_2aea45b66570:12590', 'conversation_id': 'c_2aea45b66570', 'type': 'tool_started', 'turn_id': 't_d503b85b', 'tool_call_id': 'call_01_r55rrWx9G31zYx3eftXi5165', 'tool': 'edit_file', 'args': {'path': '正文/0008 揭晓.md'}, 'input_detail': 'fdecb5ca4f7544ee9260ac862b53c63d', 'parent_operation_id': 'd9a3330cf8fc4ec7b507e8504f6a511d'}, {'seq': 12592, 'at': '2026-10-26T13:39:25.667+08:00', 'event_version': 1, 'event_id': 'c_2aea45b66570:12592', 'conversation_id': 'c_2aea45b66570', 'type': 'tool_started', 'turn_id': 't_d503b85b', 'tool_call_id': 'call_02_g26XhghJ0sWpLolYyjr08365', 'tool': 'edit_file', 'args': {'path': '设定集/角色小传/管家.md'}, 'input_detail': 'ac93ad401a6d40b19d7209d64a077ad2', 'parent_operation_id': 'd9a3330cf8fc4ec7b507e8504f6a511d'}]
- pending_review：`step-1/local_update`：{'rubric': '应读取并修改已装的「人物设定」，只改医生这一条；保留管家内容与未涉及信息，不新建重复模块，不改其他模块。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
