# 线簿上下文评测

运行：`context-20261026-150809-453038`；模型：`deepseek-flash`。

计划 6 次，执行 6 次；结果：{'fail': 1, 'pass': 5, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v08_module_autocreate | current | 1 | fail | 95.656 | 8 | 18 |
| v08_module_update | current | 1 | pass | 17.844 | 7 | 12 |
| v08_module_discuss | current | 1 | pass | 23.703 | 4 | 9 |
| v08_module_autocreate | current | 2 | pass | 88.438 | 8 | 15 |
| v08_module_update | current | 2 | pass | 21.219 | 7 | 13 |
| v08_module_discuss | current | 2 | pass | 23.688 | 5 | 11 |

## v08_module_autocreate--current--1

证据目录：`evidence/v08_module_autocreate--current--1`。

- fail：`step-1/allowed_final_changes`：['设定集/角色小传/叶蓁.md', '设定集/角色小传/周素芬.md', '设定集/角色小传/方德海.md', '设定集/角色小传/沈鹤龄.md', '设定集/角色小传/管家.md', '设定集/角色小传/许明川.md', '设定集/角色小传/顾云舟.md']
- fail：`step-1/trajectory_scope`：[{'seq': 8143, 'at': '2026-10-26T15:09:29.169+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8143', 'conversation_id': 'c_fcd126af9b4a', 'type': 'file_changed', 'turn_id': 't_df1d35be', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16', 'tool_call_id': 'call_00_1BYfB0wIBvQwDhFR2rua2977', 'action': 'create', 'path': '设定集/角色小传/沈鹤龄.md', 'before_digest': None, 'after_digest': 'e2fa4dd78e909df002519d4fc809c49d14712c136a2b4f5cd74446d8297d32fa', 'changed': True, 'size': 970}, {'seq': 8144, 'at': '2026-10-26T15:09:29.189+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8144', 'conversation_id': 'c_fcd126af9b4a', 'type': 'file_changed', 'turn_id': 't_df1d35be', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16', 'tool_call_id': 'call_01_ze32LK9JGEZ423yCMYRV8716', 'action': 'create', 'path': '设定集/角色小传/周素芬.md', 'before_digest': None, 'after_digest': '1a7f39a7290947e8521e6cf2ac7fcc97e57ffcea95b05919bc14d569172f9ea2', 'changed': True, 'size': 1001}, {'seq': 8145, 'at': '2026-10-26T15:09:29.207+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8145', 'conversation_id': 'c_fcd126af9b4a', 'type': 'file_changed', 'turn_id': 't_df1d35be', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16', 'tool_call_id': 'call_02_i5Y3hqLfq2JfOzNLwTHV4697', 'action': 'create', 'path': '设定集/角色小传/顾云舟.md', 'before_digest': None, 'after_digest': '52997c14efe3c5ee6cbe4d8954ca3269f49f5cd188025bfe0704ae79eaa42679', 'changed': True, 'size': 646}, {'seq': 8146, 'at': '2026-10-26T15:09:29.228+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8146', 'conversation_id': 'c_fcd126af9b4a', 'type': 'file_changed', 'turn_id': 't_df1d35be', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16', 'tool_call_id': 'call_03_5V1yfdAcXy5LeEIoUjg85060', 'action': 'create', 'path': '设定集/角色小传/叶蓁.md', 'before_digest': None, 'after_digest': '22fcf9b03fab677c7aba41a5e4b1ddc0020233b51d0441213e50f9d68efae56a', 'changed': True, 'size': 705}, {'seq': 8147, 'at': '2026-10-26T15:09:29.251+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8147', 'conversation_id': 'c_fcd126af9b4a', 'type': 'file_changed', 'turn_id': 't_df1d35be', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16', 'tool_call_id': 'call_04_zjbTeENoIUpp29KyDZnU4246', 'action': 'create', 'path': '设定集/角色小传/方德海.md', 'before_digest': None, 'after_digest': '97c71b00e4ec23a1cf6312869214040789cabb36c97cf6b563da5b9b61b3c10b', 'changed': True, 'size': 791}, {'seq': 8148, 'at': '2026-10-26T15:09:29.272+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8148', 'conversation_id': 'c_fcd126af9b4a', 'type': 'file_changed', 'turn_id': 't_df1d35be', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16', 'tool_call_id': 'call_05_q99B9rwHMTArWQmfJcPo7781', 'action': 'create', 'path': '设定集/角色小传/许明川.md', 'before_digest': None, 'after_digest': '453f872d854c6e8cd1c89b4fca7b8740cfd8b79a246568e7a6be2121e703eccc', 'changed': True, 'size': 870}, {'seq': 8208, 'at': '2026-10-26T15:09:32.426+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8208', 'conversation_id': 'c_fcd126af9b4a', 'type': 'file_changed', 'turn_id': 't_df1d35be', 'parent_operation_id': '3711cd8bad514fd1803ed1a3496bec98', 'tool_call_id': 'call_00_fajQGmDKKAQzx5HD7n6o3766', 'action': 'update', 'path': '设定集/角色小传/管家.md', 'before_digest': '8daa63e8d0a22044826b25c63fa8cf197c617dc7d253529d16e7ca639da0adc4', 'after_digest': '9887b80dd0df4eee19f06bb751db320527d3344f0c63f4e713841d4545f876f2', 'changed': True, 'start': 69, 'end': 74, 'size': 1026}]
- fail：`step-1/decision_scope`：[{'seq': 8152, 'at': '2026-10-26T15:09:28.958+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8152', 'conversation_id': 'c_fcd126af9b4a', 'type': 'tool_started', 'turn_id': 't_df1d35be', 'tool_call_id': 'call_00_1BYfB0wIBvQwDhFR2rua2977', 'tool': 'write_file', 'args': {'path': '设定集/角色小传/沈鹤龄.md'}, 'input_detail': '977c366dd35f4965bbc3b62416d03c7b', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16'}, {'seq': 8155, 'at': '2026-10-26T15:09:29.169+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8155', 'conversation_id': 'c_fcd126af9b4a', 'type': 'tool_started', 'turn_id': 't_df1d35be', 'tool_call_id': 'call_01_ze32LK9JGEZ423yCMYRV8716', 'tool': 'write_file', 'args': {'path': '设定集/角色小传/周素芬.md'}, 'input_detail': '8a175ae562dc473dbbce2f22d7e661ee', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16'}, {'seq': 8157, 'at': '2026-10-26T15:09:29.189+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8157', 'conversation_id': 'c_fcd126af9b4a', 'type': 'tool_started', 'turn_id': 't_df1d35be', 'tool_call_id': 'call_02_i5Y3hqLfq2JfOzNLwTHV4697', 'tool': 'write_file', 'args': {'path': '设定集/角色小传/顾云舟.md'}, 'input_detail': '9f791b1750ee445ab9c6d65768d67baa', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16'}, {'seq': 8159, 'at': '2026-10-26T15:09:29.207+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8159', 'conversation_id': 'c_fcd126af9b4a', 'type': 'tool_started', 'turn_id': 't_df1d35be', 'tool_call_id': 'call_03_5V1yfdAcXy5LeEIoUjg85060', 'tool': 'write_file', 'args': {'path': '设定集/角色小传/叶蓁.md'}, 'input_detail': 'cae1e91c4d454d0287ba441c16e7f790', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16'}, {'seq': 8161, 'at': '2026-10-26T15:09:29.228+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8161', 'conversation_id': 'c_fcd126af9b4a', 'type': 'tool_started', 'turn_id': 't_df1d35be', 'tool_call_id': 'call_04_zjbTeENoIUpp29KyDZnU4246', 'tool': 'write_file', 'args': {'path': '设定集/角色小传/方德海.md'}, 'input_detail': '068ab37680f54507bafbef19d958a3b3', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16'}, {'seq': 8163, 'at': '2026-10-26T15:09:29.251+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8163', 'conversation_id': 'c_fcd126af9b4a', 'type': 'tool_started', 'turn_id': 't_df1d35be', 'tool_call_id': 'call_05_q99B9rwHMTArWQmfJcPo7781', 'tool': 'write_file', 'args': {'path': '设定集/角色小传/许明川.md'}, 'input_detail': '2ea705276c9c483282ad49f5010b695e', 'parent_operation_id': '6c5d06cdcfd246dc9021223e355feb16'}, {'seq': 8212, 'at': '2026-10-26T15:09:32.399+08:00', 'event_version': 1, 'event_id': 'c_fcd126af9b4a:8212', 'conversation_id': 'c_fcd126af9b4a', 'type': 'tool_started', 'turn_id': 't_df1d35be', 'tool_call_id': 'call_00_fajQGmDKKAQzx5HD7n6o3766', 'tool': 'edit_file', 'args': {'path': '设定集/角色小传/管家.md'}, 'input_detail': 'e48066cbdcd0487593cf440a4b8a36e8', 'parent_operation_id': '3711cd8bad514fd1803ed1a3496bec98'}]
- fail：`step-1/has_victim`：{'value': '死者', 'modules': 1}
- fail：`step-1/self_organized`：{'verdict': 'fail', 'reviewer': 'Codex', 'reason': '只保存并应用一份聚集缘由模块，人物小传被写入旧设定目录（新建六份并修改管家）。未按要求把两类成果组织为本作品模块，并产生本例不允许的文件改动。', 'evidence': 'evidence/v08_module_autocreate--current--1/step-1.json：write_file 8152 至 8163、edit_file 8212；module_changes_applied 8319 只有聚集缘由；diffs 含七份角色小传，assistant_message 8844 列出实际写入结果。'}

## v08_module_update--current--1

证据目录：`evidence/v08_module_update--current--1`。


## v08_module_discuss--current--1

证据目录：`evidence/v08_module_discuss--current--1`。


## v08_module_autocreate--current--2

证据目录：`evidence/v08_module_autocreate--current--2`。


## v08_module_update--current--2

证据目录：`evidence/v08_module_update--current--2`。


## v08_module_discuss--current--2

证据目录：`evidence/v08_module_discuss--current--2`。


用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
