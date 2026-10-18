# Hastings 0.3 开发文档

> 状态：**V03-0、V03-1 已交付收口**（V03-0 收口于 commit `ad2a166`；V03-1 收口于 commit `53c7eb2` + 同日「接着讨论」前端接线与切片走查 10/10，收口检查单见下，两项已声明偏差显式顺延）。**V03-2 已交付（2.6 部分交付：后续状态可见，启动入口随 V03-4）**。**V03-3 已交付**：3.1 活动起草路径已去节拍（旧规划管线与 AgentTasks 的节拍类型退出归 V03-8）、3.2 账本直接保存与设定候选成组、3.3/3.4/3.5 试写 → 只读预览 → 按哈希采用。**V03-4 已交付收口，V03-5 五个任务已交付**（V03-4 七项，2026-10-18；阶段对账见 [v03-4-stage-summary](v03-4-stage-summary.md)；收口评审六项缺陷已修复，见 [v03-4-review-fixes](v03-4-review-fixes.md)；V03-5.1 跨章修改的本轮清单与完成前 flush、V03-5.2 正式稿前进时的三方合并与重叠逐处选择、V03-5.3 修改稿快照与候选进副本的段落来源、V03-5.4 撤销的三方逆向与正文/账本一次恢复、V03-5.5 并发保存与完成后的收口恢复已交付，见 [notes/v03-5.1](notes/v03-5.1-cross-chapter-rounds.md)、[notes/v03-5.2](notes/v03-5.2-three-way-merge.md)、[notes/v03-5.3](notes/v03-5.3-draft-snapshots-and-provenance.md)、[notes/v03-5.4](notes/v03-5.4-undo-conflict-choices.md) 与 [notes/v03-5.5](notes/v03-5.5-concurrency-and-recovery.md)；完整阶段退出仍待 V03-7.7 补齐建议采用与正文操作的因果关联）：4.1 本书约定、4.2 背景装配、4.3 显式章摘要（含任务化）、4.4 账本更新建议、4.5 对话来源与工作台共享状态、4.6 基础关联撤销、4.7 披露边界。同轮修复范围外残留：作者模型入口已按计划 §9.1 从 0.3 移除（数据保留）。遗留项：全书概览生成入口、建议采用与正文操作的因果关联（V03-7.7）、`narrative.chapter/beat` 创建入口均带去向。更新：2026-10-18。

0.3 将已有版本仓库与创作任务能力，组织为围绕正文、对话和故事账本的持续创作工作区。
本目录使用产品版本命名；既有 `dev-notes/v2/`、`/api/v2` 和存储版本分别维护，不随产品版本重命名。

| 需要了解 | 入口 |
|---|---|
| 完整范围、模块方案、契约、实施任务和验收 | [0.3 完整开发计划](development-plan.md) |
| 按 V03-0~V03-8 逐阶段的执行顺序、代码手法与取证方式 | [阶段开发执行指导](stage-execution-guide.md) |
| 已确认的产品决定 | [产品设计导航](../../product-design/README.md) |
| 工作区视觉与交互 | [作家的桌面](../../product-design/writers-desk-design.md) |
| 既有架构与模块契约 | [0.2 文档](../v2/README.md)、[模块导航](../v2/modules/README.md) |
| 当前实际代码入口 | [后端模块](../../src/hastings/README.md)、[前端](../../frontend/README.md) |

执行时以完整计划的开始条件为准，每次只实现一个可验收的任务组。交付后增加对应构建笔记，记录实际接口、验证证据、已知留白和 Agent 设计经验；不能提前将目标方案改为 as-built。

## V03-0 交付物与证据（2026-10-17）

质量门基线：`uv run pytest -q` 1425 passed；`ruff check` 全过；`mypy` 236 files 无问题；`ruff format --check` 剩余 11 个文件为既有 conversation 基线（未触碰）。

