# Phase 8 构建笔记：引导式生命周期（intake → 确认 → 产出 → 维护）

> 日期：2026-10-10 ～ 2026-10-11 ｜ 计划：[phase-08-guided-lifecycle-plan.md](phase-08-guided-lifecycle-plan.md)
> 一句话：把「一个工作区从构想接收到终稿维护」的全生命周期做成系统可表达、工具层可强制的形态——程序为骨（阶段门/预检/状态注入），提示词为肉（intake 协议）。

## 1. 交付内容

| 任务 | 提交 | 内容 |
|---|---|---|
| T1 | `4a1d0f9` | `story.yaml` 生命周期：六阶段迁移表（`bible/story_state.py` 纯函数）+ `StoryDoc` 读写（旧工作区 intake 兜底）+ `store.story_path` + session 接线 |
| T2 | `245f3cb` | `story_stage` 工具：advance/rollback + reason 必填 + 迁移留痕；确认门前置条件由 `_derive_prereqs` 从账本实况派生 |
| T3 | `6585b6a` | outline 第六账本：`OutlineEntry` + 章号唯一校验，经注册表三处注册泛化读写；确认门 outline 计数接线 |
| T4 | `7887066` | `write_chapter` 阶段门 + 分级预检（error：阶段/章纲缺失/无角色；warning：method 未锁、plant_plans 伏笔状态）+ outline 落盘自动推进（planned→written，force→revised） |
| T5 | `8115457` | 状态注入 B1：`build_workspace_status` 纯函数 + system prompt「当前工作区状态」节 + `HastingsSession.prompt()` 委托前 `_refresh_system()` |
| T6 | `c106ba3` | WORKFLOW_SECTION 重写为阶段感知协议：intake 三段式（五维摘要/缺口清单/≤5 问）、bible_drafting 建账顺序与三类点确认、writing 章纲锚定 |
| T7 | `612a19b` | REST `GET /api/{slug}/story` + ChatStream react-markdown/remark-gfm 渲染 + 顶栏阶段徽章（六态配色）+ 章节进度 n/m |

测试规模：后端 573 passed（P8 新增约 90 条），前端 vitest 201 passed（新增 19 条）。

## 2. 关键决策（D1–D8 落地记录）

### D1 story.yaml：工作区级生命周期（非账本）

- 位置与六账本平级（`<workspace>/story.yaml`），`stage` + `stage_history` 迁移留痕；旧工作区缺失按 intake 兜底，首次写路径触发落盘——不强制迁移旧数据；
- 迁移表集中在 `story_state.py` 纯函数（同 `state_machine.py` 哲学），正反向边逐一登记，非法迁移在 story_stage 工具层拒绝。

### D2 story_stage：唯一写入口 + 确认门从账本实况派生

- advance 前置条件（→bible_confirmed 需角色 ≥2、method 全 locked、outline ≥1）不由模型自报，`_derive_prereqs` 每次从账本实况读数——确认门的「证据」永远是文件系统里的账本，不是对话记忆；
- REST 不暴露 story.yaml 写口，「写入口唯一」红线守住。

### D3 outline 第六账本：章号唯一 + 自动对账

- 章号唯一性在工具层 upsert 时校验（重复章号拒绝，三要素指路）；状态 planned→written→revised 沿注册表迁移边；
- **write_chapter 落盘后自动推进该章 outline 状态**：正文与章纲由工具层对账，模型不必记这回事——「自动对账」是本阶段最重要的减负设计，60 章长篇的连贯性靠「章纲 + 账本摘要」而非无限上下文。

### D4 分级预检：error 阻断 / warning 进 details

- 预检顺序钉死：草稿锁/run_kind 守卫 → 阶段门 → 章纲门 → 角色门 → 落盘；顺序由测试固化（[test_tools_chapter_precheck.py](../tests/test_tools_chapter_precheck.py)），防后续改动漂移；
- warning 全部进 `AgentToolResult.details["precheck_warnings"]`，前端工具卡既有 details 摘要行零改动直接展示——分级不是两套事件，是一套事件的两种去向。

