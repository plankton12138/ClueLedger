# Phase 13 构建笔记：Canon、叙事计划与悬疑事实模型

> 关键词：Story Domain、Canon、Narrative Plan、Beat、三层事实、依赖边、悬疑规则、查询投影、幂等重放、架构版本路由、V1 dry-run
> 状态：已完成（2026-10-14）。计划：[phase-13-story-domain-plan.md](plans/phase-13-story-domain-plan.md)
> as-built 规格：[modules/story-domain.md](modules/story-domain.md)

## 1. 交付内容

| 交付物 | 位置 | 说明 |
|---|---|---|
| Canon 领域模型 | `src/hastings/domain/story/canon/models.py` | StoryPremise / Character / Relationship / Place / WorldRule / MysteryTruth / Clue / RedHerring / Foreshadow / TimelineEvent；全部 frozen dataclass，`legacy_metadata` 保留 V1 未知字段 |
| Canon 命令 | `canon/commands.py` | create_character / revise_character / change_character_role / define_mystery_truth / plan_clue / plan_red_herring / record_knowledge_change / supersede_canon_fact |
| Canon 与结构规则 | `canon/rules.py` | `canon_findings`：必要线索缺真相、误导缺真实依据、时间线冲突、Payoff 断裂（不变量 6/7/8/9） |
| Narrative 模型 | `narrative/models.py` | NarrativeNode / Beat / StateChange / DisclosureSpec；状态 kind ∈ knowledge/relationship/custom |
| Narrative 命令 | `narrative/commands.py` | create_node / create_beat / move_beat / revise_beat_purpose / delete_setup |
| 结构顺序 | `narrative/order.py` | `sort_key` / `structural_order` / `ordered_beats`；报告孤儿父级与同名序冲突，是状态折叠的唯一顺序来源 |
| 状态折叠 | `narrative/state.py` | `story_state_at_beat` / `story_state_final` → `StoryState`（按结构顺序应用内联状态、披露与独立认知对象） |
| 三层事实 | `mystery/models.py` | TruthFact / KnowledgeState / Disclosure；相互独立、不共用字段 |
| 悬疑规则 | `mystery/rules.py` | `mystery_findings`：提前知情（knows 早于首次披露）、公平性截止（`fairness_cutoff` 为项目策略，None 跳过） |
| 查询投影 | `views.py` | canon_snapshot / narrative_tree / disclosure_timeline / mystery_fairness / open_setups；纯读、携带 `commit_id`、零缓存 |
| 目录与引用原语 | `catalog.py` / `refs.py` / `kinds.py` / `changes.py` / `schema.py` / `ids.py` / `error_codes.py` / `errors.py` | StoryCatalog（by_kind/beats/nodes/canon/require/resolve_name）；new_artifact_id 唯一性、require_ref 类型校验、check_revision；DomainChange / FindingSeed / ImpactSeed / RelationSpec / StoryWarning |
| 应用层写入口 | `application/handlers/story_commands.py` | StoryCommandHandler：幂等重放 → 读 head → 命令 → 领域违规转应用信封 → Commit Service |
| 应用层读入口 | `application/queries/story_queries.py` | StoryQueries 包装五个视图 + story_state |
| 架构版本解析 | `interfaces/architecture_version.py` | `resolve_architecture_version(metadata)` 纯解析（缺失/非法回退 v1） |
| V1 dry-run 映射器 | `interfaces/importers/v1_story_mapper.py` | `map_v1_workspace`：mapped/legacy/ambiguous/error 四态；显式登记不迁移源；不写 Repository |
| 路由接线 | `settings.py` / `cli.py` / `server/hastings_session.py` / `tools/bible_tools.py` | 项目元数据解析接入真实装配点；V2 项目 `bible_upsert` 拒绝桩（描述与提示按版本） |
| 测试 | `tests/v2/story/`（14 文件） | 模型、目录、命令、规则、投影、映射器、应用层（幂等重放 / 3 章基线 / §7 闭环）、路由闸 |

