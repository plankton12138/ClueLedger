# 数据、模型输入与恢复契约

日期：2026-10-29。状态：**当前实现契约**。本文件定义职责和字段；当前进度见[开发入口](README.md)。

2026-10-30 实施补充：受理时保存任务目标 `TaskIntent`（discussion、structure、module、mixed、clarify）及目标方案与修订；作者确认候选时绑定最近一条可定位的助手回复。候选原文只扩展可修改的既有对象，不参与作者真相与约定授权。结构收据记录基础修订、改动对象、作者可读标签、操作类型、操作与代次；`no_change` 必须指向本轮实际读过、当前存在且在授权范围内的对象，否则不能作为完成依据。任务结束按当前操作代次与收据核对：结构目标没有提交时，模块保存或文字回复只记部分完成；语义完整性仍由作者核对，程序不宣称已经理解全部创作意图。

2026-10-29 实施补充：主方案与备选的正文补丁都接入同一提交决定，记录反向正文补丁，支持确定性恢复与成组撤销；作品重新打开先恢复。备选正文写入 `story/variants/{variant_id}/prose/` 的受管副本（写时复制，删除用 `.absent` 标记），主方案正文不受影响。读取依赖覆盖方案、正文、约定、模块修订与会话事件序号；撤销只回退本次改动段落。具体保证与验证见[联合提交记录](notes/P1-joint-commit.md)。

候选流程补充：主方案正文先保存在 `story/candidates/`，逐项核对通过后才通过联合提交发布；修正只允许一次有限局部替换与一次复核。未通过时保留草稿并记录作者问题，暂停后的迟到报告不能触发提交。当前支持新章节创建及其成组撤销；候选与结构关联、重启承接见[候选流程记录](notes/P4-candidate-workflow.md)。

## 1. 对象与归属

结构属于稳定的 `workspace_lineage` 和 `variant_id`，提交生成新 `revision`。名称只用于显示，ID、修订、时间和提交来源由应用生成。新格式不读取或迁移旧账本。

| 对象 | 最小内容 |
|---|---|
| StoryVariant | `id, name, base_variant_id, base_revision, revision, nodes, clues, threads, constraints, edges, event_times, bindings, assessments`；作品另记 `primary_variant_id` |
| StoryThread | `id, title, node_ids`；节点可属于多条故事内分支 |
| Clue | `id, title, author_truth: str|null, intention, resolution{intent,node_ids}, provenance` |
| StoryNode | `id, title, action, thread_ids, clue_uses[], time_note, knowledge_changes[], group_id?` |
| ClueUse | `clue_id, presentation, mode, speaker_id?, expected_interpretation?`；mode 区分叙述、行动、证词、回忆、假设等 |
| StoryEdge | `id, from_id, to_id, kind, reason`；kind 为 `next / reframe / answer` |
| EventTimeRelation | `before_node_id, after_node_id`；实际时间独立于揭示顺序 |
| KnowledgeChange | `subject_id, claim, stance: knows|believes, source_node_ids, effective_event_node_id` |
| Constraint | `id, text, strength: required|preference, scope, source_message_id, origin, revision`；scope 为全书、人物、节点或正文范围 |
| ProseBinding | `id, node_id, clue_id?, document_id, path, digest, start, end, quote, observation, assessment_id?` |
| Assessment | `id, kind, requirements[], findings[], checked_scope, structure_revision, prose_digests, source_operation_id, decision_source` |
| ChangeSet | `id, base_revision, operations[], prose_patches[], read_dependencies[], requested_scope, operation_id, generation` |

`resolution.intent` 为 `explain / leave_open / undecided`。真相和目标允许未定，未决定不等于否定事实。预期理解是创作目标，不等于读者或角色的实际认知。

provenance 区分明确作者要求、授权创作和模型建议；采用状态来自变更收据，不能由模型自报。应用不从模型填写的 requested_scope 或 origin 推导写权限。

