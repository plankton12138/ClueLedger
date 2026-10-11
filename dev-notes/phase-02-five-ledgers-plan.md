# Phase 2 开发计划：五账本全量与状态机

> 对应 [DESIGN.md](DESIGN.md) §8 路线图 **P2 Bible 完整化** 行。
> 状态：**✅ 已收口**，构建笔记见 [phase-02-five-ledgers.md](phase-02-five-ledgers.md)。

## 0. 已核实的依赖事实（计划的输入）

开工前对 P1 交付代码与 tau 内核的核查结论：

| 结论 | 证据 | 对 P2 的影响 |
|---|---|---|
| [models.py](../src/hastings/bible/models.py) 仅有 `Character`，docstring 已预留"弧光阶段等状态机字段属于 P2" | [models.py](../src/hastings/bible/models.py#L1-L5) | character 增量加 `arc` 字段，其余四账本模型新建 |
| `bible_upsert/bible_query` 的 `entity_type` 仅允许 `"character"`，错误文案预告"clue/foreshadow/method/timeline 将在 Phase 2 提供" | [bible_tools.py](../src/hastings/tools/bible_tools.py#L28-L31) | 泛化这两个工具是本阶段核心改动；P1 的"P2 才支持"测试路径将被真实实现替换 |
| [store.py](../src/hastings/bible/store.py) 已收敛全部 yaml 调用并做 slug/实体名校验 | [store.py](../src/hastings/bible/store.py#L1-L8) | P2 单文件账本沿用此约束：yaml 调用只进 store，业务只见 Pydantic 模型 |
| `bible_upsert` 已把 Pydantic `loc: msg` 原样透传进 `ToolInputError` | [bible_tools.py](../src/hastings/tools/bible_tools.py#L72-L79) | 状态迁移校验的错误消息沿用同一通道与三要素格式 |
| `cli.py` 的 `/characters` 直读入口已有 try/except 防损坏 YAML | [cli.py](../src/hastings/cli.py#L146-L153) | P2 泛化 `/bible` 命令时沿用该模式（R3 #4 教训） |
| `AgentToolResult.details` 已放结构化数据（路径、found 等） | [bible_tools.py](../src/hastings/tools/bible_tools.py#L84-L87) | timeline_check 的冲突列表放 details，给 P3 前端留数据 |
| 集成测试已建立 FakeProvider 剧本 + 三层断言范式 | [test_writing_loop.py](../tests/test_writing_loop.py#L80-L102) | P2 新剧本照此扩展，不发明新范式 |
| pyproject 依赖：pydantic + pyyaml + python-dotenv，无其他 | P1 T1 | **P2 零新增依赖**（D6） |

## 1. 目标与验收标准

**目标**：把 P1 的"单一角色卡 Bible"升级为完整五账本（角色/线索/手法/伏笔/时间线）；状态迁移、手法锁定、时间线冲突检测全部由**工具层强制**——"工具是账本的守门人"（DESIGN.md §3.3）从设计原则变成可测试的事实。

**验收标准**（DESIGN.md §8 原文）：

> 状态机/时间线测试全绿

展开为可检查的六条：

1. 五账本模型 + 状态机单测全绿：每类账本至少覆盖一条**非法迁移**，错误消息含三要素（哪里错、为什么错、怎样算对——合法去向列表）；
2. `method_lock` 后 `bible_upsert` 修改该方法必须被拒（提示先 unlock）；`method_unlock` 后可改；锁定/解锁事件记入 history；
3. `timeline_check` 对构造的时间冲突必须命中（按 D3 决策的规则集），干净时间线零误报；
4. FakeProvider 集成测试：剧本覆盖"角色+线索+伏笔+手法起草→锁定→写章→状态迁移→时间线检测"全流程，三层断言（事件序列/落盘/消息历史）；
5. 真实端点人工验收：终端完成同一流程，transcript 存 `dev-notes/artifacts/p2-acceptance-transcript.md`；
6. P1 存量工作区向后兼容：只有 `characters/` 的旧工作区照常打开（缺省账本视为空，`character.arc` 缺省 `flat`）。

**非目标（本阶段明确不做）**：

- 不做 `outline.yaml` 章节大纲账本（D5：大纲作为 `write_chapter` 的输入要点已够用，账本化推迟）；
- 不做 `write_chapter` 的 Bible 一致性预检、手法矛盾描述拦截（需要语义比对，P5 审计员）；
- 不做 method 对模型的"部分加密"（D4：与 fair-play 泄漏检测耦合，P5 统一设计）;
- 不做 `foreshadow_audit` 对账工具（P5）；
- 不做正文段落级双向引用（timeline 只记 chapter 级关联）；
- 无 Web、无 RAG。

## 2. 设计决策（本阶段新增，需评审确认）

### D1 状态迁移表（全部允许边，未列出的迁移一律拒绝）

| 账本 | 初始状态 | 允许迁移 | 语义 |
|---|---|---|---|
| character（弧光）| `flat` | flat→developing，developing→transformed | 角色弧光三阶段 |
| clue | `planned` | planned→planted，planted→discovered，discovered→interpreted，interpreted→resolved | 线索生命周期，严格线性（没写进正文谈不上被发现）|
| foreshadow | `planned` | planned→planted，planted→echoed，planted→resolved，echoed→resolved | echo 可选：埋设后可直接回收 |
| method | `drafted` | drafted→locked，locked→revealed，locked→drafted（仅 `method_unlock` 可走）| 锁定后须显式解锁才能再改 |

- timeline **无状态机**：事件只增改，无生命周期；
- 新建实体必须从初始状态开始（`data.status` 传其它值即拒）——防模型直接造一个 `resolved` 的伏笔绕过流程；
- upsert 未传 `status` → 保持旧值（不算迁移）；传入与旧值相同 → no-op 放行。

### D2 时间线时间模型：结构化整数天 + 可选时钟

`TimelineEntry` 用 `{day: int（故事内第几天，从 1 起），t_start/t_end: "HH:MM" 可选}`；相对时间（"三天后"）由模型**换算成 day 落盘**，原文叙述进 `note` 字段。冲突检测因此是纯整数/字符串区间运算——完全确定性、可单测。

- 备选（不采用）：自由文本时间 + LLM 判冲突——放弃确定性，测试只能覆盖 happy path，违背"纯规则检测"（DESIGN.md §3.3）。

### D3 不在场证明破绽检测（timeline_check 的招牌规则）——推荐方案 A

- **方案 A（推荐）**：`Method` 增加结构化字段 `alibi_claims: [{person, day, t_start, t_end, claim}]`；检测 = 与 timeline 中该人物同 day 的真实事件区间**求交**，相交即破绽。完全确定性，直击"不在场证明"这一悬疑核心，简历叙事最强（"账本守门人自动识破不在场诡计"）；
- 方案 B（最小）：只检测事件是否落在 `method.time_window` 之外。更简，但覆盖不了核心概念。
- A 的代价：schema 约 +20 行、检测约 +30 行、测试若干；风险可控。

### D4 method"部分加密"推迟

P2 中 method.yaml 对写作 agent 完全可见——它本来就是设计诡计的人。"正文工具读不到凶手字段"（DESIGN.md §2.2 注）与 fair-play 泄漏检测耦合，统一到 P5 审计员阶段设计；届时在构建笔记补记 §2.2 引用的不存在的"§5.3"。

### D5 推迟项汇总

outline.yaml 账本化、段落级双向引用、锁定后 write_chapter 矛盾拦截——均推迟（理由见非目标）。**范围红线：本阶段不偷偷上线其中任何一项。**

### D6 依赖：零新增

pydantic（模型+Literal 校验）+ pyyaml 已覆盖全部需求。

### D7 存储形态：单文件账本 = 带主键的列表

`clues.yaml / foreshadows.yaml / method.yaml / timeline.yaml` 均为列表，`name` 为主键，upsert 按 name 覆盖；character 保持 P1 一人一文件不动。method 允许多条（支线案件），锁定按 name——这也是对 DESIGN.md §3.3 `method_lock` 参数的必要扩展（加 name）。

### D8 跨账本引用校验：timeline 参与者必须已建档

`bible_upsert(timeline)` 时校验 `participants` 中每个名字存在于 characters，缺失则报错并指引"先用 bible_upsert 建角色"。理由：人物分身检测的语义直接依赖参与者身份，这是守门人职责而非过度设计。其余跨账本引用（clue→chapter 等）P2 不校验。

## 3. 任务分解

### T1 数据模型与状态机（测试先行）

- `hastings/bible/models.py`：`Character` 加 `arc: Literal["flat","developing","transformed"] = "flat"`；新建 `Clue`（content/chapter/points_to/is_red_herring/is_genuine + status）、`Method`（description/time_window/requirements/alibi_claims 按 D3/history + status）、`Foreshadow`（content/plant_chapter/plant_mode: literal[explicit,hint,symbolic]/expected_resolve_chapter + status）、`TimelineEntry`（day/t_start/t_end/chapter/participants/note，按 D2）；
- `hastings/bible/state_machine.py`（新建）：`TRANSITIONS` 表、`INITIAL_STATUS`、`validate_transition(entity_type, old, new)`、`InvalidTransitionError`——错误消息三要素；
- 测试 `tests/test_state_machine.py`：合法边全过、每账本至少一条非法边断言错误消息含合法去向、新建非初始状态拒绝。

### T2 存储层扩展（测试先行）

- `store.py`：四账本 path 属性 + 泛型 `load_ledger/save_ledger`（`TypeVar` + `model_validate` 收窄，yaml 调用仍收敛本模块）；P1 角色函数不动；
- 测试扩展 `tests/test_bible_store.py`：往返一致、缺文件视为空、**P1 旧工作区（仅 characters/）兼容**（验收标准 6）。

### T3 bible_upsert / bible_query 泛化（测试先行）→ CP1

- 实体注册表模式：`entity_type → (model_cls, 账本路径, 摘要 formatter)`，拒绝 if-else 汤（R3 专查）；
- upsert 接入 `validate_transition`（新建/更新两条路径）+ D8 参与者校验；
- query 五账本统一：带 name 返回 YAML 全文，不带返回摘要列表；查无返回引导性提示（泄压阀：空结果≠error）；
- `render_call` 泛化（"写入线索 C1""伏笔 F1 → planted"）；
- 测试：每账本正常路径 + 非法迁移 + 参与者未建档，断言错误消息。

### T4 method_lock / method_unlock（测试先行）

- `tools/method_tools.py`：`method_lock(name, chapter, reason)` 走 drafted→locked；`method_unlock(name, reason)` 走 locked→drafted（**仅此工具允许此边**）；两次操作都追加 `history`（章节+理由+时间戳）；
- 与 upsert 协同：locked 状态下 `bible_upsert` 修改 method 任何字段必须被拒，提示"先 method_unlock"（错误消息给下一步动作）；
- 测试：锁定→修改被拒→解锁→修改成功→再锁定→revealed；history 断言。

### T5 timeline_check（测试先行）→ CP2

- `hastings/bible/timeline_check.py` 纯函数（账本快照进，冲突列表出），规则按 D3 决策：
  - R0 数据自检：t_end < t_start、day < 1、时刻格式非法（数据错误回喂，非环境故障）；
  - R1 人物分身：同角色同 day 时钟区间重叠（不同事件；相切不算）；
  - R2 不在场破绽（方案 A）：alibi_claims 与该人物同 day 真实事件区间相交；
- `tools/timeline_tools.py` 包装：可选章节范围参数；无冲突返回"时间线自洽"（合法空结果正常返回，不报 error）；冲突列表同时放 `details`（P3 前端数据源）；
- 测试：逐规则构造命中用例 + 干净账本零冲突。

### T6 集成测试扩展

`tests/test_writing_loop.py` 新增全流程剧本（FakeProvider 预录）：

```text
user: "建角色、登记线索与伏笔、起草手法并锁定，然后写第一章"
  → bible_upsert(侦探) → bible_upsert(嫌疑人)
  → bible_upsert(clue) → bible_upsert(foreshadow) → bible_upsert(method, drafted)
  → method_lock → write_chapter(ch01)
  → bible_upsert(foreshadow, planted)   # 章后对账：伏笔已埋
  → timeline_check → 总结
```

三层断言照旧；落盘断言扩到 4 个 yaml + 1 个 md；断言 method history 含锁定记录、伏笔状态迁移落盘。

### T7 prompt 与 CLI 小步更新

- `prompts.py` 工作流程节：状态变更必须走工具、锁定/解锁语义一句话版（细节仍在各工具 `prompt_guidelines`，不重复维护）；
- `cli.py`：`/characters` 泛化为 `/bible [type]`（缺省打印五账本摘要；直读 try/except 防损坏 YAML 崩 REPL，沿用 R3 #4 模式）；
- 测试适配：`test_prompts.py`、`test_renderer.py`、`test_entry.py`。

### T8 质量门 + 红线 + 真实端点人工验收

1. 四道门全绿（ruff check / ruff format / mypy / pytest）；
2. 红线重跑：`tau_ai`/`tau_agent` 与上游 diff 为空、零 import tau_coding、依赖零新增（输出存档进构建笔记）；
3. 真实端点完成验收标准第 5 条，transcript 存档；若暂无 key 按 P1 T7-4 惯例记留白。

### T9 构建笔记 + R5 关闭

`dev-notes/phase-02-five-ledgers.md`：交付物、D1–D8 决策落锤、「Agent 设计经验」四透镜专节（重点：状态机错误消息即 API 的实践、跨账本校验的守门人边界、timeline_check 泄压阀三情形落点）、四道门与红线输出、R3/R5 结论。

## 4. 评审方案

### R1 自动化门禁（每次提交前）

```powershell
uv run ruff check .; uv run ruff format --check .; uv run mypy; uv run pytest
```

### R2 架构红线评审（阶段验收时）

- `tau_ai`/`tau_agent` 与上游 diff 为空；
- hastings 零 import `tau_coding`；
- 依赖零新增（diff pyproject 确认）。

### R3 代码评审（P2 全量 diff 完成后）

TRAE-code-review skill 评审，重点关注清单：

1. 状态迁移校验是否**只存在于工具层**（store 纯读写、prompt 无硬约束）；
2. 非法迁移错误消息是否三要素齐全（模型可据此自我修正）；
3. timeline/alibi 区间运算边界是否正确（相切不算重叠、跨零点时刻）；
4. `/bible` 直读入口是否沿用 try/except 防崩 REPL；
5. `timeline_check` 结果 details 是否结构化（事件契约服务 P3 前端）；
6. 范围：D5 推迟项无一带入。

问题分级沿用：blocker 必修，建议记入构建笔记"已知留白"。

### R4 检查点评审（执行中，两个 checkpoint）

- **CP1（T3 完成后）**：逐账本核对 Pydantic 字段 ↔ JSON Schema ↔ 错误消息三者一致；D1 迁移表逐边核对。
- **CP2（T5 完成后）**：timeline_check 规则用例覆盖核对；纯函数干跑一遍构造冲突。

### R5 阶段验收评审（全部任务完成后）

对照 DoD（§5）逐条打勾留证据；结论写入 T9 构建笔记。**R5 通过 = P2 正式关闭**，才允许进入 P3。

## 5. DoD（Definition of Done）清单

- [ ] 依赖零新增；`uv.lock` 无实质变化
- [ ] 五账本模型 + 状态机单测全绿；非法迁移错误消息三要素齐全
- [ ] method_lock/unlock 全链路测试通过；history 记录锁定/解锁
- [ ] timeline_check 逐规则命中构造用例；干净账本零误报
- [ ] bible_upsert/query 五账本泛化；查无结果返回引导性提示（非 error）
- [ ] P1 存量工作区向后兼容（缺省账本=空，arc 缺省 flat）
- [ ] 集成测试三层断言全过（含 method history 与伏笔状态落盘）
- [ ] prompt/CLI 更新；`/bible` 直读防崩
- [ ] 四道质量门全绿；mypy strict 无新增豁免
- [ ] 红线：tau_ai/tau_agent diff 为空；零 import tau_coding；依赖零新增
- [ ] 真实端点人工验收 + transcript 存档（或按 T8-3 记留白）
- [ ] R3 评审完成，blocker 清零
- [ ] `dev-notes/phase-02-five-ledgers.md` 构建笔记发布

## 6. 风险与对策

| 风险 | 概率 | 对策 |
|---|---|---|
| 状态机过严，模型频繁被拒陷入循环 | 中 | 错误消息永远列出合法去向（三要素）；验收 transcript 观察修正行为；确属过严则评审修订 D1 边表 |
| D2 的 day 换算对模型不自然（相对时间换算出错） | 中 | 工具 guidelines 给换算示例；R0 把非法值当数据错误回喂，模型可自我修正 |
| D3 方案 A 的 alibi_claims schema 让模型困惑 | 低 | 字段 description 写清示例；FakeProvider 剧本即标准答案 |
| 五账本 upsert 分发逻辑膨胀成 if-else 汤 | 中 | T3 实体注册表模式；R3 专查 |
| D8 参与者校验与"先写后建"工作流冲突 | 低 | 报错指引先建角色——这本来就是期望工作流 |
| P1 存量 YAML 缺 arc 字段加载报错 | 低 | arc 带默认值；store 测试加旧格式兼容用例 |
| 范围失控（顺手做预检/审计/部分加密） | 中 | 非目标清单即红线；R3 第 6 条专查 |

## 7. 执行顺序

```text
T1 模型与状态机（测试先行）
→ T2 存储层扩展（测试先行）
→ T3 upsert/query 泛化（测试先行，CP1 评审）
→ T4 method_lock/unlock（测试先行）
→ T5 timeline_check（测试先行，CP2 评审）
→ T6 集成测试扩展
→ T7 prompt + CLI 小步更新
→ T8 质量门 + 红线 + 真实端点验收（需用户 API key 在场）
→ R3 代码评审
→ T9 构建笔记 + R5 验收
```

提交策略沿用 P0/P1：原子提交（feat(bible) / feat(tools) / test / docs 分开），**每笔提交前先经用户批准**。