质量门：pytest **1094 passed**（Phase 12 基线 906 + 新增 188）；ruff check / format / mypy strict 全绿。

review 收口轮（2026-10-14，见 §4）：pytest **1094 passed**（story 专项 349）；ruff check / format / mypy strict 全绿。

## 2. 关键决策

- **命令是纯函数，Application Handler 是唯一写路径**：领域命令只吃 `StoryCatalog` 与 `NewId`、只吐 `DomainChange`，绝不 import Repository 写端口。取 head、投影目录、转错误信封、组装 `CommitRequest` 全部收在 handler——领域可被无数据库单测，写路径也只有一处会腐烂。
- **幂等重放必须早于命令重执行**（评审 P1 定型）：Commit Service 的幂等键比对的是「写什么」的内容指纹，而命令每次执行都生成新的 Artifact ID（`new_artifact_id` 又要求 ID 未用过），因此**确定性 Artifact ID 方案与重放天然冲突**。重放表放在 `(project_id, idempotency_key)` 上、用「命令身份 + 规范化意图」的指纹校验，命中即返回首次结果对象，不再读 head、不重跑命令。同键不同指纹显式抛 `REPO_IDEMPOTENCY_CONFLICT`，与 Commit Service 同哲学：不静默返回旧结果。
- **版本判定点收敛到「项目解析这一处」**（ADR §2.2）：`resolve_architecture_version` 是纯函数，I/O 集中在 `settings.py` 的 `workspace_architecture_version`（读 `settings.yaml` 元数据），CLI 与 Web 会话共用同一解析结果——不在两处各写一份「默认 v1」。
- **`constrains` 边的方向以“主动方”为准**：世界规则约束某 Beat → source=规则、target=beat，与「motivates=角色驱动 Scene」同向；`changes_state` 与 `record_knowledge_change` 同向（source=beat、target=状态持有者）。方向不靠注释，由测试固化。
- **`create_beat` 先物化全部 Iterable**：这些参数既用于引用校验，又写入 Beat 内容与关系边；若在校验循环里消费生成器，后面拿到的是空序列。物化是「一次消费、多处使用」的安全前提，也避免了后续新增物化点时的隐性耗尽。
- **投影不缓存、显式 Commit**：计划 T13.7 要求「缓存必须带 Commit ID」，实现选择更简单的等价方案——领域层不缓存，调用方每次显式给出目标 Commit，head 前移后重新构造目录，从结构上不可能返回旧结果。
- **公平性阈值是参数不是常量**：`fairness_cutoff` / `deadline` 由项目策略传入，非悬疑项目传 `None` 使整类判定短路，绝不把百分比硬编码成所有题材共用。

## 3. Agent 设计经验（四透镜）

**① 错误消息是喂给模型的 API。** 领域违规带三要素（where=「命令.字段」、why=违反的规则、fix=最小可行修正），在应用边界转成 `ViolationError` 时**原样透传、不做“人话翻译”**——字段级定位（如 `CreateBeat.constraints`）直达模型可改之处。规则的 Finding 同样自足：`STORY_INVALID_KNOWLEDGE_ORDER` 的 where 精确到 `knowledge:{角色}->{事实}`，details 带知情节拍与披露节拍，fix 给两条出路（推到披露之后 / 改为 suspects·misbelieves）。模型不需要猜「为什么被拒」。

**② 硬约束放工具层，prompt 只负责解释世界。** 引用存在性、类型匹配、`order_key` 唯一、`basis` 必填、revision 过期——全部由命令层在产生 `DomainChange` 之前拒绝，不依赖模型自觉。`DomainChange` 是「可提交的候选」，空则不进 Commit Service。版本路由同理：V2 不是靠 prompt 叮嘱「别用 bible_upsert」，而是把该写工具换成拒绝桩这一硬事实；工具描述与提示按版本生成，避免「提示要求调用、工具必然拒绝」的自相矛盾循环（评审 P2）。