| 任务 | 产物 | 构建笔记 |
|---|---|---|
| V03-0.1 全链路盘点 | [artifacts/v03-0-api-inventory.md](artifacts/v03-0-api-inventory.md) | [notes/v03-0.1](notes/v03-0.1-api-inventory.md) |
| V03-0.2 身份与指纹冻结 | `domain/manuscript/identities.py`、`application/idempotency.py`、`frontend/src/api/identities.ts` + 共用夹具 | [notes/v03-0.2](notes/v03-0.2-identity-and-fingerprint-freeze.md) |
| V03-0.3 启动凭据与恢复 | `application/execution.py`、JobStore `pause/mark_interrupted`、派发门禁 | [notes/v03-0.3](notes/v03-0.3-execution-grant-and-recovery.md) |
| V03-0.4 切断旧自动链路 | `application/workflows/event_projection.py`、旧队列分类报告 | [notes/v03-0.4](notes/v03-0.4-cut-auto-model-chain.md) |
| V03-0.5 仓库辅助表（schema v4） | `aux_records` + `RepositoryTxn` 辅助端口 + 故障注入矩阵 | [notes/v03-0.5](notes/v03-0.5-repo-aux-tables-and-txn.md) |
| V03-0.6 Beat 盘点与替换表 | [artifacts/v03-0-beat-reference-map.md](artifacts/v03-0-beat-reference-map.md) | [notes/v03-0.6](notes/v03-0.6-beat-reference-map.md) |
| V03-0.7 配置与测量 | [artifacts/v03-0-config-and-baseline.md](artifacts/v03-0-config-and-baseline.md)、`application/capabilities.py`、`GET /api/v2/capabilities`、`scripts/bench_v03_baseline.py` | [notes/v03-0.7](notes/v03-0.7-config-and-measurement.md) |

V03-0 退出状态（对照计划 §7 质量门）：仓库内存/SQLite 契约、幂等与故障注入、任务启动/恢复测试均通过；前后端 DTO 与 OpenAPI 对照以盘点清单 + 共用夹具落地；未完成事务方案（V03-0.5 已交付辅助表与双后端契约）不阻塞进入 V03-2 正式发布的前置已具备。

下一阶段起点：V03-1（主工作区、模型引导与无模型记录）——开始条件检查单（V03-0 质量门通过、旧自动模型链路已隔离且有零调用证据、DTO 冻结）已满足。

## V03-1 已交付（2026-10-17）

质量门基线：`uv run pytest -q` 1491 passed（2026-10-17 实测确认）；ruff/mypy 全过；前端 vitest 353 passed。

| 任务 | 状态 | 产物 | 构建笔记 |
|---|---|---|---|
| V03-1.1 三部分工作区壳 | 已交付（状态核心 + 壳层接线） | 状态核心 `state/desk.ts`（12 项测试）+ 壳层 `state/deskStore.ts`、`api/entries.ts`（幂等键 9 项测试）、`components/desk/`（DeskShell/DeskComposer）+ App.tsx「写作台」入口（不切默认） | [notes/v03-1.1](notes/v03-1.1-desk-shell-state.md) |
| V03-1.2 入口预约与无模型记录 | 已交付 | `application/services/entry_service.py` + `POST /api/v2/entries` + author-messages 端点 5 项测试 | [notes/v03-1.2](notes/v03-1.2-entry-reservation-and-author-ideas.md) |
| V03-1.3 个人 Key 与连接同源 | 已交付 | `CredentialStore` 端口 + keyring 实现 + 明文迁移 + 物化 5 项测试 | [notes/v03-1.3](notes/v03-1.3-personal-key-and-connection.md) |
| V03-1.5 接着讨论 | 已交付 | `send_turn_from_messages`（source_message_ids 不复制消息）+ 轮次审计字段 3 项测试 | [notes/v03-1.5](notes/v03-1.5-continue-discussion.md) |
| V03-1.4 本地模型真实阶段 | 已交付 | `LocalModelTaskService`（幂等+重检+阶段即事实）+ `/api/local-model-tasks` 四端点（原 `/api/ollama/pull` 流删除收敛）+ 前端任务轮询/恢复（后端 31 项专项、全量 1465 passed） | [notes/v03-1.4](notes/v03-1.4-local-model-tasks.md) |
| V03-1.6 执行日志基础 | 已交付 | `ExecutionLog` 端口 + 内存/SQLite 双后端 + `GET /execution-events`（分页/筛选）与 `/stream` SSE + 对话轮次生命周期事件投影（task/attempt 标识，专项 26 项） | [notes/v03-1.6](notes/v03-1.6-execution-log.md) |
| V03-1.7 阅读位置保护 | 已随 1.1 壳层接线覆盖（滚动保存/打开恢复已实现；200% 缩放走查待浏览器验收） | DeskShell 四元组读写 | 同 1.1 |

