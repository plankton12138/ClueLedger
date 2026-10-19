# Hastings 0.3 开发文档

> 状态：**V03-0、V03-1 已交付收口**（V03-0 收口于 commit `ad2a166`；V03-1 收口于 commit `53c7eb2` + 同日「接着讨论」前端接线与切片走查 10/10，收口检查单见下，两项已声明偏差显式顺延）。**V03-2 已交付（2.6 部分交付：后续状态可见，启动入口随 V03-4）**。**V03-3 已交付**：3.1 活动起草路径已去节拍（旧规划管线与 AgentTasks 的节拍类型退出归 V03-8）、3.2 账本直接保存与设定候选成组、3.3/3.4/3.5 试写 → 只读预览 → 按哈希采用。**V03-4 已交付收口，V03-5 五个任务已交付**（V03-4 七项，2026-10-18；阶段对账见 [v03-4-stage-summary](v03-4-stage-summary.md)；收口评审六项缺陷已修复，见 [v03-4-review-fixes](v03-4-review-fixes.md)；V03-5.1 跨章修改的本轮清单与完成前 flush、V03-5.2 正式稿前进时的三方合并与重叠逐处选择、V03-5.3 修改稿快照与候选进副本的段落来源、V03-5.4 撤销的三方逆向与正文/账本一次恢复、V03-5.5 并发保存与完成后的收口恢复已交付，见 [notes/v03-5.1](notes/v03-5.1-cross-chapter-rounds.md)、[notes/v03-5.2](notes/v03-5.2-three-way-merge.md)、[notes/v03-5.3](notes/v03-5.3-draft-snapshots-and-provenance.md)、[notes/v03-5.4](notes/v03-5.4-undo-conflict-choices.md) 与 [notes/v03-5.5](notes/v03-5.5-concurrency-and-recovery.md)；完整阶段退出仍待 V03-7.7 补齐建议采用与正文操作的因果关联）：4.1 本书约定、4.2 背景装配、4.3 显式章摘要（含任务化）、4.4 账本更新建议、4.5 对话来源与工作台共享状态、4.6 基础关联撤销、4.7 披露边界。同轮修复范围外残留：作者模型入口已按计划 §9.1 从 0.3 移除（数据保留）。遗留项：全书概览生成入口、建议采用与正文操作的因果关联（V03-7.7）、`narrative.chapter/beat` 创建入口均带去向。**V03-6.1 已交付**：统一图谱只读投影（节点/边/来源/语义层/证据状态，`GET /graphs`），四类图谱在它之上筛选。**V03-6.2/6.3/6.4 已交付**：6.2 人物图（真关系/认知分层、作者全知或角色视角、叙述位置），6.3 线索图（只保留线索相连边、计划出现/已在正文出现分离、后文线索 `within_position` 标记、`missing` 为合法缺口），6.4 交互与布局持久化（拖动零领域写入、本地区布局偏好、右栏小图与主区展开、来源定位与加入讨论、返回原正文位置与副本），6.5 文字等价操作、键盘与大数据布局（文字目录 listbox、画布/目录切换、落定帧统一落盘、半径随规模增长与大图虚拟化）——**V03-6 全部交付，第二批统一退出条件达成**。**V03-7.1 已交付**：讨论/候选/执行三分意图与范围（`POST /conversations/{id}/intents`），只认作者消息/按钮授权、讨论无写权限、同范围不重复审批；执行入口与预算等待随 V03-7.3。**V03-7.2 已交付**：全局上下文与原文覆盖台账（`revision_work` aux 表）、逐章读取登记与五状态下结论、摘要不得记 `checked_unchanged`，`POST/GET /projects/{id}/revision-work` 与 `/reads`、`/coverage/{chapter_id}`；预算与两轮修正上限随 V03-7.3，统一发布与副本门禁随 V03-7.4。**V03-7.3 已交付**：有限执行的任务账本（`domain/revision/task.py` 的预算/修正轮次/状态机、`application/services/revision_task_service.py` 的 `revision_task` 记录与继续语义）、`POST/GET /projects/{id}/revision-tasks` 与 `/current`、`/{task_id}/stop`、`/{task_id}/continue`；默认 12 次调用且启动只能缩小、修正最多两轮、到上限先落 `waiting` 再报错、继续只追加预算不清零消耗、同作品同时只允许一个进行中任务；`publication.blockers` 只报告不发布。**V03-7.4 已交付**：内部工作版本暂存区（`domain/revision/staging.py`：章全文 + 账本新建/修订、按章/按 stage_id 覆盖、修订必带 `expected_revision`）与统一发布（`application/services/revision_publish_service.py`：正文与账本进同一个 Commit；活动副本、基础前进、未覆盖、预算耗尽整体拒绝，不产生半套版本）；端点 `POST/DELETE .../revision-tasks/{id}/staged-chapters`、`/staged-ledger`、`GET .../staged`、`POST .../publish`；暂存章按章基础 CAS，发布成功记 `changed` 并标完成，同 `operation_id` 可回放。执行循环（谁读章、调模型、调暂存与记账）仍未接。**V03-7.5 已交付**：手法图只投影账本已有文字与关系（`domain/story/graph.py::method_projection`：以真相条目为中心，沿凶手/协助者/真实时间线事件两跳取既有子图，认知/披露/计划层不入图；不新增步骤对象、不调模型），缺口逐条给出（无真相、三段文字缺失、时间线悬空、无线索指向），完整度只数三段文字并明说不代表逻辑证明；`GET /projects/{id}/graphs/method`；前端增加「手法结构」标签与主区摘要（原文/尚未记录/缺口/完整度）。**V03-7.6 已交付**：时间线投影（`domain/story/timeline.py`：按角色/地点分轨、真实事件与证词/认知分轨、时间原文不解析不排序、叙述位置只来自获知节拍；缺口逐条报出）；`GET /projects/{id}/graphs/timeline`；前端「时间线」标签与文字面板（分轨事件、证词模式、所指事件真实时间、缺口）。**V03-7.7 已交付**：影响高亮只按实际版本差异回读（`application/queries/impact.py`：章/账本变化字段、后续修改标记、`basis: version_diff` 与「不代表修订完成/质量分数/嫌疑概率」）、`GET /projects/{id}/operations/{operation_id}/impact`；采用账本候选可带 `source_operation_id` 记因果链接（`application/services/revision_links.py`），撤销预览把关联采用中被修改条目纳入同一三方检查（新建条目只报告 `skipped_created`，不删除）；前端「本次影响」面板。**V03-7 全部交付**，阶段退出演练待 V03-8.8。**V03-8.1 已交付**：启动恢复收敛为单一入口 `recover_on_startup()`（执行中的对话/任务标中断、旧自动队列启动即隔离为 `historical_paused`，只改状态不自动重发模型调用），取证端点 `GET /api/v2/recovery`（上次恢复摘要、旧队列快照、任务状态分布与等待清单）；升级八步中「只读盘点」「旧任务隔离」已代码化并有测试，备份/影子核对/默认切换/回退预案分别归属 V03-8.2/8.3。**V03-8.2 已交付**：0.3 完整备份/恢复（同作品；预览回报覆盖声明，恢复经统一发布器、差异未确认 409、确认后正文+账本同一 Commit，跨作品搬运与 ID 重映射显式不做）。**V03-8.3 已交付**：装配链复用与生产启动闭环冒烟（不传特殊开关走完创建→导入→账本→跨章修改→撤销→候选采用→备份恢复；真 uvicorn 进程留 8.7）。**V03-8.4 已交付**：日志断线/游标补齐与压力（`tail=0` 排空积压后关闭不截断、断线重连游标口径、旧代迟到结果 generation 栅栏、多作品/多订阅者隔离、full 规模分页 p95 1.29ms；前端日志面板消费端仍未接）。**V03-8.5 已交付**：四种窗口视觉走查 37/37——修复旧版对话栏默认开启对三部分布局的挤压（1024 下正文区基线仅 98px）、壳层响应式抽屉（≥1280 三部分 / 中屏折叠左导航 / 小屏主区优先）、两处伪入口、正文 18px 与 44em 行宽、输入框/对话/账本 14px、正文焦点轮廓、次级文字对比度 4.82:1；键盘四项与中文输入法组合保护实测通过；截图归档 `artifacts/visual-walkthrough-8.5/`。**V03-8.6 已交付**：126 条 V2 路由逐条分类成 API 调用矩阵；13 个排除功能启动面统一 410 `FEATURE_RETIRED_IN_03`（无副作用、只读历史保留）；旧单章导入接入统一发布器（此前唯一绕过写路径）；前端默认切写作台、三个旧页只读化、客户端退役函数清理。**V03-8.7 已交付**：真模型与本地接入实测（本地 Ollama qwen3:8b）——讨论流式、试写候选→采用→Commit、修订任务预算/覆盖/停止 6 项通过，环境失败作为独立证据记录（`CHAT_PROVIDER_FAILED`）；驱动与报告入库 `eval/`。**V03-8.8 已交付文档收口**：模块 as-built（`modules/`）、运行说明与发布记录（`release.md`）、33 项验收追踪（28 通过 / 5 部分 / 十场景未执行）；`src/hastings/README.md` 与 `AGENTS.md` 导航更正。**结论：0.3 功能面交付完成，完整验收未过，不标「完整交付」**——第三批阶段退出（雪山旅馆夹具十场景）未执行，未验项见 [release.md](release.md) §5 与 [验收追踪](artifacts/v03-8.8-acceptance-tracking.md)。更新：2026-10-19。