**③ 泄压阀——区分违规 / 空结果 / 环境故障。** 违规走 `StoryViolation`；合法空结果（移动到自己所在位置、修改值与当前值相同、查询范围没有未回收伏笔）走 `is_empty()` 的 `DomainChange` → `NO_CHANGE`，不抛异常、不提交。把「没有变化」污名化成故障，模型就会陷入道歉-重试循环。环境故障不由领域层产出，留给 Repository / Agent 端口——三层路径互不串味。

**④ 可观测性是事件契约的一部分。** 规则永不抛异常、只产出结构化 `FindingSeed`（where / why / fix / severity_hint / details）：同一份体检结果既服务模型（读 fix 改稿），也服务审计与前端（读 details 定位）。投影都携带 `commit_id`，disclosure_timeline 对「未绑定节拍」的披露降级为 `StoryWarning` 记录而非丢弃——未知/可疑输入显式上报、不静默吞。

## 4. 评审记录

- 自查通过：计划 §8 退出条件逐条核对——领域层无数据库/Web/Agent import（依赖方向测试通过）；所有写命令经 Handler 与 Commit Service（`tests/v2/story/test_story_commands.py`）；V2 项目不可调用 `bible_upsert`（路由闸测试）；V1 行为未变（全量 V1 测试零回归）；dry-run 每个源字段有 mapped/legacy/ambiguous/error 结果；模块文档 as-built、构建笔记含四透镜；Phase 15 只需 Artifact + Relation + views 即可做确定性影响（`change_character_role`/`supersede_canon_fact` 已产出 `ImpactSeed`）。
- 计划 §7 验收场景闭环：`test_acceptance_loop_misbelief_then_corrected_early_knowledge` 建立 3 章基线（真凶、见证者、1 条真相事实、知情节拍、披露节拍），记录见证者 `misbelieves`（合法，不拦）与真凶 `knows`（于披露之前 → `STORY_INVALID_KNOWLEDGE_ORDER`，定位到 `know_beat`/`reveal_beat`），再用既有 `move_beat` 把披露节拍前移（节拍 ID 不变），重算后 `mystery_findings(after) == ()`——修正走同一套命令与提交路径。

### 外部 review 收口（2026-10-14）

外部 review 发现 3 个 P1、2 个 P2，均按要求顺序收口并以专项测试固化：

- **[P1] `StoryCommandHandler` 破坏提交层幂等重试语义**：原实现每次重试都重新读新 head、重跑命令、生成新 Artifact ID，把请求交给 Commit Service 时 `expected_head` 与 `changes` 已变，同 key 重试被判 `REPO_IDEMPOTENCY_CONFLICT`。收口：`StoryCommand` 改 frozen dataclass（`name` + `intent` + `apply_fn`），新增 `story_command()` 工厂与 `_intent_fingerprint`（`content_hash`，参数值 `repr` 归一、键排序）；`execute` 前置 `(project_id, idempotency_key)` 重放检查，命中同指纹返回首次结果对象、异指纹抛冲突。关键裁决：**重放必须早于命令执行**——`new_artifact_id` 要求 ID 未用过，确定性 ID 方案会与重放互斥。
- **[P1] V2 的 `bible_upsert` 禁写守卫没接入真实运行路径**：原实现只在测试里手工传 `v2`，CLI 与 Web 会话都按默认 `v1` 装配。收口：新增 `settings.workspace_architecture_version`（读 `settings.yaml` 元数据），`cli.build_harness` 与 `server.hastings_session.from_workspace` 均以它驱动 `create_writing_tools` 的 `architecture_version`；`resolve_architecture_version` 从此有生产调用者。
- **[P1] Narrative 命令没有完整维护显式依赖图**：`create_beat` 接收 `constraints` 却不建 `constrains` 边、不校验约束对象，内联 `state_changes` 也不建 `changes_state` 边；依赖只存在 content 里，Relation 图看不见。收口：`constraints` 先 `require_ref` 校验（限 `WorldRule`），再为每条世界规则建 `constrains`（source=规则、target=beat）；对带 `character_id` 的内联状态建 `changes_state`（source=beat、target=角色）；全部 Iterable 前置物化，消除生成器耗尽。
- **[P2] V2 拒绝桩仍向模型发 V1 写入指令**：收口为按版本生成 `description` 与 `prompt_guidelines`，V2 文案明确「该工具已停用、改用领域命令」。
- **[P2] mypy 类型门失败**：`V1`/`V2` 被推断为普通 `str`，与返回类型 `Literal["v1","v2"]` 不兼容。收口：常量声明为 `Final[ArchitectureVersion]`，`ArchitectureVersion` 定义提前到常量之前。

