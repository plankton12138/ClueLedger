# Phase 13 计划：Canon、Narrative Plan 与悬疑事实模型

> 状态：待执行。
> 依赖：Phase 12 完成。
> 目标：用稳定 ID、意图明确的命令和显式依赖表示故事事实与叙事结构，替代通用 Bible upsert 的目标能力。

## 1. 开始条件

- Repository 契约稳定且 SQLite/内存实现通过；
- Commit Service 支持多 Artifact + Relation 原子提交；
- 当前六账本字段与真实样例已经盘点；
- 已决定第一版支持的 Canon/Plan schema，未覆盖字段有 extension/legacy 保留策略。

## 2. 阶段交付

1. Canon 与 Narrative Plan 纯领域模型；
2. Truth / Knowledge / Disclosure 三层悬疑事实；
3. 第一批意图明确的领域命令；
4. 领域不变量和稳定错误码；
5. 依赖边生成；
6. Story Snapshot、Narrative Tree、Disclosure Timeline 等查询投影；
7. 从 V1 fixture 到 V2 Proposal 的 dry-run 映射器；
8. 新建 V2 项目的最小 API/CLI，用于创建和读取故事基线；
9. 旧 `bible_upsert` 仍服务 V1 项目，但 V2 项目不可调用。

## 3. 非目标

- 不迁移用户真实项目；
- 不生成正文；
- 不执行语义影响分析；
- 不实现 Author Model；
- 不让 Agent 直接调用完整领域命令集；
- 不在本阶段删除旧 Bible Store。

## 4. 实施顺序

### T13.1 Schema 与兼容策略

为 Character、Relationship、Place、WorldRule、MysteryTruth、Clue、RedHerring、Foreshadow、TimelineEvent、Series/Book/Arc/Chapter/Scene/Beat 定义最小字段和 schema version。

先用当前 fixture 和三组边界样例写 schema 测试：缺字段、未知字段、旧字段映射。未知旧字段进入显式 `legacy_metadata`，不能静默丢失。

### T13.2 稳定 ID 与引用

所有引用只存 ID。实现显示名索引和歧义错误：同名角色允许存在，按名称查询多结果时要求用户选择 ID。为 V1 导入生成确定性 ID 映射，映射表进入 ImportReport。

### T13.3 领域命令与部分修改

按模块文档逐个实现命令。每个命令先写：成功、no-op、缺失引用、基础 revision 过期、部分字段不擦除的测试。

领域命令返回 DomainChange，不调用 CommitRepository。Application handler 负责：读 Snapshot → 执行领域命令 → Guard → Commit。

### T13.4 三层事实与状态计算

建立 TruthFact、KnowledgeState/BeliefState、Disclosure。实现 `StoryStateAtBeat` 的确定性折叠：按结构顺序应用 Beat state changes。出现缺失前置或同一顺序冲突时产生领域错误/警告，不让结果取决于字典遍历顺序。

### T13.5 悬疑规则

实现第一批确定性规则：

- 时间和地点冲突；
- 角色提前知情；
- Clue 没有关联 Truth；
- Red Herring 没有真实人物依据；
- Setup/Payoff 引用断裂；
- 必要线索在项目设定的公平性截止点前没有 Disclosure。

规则返回结构化 Finding Seed；严重度由项目策略决定。

### T13.6 Dependency Edges

领域命令同步产生 `motivates/reveals/changes_state/sets_up/pays_off/requires/constrains` 边。删除/替代对象时更新 relation revision，不物理删除历史边。

### T13.7 查询投影

投影由 Snapshot 计算或缓存：Canon Snapshot、Narrative Tree、Story State at Beat、Disclosure Timeline、Mystery Fairness、Open Setups。缓存必须带 Commit ID，head 改变后不能返回旧结果。

### T13.8 V1 Dry-run Mapper

读取 Phase 11 fixture，生成：实体映射、无法表达字段、歧义、错误、预期 Artifact/Relation 数。只返回报告和 Proposal，不写 V2 Repository。此步骤为 Phase 17 真迁移提供证据。

## 5. 建议文件边界

```text
src/hastings/domain/story/
  canon/{models.py,commands.py,rules.py}
  narrative/{models.py,commands.py,order.py,state.py}
  mystery/{models.py,rules.py,views.py}
  changes.py
src/hastings/application/handlers/story_commands.py
src/hastings/application/queries/story_queries.py
src/hastings/interfaces/importers/v1_story_mapper.py
tests/v2/story/
```

## 6. 关键测试

- 同名角色具有不同 ID；
- 改名后所有引用有效；
- partial revise 不擦除未提供字段；
- ChangeCharacterRole 产生正确边变化和影响种子；
- MoveBeat 保留 ID 并改变状态计算顺序；
- Truth/Knowledge/Disclosure 互相独立；
- 提前知情与合法误解能区分；
- Red Herring 的真实原因规则；
- Setup 删除后 Payoff stale；
- 公平性截止策略可配置；
- 投影绑定 Commit；
- V1 dry-run 记录所有未知字段，不写 Repository。

## 7. 验收场景

构造一个 3 章悬疑基线：1 个真凶、1 个协助者、3 条必要线索、2 条误导、1 条角色误解。通过命令建立并提交；读取 Story State 与 Disclosure Timeline；故意让角色提前得知秘密并得到定位明确的错误；修正后重新提交并通过确定性规则。

## 8. 退出条件

- 领域层无数据库、Web、Agent/Tau import；
- 所有写命令都经 Application Handler 和 Commit Service；
- V2 项目无法调用 `bible_upsert`；
- V1 项目行为未变；
- dry-run fixture 的每个源字段都有 mapped/legacy/ambiguous/error 结果；
- 模块文档更新为 as-built，构建笔记完成四透镜；
- Phase 15 可以仅使用 Artifact、Relation 和 Story views 计算确定性影响。

## 9. 停止条件

- 当前 V1 字段无法无损保留且没有作者认可的 legacy 策略；
- Story schema 试图一次覆盖所有类型导致核心命令无法冻结；
- 为适配 UI 出现直接修改 Revision content 的要求。

出现后缩小第一版 schema，保留显式 extension，不创建旁路。

