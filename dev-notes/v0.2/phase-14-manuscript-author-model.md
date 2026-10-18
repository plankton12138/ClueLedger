# Phase 14 构建笔记：正文版本、人工编辑与作者模型

> 关键词：Manuscript、TextUnit、Edit Operation、Patch Proposal、逐 hunk 接受、乐观并发、保存与分析解耦、Outbox 补偿、Author Observation、Preference Hypothesis、置信度门槛、风格方向聚合、历史恢复、V2 REST、重启恢复
> 状态：已完成（2026-10-15）。计划：[phase-14-manuscript-author-model-plan.md](plans/phase-14-manuscript-author-model-plan.md)
> as-built 规格：[modules/manuscript-author-model.md](modules/manuscript-author-model.md)

## 1. 交付内容

| 交付物 | 位置 | 说明 |
|---|---|---|
| 正文领域模型 | `src/hastings/domain/manuscript/`（models / operations / diff / commands / catalog / kinds / errors / schema） | Chapter / TextUnit / Edit Operation（insert/replace/delete/split/merge/move）；段落谱系 `derived_from`；删除不物理移除 |
| 章基础（乐观并发） | `domain/manuscript/catalog.py` `content_base` / `chapter_base` | 活动段落有序 `(unit_id, 文本)` 的内容哈希；目录读投影与命令应用后状态共用同一计算（评审 #4） |
| Agent Patch | `domain/manuscript/models.py`（ManuscriptPatchProposal / PatchHunk）+ `commands.py`（propose_patch / accept_patch_hunks / reject_patch） | 提案只登记 hunk 与所基于的章基础，**无法直接提交**；逐 hunk 接受把本提案造成的移动原子记回 `applied_base`（评审 #4） |
| 作者模型领域 | `src/hastings/domain/author_model/`（models / rules / commands / catalog / kinds） | AuthorObservation / PreferenceHypothesis / AuthorExample；分类管线（规则优先、低置信保持 candidate）、置信度门槛（评审 #1）、风格方向聚合（评审 #2）、scope 优先级、revoke/supersede、样例引用-only + 软删除 |
| 应用层写入口 | `application/handlers/manuscript_commands.py`、`handlers/author_model_commands.py` | 幂等重放（命令身份 + 意图指纹，重放早于命令重执行）→ 读 head → 纯函数命令 → 领域违规转应用信封 → Commit Service |
| 保存工作流 | `application/workflows/manual_edit.py` | 保存只做 Commit 即返回；**不等待任何后台任务**（评审 #3） |
| 分析调度消费者 | `application/workflows/analysis_dispatch.py` | 实现 PublishSink 接在 OutboxPublisher 之后：章提交事件 → 分析 Job；按 event_id 双层去重，消费失败事件保持待发布（补偿由持久 Outbox 承载，评审 #3） |
| 作者学习工作流 | `application/workflows/author_learning.py` | 从保存事件提取 EditSample → 分类 → 归纳/强化偏好 |
| 历史恢复 | `application/workflows/restore_version.py` | 恢复 = 读源 Commit 章文本 → 当前 head 上一次 `save_full_text` 提交；不重写历史；源缺章违规归因 `restore.source` |
| V2 REST 面 | `src/hastings/server/v2.py` + `app.py` 挂载 | 独立 `/api/v2` 前缀：项目创建、章导入、整篇/单章查询、保存、恢复、作者模型 preferences/observations/evidence 查询；错误信封按 code 分类状态码、三要素直通 |
| 基础设施运行时件 | `infrastructure/runtime.py`（SystemClock / UuidIdGenerator）、`infrastructure/jobs.py`（MemoryJobStore） | 端口的真实实现；V2Runtime 装配内存/SQLite 双后端 |
| 测试 | `tests/v2/manuscript/`、`tests/v2/author_model/`、`tests/v2/server/` | Phase 14 专项 **168 项**；覆盖六种操作、谱系、no-op、冲突、部分接受、Patch scope、恢复、分类/激活/撤销、重启恢复（SQLite） |

质量门：全量 pytest **1262 passed**（V2 共 484）；ruff check / format / mypy strict 全绿（2026-10-15）。

## 2. 关键决策