## 5. 踩过的坑（代码级，辅助记录）

- **物化 Iterable 之前的引用校验会吞掉生成器**：`create_beat` 的 `setups`/`payoffs` 等先在校验循环里 `for` 一遍，后续再写进 Beat 内容时拿到空元组。修法：函数顶部一次性 `tuple(...)`。这是「校验与使用共用同一 Iterable」的通病。
- **循环变量复用导致 mypy 误判类型**：同一函数内先用 `spec` 遍历 `DisclosureSpec`，再复用 `spec` 遍历 `StateChange`，mypy 按首用推断类型，报 `"DisclosureSpec" has no attribute "character_id"`。修法：改名为 `change_spec`（同函数内语义不同的循环变量不复用）。
- **确定性 ID 方案与幂等重放的互斥**：曾倾向「重试时生成同一 Artifact ID 以稳定指纹」，但 `new_artifact_id` 会对已存在 ID 抛 `STORY_DUPLICATE_ID`，重放会先撞唯一性。结论：重放必须在命令执行之前，指纹改由「命令意图」而非「产出物 ID」承担。
- **同名角色 + 结构顺序测试的断言语境**：`NarrativeTree.order` 是 `structural_order` 展开的**全部节点 + 节拍**，不是仅 beat 列表；早期断言「order == beats」失败。改为断言集合相等 + 计数。
- **规则 finding 的定位字段**：`STORY_INVALID_KNOWLEDGE_ORDER` 的 `where` 是 `knowledge:{角色}->{事实}`，节拍信息在 `why` 与 `details` 里，不在 `where`——断言时误把节拍塞进 `where` 会失败。
- **`ruff format .` 波及无关文件**：工作区存有其他会话的未提交改动，全仓 format 会把格式化噪声混进它们的 diff。限定路径 format 是更安全的默认（Phase 12 已记，本轮再次验证）。

## 6. 留白

- **首批命令不含 `TruthFact` / `Foreshadow` 的产品级创建命令**：其建立依靠 `create_beat` 的内联披露或测试内助手。计划 §7 验收场景里「必要线索指向的真相」由此补齐；产品级命令（以及 `Relationship`/`Place`/`StoryPremise`/`TimelineEvent` 的创建命令）留待真正需要时按同一命令模式补。
- **`AcceptStoryProposal` 未实现**：它要求「Proposal 基础版本仍有效」的比对，依赖可编程 Proposal 提交通道；本阶段只交付领域命令 + handler，尚无 Proposal 载体，延后到 Phase 14/16 Agent 提案落地时。
- **重放表在进程内**：`StoryCommandHandler._replays` 是实例内字典，跨进程持久化的幂等重放依赖 Commit Service 的内容级幂等；重放表持久化留待 Phase 14「最小 API」有长驻服务进程时落地（已在 handler docstring 声明）。
- **`renders` / `mentions` 边未产生**：属 Phase 14 正文层，本阶段只在 `kinds.py` 预留常量。
- **结构告警未升格为硬约束**：`structural_order` 报告孤儿父级与同名序冲突为 `StoryWarning`；`create_beat` 的 `preconditions` 只校验引用存在，是否「在此前状态成立」交由 `story_state_at_beat` 与使用方判定。
- **「最小 API/CLI」止于 handler + queries**：HTTP/CLI 外壳留 Phase 14，本阶段只交付两个可编程入口（与立项时的架构分叉一致）。
