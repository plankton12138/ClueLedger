# Story Domain：Canon、叙事计划与悬疑模型

> 状态：target，计划在 Phase 13 交付。
> 目标包：`hastings.domain.story`。

## 1. 职责

Story Domain 表达“故事中什么是真的”和“故事准备怎样展开”，并在领域命令执行时维护业务不变量、产生 Revision Proposal 和 Dependency Edges。

该模块不保存数据、不调用模型、不判断用户是否已授权，也不生成正文。

## 2. 聚合与对象

### 2.1 Canon

| 类型 | 主要内容 |
|---|---|
| `StoryPremise` | 核心命题、类型、读者承诺、主题边界 |
| `Character` | 身份、目标、需求、秘密、弧光、特征 |
| `Relationship` | 双方、关系类型、公开状态、真实状态、演变 |
| `Place` | 空间、进入条件、相邻关系、重要特性 |
| `WorldRule` | 规则、条件、例外、代价 |
| `MysteryTruth` | 真凶、动机、手法、掩盖方式、真实时间线 |
| `Clue` | 客观事实、来源、指向、必要性、披露计划 |
| `RedHerring` | 错误解释、人物依据、解除方式 |
| `Foreshadow` | 建立内容、预期回收、允许的变体 |
| `TimelineEvent` | 时间、地点、参与者、前后状态 |

所有对象以稳定 ID 关联；名称只用于显示和搜索。

### 2.2 Narrative Plan

```text
Series → Book → Arc → Chapter → Scene → Beat
```

`Beat` 至少包含：

- `purpose`：为什么存在；
- `pov_character_id`；
- `participants`；
- `preconditions`；
- `state_changes`；
- `reader_disclosures`；
- `setups` / `payoffs`；
- `depends_on`；
- `constraints`；
- `order_key`。

场景和节拍的顺序使用可重排键，不把“第几章”作为永久身份。

### 2.3 三层事实

| 层 | 领域对象 | 关键查询 |
|---|---|---|
| 真相 | `TruthFact`、`TimelineEvent` | 在故事世界中实际发生了什么 |
| 角色认知 | `KnowledgeState`、`BeliefState` | 某角色在某 Beat 前后知道或相信什么 |
| 读者披露 | `Disclosure` | 读者在何时获得了什么信息、以何种解释获得 |

## 3. 领域命令

禁止使用 `upsert(kind, data)`。每个命令必须表达作者意图，并让领域层有机会验证语义。

第一批命令：

| 命令 | 意图 | 典型不变量 |
|---|---|---|
| `CreateCharacter` | 新建角色 | ID 唯一，必需身份字段完整 |
| `ReviseCharacter` | 修改角色某些属性 | 指定基础 Revision，未提供字段保持不变 |
| `ChangeCharacterRole` | 改变角色在故事中的作用 | 产生角色→Arc/Beat 的影响种子 |
| `DefineMysteryTruth` | 建立或替换案件真相 | 手法、动机、凶手和时间线引用有效 |
| `PlanClue` | 规划真实线索 | 必须关联 TruthFact，标明必要性 |
| `PlanRedHerring` | 规划误导项 | 必须有真实的人物或世界原因 |
| `RecordKnowledgeChange` | 某角色获知/误解事实 | 来源 Beat 存在，前后状态可计算 |
| `CreateChapter/Scene/Beat` | 创建叙事结构 | 父级存在，顺序有效 |
| `MoveBeat` | 移动节拍 | 保持稳定 ID，重新验证前置条件与回收关系 |
| `ReviseBeatPurpose` | 改变场景/节拍作用 | 产生正文与上下游结构影响种子 |
| `AcceptStoryProposal` | 接受候选事实或结构 | Proposal 基础版本仍有效 |
| `SupersedeCanonFact` | 用新事实替代旧事实 | 记录替代关系，不删除历史 |

命令执行结果为 `DomainChange`：包含新内容、关系变化、影响种子、警告和用户可读摘要；它仍需由守卫与 Commit Service 提交。

## 4. 依赖边

Story Domain 在写入时维护显式依赖：

| 边类型 | 示例 |
|---|---|
| `constrains` | 世界规则约束某 Beat |
| `motivates` | 角色目标驱动某 Scene |
| `reveals` | Beat 向角色或读者披露 TruthFact |
| `changes_state` | Beat 改变关系或 KnowledgeState |
| `sets_up` / `pays_off` | 伏笔建立与回收 |
| `requires` | 线索或场景依赖前置事实 |
| `renders` | Manuscript 单元实现某 Beat |
| `mentions` | 正文提到某 Canon 对象，置信度可低于显式边 |

显式结构边由命令确定；从正文抽取的语义边必须标记来源和置信度，不能与作者确认的显式边混为一类。

## 5. 领域不变量

1. 名称修改不改变 ID；
2. 部分修改不擦除未提供字段；
3. 被替代事实保留历史和替代关系；
4. Beat 的前置条件必须在此前状态中成立，或产生明确警告；
5. 角色不能在获得某事实之前以“已知”为前提行动；
6. 一个客观事件不能让同一角色在同一不可兼容时间出现在两个地点；
7. 必要线索必须关联 MysteryTruth；
8. Red Herring 必须关联真实原因，不能只有“误导读者”的元目的；
9. Payoff 必须引用已有 Setup；删除 Setup 时对应 Payoff 进入 stale；
10. Narrative Plan 的结构移动不改变 Artifact 身份；
11. 公平性阈值是项目策略，不硬编码成所有题材共用的百分比；
12. 领域层允许返回警告，但违反硬不变量时不得产生可提交变化。

## 6. 查询视图

- `CanonSnapshot`：指定 Commit 下全部有效事实；
- `StoryStateAtBeat`：某 Beat 前后人物、关系、物品和知识状态；
- `DisclosureTimeline`：事实何时向哪些角色和读者披露；
- `MysteryFairnessView`：必要线索、出现位置、解释与揭晓位置；
- `NarrativeTree`：结构树和每个节点的 stale/status；
- `OpenSetupsView`：尚未回收或已经逾期的 Setup。

查询视图是派生结果，不得反向成为写入入口。

## 7. 错误与泄压阀

- 违规示例：`STORY_UNKNOWN_ARTIFACT`、`STORY_INVALID_KNOWLEDGE_ORDER`、`STORY_TIMELINE_CONFLICT`、`STORY_ORPHAN_PAYOFF`；
- 合法空结果：移动到当前所在位置、修改值与当前值相同、查询范围没有未回收伏笔；
- 环境故障不在领域层产生，由 Repository 或 Agent 端口报告。

每个错误带：错误位置、违反的规则、当前值与期望值、最小可行修正示例。

## 8. 测试重点

- 修改显示名后所有 ID 引用仍有效；
- 部分角色修改不会擦除秘密和关系；
- 改变角色作用产生预期影响种子；
- 移动 Beat 后重新计算 KnowledgeState；
- 角色提前知情被拒或形成明确 Finding；
- 删除 Setup 使 Payoff stale；
- Red Herring 缺少人物依据被拒；
- 项目公平性策略可配置且不影响非悬疑项目；
- 领域测试不需要数据库、Web 或模型。