正文区间沿用服务端 LF 规范化后的 Unicode 字符 `[start,end)`，digest 校验文件原始字节。前端使用位置转换，不能直接拿 UTF-16 字符下标切正文；测试覆盖 emoji、中文和 CRLF。

## 2. 图与备选

next 表示揭示推进，可分支、汇合且无环；实际时间关系另行无环校验。reframe 和 answer 可以从后文指向前文，不参加推进无环校验。引用必须在当前方案有效，删除节点不能遗留悬空关系。

顺序允许未定。普通预览需要明确线段与顺序；有正文的试读使用实际阅读前缀，无正文时使用明确的呈现稿。不能把偶然的拓扑排序当成作者决定。

备选拥有完整可解析的不可变基线和自身修订；首版可保存完整结构快照，优先保证恢复简单。不共享可变对象，备选正文也有受管工作副本，禁止覆盖主方案正文。

主方案的正文身份映射到正式文件路径；备选已保存正文映射到该方案的受管文本副本。采用备选形成明确 ChangeSet，核对主方案结构与正文后更新选定内容和关联。浏览方案不改变主方案指针。

局部采用先计算所选内容的必需前提、回应与正文影响；模型补查关系遗漏，明确未覆盖范围。范围外改写需明确授权，已要求同步调整的范围不逐项再次确认。

## 3. 存储与提交

在作品状态目录新增 `story/`，保存不可变修订、当前指针、变更收据、工作副本和提交日志。正文继续是文本文件。P0 固定具体文件布局。

提交一个完整变更单元：

1. 模型只写候选工作副本，正式正文不接收半段流式结果。
2. 固定 read set：方案修订、正文指纹、约定、候选版本和任务授权。
3. 作品协调锁内重查 read set、代次、范围和请求键，持久保存 prepared 日志及新旧内容。
4. 持久保存提交决定，这是和暂停竞争的边界；未通过的旧代次不得提交。
5. 完成结构、正文与当前指针更新，保存收据，再发送 changes_committed。
6. 补记 Git 历史；失败单独记录，不重复调用模型或重复提交内容。

多个文本文件无法一次物理原子替换。本方案保证应用逻辑可见性：受管读写共享协调规则，恢复前不向 UI 或模型提供半提交状态；不承诺外部编辑器看不到中间文件变化。

崩溃时，只有 prepared 而无提交决定则恢复原状态；已有提交决定则继续完成。目标出现既非原内容也非目标内容的外部编辑时停止覆盖，保留副本并报告冲突。服务启动只做确定性恢复，不自行恢复模型生成。

同一请求键和负载返回同一收据，同键不同负载报冲突。重复继续、事件重放和网络重试不能重复创建节点或写正文。撤销使用新 ChangeSet 与新键。

撤销核对目标是否仍处于本次提交后的版本，只恢复涉及对象并保留后续无关编辑。后续有依赖或同目标新编辑时显示影响，形成新的调整方案，不能整本回滚覆盖。

## 4. 正文依据和失效

计划意图、文章出现、语义是否充分分别保存，不允许模型直接写全局 resolved=true。显示“已回应”需要当前正文依据及核对或作者裁决来源。

正文修改、删除或外部变化使相关定位、摘要、知情与核对失效；结构、约定变化使依赖旧要求的核对失效。角色知情按事件位置取值，回写前章不能使用后章状态，早期变化需要检查后续依赖。

原句重新定位只恢复定位有效性，不能自动恢复语义。旧摘要可辅助寻找原文，不作为当前事实。无来源保留待核对，不启动隐含模型抽取。合法空结果与失败分别记录。

正文中的证词只能建立证词观察；不能自动写入作者真相。普通改文不授权改变结局，作者明确改变结局时才更新相关结构。

## 5. 实际模型输入

任务受理保存 StoryTaskContext：作品、方案和修订、模式、所选节点／篇章、阅读截止、作者要求与写权限。每个模型阶段另存实际输入，不能用初始快照冒充补读后的请求。