0.3 将已有版本仓库与创作任务能力，组织为围绕正文、对话和故事账本的持续创作工作区。
本目录与既有 `dev-notes/v0.2/` 均按产品版本命名；`/api/v2` 与存储版本标识独立维护，不随产品版本重命名。

| 需要了解 | 入口 |
|---|---|
| 完整范围、模块方案、契约、实施任务和验收 | [0.3 完整开发计划](development-plan.md) |
| 按 V03-0~V03-8 逐阶段的执行顺序、代码手法与取证方式 | [阶段开发执行指导](stage-execution-guide.md) |
| 已确认的产品决定 | [产品设计导航](../../product-design/README.md) |
| 工作区视觉与交互 | [作家的桌面](../../product-design/writers-desk-design.md) |
| 既有架构与模块契约 | [0.2 文档](../v0.2/README.md)、[模块导航](../v0.2/modules/README.md) |
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

## V03-6 人物关系与线索关联图（V03-6.1/6.2/6.3/6.4 已交付，2026-10-18）

开始条件：账本、正文来源和证据状态查询共用稳定契约 ✓（V03-2.5 证据核对 + V03-3.2 账本查询）。

| 任务 | 状态 | 产物 | 构建笔记 |
|---|---|---|---|
| V03-6.1 图谱统一只读投影 | 已交付 | `domain/story/graph.py`（节点/边纯投影：四语义层、引用字段导边、显式关系边、悬空引用不入图）、`application/queries/graphs.py`（`GraphQueries`：固定 Commit + 中文标签 + M05 证据状态）、`GET /projects/{id}/graphs`；专项 11 项 | [notes/v03-6.1](notes/v03-6.1-graph-projection.md) |
| V03-6.2 人物图 | 已交付 | `domain/story/graph.py::character_projection`（真关系/认知分层、作者全知或角色视角隐藏 `true_state`、按获知事件的位置筛选、未记录不合成 unaware）、`domain/story/narrative/order.py::last_beat_of_node`（章→末节拍，不用章节序号）、`GraphQueries.characters`、`GET /projects/{id}/graphs/characters`；专项 14 项 | [notes/v03-6.2](notes/v03-6.2-character-graph.md) |
| V03-6.3 线索图 | 已交付 | `domain/story/graph.py::clue_projection`（只保留与线索相连的边、计划出现/已在正文出现分离、`within_position` 标记后文线索、`missing` 为合法缺口）、`views.py` 公开 `position_map`/`reader_reveals` 复用同一口径、`GraphQueries.clues`、`GET /projects/{id}/graphs/clues`；专项 15 项 | [notes/v03-6.3](notes/v03-6.3-clue-graph.md) |
| V03-6.4 交互与布局持久化 | 已交付 | 前端 `state/graphLayout.ts`（拖动只写本地区、确定性初始布局、引用文案）、`api/graphs.ts`（只读客户端 + 来源定位）、`components/desk/GraphCanvas/GraphDetail/GraphViewTabs/useGraphData/GraphPanel/GraphOverview.tsx`、壳层接线（`DeskShell`/`deskStore`/`DeskComposer`）；后端 `EvidenceService.list` 加 `target_id` 过滤 + `/evidence-links?target_id=`（供来源定位）；前端专项 15 项（graphLayout 11 + graphs 4）、后端专项 7 项 | [notes/v03-6.4](notes/v03-6.4-graph-interaction.md) |
| V03-6.5 文字等价操作、键盘与大数据布局 | 已交付 | 前端 `state/graphLabels.ts`（标签口径单源）、`state/graphDirectory.ts`（文字目录模型 + 键盘移动 + 查看方式偏好，纯函数）、`components/desk/GraphDirectory.tsx`（listbox 目录：方向键/Home/End 移动、回车/空格选中）、`GraphPanel` 画布/文字目录切换（共用同一详情区）、`GraphCanvas` 落定帧统一落盘 + 超 60 对象只渲染视口 + 缩放 0.1–2、`graphLayout.initialLayout` 半径随规模增长；前端专项 16 项（graphDirectory 12 + graphLayout 4） | [notes/v03-6.5](notes/v03-6.5-graph-text-path.md) |

