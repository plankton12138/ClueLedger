# Story Domain：Canon、叙事计划与悬疑模型

> 状态：**as-built**（Phase 13 已交付，2026-10-14）。构建笔记见 [../phase-13-story-domain.md](../phase-13-story-domain.md)。
> 实际包：`hastings.domain.story`。
> 关键词：Canon、Narrative Plan、Beat、三层事实、依赖边、悬疑规则、查询投影、dry-run 映射器、架构版本路由。

## 1. 职责

Story Domain 表达“故事中什么是真的”和“故事准备怎样展开”，并在领域命令执行时维护业务不变量、产生 Revision Proposal 和 Dependency Edges。

该模块不保存数据、不调用模型、不判断用户是否已授权，也不生成正文。命令是**纯函数** `fn(catalog, new_id, ...) -> DomainChange`，不触达 Repository；唯一写路径是应用层 `application/handlers/story_commands.py`（读 Snapshot → 执行命令 → Guard → Commit Service）。

## 2. 聚合与对象

所有对象以稳定 ID 关联；名称只用于显示与搜索（`catalog.resolve_name` 在多个同名对象时抛 `STORY_AMBIGUOUS_NAME`）。V1 未知字段进入 `legacy_metadata`，不静默丢失。

### 2.1 Canon（`canon/models.py`）

| 类型 | 主要内容 |
|---|---|
| `StoryPremise` | 核心命题、类型、读者承诺、主题边界 |
| `Character` | 身份、目标、需求、秘密、弧光、特征、作用（`CHARACTER_ROLES`） |
| `Relationship` | 双方、关系类型、公开状态、真实状态、演变 |
| `Place` | 空间、进入条件、相邻关系、重要特性 |
| `WorldRule` | 规则、条件、例外、代价 |
| `MysteryTruth` | 真凶、协助者、动机、手法、掩盖方式、真实时间线 |
| `Clue` | 客观事实、来源、指向的真相、必要性、披露计划 |
| `RedHerring` | 错误解释、真实依据（人物或世界原因）、解除方式 |
| `Foreshadow` | 建立内容、预期回收、允许的变体 |
| `TimelineEvent` | 时间、地点、参与者、前后状态 |

### 2.2 Narrative Plan（`narrative/models.py`）

```text
Series → Book → Arc → Chapter → Scene → Beat
```

`NarrativeNode`（前五类）与 `Beat` 分属两类 Artifact。`Beat` 字段：`purpose`、`pov_character_id`、`participants`、`preconditions`、`state_changes`（`StateChange`，kind ∈ `knowledge`/`relationship`/`custom`）、`reader_disclosures`（`DisclosureSpec`）、`setups`、`payoffs`、`depends_on`、`constraints`、`order_key`。场景和节拍的顺序使用可重排键（`order_key`），不把“第几章”作为永久身份。

### 2.3 三层事实（`mystery/models.py`）

| 层 | 领域对象 | 关键查询 |
|---|---|---|
| 真相 | `TruthFact`（可关联 `TimelineEvent`）、`TimelineEvent` | 在故事世界中实际发生了什么 |
| 角色认知 | `KnowledgeState`（`mode` ∈ `knows`/`suspects`/`misbelieves`/`unaware`） | 某角色在某 Beat 前后知道或相信什么 |
| 读者披露 | `Disclosure`（`audience`/`interpretation`） | 读者在何时获得了什么信息、以何种解释获得 |

三者相互独立、各自成 Artifact，不共用字段；`misbelieves`/`suspects` 是合法认知。

## 3. 领域命令

禁止使用 `upsert(kind, data)`。每个命令表达作者意图，并让领域层有机会验证语义；执行结果统一为 `DomainChange`。

已交付命令（as-built）：

| 命令 | 模块 | 意图 | 关键不变量 |
|---|---|---|---|
| `create_character` | canon | 新建角色 | ID 唯一、角色作用合法、同名可共存 |
| `revise_character` | canon | 部分修改角色 | 未提供字段保持不变（不变量 2）；值未变 → 合法空结果 |
| `change_character_role` | canon | 改变角色作用 | 维护 `motivates` 边并产生 `ImpactSeed` |
| `define_mystery_truth` | canon | 建立案件真相 | 凶手/协助者/时间线引用有效 |
| `plan_clue` | canon | 规划真实线索 | 指向的真相引用有效；产生 `requires` 边 |
| `plan_red_herring` | canon | 规划误导项 | `basis` 必填（不变量 8） |
| `record_knowledge_change` | canon | 记录角色认知 | 事实须为 `TruthFact`；`at_beat_id` 产生 `changes_state` 边 |
| `supersede_canon_fact` | canon | 用新事实替代旧事实 | 产生 `supersedes` 边；旧对象保留（不变量 3） |
| `create_node` | narrative | 新建结构节点 | 父级存在且类型匹配、`order_key` 同父级唯一 |
| `create_beat` | narrative | 新建节拍 | 引用与结构边同事务建立（`sets_up`/`pays_off`/`requires`/`reveals`/`constrains`/`changes_state`） |
| `move_beat` | narrative | 移动节拍 | 保持稳定 ID，重排状态折叠顺序 |
| `revise_beat_purpose` | narrative | 改变节拍作用 | 产生正文与上下游结构影响种子 |
| `delete_setup` | narrative | 撤销伏笔建立 | 使对应 Payoff 进入 stale（不变量 9） |