装配顺序：本次要求和范围 → 必需约定 → 相关线索真相与结构 → 必需前提与回应目标 → 当前角色和读者信息边界 → 原文与必要前后文 → 可选参考和近期讨论。

必需项由任务、作用域、明确参考和已知关系递归选取，不能只取一层邻居或只靠相似度。补读中新发现的依赖进入 read set。全局约定保持原文及有效修订，可选背景允许摘要。

先预留输出和工具补读空间。必需集合超限时按节点拆组，只装第 1 组并把本轮范围写进请求；单个节点的必需内容仍放不下时，连同依赖它的后继一起停下，全部装不下返回 STORY_CONTEXT_INCOMPLETE，不能截断必需要求继续。每个拆分单元仍完整携带其适用约束。

输入快照记录模板版本、操作代次、模型、实际 messages 或可回读附件、来源修订与内容摘要校验值、覆盖、工具读取和省略理由，不保存凭据与鉴权头。

压缩只整理讨论，当前约定和结构重新装入。新会话与继续使用当前数据；来源变化但不改变创作决定时重装继续，有实质取舍冲突时提问。

试读使用独立请求与独立会话（`origin=reader`），过滤真相、未来节点标题、作者对话、方案比较、摘要和缓存，工具同样限制范围。首版读者阶段使用固定中性提问；作者自由表达用于确定位置与任务，不原样传入读者请求，避免问题本身泄露隐藏信息。执行时没有工具、目录、摘要与作者历史，发送前用隐藏字符串自检实际文本。只能保证作品隐藏内容未被提供，不能保证模型没有外部常识，也不能代表所有读者。

## 6. 操作与代次

Operation 字段：`id, owner, conversation_id?, purpose, status, generation, phase, request_key, instruction_log, context_refs, intent, checkpoints, committed_receipts, pending_units, last_seq`。owner 为作品或模型测试对象，来自服务端绑定；`request_key` 是受理时的幂等键，压缩与连接测试没有会话受理记录可查，靠它认重复。`intent` 记录受理时确定的任务目标与方案身份，只用于判定完成。

状态主线 accepted → running → completed；运行中可转 paused、awaiting_answer、needs_attention 或 failed。作者继续后重查恢复条件、增加 generation。失败不能伪装为暂停；可恢复失败提供继续入口。

暂停先持久撤销代次资格，再取消远端模型请求；不必等远端完全释放才回应已暂停。旧代次文本、工具结果和提交在服务端被拒收。已越过提交决定的完整单元保留，后续阶段不启动。

继续使用已有草稿和未完成项发起新代次，不承诺恢复原连接。插话把暂停、追加要求与登记后续代次作为一个幂等控制请求，不能由前端拼两次请求。浏览其他节点不重定向运行任务。

服务重启将未终结操作恢复为需作者继续；事件读取不启动模型。同一操作只有一个有效代次；作品提交共享协调锁，多端重复点击只得到同一受理结果。

## 7. 授权与核对

讨论可保存候选，正式修改需明确创作要求。已明确请求直接执行范围内改动，不逐字段确认。授权由应用持有，模型不得自行修改范围、代次、原始要求或检查结论来源。作者确认上一轮候选时，候选原文只把其中点到的既有对象纳入可修改范围；作者真相与必需约定仍须作者原话直接给出，候选里的命令句不构成授权。

核对覆盖必需呈现、提前披露、角色知情、作者真相与必要前提，输出具体位置、原句、对应要求、不确定项与未覆盖范围。默认一次局部修正和一次复核，关键问题未解决则保留候选并提问。

程序保证结构完整性和执行边界，语义核对存在误判。作者决定保留问题时记录裁决与作用域，不覆盖原核对记录伪装通过。

当前模型字段与请求结构可查[机器可读实现契约](artifacts/implemented-contracts.json)。自动定位、试读来源、拆组继续、作者明确修改与恢复边界见[收尾记录](notes/P7-implementation-and-acceptance.md)。
