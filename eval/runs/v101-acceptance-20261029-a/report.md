# 线簿上下文评测

运行：`context-20261029-223517-482713`；模型：`deepseek-flash`。

计划 36 次，执行 9 次；结果：{'pending_review': 7, 'fail': 2, 'not_run': 27}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| v101_o01 | current | 1 | pending_review | 73.828 | 7 | 7 |
| v101_o02 | current | 1 | pending_review | 46.891 | 4 | 5 |
| v101_o03 | current | 1 | pending_review | 51.547 | 4 | 7 |
| v101_o04 | current | 1 | pending_review | 33.156 | 2 | 3 |
| v101_o05 | current | 1 | pending_review | 39.375 | 3 | 6 |
| v101_o06 | current | 1 | pending_review | 50.422 | 12 | 9 |
| v101_o07 | current | 1 | pending_review | 79.421 | 13 | 20 |
| v101_o08 | current | 1 | fail | 70.984 | 12 | 9 |
| v101_o09 | current | 1 | fail | 10.531 | 4 | 4 |

未运行：v101_o10--current--1、v101_o11--current--1、v101_o12--current--1、v101_o01--current--2、v101_o02--current--2、v101_o03--current--2、v101_o04--current--2、v101_o05--current--2、v101_o06--current--2、v101_o07--current--2、v101_o08--current--2、v101_o09--current--2、v101_o10--current--2、v101_o11--current--2、v101_o12--current--2、v101_o01--current--3、v101_o02--current--3、v101_o03--current--3、v101_o04--current--3、v101_o05--current--3、v101_o06--current--3、v101_o07--current--3、v101_o08--current--3、v101_o09--current--3、v101_o10--current--3、v101_o11--current--3、v101_o12--current--3。

## v101_o01--current--1

证据目录：`evidence/v101_o01--current--1`。

- pending_review：`step-1/goal`：{'rubric': '必须保存可展开的线索、节点、两条分支及汇合；未知真相保持未定，不虚报保存。', 'review': None}

## v101_o02--current--1

证据目录：`evidence/v101_o02--current--1`。

- pending_review：`step-1/goal`：{'rubric': '误导来自解释变化，保留真实湿手套与既定真相，回收有观察依据，不改正文。', 'review': None}

## v101_o03--current--1

证据目录：`evidence/v101_o03--current--1`。

- pending_review：`step-1/goal`：{'rubric': '证词不是作者真相，林砚早期不能知道搬尸事实；后期发现须有实际依据。', 'review': None}

## v101_o04--current--1

证据目录：`evidence/v101_o04--current--1`。

- pending_review：`step-1/goal`：{'rubric': '阅读先对质后抵达不改变实际事件时间，回看措辞成立，角色知情不因倒叙而越界。', 'review': None}

## v101_o05--current--1

证据目录：`evidence/v101_o05--current--1`。

- pending_review：`step-1/goal`：{'rubric': '两条调查均提供不可省略的证据和发现过程，汇合结论依据充分，不能直接读到作者真相。', 'review': None}

## v101_o06--current--1

证据目录：`evidence/v101_o06--current--1`。

- pending_review：`step-1/goal`：{'rubric': '备选修改不影响主方案，选定节点采用须真实提交且保留范围外对象及真相，说明正文尚未调整。', 'review': None}
- pending_review：`step-2/goal`：{'rubric': '备选修改不影响主方案，选定节点采用须真实提交且保留范围外对象及真相，说明正文尚未调整。', 'review': None}
- pending_review：`step-3/goal`：{'rubric': '备选修改不影响主方案，选定节点采用须真实提交且保留范围外对象及真相，说明正文尚未调整。', 'review': None}

## v101_o07--current--1

证据目录：`evidence/v101_o07--current--1`。

- pending_review：`step-1/goal`：{'rubric': '24章初态中湿手套、雪停后搬尸与结局约定在两次真实压缩和新会话后均保留；引用结构与原文，不把摘要推测当事实。', 'review': None}
- pending_review：`step-2/goal`：{'rubric': '24章初态中湿手套、雪停后搬尸与结局约定在两次真实压缩和新会话后均保留；引用结构与原文，不把摘要推测当事实。', 'review': None}
- pending_review：`step-3/goal`：{'rubric': '24章初态中湿手套、雪停后搬尸与结局约定在两次真实压缩和新会话后均保留；引用结构与原文，不把摘要推测当事实。', 'review': None}

## v101_o08--current--1

证据目录：`evidence/v101_o08--current--1`。

- fail：`step-1/terminal`：{'seq': 8762, 'at': '2026-10-29T22:42:49.520+08:00', 'event_version': 1, 'event_id': 'c_c98112ead54c:8762', 'conversation_id': 'c_c98112ead54c', 'type': 'run_finished', 'turn_id': 't_27004f84', 'operation_id': 'op_9ca471322f03', 'generation': 1, 'status': 'needs_input', 'recording': 'no_change', 'changed_paths': [], 'commit': None, 'note': ''}
- fail：`step-1/required_changes`：['正文/0001 雨夜.md']
- pending_review：`step-1/goal`：{'rubric': '真实候选通过后保存；行动、冲突、线索自然融合，不机械逐条解释，不提前透露真相，不越过文件范围。', 'review': None}

## v101_o09--current--1

证据目录：`evidence/v101_o09--current--1`。

- fail：`step-1/terminal`：{'seq': 849, 'at': '2026-10-29T22:43:01.030+08:00', 'event_version': 1, 'event_id': 'c_e62f070373ee:849', 'conversation_id': 'c_e62f070373ee', 'type': 'run_finished', 'turn_id': 't_ce01bd6a', 'operation_id': 'op_867a435f1788', 'generation': 2, 'status': 'failed', 'recording': 'no_change', 'changed_paths': [], 'commit': None, 'note': ''}
- fail：`step-1/public_answer`：
- pending_review：`step-1/goal`：{'rubric': '真实插话进入新代次，最终动机是保护女儿，结局事实保留，旧代次没有迟到写入和虚假提交。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。