未交付命令（明确延后，见构建笔记留白）：

- `AcceptStoryProposal`：将候选事实/结构转为正式 Revision，其验收依赖 Proposal 基础版本比对——本阶段尚无可编程 Proposal 提交通道，延后到 Phase 14/16（Agent 提案真正落地时）。
- 首批命令集中服务基线构建，`TruthFact` / `Foreshadow` 尚无独立产品命令（其建立经 `create_beat` 的内联披露或测试内助手补齐）。

命令执行结果为 `DomainChange`（`artifact_changes` / `relation_changes` / `impact_seeds` / `warnings` / `findings` / `summary`）；它仍需由守卫与 Commit Service 提交。`is_empty()` 表示合法空结果。

## 4. 依赖边

类型常量在 `kinds.py`。Story Domain 在写入时维护显式依赖：

| 边类型 | source → target | 产生点 |
|---|---|---|
| `constrains` | 世界规则 → Beat | `create_beat`（不变量：世界规则约束某 Beat） |
| `motivates` | 角色 → Scene/Beat | `change_character_role` |
| `reveals` | Beat → TruthFact | `create_beat`（含 `audience`/`interpretation`） |
| `changes_state` | Beat → 角色 | `create_beat` 内联状态；`record_knowledge_change` |
| `sets_up` / `pays_off` | Beat → Foreshadow | `create_beat` |
| `requires` | Clue/Beat → 事实/前置 | `plan_clue`；`create_beat.depends_on` |
| `supersedes` | 新事实 → 旧事实 | `supersede_canon_fact` |

`renders`（正文实现某 Beat）与 `mentions`（正文提及 Canon，置信度低于显式边）为 Phase 14 预留，本阶段不产生。显式结构边由命令确定；从正文抽取的语义边必须标记来源和置信度，不能与作者确认的显式边混为一类。

## 5. 领域不变量

命令层与规则层共同把关。硬不变量在写入前拒绝（命令层），事后体检由确定性规则产出 Finding（不抛异常）。

1. 名称修改不改变 ID（`release_character` 类操作只改 content）；
2. 部分修改不擦除未提供字段（`revise_character`）；
3. 被替代事实保留历史和替代关系（`supersede_canon_fact` + `canon_snapshot` 排除已替代项）；
4. Beat 的前置条件必须在此前状态中成立，或产生明确警告（`story_state_at_beat`）；
5. 角色不能在获得某事实之前以“已知”为前提行动（`mystery_findings` → `STORY_INVALID_KNOWLEDGE_ORDER`）；
6. 一个客观事件不能让同一角色在同一不可兼容时间出现在两个地点（`canon_findings` → `STORY_TIMELINE_CONFLICT`）；
7. 必要线索必须关联真相（`canon_findings` → `STORY_CLUE_WITHOUT_TRUTH`）；
8. Red Herring 必须关联真实原因（命令层 `basis` 必填；`STORY_RED_HERRING_WITHOUT_CAUSE`）；
9. Payoff 必须引用已有 Setup；删除 Setup 时对应 Payoff 进入 stale（`STORY_ORPHAN_PAYOFF`、`delete_setup`）；
10. Narrative Plan 的结构移动不改变 Artifact 身份（`move_beat`）；
11. 公平性阈值是项目策略（`fairness_cutoff` 参数），不硬编码成所有题材共用的百分比；非悬疑项目传 `None` 即跳过；
12. 领域层允许返回警告，但违反硬不变量时不得产生可提交变化。

## 6. 查询视图

投影是派生结果，不得反向成为写入入口：全部是纯读函数 `f(catalog) -> view`，不触碰 Repository、不产生 DomainChange。每个视图携带 `commit_id`；领域层不做缓存，调用方每次显式给出目标 Commit，因此不可能返回旧结果（T13.7「缓存必须带 Commit ID」由「无缓存 + 显式 Commit」满足）。