**V03-6 阶段退出（第二批统一退出条件，2026-10-19）**：6.1–6.5 全部交付，人物图与线索图具备真实来源与可操作的文字路径——不用画布也能逐条查看条目、定位正文依据、加入讨论并继续写作（A22）。完整跨章/重启/冲突/关联撤销演练见 V03-5 节，等 V03-7.7 补齐建议采用的因果关联后作为第二批整体演练复跑。

质量门：后端未改动（V03-6.5 只改前端与文档），沿用 `uv run pytest -q` **1810 passed**（V03-6.2 +14、V03-6.3 +15、V03-6.4 +1）口径；`ruff check src tests` 全过；`mypy src/hastings` 244 files。前端 `vitest` **420 passed**（49 文件；含 V03-6.5 新增 graphDirectory 12 + graphLayout 4）、`npm run build`（tsc --noEmit + vite build）通过，主 bundle 826.71 kB（gzip 271.82）。留白：`story.place.adjacent` 未登记引用校验故暂不成边；真实工作区尚无 `evidence.link` 写路径，证据状态多为 `not_recorded`（用直接提交测试覆盖 `needs_check`）；线索与误导解释/「反驳」尚无正式关系类型，故不自动连线（等经 `catalog.relations` 记录后自动带上）；出现的章节分组由前端用叙事树解析、不在线索图合成结构边；手法图/时间线带类型端点随 V03-7。V03-6.4/6.5 留白：视角/叙述位置切换控件未接（后端已支持 `perspective`/位置/`focus`，前端按缺省全知全图取数、目录按全知口径成行）、图谱旅程的 Playwright E2E 与真实浏览器键盘走查未补（Playwright J7-J8 图谱旅程留白）、bundle 无 code-split。环境提示：本机 `uv run ruff` 为 0.16.10（pyproject `ruff>=0.5` 未锁版本），`ruff format --check` 会命中 17 个与本任务无关的历史文件，属既有版本漂移、不在本任务范围。

## V03-7 对话整体调整、手法结构与时间线（V03-7.1–7.7 已交付，2026-10-19）

开始条件：跨章发布、后续冲突处理、叙述位置/故事时间与图谱基础均已通过 ✓（V03-5/V03-6 收口）。