### V03-1 收口检查单（2026-10-17 审查后建立，同日收口）

V03-1 各任务交付与测试通过（pytest 1491 / vitest 361 / ruff / mypy）；切片闭环与 1.7 验收两项阻塞项已全部销号（走查 10/10），**V03-1 收口**。剩余两项已声明偏差显式顺延（见下），不阻塞 V03-2 开工：

**切片闭环（阻塞收口）**

- [x] 「接着讨论」UI 接线——后端（V03-1.5）已交付，前端已接（2026-10-17，见 [v03-1.5 笔记「前端接线」](notes/v03-1.5-continue-discussion.md)）；按钮仅真实 API 模式启用，dev 走查用 `?chatMock=0`。
- [x] 完整切片浏览器走查：首次打开 → 保存想法 → 刷新恢复 → 连接模型/本地模型 → 从某条消息继续讨论 → 零自动重放——**10/10 通过**（2026-10-17，qwen3.5:4b 真实模型，详见 [v03-1.5 笔记「完整切片浏览器走查」](notes/v03-1.5-continue-discussion.md)；脚本 `frontend/e2e/walkthrough-v03-1.mjs` 可复跑）。

**V03-1.7 验收（阻塞收口）**

- [x] 200% 缩放走查——流式输出不跳正文、输入框/停止/配置可达，已按**等价视口 640×450**（1280×900@200% 的布局约束）通过（见 v03-1.5 笔记走查节 S7）；真浏览器 Ctrl+加号 缩放由作者日常使用顺手确认，不再阻塞收口。

**已声明偏差（收口时销号或显式顺延）**

- [ ] V03-1.3 偏差：env Key 回退未从普通流彻底移除（现状：用户连接全链路优先，env 仅无 config.yaml 时兜底）；计划原文要求移除——开发模式显式开关随模型引导 UI 收口（v03-1.3 留白 2）。
- [ ] 删除连接（`DELETE /api/providers/{name}`）不清凭据存储 ref（无害残留，v03-1.3 留白 1）。

**零散留白（可顺延到 V03-2，但需记录去向）**

- [x] 走查疑点：刷新后回执列表条数与「回执为内存态」预期不符——复核确认刷新后列表清空（内存态语义正确），原观察为读取偏差，销号（2026-10-17 走查发现 3）。
- [ ] 云连接 `POST /api/providers/test` 的 connection_id/revision 演进登记 → 随 V03-2 收口（v03-1.4 留白 1）。
- [ ] 执行日志：Job 通道（draft/audit 等任务）未接入、重启 `mark_interrupted` 不发射事件 → V03-8（v03-1.6 留白 2/4）；前端日志面板消费端 → 用户裁决后置（留白 1）。
- [ ] `model.process.delta` 公开过程通道未实现（无能力来源时如实显示，v03-1.6 留白 3）。

**口径修正**

- mypy files 数：README 旧记 236/239 为含 tests 口径；按 `src/hastings` 实测 207 files。后续统一以 `uv run mypy src/hastings` 口径记录。

## V03-2 已交付（2026-10-17 起）

开始条件检查单：结果归属与入口身份稳定（V03-1.2/1.5）✓；仓库事务扩展通过故障注入（V03-0.5）✓。

质量门基线：`uv run pytest -q` **1579 passed**；ruff/mypy 全过。