### D5 状态注入 B1：注入节只陈述事实，不解释怎么做事

- `HastingsSession.prompt()` 委托前 `_refresh_system()` 重算 system（复用 P6 D3 赋值手法，零内核改动）；reconcile 队列经同一入口自动覆盖；运行中不刷新（与 busy 防护同语义）；
- **位置决策偏离计划**：状态汇总纯函数计划写在 `bible/story_state.py`，实际落在 `bible/stats.py`——store.py 已导入 story_state（StoryDoc），再反向导入 store 即循环；且 story_state 的模块宪章是「纯迁移表，不碰文件系统」，状态汇总要读盘，本来就放错。教训见「踩过的坑」；
- 注入节格式（语言节之后、工作流程之前）：先事实后协议，职责分离。

### D6 intake 协议：prompt 讲协议，门在工具层

- 通用规则收敛为 4 条 + 四阶段协议段；当前阶段感知交给 D5 注入节，prompt 永不描述「现在是什么阶段」；
- intake 协议违规（模型不等确认就建账）的代价被工具层限制在「多建了账」而非「写飞了正文」——write_chapter 阶段门拦住一切；这是风险表预判的兑现。

### D7 前端：MD 渲染 + 区分度 + 阶段徽章

- agent 消息 react-markdown + remark-gfm（GFM 表格是缺口清单/对比表刚需），组件 `memo` 化；用户消息保持纯文本——只有 agent 产出是结构化的；
- 顶栏阶段徽章六态配色（`state/story.ts` 纯映射，未知 stage 降级原样展示）+ 章节进度 n/m + 完备度速览收进 title（顶栏一行信息密度约束）；slug 变化 + run 结束 refetch，失败降级隐藏徽章。

### D8 REST story 端点：单一只读派生

- `_session(slug)` 守门 + stats 复用，零新存储；账本损坏 422、IO 故障 500——与 stats 端点同出口、同三要素 detail 格式。

## 3. Agent 设计经验（四透镜）

### 3.1 错误消息是喂给模型的 API——阶段门的拒绝消息是一次「状态教育」

write_chapter 的阶段门拒绝消息固定三要素：哪里错（当前 stage=intake，你请求写第 1 章）、为什么错（写作门只对 writing/revising 放行）、怎样算对（先建 Bible 并请作者确认，再用 story_stage 工具推进阶段）。它不是给人看的报错——模型收到后会照着「怎样算对」调 story_stage，完成第一次真正的生命周期自愈。同样手法用在章纲门（指向 bible_upsert outline）与角色门（指向 save_character）；REST story 端点的 422 detail 也保持同构（前端/审计是另一类读者）。

### 3.2 硬约束放工具层，prompt 只负责解释世界——「热心」的模型拦得住

intake 协议（D6）在 prompt 里写「禁止建账、禁止写正文」，但真正的保险丝是：就算强模型热心突破，write_chapter 阶段门在工具层拦住一切——协议违规的代价被封顶在「多建了账」。反过来，协议本身（建账顺序、三类点确认、≤5 问）纯靠 prompt 表达，因为它们是礼仪不是安全线。判断一条约束放哪层的标准：违反它的最坏代价是什么——代价不可接受的进工具层，代价可接受的进 prompt。

### 3.3 泄压阀三情形——「空工作区」是合法快照，不是故障

状态注入的降级设计完整覆盖三情形：参数违规（不适用——注入无参数）；**合法但空结果**（新工作区全零计数按正常快照返回，`章纲未覆盖：全部未建` 就是给模型的下一步指路，防止模型把「没有数据」当错误陷入道歉-重试循环）；**环境故障**（账本/story.yaml 损坏 → stderr 三要素告警 + 跳过该节，写作流不中断——P5 OSError 教训的直接复用）。REST 侧同一哲学的镜像：损坏 422（可修复，指路）、IO 500（环境故障，归因）。

### 3.4 可观测性是事件契约的一部分——warning 与留痕都走既有通道