| 任务 | 状态 | 产物 | 构建笔记 |
|---|---|---|---|
| V03-7.1 意图区分与授权 | 已交付（后端与契约 + 只读前端展示） | 领域 `domain/conversation/intent.py`（确定性三分：讨论无写权限/候选只读/执行写范围，假设线索优先，无法判定降级讨论）、应用 `application/services/intent_service.py`（只读 `classify`：只认作者消息、`guard_intent_write_scope` 硬断言、同范围 `reuses_authorization`）、`POST /conversations/{id}/intents`、错误码 `INTENT_WRITE_SCOPE_INVALID`；前端 `api/intent.ts` + `components/chat/IntentNote.tsx`、`ChatComposer` 输入预览意图条、`ChatMessages` 作者消息「意图区分」；后端专项 27 项（全量 1837）、前端专项 8 项 | [notes/v03-7.1](notes/v03-7.1-intent-and-authorization.md) |
| V03-7.2 全局上下文与原文覆盖台账、内部工作版本连续修订 | 已交付（后端契约 + 领域台账） | 领域 `domain/revision/coverage.py`（五状态 `unread/read/checked_unchanged/changed/blocked` + 读取种类 `summary`/`full` + `ReadRange` 区间；**摘要不得记 `checked_unchanged`** 为领域硬约束；来源变化重置区间）、`domain/revision/{error_codes,errors}.py`、应用 `application/services/revision_work_service.py`（`RevisionWorkService`：打开/恢复工作版本绑定正式 head、逐章登记读取与下结论、回读覆盖台账与全局上下文；aux 表 `revision_work`/`current` 一条记录原子读-改-写）、`interfaces/api/revision.py` + `server/v2.py`（`POST/GET /projects/{id}/revision-work`、`POST .../reads`、`POST .../coverage/{chapter_id}`; `CoverageViolation` 信封）；专项 26 项 | [notes/v03-7.2](notes/v03-7.2-coverage-ledger.md) |
| V03-7.3 调用预算、两轮修正上限、等待作者/停止/失败/继续 | 已交付（后端契约 + 领域状态机；执行循环与发布随 V03-7.4） | 领域 `domain/revision/task.py`（`RevisionBudget`：12 次默认、启动只能缩小、`consume(calls=n)` 整笔记账不截断、修正最多 2 轮、继续只追加上限；`RevisionTask`：`queued/running/waiting/cancelled/retryable_failed/terminal_failed/completed` + `attempt`/`grant_version`/`requires_revalidation`；`publication_blockers` 只报告不发布）、应用 `application/services/revision_task_service.py`（`revision_task` aux 表 `current` 单记录原子读改写、按 id 归档；预算/修正不足时**先落 waiting 再报错**；同作品同时只允许一个进行中任务，新任务 409）、`interfaces/api/revision.py`（`POST/GET /projects/{id}/revision-tasks`、`/current`、`/{task_id}/stop`、`/{task_id}/continue`）；专项 21 项（领域 11 + 接口/服务 10） | [notes/v03-7.3](notes/v03-7.3-budget-and-continuation.md) |
| V03-7.4 统一发布账本与正文、活动副本门禁、基础重验 | 已交付（后端契约 + 领域暂存区；执行循环未接） | 领域 `domain/revision/staging.py`（`StagedChapter` 章基础 + 全文、`StagedLedgerChange` 修订必带 `expected_revision`、按 chapter_id/stage_id 覆盖）、应用 `application/services/revision_publish_service.py`（`revision_stage` 辅助表；首次暂存把 queued 推进 running；发布门禁：活动副本 → 基础前进 → 覆盖（未覆盖章必须已暂存）→ 预算/待定选择；正文命令 + 账本 `extra_changes` 经 `publish_commands` 进**同一个 Commit**；成功后暂存章记 changed、任务记 `published_operation_id`/`published_commit_id` 并清空暂存；同 operation_id 回放）、`interfaces/api/revision.py`（`POST/DELETE .../staged-chapters`、`/staged-ledger`、`GET .../staged`、`POST .../publish`）；专项 16 项（暂存领域 5 + 接口 11） | [notes/v03-7.4](notes/v03-7.4-unified-publish.md) |
| V03-7.5 手法图忠实投影已有正式文字与关系、缺口/来源 | 已交付（后端投影 + 前端标签与摘要；时间线随 V03-7.6） | 领域 `domain/story/graph.py::method_projection`（以 `story.mystery_truth` 为中心，沿凶手/协助者/真实时间线事件两跳取既有引用：事件地点与参与者、事件上的真相事实、指向事实的线索；边只取 `semantic_layer=truth` 且两端在子图内；**不新增步骤/条件/证据对象、不调模型**）、`application/queries/graphs.py::method`（固定 Commit、附展示标签与证据状态）、`GET /projects/{id}/graphs/method`（`at_commit_id`/`focus`）；缺口 `METHOD_TRUTH_NOT_RECORDED`/`METHOD_TEXT_NOT_RECORDED`/`METHOD_TIMELINE_UNRESOLVED`/`METHOD_CLUES_NOT_RECORDED` 每条带 where/why/fix，`completeness` 只数三段文字并注明不代表逻辑证明；前端 `api/graphs.ts`、`useGraphData`、`GraphViewTabs`「手法结构」、`GraphPanel` 手法摘要；专项 10 项（领域 5 + 接口 5）+ 前端 2 项 | [notes/v03-7.5](notes/v03-7.5-method-graph.md) |
| V03-7.6 时间线真实/证词分轨、未知时间、获知事件和叙述位置 | 已交付（后端投影 + 前端文字面板） | 领域 `domain/story/timeline.py`（按参与者/地点分轨；`story.timeline_event` 与 `story.knowledge` 分轨，证词保留 mode/中文标签/备注；事件时间原样显示、不解析不排序；叙述位置只由 `at_beat_id` 经 `position_map` 计算；`focus` 只收窄范围且事件内容不变；缺口 `TIMELINE_NO_EVENTS`/`TIME_NOT_RECORDED`/`PLACE_NOT_RECORDED`/`PARTICIPANTS_NOT_RECORDED`/`TESTIMONY_FACT_UNRESOLVED`/`TESTIMONY_EVENT_NOT_RECORDED`/`TESTIMONY_POSITION_UNRESOLVED`）、`application/queries/graphs.py::timeline`、`GET /projects/{id}/graphs/timeline`（`group_by=character|place`、`focus`）；前端「时间线」标签、`fetchTimelineGraph`、`useGraphData` 归一、`TimelinePanel` 分轨文字视图与缺口；专项 11 项（领域 6 + 接口 5）+ 前端 2 项 | [notes/v03-7.6](notes/v03-7.6-timeline.md) |
| V03-7.7 实际版本影响高亮与继续创作、整体操作关联撤销 | 已交付（后端契约 + 前端面板；正文内联高亮随整体执行界面） | 查询 `application/queries/impact.py`（`GET /projects/{id}/operations/{operation_id}/impact`：按操作记录与 `diff` 回读章/账本变化字段、字符级差异统计、`has_later_changes`、`basis=version_diff` 且 note 明确不代表质量评价；`continuation` 给出可继续章节与副本阻塞状态）、因果链接 `application/services/revision_links.py`（`revision_links` 辅助表；采用候选可带 `source_operation_id`，服务端核对操作记录真实存在）、撤销 `undo_service._related_acceptance_rows`（关联采用中被修改条目进同一三方检查；新建条目报 `skipped_created` 不删除、不阻塞原撤销）、候选采用接口 `source_operation_id` 参数（无效 ID → `REVISION_OPERATION_NOT_FOUND`）、前端 `api/impact.ts` + `OperationFollowups`「本次影响」；专项 6 项（接口 5 + 未知来源 1）+ 前端 1 项 | [notes/v03-7.7](notes/v03-7.7-impact-and-related-undo.md) |
| V03-8.1 新旧数据副本升级、重试、迁移中断与过期模型任务恢复演练 | 部分交付（启动恢复与旧任务隔离已代码化；备份/影子核对/默认切换/回退预案随 V03-8.2/8.3） | `V2Runtime.recover_on_startup()`（lifespan 唯一入口：对话轮次与执行中任务标中断 → waiting、旧自动队列 `manuscript.analysis`/`style_observer` 启动即分类暂停为 `historical_paused`，`revision_links` 等辅助表不动）、`GET /api/v2/recovery`（`last_recovery` 摘要 + `legacy_auto_snapshot` + `task_states` + `waiting_task_ids`，恢复只标记状态）、测试 4 项（恢复前无报告、遗留库启动后两类任务均 waiting 且事件无 `agent.output.proposed`、干净启动全零、再次启动暂停幂等不重复写事件）；搬家式升级（一致性快照/清单哈希/影子核对）未做，见构建笔记八步对照 | [notes/v03-8.1](notes/v03-8.1-recovery-and-legacy-isolation.md) |
| V03-8.2 0.3 完整备份/恢复、ID 冲突、正文阅读导出 | 已交付（同作品恢复；跨作品搬运与 ID 重映射显式不做） | `application/services/backup_service.py`（预览按 head 构建导出包并回报 `BACKUP_COVERAGE` 覆盖声明；恢复经 `PublishManuscriptChange`：差异未确认 409 `EXPORT_RESTORE_CONFLICT` 列章与 `changed_fields`，确认后 `save_full_text`+账本 `ArtifactChange` 进同一 Commit，操作 ID 绑定整包指纹与确认状态；包内对象缺失 `EXPORT_RESTORE_NEW_OBJECT`）、端点 `GET /projects/{id}/backup`、`POST /projects/{id}/restore`（`content_base64` + `confirm_overwrite`）、导出器 `unzip_package`（拒绝非 zip/缺 manifest/目录穿越）；测试 8 项；全量 1939 passed，ruff/mypy 260 files | [notes/v03-8.2](notes/v03-8.2-backup-and-restore.md) |
| V03-8.3 生产构建与默认后端启动完整闭环 | 已交付（装配链复用 + 冒烟测试；真 uvicorn 进程与模型步骤留 8.7） | `server/__main__.py` 抽出 `assemble_server()`（config 解析→静态目录探测→`.env`→`load_server_config`→引导档案/未配置判定→凭据存储→`ProviderBook`→`WorkspaceRegistry`→`V2Runtime.sqlite`→`create_app`）与 `AssembledServer`，`main()` 只剩参数解析/打印/`uvicorn.run`，`StartupError`（退出码 1）与 `ProviderConfigError` 先于 `ValueError`（退出码 2）区分；冒烟 `tests/test_server_production_loop.py` 用同一段装配走完整回路：能力公布→创建作品→导入→账本保存→跨章副本修改与发布→撤销→候选暂存/采用（带 `source_operation_id`，影响查询回报关联采用）→备份预览/导出/恢复（未确认 409→确认还原）→恢复后再改一轮；未配置模式显式断言（服务照起、可写） | [notes/v03-8.3](notes/v03-8.3-production-startup-loop.md) |
| V03-8.4 日志断线/游标补齐、任务迟到、多窗口压力 | 已交付（后端契约 + 专项测试；前端日志面板消费端仍未接） | `interfaces/api/execution_log.py` 帧生成提为模块级 `event_stream`（路由与测试共用唯一实现）；`tail=0` 修正为「排空当前积压后关闭」——超过 page_size 的积压连续取页不截断（旧实现一页即关会静默丢尾部），`tail>=1` 持续流与注释帧心跳不变；`next_cursor` 语义沿用 V03-1.6 契约（`None`=取尽），断线重连每页从最后收到事件前进游标；专项 14 项：双后端断线补齐（含断线期间新事件）、同游标重取不漂移、tail=0 排空、游标续收不重复、心跳是注释帧、limit 封顶、归属 404、旧代迟到结果被 generation 栅栏丢弃、多作品并发分页隔离、多订阅者互不干扰、full 规模 p95 取证 1.29ms；环境约束：本机 TestClient 整体缓冲响应，无限流出不了 `__enter__`，持续流/心跳在生成器层测 | [notes/v03-8.4](notes/v03-8.4-log-cursor-and-pressure.md) |
| V03-8.5 四种窗口与缩放/键盘/中文输入法视觉走查 | 已交付（走查 37/37；截图归档 artifacts/visual-walkthrough-8.5/） | 走查脚本 `frontend/e2e/walkthrough-v03-8.5.mjs`（standalone，真 API + build 产物 + 定速假模型；真实状态：API 建作品→账本两人物一关系→UI 导入三章→保存想法→接着讨论→停止）。修复七项：①进入写作台自动收起旧版对话栏（基线 1440 正文区仅 514px、1024 仅 98px）；②壳层响应式（≥1280 三部分 / 1024–1279 左导航折叠为抽屉 / <1024 主区优先，头部目录/侧栏开关 + Esc/遮罩；修复后正文区 792/734/734/356px 无溢出）；③移除「正文候选/差异对比」伪入口（阶段已交付却进占位）并修版本历史未开章进占位；④正文 15→18px 且行宽封顶 44em 居中；⑤输入框/讨论/账本 12→14px；⑥正文纸面补 focus-visible 焦点轮廓；⑦次级文字令牌 sub 4.21→4.82:1（mute 压暗）。键盘四项（编辑保存/专注写作/图谱目录方向键/回车停止）与中文输入法组合保护（组合期 Enter 不提交、Ctrl+Enter 才保存、草稿刷新恢复）实测通过；vitest 433 passed 无回归 | [notes/v03-8.5](notes/v03-8.5-visual-walkthrough.md) |
| V03-8.6 API 调用矩阵、旧入口不可绕过证明与默认工作区 | 已交付（126 条 V2 路由逐条分类；守卫 20 项；默认切写作台） | 见 [API 矩阵](artifacts/v03-8.6-api-matrix.md)。后端：`interfaces/api/_retired.py` 统一拒启（**410 `FEATURE_RETIRED_IN_03`** + 三要素必须给替代路径），变更集逐章审批流（analyze/plan/approve/execute/execute-agent/review/merge）、独立审计、重写工作流、多 Agent 协调共 13 个启动面拒启且无副作用（版本头/Job 数不变）；只读保留 `GET /changes(/{cs})`；**旧单章导入从直提领域命令改为 `publish_command`**（此前是唯一绕过统一发布器的写路径，现返回 `operation_id` 且操作记录可回读）；`style_observer` 角色不签发凭据；语料/风格语料静态守卫（0.3 代码零引用）。前端：默认视图与导航首位切到写作台（进入时收起旧版对话栏）；变更影响/修订审阅/悬疑审计改为只读回看（移除分析/计划/批准/执行/审阅/合并与「运行一致性审计」）；删除未挂载的 `ChangeCenter.tsx` 与 `changeAction`/`runAudit`/`coordinate` 客户端函数 | [notes/v03-8.6](notes/v03-8.6-api-matrix-and-default-desk.md) |
| V03-8.7 真模型与本地接入实测、预算/覆盖/失败证据 | 已交付（真模型 6 通过 + 1 失败证据；本地 Ollama） | 驱动 `eval/v2/desk_driver.py`（进程内 V2 + 真实 Provider，报告入库 `eval/runs/2026-10-19-v03-8.7-real-model/`）。真模型证据：S1 讨论流式 36 增量/终态/零 Job；S2 试写候选 213 字（预览哈希）→ 采用 HTTP 200 + `operation_id` → Commit 落盘；S3 修订任务 `call_limit=1`/`calls_used=0`（开任务零调用）、两章读取+`changed` 结论覆盖 2/2 `complete=true`、停止终态 `cancelled`；**S4 失败证据**：不存在的模型 → `turn.failed` + `CHAT_PROVIDER_FAILED`（environment 三要素，不自动换厂商）。口径修正三处（`call_limit`/`status`/`cancelled`），覆盖断言改为全书。留白：模型驱动修订全循环未跑（成本，8.8 可加 `--deep`）；config 默认模型 qwen3:4b 未安装，实际用 qwen3:8b 并记录；远程 Provider 无 Key 未调用 | [notes/v03-8.7](notes/v03-8.7-real-model.md) |
| V03-8.8 更新 as-built、导航、运行说明与发布记录 | 已交付文档收口（**0.3 不标完整交付**） | 新增 [modules/](modules/README.md)（0.3 as-built：desk-surface、revision-and-coverage、publish-import-recovery、execution-log、api-matrix-and-retirement，未变模块仍以 v0.2/modules 为契约）、[release.md](release.md)（发布记录 + 运行说明 + 显式未验项）、[33 项验收追踪](artifacts/v03-8.8-acceptance-tracking.md)（A01–A33 逐条证据与结论：28 通过/5 部分；§9.4 十场景未执行；三批退出一/二达成、**第三批未端到端**）；`src/hastings/README.md` 补 0.3 增量与默认入口状态、`AGENTS.md` 版本导航更正。结论：功能面 V03-0–8.7 交付完成，**完整验收未过**（雪山旅馆夹具未建、十场景未跑、真 uvicorn 日志缺失、前端日志面板未接、V1 语料写接口仍可手工调用、节拍类型未退出、远程 Provider 无 Key） | [notes/v03-8.8](notes/v03-8.8-as-built-and-release.md) |