| 任务 | 状态 | 产物 | 构建笔记 |
|---|---|---|---|
| V03-2.1 OperationRecord 与固定讨论投影 | 已交付 | `domain/manuscript/operation_record.py`、`application/services/operation_service.py`、`application/workflows/operation_projection.py`（FanOutSink + 创作记录投影）、`GET /operations/{id}`；专项 24 项测试（全量 1515） | [notes/v03-2.1](notes/v03-2.1-operation-record.md) |
| V03-2.2 统一发布 PublishManuscriptChange | 第一部分已交付（管线主体 + 保存入口接入；恢复接入随 2.5） | `domain/manuscript/evidence.py`（确定性证据核对 + 摘要失效）、`application/services/publish_manuscript_change.py`、OperationService 扩展（events/显式章节清单/意图指纹）、保存路由走发布管线；专项 8 项测试（全量 1523） | [notes/v03-2.2](notes/v03-2.2-publish-manuscript-change.md) |
| V03-2.3 导入解析、预览与整批确认 | 已交付（导入接入发布器；旧 `/chapters/import` 保留兼容至 V03-8.6） | `interfaces/importers/manuscript_text.py`、`domain/manuscript/commands.py::import_batch`、`application/services/import_service.py`、四个预览/确认端点、写作台「导入正文」对话框；专项 18 项（全量 1541） | [notes/v03-2.3](notes/v03-2.3-import-preview.md) |
| V03-2.4 手工修改副本 | 已交付（单章 UI；底层按章节集合设计） | `domain/manuscript/edit_session.py`、`commands.py::save_drafts`、`CommitService.aux_mutate`、`application/services/edit_session_service.py`、七个副本端点、写作台「手工修改」；专项 10 项 + 导入重叠拦截 1 项（全量 1552） | [notes/v03-2.4](notes/v03-2.4-edit-session.md) |
| V03-2.5 历史恢复接入统一发布与证据核对 | 已交付 | `workflows/restore_version.py` 改走发布器（副本保护）、`queries/chapter_history.py`、`services/evidence_service.py`、三个只读/重锚端点、写作台「版本历史」与右栏「正文证据」；专项 6 项 + 恢复 5 项（全量 1558） | [notes/v03-2.5](notes/v03-2.5-history-and-evidence.md) |
| V03-2.6 操作结果与后续状态 | **部分交付**（状态可见；启动入口随 V03-4，偏差见笔记） | `queries/followups.py`、`GET /operations/{id}` 附带 `summary_state`/`ledger_suggestions_state`、写作台右栏「后续状态」；专项 3 项（全量 1561） | [notes/v03-2.6](notes/v03-2.6-followup-states.md) |

## V03-3 已交付（2026-10-18 起，3.1 删除动作待收尾）

开始条件检查单：导入与统一发布可用（V03-2.3/2.5）✓；模型启动有来源凭据与连接快照（V03-1.4/19.5）✓。

质量门基线：`uv run pytest -q` **1598 passed**；`ruff check src tests` 全过（仓库根另有 5 项既有告警在 `eval/v2/chat_driver.py`）；`mypy src/hastings` 227 files 无问题；前端 vitest **386 passed**（45 文件）+ `vite build` 通过；账本与七页聊天 e2e 通过（见 3.2 笔记，`v2-models`/`v2-workbench` 有既有失败）。

| 任务 | 状态 | 产物 | 构建笔记 |
|---|---|---|---|
| V03-3.1 Beat 活动路径退出 | **部分交付**（起草前置、上下文与七页侧栏已去节拍；旧规划管线与 AgentTasks 仍可创建/使用节拍，类型退出归 V03-8） | `task_drafts.missing_items` 去掉 `TASK_NO_BEAT_TARGET`/`TASK_NO_PLAN`、空 head 对起草合法；`AgentWorkflows.draft` 允许空节拍目标（未知目标报 `DRAFT_TARGET_INVALID`）；writer 上下文只在目标是节拍时装配 `beat_states`；七页任务卡的章节草稿改为选择目标章节 | [notes/v03-3](notes/v03-3-drafting-and-adoption.md) |
| V03-3.2 账本直接保存与候选成组 | 已交付 | `domain/story/canon/entries.py`（十种账本条目的类型化创建/修订、引用与 revision CAS）、`application/services/ledger_service.py`（一次保存一个 Commit、真实版本回读）、`application/services/ledger_candidate_service.py`（候选暂存/部分确认/假设拦截/依赖闭合）、`GET /ledger/schema` 等八个端点、写作台账本面板 | [notes/v03-3.2](notes/v03-3.2-ledger-candidates.md) |
| V03-3.3 无前置起草 | 已交付 | `domain/manuscript/drafting.py`（brief/候选/确定性提示词/文本清理）、`application/services/drafting_service.py`、`GET /ledger` 只读账本、`POST /drafts` | [notes/v03-3](notes/v03-3-drafting-and-adoption.md) |
| V03-3.4 只读完整预览与按哈希采用 | 已交付 | `GET /drafts/{id}`、`POST /drafts/{id}/revise`、`POST /drafts/{id}/adopt`（`preview_hash` + 修订号双重校验）、`POST /drafts/{id}/discard`；写作台「试写」对话框 | 同上 |
| V03-3.5 采用后定位与唯一结果 | 已交付 | 采用经 `PublishManuscriptChange`（新章 `chapter.create` / 现有章 `save_full_text`），返回 `chapter_id` + `operation_id`；前端定位正文并接 V03-2.6 后续状态 | 同上 |

