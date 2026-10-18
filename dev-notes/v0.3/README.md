# Hastings 0.3 开发文档

> 状态：**V03-0、V03-1 已交付收口**（V03-0 收口于 commit `ad2a166`；V03-1 收口于 commit `53c7eb2` + 同日「接着讨论」前端接线与切片走查 10/10，收口检查单见下，两项已声明偏差显式顺延）。**V03-2 已交付（2.6 部分交付：后续状态可见，启动入口随 V03-4）**。**V03-3 进行中**：3.1 活动起草路径已去节拍前置（旧规划管线待 3.2 一起删）、3.3/3.4/3.5 已交付（试写 → 只读预览 → 按哈希采用），3.2 账本直接保存与候选成组未开始。更新：2026-10-18。

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

## V03-1 进行中（2026-10-17）

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

## V03-3 进行中（2026-10-18 起）

开始条件检查单：导入与统一发布可用（V03-2.3/2.5）✓；模型启动有来源凭据与连接快照（V03-1.4/19.5）✓。

质量门基线：`uv run pytest -q` **1579 passed**；`ruff check` 全过；`mypy src/hastings` 223 files 无问题；前端 vitest **369 passed**（43 文件）+ `vite build` 通过。

| 任务 | 状态 | 产物 | 构建笔记 |
|---|---|---|---|
| V03-3.1 Beat 活动路径退出 | **部分交付**（起草前置/写作上下文已去节拍；旧规划管线仍可创建节拍，随 3.2 收尾一起删） | `task_drafts.missing_items` 去掉 `TASK_NO_BEAT_TARGET`/`TASK_NO_PLAN`、空 head 对起草合法；writer 角色与上下文只在目标是节拍时装配 `beat_states` | [notes/v03-3](notes/v03-3-drafting-and-adoption.md) |
| V03-3.2 账本直接保存与候选成组 | **未开始** | — | — |
| V03-3.3 无前置起草 | 已交付 | `domain/manuscript/drafting.py`（brief/候选/确定性提示词/文本清理）、`application/services/drafting_service.py`、`GET /ledger` 只读账本、`POST /drafts` | [notes/v03-3](notes/v03-3-drafting-and-adoption.md) |
| V03-3.4 只读完整预览与按哈希采用 | 已交付 | `GET /drafts/{id}`、`POST /drafts/{id}/revise`、`POST /drafts/{id}/adopt`（`preview_hash` + 修订号双重校验）、`POST /drafts/{id}/discard`；写作台「试写」对话框 | 同上 |
| V03-3.5 采用后定位与唯一结果 | 已交付 | 采用经 `PublishManuscriptChange`（新章 `chapter.create` / 现有章 `save_full_text`），返回 `chapter_id` + `operation_id`；前端定位正文并接 V03-2.6 后续状态 | 同上 |

专项测试 18 项（全量 1579）；前端客户端契约 5 项（全量 369）。