质量门（V03-8.1）：`uv run pytest -q` **1931 passed**（+4 专项）；`ruff check src/hastings tests/v2` 全过；`mypy src/hastings` 259 files 无问题；前端未改动。留白：一致快照/清单哈希/ID 冲突策略与影子核对属 V03-8.2，默认切换与回退预案属 V03-8.3。
质量门（V03-8.2）：`uv run pytest -q` **1939 passed**（+8 专项）；`ruff check src/hastings tests/v2` 全过；`mypy src/hastings` 260 files 无问题；前端未改动。留白：恢复到新作品需要 artifact ID 重映射表（0.3 不做，既有 `apply_exported_package` 仍面向另一仓库实例的空项目）；恢复不做三方合并——确认即按包内容覆盖，保留后来的修改请用撤销；覆盖核对以「副本/操作/建议/日志不在备份范围」为准；凭据排除是结构性保证，真凭据取证随 V03-8.7。

质量门（V03-8.3）：`uv run pytest -q` **1941 passed**（+2 冒烟；启动契约 20 项回归全过）；`ruff check src/hastings tests` 全过；`mypy src/hastings` 260 files 无问题；前端未改动。留白：真 uvicorn 进程启动需运维日志证据；模型生成/试写/摘要属 V03-8.7 真模型实测。