- **段落是最小正文单位，顺序由章显式持有**：结构性编辑只改写章的 `text_unit_ids`，纯替换只改写目标段落；避免整章段落因顺序变化被反复改写。大段落重写允许生成新 TextUnit 谱系（`derived_from`），不以字符偏移当永久 ID。
- **章基础用内容哈希而非 Revision 身份**：命令是纯函数，拿不到提交时才分配的 Revision，但拿得到「应用本批操作后」的全部段落文本。目录读投影与命令层经同一个 `content_base` 计算，逐 hunk 接受才能在提交前算出应用后的基础并原子记回提案（评审 #4 的根修）。
- **Agent Patch 无法直接提交是命令层硬约束**：提案 Artifact 只落 hunk 与状态；正文变化只经 `accept_patch_hunks` 发生。范围硬约束（hunk 引用必须在本章活动段落内）在 `propose_patch` 拒绝，Agent 无法借提案旁改他章。
- **保存与分析完全解耦（评审 #3 的结构性修法）**：保存路径只提交；「待调度」是 Outbox 里的待发布事件（持久化），「已调度」是 `mark_published`（持久化）——补偿与去重不再存于返回值或进程内存。每个正文 Commit 至多一个同类型分析 Job 由消费者按 event_id 保证。
- **作者模型没有自己的存储**：观察、偏好、样例都是普通 Artifact，随 Commit 持久化——这是「重启后偏好状态一致」无需额外机制的原因。
- **低置信证据不激活（评审 #1）**：证据汇总同时检查类别与观察状态/置信度，置信度门槛在 `rules` 层作为可注入策略参数，不是 prompt 叮嘱。
- **分类只回答「是什么」，归纳还需「往哪边」（评审 #2）**：语言风格类别下提取具体方向（如精简 vs 扩写），方向相反的证据互不强化，避免「持续扩写却被归纳为偏好精简」。
- **恢复 = 新 Commit**：恢复复用 `save_full_text` 的 diff 规范化，与人工保存同一写路径、同一套乐观并发与幂等重放；恢复不能绕过并发保护，旧版本永远可追溯。
- **V2 REST 独立前缀、project_id 直连**：`/api/v2` 与 V1 共存互不依赖（退出条件：V2 保存不碰旧 `.revisions`/reconcile 写链）；身份直接用 Repository 的 `ProjectId`，不引入第二套 slug 映射。

## 3. Agent 设计经验（四透镜）

**① 错误消息是喂给模型的 API。** 领域违规三要素（where/why/fix）在应用边界转 `ViolationError` 原样透传，REST 层再按 code 分类状态码、信封直通——`MANUSCRIPT_BASE_CONFLICT` 的 details 带期望与实际基础，客户端拿到的就是重试所需的一切。本轮最有价值的细化是**同一错误码必须能区分错因**：恢复时「章在源 Commit 不存在」的 where 归到 `restore.source`，不与 head 上「章不存在」混淆——模型拿着 `MANUSCRIPT_UNKNOWN_CHAPTER` 就能分辨该换源版本还是该查当前正文。

**② 硬约束放工具层，prompt 只负责解释世界。** 「Agent Patch 无法直接提交」「保存意图只允许当前章」「Patch hunk 不得越章」「低置信证据不激活」全部是命令/规则层的拒绝，不依赖模型自觉。逐 hunk 接受的并发语义也做成硬事实：本提案自己造成的 base 移动记在 `applied_base` 里不算冲突，外部编辑移动基础才显式 `MANUSCRIPT_BASE_CONFLICT`——正确行为靠结构保证，不靠模型「理解」哪次移动算谁的。

**③ 泄压阀——区分违规 / 空结果 / 环境故障。** no-op 保存（文本未变）是合法空结果：`is_empty()` → `NO_CHANGE`，不提交、不产生事件、不调度分析，HTTP 返回 200 带 `status=no_change`——把「没改动」当故障会制造道歉-重试循环。环境故障不进保存路径：JobStore 挂起时保存照常成功响应，任务创建留给 Outbox 消费者重试补偿；消费失败抛异常使事件保持待发布，而不是吞掉。

**④ 可观测性是事件契约的一部分。** 「正文已变」与「下游该分析」经 `manuscript.chapter.committed` 与 Commit 同事务落盘（ADR-007），没有双写窗口；Outbox 同时承载内核事件（commit/revision/branch）与业务事件，同一 Outbox 服务多类读者、各取所需。REST 错误信封的 `details` 结构化保留给前端/审计；进程内已知盲区（prose_guard 告警走 stderr 不入转录）继续记入留白，不假装已覆盖。

## 4. 评审记录

- 计划 §8 退出条件逐条核对：V2 保存完全不调用旧 `.revisions`/reconcile 写链（独立 `/api/v2`，测试走 `build_v2_app`）；保存与分析解耦有故障测试（`test_analysis_dispatch_compensates_after_restart`、`tests/v2/server/test_restart_recovery.py`）；Agent Patch 无法直接提交（命令层约束 + 专项测试）；每条 active 偏好有证据和范围（`preference_evidence` 反查 + 测试清单 14）；前端未动、无自动 force；模块文档 as-built + 本笔记收口。
- 计划 §6 清单 15 项全覆盖（六操作/谱系/no-op/Commit+Outbox/调度故障/并发冲突/Patch scope/部分接受/恢复新 Commit/事实不入文风/多证据激活/scope 优先级/revoke 排除/证据反查/项目偏好不升全局）。
- 计划 §7 验收场景 1–8 闭环：建章三段 → 保存得新 Commit → Provider 故障正文保持 → 重跑分析生成 candidate → 追加证据按策略激活 → Draft Context 可用 → 撤销后排除 → 重启后正文/偏好/任务一致（SQLite 集成验收）。