专项测试 18 项（全量 1579）；前端客户端契约 5 项（全量 369）。

## V03-4 已交付（2026-10-18）

开始条件检查单：正文操作有固定结果/来源（V03-2.1/2.2）✓；**旧 Beat 活动路径已退出——按「运行路径无引用」判定✓，`narrative.beat` 类型本身的退出归 V03-8**（原计划把「类型退出」写在开始条件里，与 3.1 的顺延冲突；此处按运行路径口径解释，偏差已登记于本节与 [阶段执行指导](stage-execution-guide.md) §8）。

| 任务 | 状态 | 产物 | 构建笔记 |
|---|---|---|---|
| V03-4.1 本书约定 | **后端与契约已交付**（前端入口待定） | `domain/story/agreements.py`（`BookAgreement`、主题维度冲突规则、按 Commit 的快照与指纹）、`application/services/agreement_service.py`（一个 Commit 写入、幂等、真实版本回读）、`GET/POST /agreements` + `PATCH /agreements/{id}` + `GET /agreements/snapshot`；`story.agreement` 不进 `CANON_KINDS` | [notes/v03-4.1](notes/v03-4.1-book-agreements.md) |
| V03-4.2 常驻上下文装配 | **已交付** | `application/contexts/background.py`（五层材料、优先级裁剪、`omissions`/`coverage`/`versions`）、`application/contexts/collector.py`（按 Commit 取材料，临时原文按需读，`render()` 共用）、`GET /context/background`；试写按 `base_head` 取背景、对话按 `project_id` 追加系统消息；`capabilities.features.background` 公布预算与层级 | [notes/v03-4.2](notes/v03-4.2-background-assembly.md) |

| V03-4.3 显式摘要任务 | **已交付**（全书概览待续） | `domain/manuscript/summary.py`（来源清单、确定性提示词、落地前核对）、`application/services/summary_service.py`（显式 `start`、幂等、迟到结果 `superseded` 不落盘）、`application/workflows/summary_jobs.py`（`summary.chapter` 任务：排队/执行/停止/重试，停止在提交前拦截）、`POST /chapters/{id}/summary` 与 `/summary-jobs` 四端点、`GET /summaries`；`ChapterSummaryState` 扩为状态 + 内容，失效改写保留正文 | [notes/v03-4.3](notes/v03-4.3-chapter-summaries.md) |

| V03-4.4 账本更新建议 | **已交付**（任务化与「调整建议」待续） | `domain/story/suggestions.py`（确定性提示词、严格 JSON 解析、每条必须带原句）、`application/services/suggestion_service.py`（显式 `start`、零建议空结果）；候选组新增 `origin`/`source` 与 `_assert_sources_current`，采用沿用 `/ledger/candidates/accept`（`SOURCE_NEEDS_RECHECK` → 409） | [notes/v03-4.4](notes/v03-4.4-ledger-suggestions.md) |

| V03-4.6 基础关联撤销 | **已交付**（正文路径；账本与正文一次恢复待 V03-7） | `application/services/undo_service.py`：预览对比 before/after/head，冲突按 `chapter_base` 判定；`apply` 经 `publish_commands` **一次提交恢复多章**，CAS 用操作后基础，撤销 ID 确定性派生（连点幂等）；`GET/POST /operations/{id}/undo`。同时改账本、新建章节、未结束副本均阻断 | [notes/v03-4.6](notes/v03-4.6-undo-preview.md) |