质量门（V03-8.4）：`uv run pytest -q` **1955 passed**（+14 专项）；`ruff check src/hastings tests/v2` 全过、`ruff format --check`（本任务文件）干净；`mypy src/hastings` 260 files 无问题；前端未改动。full 规模取证：12 作品 × 833 事件，分页 p95 1.29ms / max 1.32ms（§9.6 目标 <1000ms）。留白：`mark_interrupted` 不发射执行事件（V03-1.6 留白，去向 8.7/8.8 销号或顺延）；前端日志面板消费端未接（用户裁决后置）；心跳帧不带游标（客户端游标经 REST 分页建立）；多窗口同时保存的稳定稿由 V03-5.5 覆盖，本轮未重复。环境约束记录：本机 starlette 1.7 TestClient 整体缓冲响应，无限流（`tail>=1`）出不了 `stream().__enter__`——后续无限流 SSE 用例走生成器层或真实服务，不要走 TestClient。

质量门（V03-8.5）：走查脚本 **37/37 通过**、控制台无 error；`npm run build`（tsc --noEmit + vite build）通过；`vitest` **433 passed（51 文件）**与 V03-7.7 基线一致；后端未改动。截图证据归档 `artifacts/visual-walkthrough-8.5/`（9 张，均来自真实内容与状态）。留白：真浏览器 200% 缩放与真实 IME 组合由作者日常确认（headless 以 640×450 等价视口 + 合成组合事件代理）；`mute` 令牌 3.88:1 仅占位符/旧版聊天元信息（待产品裁决是否占位符也达 AA）；画布视图不接键盘（文字目录为既定键盘路径）；写作台内旧版对话栏可手动打开，打开后仍挤压三部分（隐藏开关未做，交互待裁决）。

