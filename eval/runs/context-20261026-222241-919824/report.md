# 线簿上下文评测

运行：`context-20261026-222241-919824`；模型：`deepseek-flash`。

计划 1 次，执行 1 次；结果：{'fail': 1, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| module_only_first_chapter | current | 1 | fail | 24.875 | 8 | 11 |

## module_only_first_chapter--current--1

证据目录：`evidence/module_only_first_chapter--current--1`。

- fail：`step-1/terminal`：{'seq': 1147, 'at': '2026-10-26T22:23:07.271+08:00', 'event_version': 1, 'event_id': 'c_ac9144b3f067:1147', 'conversation_id': 'c_ac9144b3f067', 'type': 'run_finished', 'turn_id': 't_b0fc31b5', 'status': 'failed', 'recording': 'committed', 'changed_paths': ['正文/0001 雪夜.md'], 'commit': 'c933a4f3ac789cd76ef5dfd7fee51acdf4641bf1', 'note': ''}
- fail：`step-1/trajectory_scope`：[{'seq': 914, 'at': '2026-10-26T22:23:01.850+08:00', 'event_version': 1, 'event_id': 'c_ac9144b3f067:914', 'conversation_id': 'c_ac9144b3f067', 'type': 'file_changed', 'turn_id': 't_b0fc31b5', 'parent_operation_id': 'd637d9cbb6d64e86bff4e12db8a146c1', 'tool_call_id': 'call_00_ET_GumUnqX8ait3WnRWtI1B2163', 'action': 'create', 'path': '正文/0001 雪夜.md', 'before_digest': None, 'after_digest': '10a965205bf00d88ca388cca92af359c70e3af4eebe8579ee72d2d58876f6708', 'changed': True, 'size': 1265}]
- fail：`step-1/scope_before_write`：[{'seq': 1117, 'at': '2026-10-26T22:23:04.710+08:00', 'event_version': 1, 'event_id': 'c_ac9144b3f067:1117', 'conversation_id': 'c_ac9144b3f067', 'type': 'file_changed', 'turn_id': 't_b0fc31b5', 'parent_operation_id': '4ef660d355c5495ca7ab85d6e51fa190', 'tool_call_id': 'call_00_MKDMLSi5lNFa8MDBgVKH7911', 'action': 'update', 'path': '正文/0001 雪夜.md', 'before_digest': '10a965205bf00d88ca388cca92af359c70e3af4eebe8579ee72d2d58876f6708', 'after_digest': 'd5748b3edceccc811797eaa14ab5d9a97dcde011885d3e5fd8487e9fba1ce13e', 'changed': True, 'start': 176, 'end': 249, 'size': 1266}, {'seq': 1127, 'at': '2026-10-26T22:23:05.774+08:00', 'event_version': 1, 'event_id': 'c_ac9144b3f067:1127', 'conversation_id': 'c_ac9144b3f067', 'type': 'file_changed', 'turn_id': 't_b0fc31b5', 'parent_operation_id': 'c5333e6c6a5747a39a77bd2ceb790a16', 'tool_call_id': 'call_00_ET_EGBmviCsSvsutizT1brC0318', 'action': 'update', 'path': '正文/0001 雪夜.md', 'before_digest': 'd5748b3edceccc811797eaa14ab5d9a97dcde011885d3e5fd8487e9fba1ce13e', 'after_digest': '2f0e6a32760574449f3639709c1329a910a5fd58893c7e5d394d3c9cfd925564', 'changed': True, 'start': 322, 'end': 357, 'size': 1266}]
- pending_review：`step-1/uses_assembled_modules`：{'rubric': '应读取已装模块再完成正文，保留医生、摄影师和雪夜封路、电话失声的设定；不把计划当成既往正文，不凭空创建或反复查询旧版小传路径，不因缺少旧身份记录阻塞第一章。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