| 视图函数 | 返回 | 位置 |
|---|---|---|
| `canon_snapshot` | `CanonSnapshot`（有效事实 + 被替代清单） | `views.py` |
| `narrative_tree` | `NarrativeTree`（结构树 + 顺序 + 顺序告警，节点带 `status`/`stale`） | `views.py` |
| `disclosure_timeline` | `DisclosureTimeline`（读者 `reveal` 与角色 `knowledge` 两通道合并，按结构位置排序） | `views.py` |
| `mystery_fairness` | `MysteryFairnessView`（必要线索位置/指向/揭晓 + 规则 findings） | `views.py` |
| `open_setups` | `OpenSetupsView`（未回收/逾期 Setup；空结果是合法空结果） | `views.py` |
| `story_state_at_beat` / `story_state_final` | `StoryState`（按结构顺序折叠的人物/关系/知识/披露状态） | `narrative/state.py` |
| `structural_order` / `ordered_beats` | `Ordering`（结构顺序与顺序告警；状态折叠的唯一顺序来源） | `narrative/order.py` |

应用层只读门面 `application/queries/story_queries.py` 的 `StoryQueries` 包装以上视图（`catalog` / `canon_snapshot` / `narrative_tree` / `disclosure_timeline` / `mystery_fairness` / `open_setups` / `story_state`）。

## 7. 错误与泄压阀

`error_codes.py` 的稳定码（每个都带三要素：位置 / 违反的规则 / 当前值与期望值 + 最小修正）：

- 引用与身份：`STORY_UNKNOWN_ARTIFACT`、`STORY_AMBIGUOUS_NAME`、`STORY_DUPLICATE_ID`；
- 命令与字段：`STORY_MISSING_REQUIRED_FIELD`、`STORY_INVALID_COMMAND`、`STORY_STALE_REVISION`、`STORY_PARTIAL_NO_ERASE`；
- 悬疑与结构不变量：`STORY_INVALID_KNOWLEDGE_ORDER`、`STORY_TIMELINE_CONFLICT`、`STORY_ORPHAN_PAYOFF`、`STORY_ORPHAN_SETUP`、`STORY_CLUE_WITHOUT_TRUTH`、`STORY_RED_HERRING_WITHOUT_CAUSE`、`STORY_FAIRNESS_VIOLATION`、`STORY_MISSING_PRECONDITION`。

泄压阀三分：

- 违规 → `StoryViolation`（硬约束拒绝，附修正指引），在应用边界转为 `ViolationError`（`category=VIOLATION`，四要素原样透传，不做“人话翻译”）；
- 合法空结果 → 命令返回 `is_empty()` 的 `DomainChange`（如移动到当前所在位置、修改值与当前值相同），handler 转为 `NO_CHANGE`，不提交、不抛异常；
- 环境故障不在领域层产生，由 Repository 或 Agent 端口报告。

## 8. 架构版本路由

V2 项目的 `bible_upsert` 写工具被替换为**拒绝桩**，V1 行为不变。判定点是唯一的项目解析处：

- 纯解析：`interfaces/architecture_version.py` 的 `resolve_architecture_version(metadata) -> ArchitectureVersion`（缺失/非法一律回退 `v1`；`ARCHITECTURE_VERSION_KEY="architecture_version"`，常量 `V1`/`V2`）；
- I/O 收敛：`settings.py` 的 `workspace_architecture_version(workspace)` 读取 `settings.yaml` 元数据，生产代码（CLI `build_harness`、Web 会话 `from_workspace`）据此把版本传给 `create_writing_tools` / `create_bible_tools`。

工具层拒绝桩的工具描述与 `prompt_guidelines` 按版本生成：V2 下发的是「已停用、改用领域命令」的文案，不再指示模型调用一个必然被拒的写工具。

## 9. V1 映射器已退役

2026-10-15 按项目未上线的决策删除旧账本映射器、工作区导入器及专用测试夹具。
Story Domain 的模型、命令与投影继续使用 V2 仓库，不再依赖旧账本转换。
历史映射设计见 Phase 13 构建记录，当前决策见[旧工作区退役记录](../phase-17-workspace-retirement.md)。

## 10. 测试重点

测试目录 `tests/v2/story/`：模型往返、目录/稳定 ID 与歧义、Canon 命令、Narrative 命令与结构顺序、状态折叠、Canon 与悬疑规则、五个投影、应用层写入口（含幂等重放、3 章悬疑基线与 §7 误解决策闭环）、路由闸。

领域测试不需要数据库、Web 或模型。