质量门（V03-8.6）：`uv run pytest -q` **1975 passed**（基线 1955 + 守卫 20）；`ruff check src/hastings tests` 与 `ruff format --check`（本任务文件）全过；`mypy src/hastings` 261 files 无问题。前端 `npm run build` 通过、`vitest` **433 passed** 无回归。矩阵见 [artifacts/v03-8.6-api-matrix.md](artifacts/v03-8.6-api-matrix.md)：126 条 V2 路由分类；13 个排除功能启动面 410 + 无副作用证明；旧单章导入接入统一发布器（此前唯一绕过写路径）；作者模型路由不存在、`style_observer` 不签发凭据、语料零引用（静态守卫）。留白：V1 语料写接口仍可手工调用（待 8.8 裁决是否拒启）；`coordinator`/`auditor` 角色仍可建 Job（旧任务回看）；旧变更集 impact/plan/hunk 详情不再在前端渲染（数据保留，可经 API 回读）；底层 review/audit/rewrite 服务未删，只保证入口不可达。

质量门（V03-8.7）：真模型走查 **6 通过 + 1 失败证据**（本地 Ollama `qwen3:8b`，报告 `eval/runs/2026-10-19-v03-8.7-real-model/`）；驱动 `ruff` 干净；本轮仅新增 eval 驱动与文档，后端/前端代码未改动。证据：讨论流式 36 增量；试写候选 213 字 → 采用 `operation_id` → Commit；修订任务 `call_limit=1`/零调用开任务/覆盖 2/2/停止 `cancelled`；环境失败 `CHAT_PROVIDER_FAILED` 单列。留白：模型驱动的修订全循环未跑（成本，8.8 可加 `--deep`）；`config.yaml` 默认模型 qwen3:4b 未安装（实际用 qwen3:8b，记录不改配置）；远程 Provider 无 Key、未调用，不伪造证据；单次成功不宣称全书无冲突或文学质量。