### 外部 review 收口（2026-10-15）

外部评审发现 3 个 P1、1 个 P2 及完成度缺口，全部收口并以专项测试固化：

- **[P1] 低置信度候选被自动激活**：证据汇总只检查类别未检查观察状态/置信度，两个独立 Commit 中置信度 0.01 的 candidate 仍生成 active 偏好。收口：激活策略把证据可信度纳入判定，置信度门槛为可注入策略参数（`domain/author_model/rules.py`）。
- **[P1] 编辑类别被直接当成偏好方向**：所有语言风格变化被归纳为同一句「偏好更精简的措辞与句式」，持续扩写也会得到「偏好精简」。收口：归纳提取具体方向、区分相反证据，方向互斥的证据不互相强化。
- **[P1] 保存等待分析任务创建、补偿与去重仅存于进程**：`save()` 提交后继续 `await jobs.create()`；失败补偿只活在返回值、去重只活在实例字典。收口：保存只提交；调度改由持久化 Outbox 消费者独立承担——「待调度」与「已调度」都是持久标记，重启后未发布事件重投（补偿）、已发布事件不再出现（去重）。
- **[P2] Patch 无法连续逐 hunk 接受**：首次接受改变正文基础后，提案仍用原始 base 比较，剩余 hunk 报 `MANUSCRIPT_BASE_CONFLICT`。收口：章基础统一为内容哈希（读投影与命令应用后同源），接受后的基础原子记回提案 `applied_base`，比较用 `applied_base or base`——本提案的移动不算外部冲突。
- **完成度缺口**：T14.8 最小 API（本轮交付，仅后端）、历史恢复为新 Commit（`RestoreChapterVersion` + REST 端点 + 验收测试）、重启恢复集成验收（SQLite 下正文/偏好/任务三类事实，`test_restart_recovery.py`）、文档收口（本笔记 + 模块 as-built + 计划状态）——均已补齐。

## 5. 踩过的坑（代码级，辅助记录）

- **`ManualEditResult` 的属性在 `edit` 内**：`result.summary` AttributeError；summary/status 等是内层 `ManuscriptCommandResult` 的字段（部分有代理属性、部分没有），断言前先确认代理面。
- **Outbox 里的事件不止业务事件**：Commit Service 与 Commit 同事务写入内核事件（`repository.commit.created` / `artifact.revision.created` / `branch.head.moved`），`publish_pending` 返回的是全部发布数——「调度了几个分析任务」必须断言 JobStore，不能断言发布数。
- **no-op 测试的自证陷阱**：只回传首段文本等于「删除后两段」，是真实编辑、必得 `committed`；no-op 用例必须回传整章文本。
- **FastAPI 异常处理按异常类型注册**：`HastingsError` handler 捕不住领域异常（`ManuscriptViolation` / `AuthorModelViolation`）；领域违规逃到端点外就是 500。修法：每个领域违规类型单独注册 handler，统一转应用信封。
- **`catalog.entries` 按 artifact_id 排序**：取「第一章」不能取 `entries[0]`——UUID 派生 ID 无语义顺序，必须按 kind 过滤。
- **REST 面的同步/异步边界**：`CommitManualEdit.save` 是 async（领域 handler 同步），端点漏 `await` 得到 coroutine 属性错误；测试侧同步驱动用 `asyncio.run`。

## 6. 留白

- **JobStore 是进程内实现**：分析任务的持久化落地（SQLite JobStore）未做；「任务恢复」当前指 Outbox 事件重投后由新消费者重建任务，任务状态本身跨重启不存活。
- **发布循环未接服务生命周期**：`dispatch_pending` 是手动/定时驱动点；uvicorn lifespan 常驻循环与退避策略留待接入真实部署时补。
- **消费者只建任务不执行**：分析 Worker（真正跑分类/归纳的后台执行器）属后续阶段；`author_learning` 已具备从事件到偏好的完整链路，等待任务执行器接线。
- **重放表在 handler 实例内**：跨进程持久化幂等重放依赖 Commit Service 的内容级幂等（与 StoryCommandHandler 相同）。
- **恢复经 `save_full_text`**：整章文本往返（`\n\n` join/split）语义下，大改段落以新 TextUnit 身份重生，逐 Revision 恢复与 `ReanchorTextUnits` 未实现。
- **版本历史列表端点未做**：`commit_chain` 在事务层，只读端口未暴露链查询；「版本历史可查看」的 HTTP 面等前端页面一起做。
- **作者模型无 HTTP 写出口**：preferences 的确认/编辑/撤销命令在 `handlers/author_model_commands.py` 就绪，REST 写端点与前端「作者模型页」留待 UI 阶段。
- **正文与 Story 关联未打通**：TextUnit 的 `scene_id`/`narrative_chapter_id` 字段已预留但无命令维护，属 Phase 15 变更影响的依赖来源。