预检 warning 进 `details["precheck_warnings"]` 而非新事件类型——前端工具卡的 details 摘要行零改动展示，事件契约零变更；story_stage 每次迁移留痕进 story.yaml（who/when/why），生命周期有了可审计的时间线；story 端点纯只读派生，不给系统增加第二个事实源。可观测性不是加日志，是把观察点设计进既有契约。

## 4. 踩过的坑

1. **循环导入：状态汇总函数不能放 story_state.py**——store.py 顶部就 `from ... story_state import StoryDoc`，story_state 反向导入 store 即循环；且该模块宪章是纯迁移表。修正：放 stats.py（store 的下游，且「不引入第二份数据源」约定本来就归它管）。计划阶段没画导入方向图，实现时才发现——**给新模块定职责时先画 import DAG**。
2. **注册表输出按 name 排序**——泛化摘要与 bible_types 的测试断言两次被排序打脸（T3），期望值从「书写顺序」改为「排序后顺序」。教训：断言派生数据顺序前，先确认数据源有没有隐式排序。
3. **计划文档的任务表把 T5 函数归属写错**（story_state.py）——计划里「已核实的依赖事实」表格没覆盖新函数与既有模块的导入关系。后续计划表增加「新代码放哪」列时须先跑一遍导入方向推演。

## 5. 验证输出存档（七道门 + 红线，T7 后复跑全绿）

| 门 | 结果 |
|---|---|
| pytest | 573 passed, 1 warning（11.8s） |
| ruff check / format --check | 0 errors / 150 files formatted |
| mypy | Success: no issues in 77 source files |
| tsc --noEmit | 0 errors |
| vitest | 201 passed (20 files) |
| frontend build | ✓ built in 12.4s |
| tau 内核 diff | 空（82bdb68..HEAD，src/tau_agent、src/tau_ai 零改动） |
| 零 import tau_coding | ✓（仅注释提及设计出处） |

## 6. DoD 对照（计划 §1 八条）

| # | 验收标准 | 结论 |
|---|---|---|
| 1 | 阶段门：六阶段迁移表 + write_chapter 阶段门三要素拒绝 | ✓ T1+T4（test_story_state / test_tools_chapter_precheck） |
| 2 | story_stage：advance/rollback + reason + 确认门前置校验 + 留痕 | ✓ T2（test_tools_story_stage） |
| 3 | outline 第六账本：章号唯一 + 泛化读写 + 落盘自动推进 | ✓ T3+T4（test_tools_outline） |
| 4 | 分级预检：error 三类阻断 / warning 两类进 details | ✓ T4（19 条专项测试） |
| 5 | 状态注入 B1：prompt 前刷新 + 两快照 + 降级泄压阀 | ✓ T5（test_prompts / test_server_session） |
| 6 | intake 协议：三段式 + 建账顺序 + 三类点确认 | ✓ T6（test_prompts 5 条协议断言） |
| 7 | 前端：MD 渲染 + 消息区分 + story 端点 + 阶段徽章 | ✓ T7（vitest 201 + build 全绿） |
| 8 | 红线：tau diff 空 / 零 tau_coding / 写入口唯一 / 七道门全绿 / DESIGN §8 顺延 / 四透镜专节 | ✓ 本节 + §5，§8 顺延见立项提交 `38e8940` |

## 7. 已知留白（转入后续阶段）

- **真 LLM intake 冒烟走查未做**：协议文本有确定性测试钉死，但强模型实际遵循度（三段式是否输出、是否抢跑建账）需真模型走查——留待 P9 开工前用 CLI 跑一遍测试构想；
- MD 流式渲染只做了组件 memo，段落级增量渲染未做——超长回复流式期仍整段重渲，卡顿出现时再优化；
- 作者确认结构化交互卡、intake 问卷 UI 化 → 后续 UI 打磨阶段（计划 §4 移交清单）；
- outline 幕级汇总视图 → P10/P11；
- story.yaml 人工编辑入口 → 有真实诉求再立项。

## 8. 评审记录

- 2026-10-11：用户授权一次性完成 T5–T7 与收尾；本笔记自评七道门全绿、DoD 八条逐项过；R5 验收待用户复核。