质量门（V03-8.8）：本轮只改文档，未动代码——全量 pytest **1975 passed**、vitest **433 passed**、ruff/mypy 沿用 8.7 基线。文档新增：`modules/`（5 篇 0.3 as-built + 索引）、`release.md`（运行说明 + 发布记录 + 未验项）、`artifacts/v03-8.8-acceptance-tracking.md`（A01–A33 逐条结论：28 通过/5 部分；§9.4 十场景未执行；三批退出一/二达成、第三批未端到端）。结论：**0.3 不标「完整交付」**——功能面 V03-0–V03-8.7 交付完成，完整验收未过，未验项已显式列出（十场景夹具、真模型修订全循环、真 uvicorn 日志、前端日志面板、V1 语料写接口、节拍类型退出、远程 Provider）。

质量门：`uv run pytest -q` **1863 passed**（V03-7.2 后端专项 26）；`ruff check src tests` 全过；`ruff format --check`（本任务文件）全过；`mypy src/hastings` 252 files 无问题。前端未改动（本任务为后端契约 + 领域台账），沿用 V03-7.1 口径 `vitest` 428 passed / `npm run build` 通过。留白：预算与两轮修正已随 V03-7.3 交付；统一发布与副本门禁属 V03-7.4（本次 `changed` 仅是**记录**已改，分支暂存/合并未接）；`context` 全局上下文当前是可回读 REST 投影，并入模型 `ContextSnapshot` 供执行循环属 V03-7.4；作者侧覆盖清单界面随整体执行界面阶段补齐。

质量门（V03-7.3）：`uv run pytest -q` **1884 passed**（+21 专项：领域 11 + 接口/服务 10）；`ruff check src/hastings tests/v2` 全过；`mypy src/hastings` 254 files 无问题。前端未改动（本任务为后端契约 + 领域状态机），沿用 V03-7.1 口径 `vitest` 428 passed / `npm run build` 通过。留白：统一发布已随 V03-7.4 交付；执行循环（谁在何时读章、调模型、调 `consume_calls` 与 `stage_*`）仍未接；`waiting_reason=author_choice` 目前只计 1 个待定选择，选择项本身仍由 V03-7.1 的 `choices` 承载、尚未写进任务记录；无任务面板，作者侧预算/覆盖/等待界面随整体执行界面阶段补齐；预算默认 12 仍为工程初值（V03-0.7 口径）。

质量门（V03-7.4）：`uv run pytest -q` **1900 passed**（+16 专项：暂存领域 5 + 接口 11）；`ruff check src/hastings tests/v2` 全过；`mypy src/hastings` 256 files 无问题。前端未改动（本任务为后端契约 + 领域暂存区），沿用 V03-7.1 口径 `vitest` 428 passed / `npm run build` 通过。留白：执行循环未接（谁读章、调模型、调 `stage_*` 与 `consume_calls`）；账本只暂存条目本身、不暂存 `relation_changes`，同一批新建条目不能互相引用（按基础目录校验）；基础前进一律阻断，M10 §10 的「无关变化可重核重建提交计划」未做；不支持用空文本删除整章；暂存与发布没有界面入口。旧入口枚举测试同步修正：`stage` 改为按路径段精确匹配，避免误伤 `staged-chapters`。

质量门（V03-7.5）：`uv run pytest -q` **1910 passed**（+10 专项：领域 5 + 接口 5）；`ruff check src/hastings tests/v2` 全过；`mypy src/hastings` 256 files 无问题。前端 `vitest` **430 passed**（50 文件，含手法图客户端 1 + 空态文案 1）、`npm run build` 通过（主 bundle 830.41 kB / gzip 273.17）。留白：时间线图属 V03-7.6；刻意不解析步骤（需要步骤时先有正式领域对象与作者确认）；同批新建条目引用与 `relation_changes` 不入手法子图；侧栏概览没有单独的缺口摘要块；完整度只表示三段文字记录数，不评价手法可行性。

质量门（V03-7.7）：`uv run pytest -q` **1927 passed**（+6 专项：接口 5 + 来源校验 1，其中影响/撤销接口 5）；`ruff check src/hastings tests/v2` 全过；`mypy src/hastings` 259 files 无问题。前端 `vitest` **433 passed**（51 文件）、`npm run build` 通过。留白：关联采用只报告被修改条目，其新建条目不能随撤销删除（本版无删除账本操作）；影响统计是字符级增删不是可视化 diff，正文内联高亮随整体执行界面补齐。

质量门（V03-7.6 基线）：**1921 passed**（+11 专项：领域 6 + 接口 5）；`ruff check src/hastings tests/v2` 全过；`mypy src/hastings` 257 files 无问题。前端 `vitest` **432 passed**（50 文件，含时间线客户端 1 + 空态 1）、`npm run build` 通过（主 bundle 833.85 kB / gzip 274.10）。留白：事件之间没有结构化顺序，故不排序；`story.knowledge.at_beat_id` 无账本写入口（0.3 不以节拍为前置），新记录的叙述位置通常为 null；证词时间没有独立字段、只保留备注原文；按时段筛选/高亮未做；时间线不进入画布布局，走文字面板。