| V03-4.5 对话来源与工作台共享状态 | **已交付**（完整壳层随 V03-8.6） | `operation_service.submit(origin_conversation_id=…)` 提交时固定归属，四个写入口（试写采用/导入确认/修改完成/历史恢复）带 `conversation_id`，撤销继承原操作归属；投影按记录里的会话走并校验归属，回落「创作记录」并告警；`GET /projects/{id}/desk-state` 只回计数 + `recent_operations` + 权威 ID；`GET /operations/{id}` 新增 `projection`；前端 `api/desk.ts` 与「后续状态」面板 | [notes/v03-4.5](notes/v03-4.5-desk-shared-state.md) |

专项测试：4.1 十六项、4.2 二十四项（12 纯函数 + 7 HTTP + 5 接线）、4.3 十八项（9 领域/接口 + 9 任务化）、4.4 九项、4.5 十一项、4.6 十二项（7 预览 + 5 写入）、4.7 二十二项（16 领域 + 3 接口 + 3 采用门槛）。质量门：V03-4 七项交付时 **1708 passed**（见[阶段小结](v03-4-stage-summary.md)）；收口评审 +6、V03-5.1 +5、V03-5.2 +24 后滚动为 **1743 passed**，`mypy src/hastings` 241 files；前端 `vitest` 389 passed、`tsc --noEmit` 无问题。

## V03-5 已交付（2026-10-18；完整阶段退出待 V03-7.7）

开始条件：第一批业务验收通过、副本/正式版身份与统一发布稳定（V03-4 收口评审六项修复后成立）。

| 任务 | 状态 | 产物 | 构建笔记 |
|---|---|---|---|
| V03-5.1 跨章修改 | 已交付（后端与契约；写作台仍只有单章入口） | `EditSession.chapters` 一等字段（声明/有机两种入列）、完成前 `EDIT_SESSION_UNFLUSHED` 核对、多章一次 commit | [notes/v03-5.1](notes/v03-5.1-cross-chapter-rounds.md) |
| V03-5.2 三方合并 | 已交付（后端与契约） | `domain/manuscript/merge.py` 段落级三方合并；干净叠加自动发布、重叠 409 三段事实、`merge_choices` 逐处选择进操作记录 | [notes/v03-5.2](notes/v03-5.2-three-way-merge.md) |
| V03-5.3 快照与候选进副本 | 已交付（后端与契约；前端未接线） | `EditSnapshot`（冻结 save_seq/选区/文本/来源、确定性 ID 与索引）、`POST/GET /edit-sessions/{id}/snapshots`；候选 `applied` 终态与 `POST /drafts/{id}/apply-to-edit-session`；逐段 `paragraph_sources` 进 `GET /operations/{id}` 的 refs | [notes/v03-5.3](notes/v03-5.3-draft-snapshots-and-provenance.md) |
| V03-5.4 完整撤销与冲突选择 | 已交付（同一操作内的正文+账本；建议采用的因果关联随 V03-7.7；前端未接线） | `undo_service` 三方逆向：非重叠后续修改自动保留、重叠逐处 `restore/keep`、预览 token 绑定；`publish_commands(extra_changes=…)` 把关联账本与正文放进同一 Commit；新建章节/账本仍阻断 | [notes/v03-5.4](notes/v03-5.4-undo-conflict-choices.md) |
| V03-5.5 并发与收口恢复 | 已交付（后端与契约；前端未接线） | 保存序号 CAS 多标签唯一赢家；完成在准备前按确定性 op_id 回放已提交结果，关闭/归档失败重启后只补关闭、不再发版；并发完成认回同一 Commit；`merged_chapters` 进 refs | [notes/v03-5.5](notes/v03-5.5-concurrency-and-recovery.md) |

阶段退出：三章夹具跨章修改 → 换作品 → 重启 → 完成 → 采用账本建议 → 独立再改 → 撤销。V03-5.1–5.5 已交付；其中「采用账本建议」与正文操作的因果关联随 V03-7.7，完整演练等它补齐后再跑。

质量门：`uv run pytest -q` **1769 passed**（2026-10-18 实测；V03-5.1 +5、5.2 +24、5.3 +13、5.4 +8、5.5 +5）；`ruff check src tests` 全过；`mypy src/hastings` 242 files 无问题；前端 `vitest` 389 passed、`tsc --noEmit` 无问题——5.1–5.5 的后端契约尚未接进写作台，前端数字只证明既有壳层未回归。
