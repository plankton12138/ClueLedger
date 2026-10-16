# hastings — 悬疑小说写作 Agent 应用层

tau 内核之上的**薄应用层**：装配（工具注册、system prompt、事件订阅）与落盘（Bible / 正文 / 审计）全部在此完成，`tau_agent` / `tau_ai` 零改动。设计说明见 [dev-notes/v1/DESIGN.md](../../dev-notes/v1/DESIGN.md)，AI 检索入口见 [dev-notes/README.md](../../dev-notes/README.md)。

## 子包职责

| 路径 | 职责 | 关键文件 |
|---|---|---|
| `bible/` | 账本域：数据模型与状态机、工作区六阶段生命周期、派生统计与审计、时间线检测 | models / store / state_machine / story_state / stats / audit / timeline_check |
| `tools/` | 小说写作工具集（**硬约束所在层**）：账本读写、正文、手法锁、时间线、阶段机；V2 项目下旧写工具（bible_upsert/write_chapter/method_lock/unlock/story_stage）经 `architecture_gate` 换为拒绝桩（Phase 17 T17.6） | bible_tools / chapter_tools / method_tools / timeline_tools / story_tools / architecture_gate / errors |
| `manuscript/` | 正文 SSOT 与保存不变量：段落 diff、revision 归档、write_guard 守门、写作守门（正文直出纠偏 / 回合规速） | store / diff / write_guard / guards / prose_guard / advance_guard |
| `server/` | FastAPI REST + WebSocket 桥、会话装配、工作区注册表、对账队列；V2 REST 面（`/api/v2`，正文保存/恢复与作者模型查询） | app / ws / rest / hastings_session / session / registry / reconcile / v2 |
| `rag/` | 语料与文风：确定性文风档案 + 语料库存储（向量检索待交付） | style_profile / corpus_store |
| V2 五层 `domain/ application/ agents/ infrastructure/ interfaces/` | V2 重建（Phase 11–）：端口 Protocol、错误信封、仓库内核（CommitService + 内存/SQLite 双后端 + inspection）、Story 领域（Canon/叙事/悬疑命令 + 投影）、正文与作者模型（Edit Operation / Patch / 逐 hunk 接受 / 偏好激活 / 保存与分析解耦 / 历史恢复）、变更集与影响分析、无状态 Agent 工作者与持久 Job | application/ports/、application/services/commit_service.py、domain/story/、domain/manuscript/、domain/author_model/、infrastructure/repository/、infrastructure/events/、server/v2.py；as-built 见 [dev-notes/v2/modules/repository-kernel.md](../../dev-notes/v2/modules/repository-kernel.md)、[dev-notes/v2/modules/story-domain.md](../../dev-notes/v2/modules/story-domain.md)、[dev-notes/v2/modules/manuscript-author-model.md](../../dev-notes/v2/modules/manuscript-author-model.md) 与 [dev-notes/v2/modules/interfaces-observability.md](../../dev-notes/v2/modules/interfaces-observability.md) |
| `eval/` | 评测 harness 占位（P13 待交付） | — |
| 顶层模块 | CLI 与渲染、system prompt 组装、会话/事件持久化、多会话索引、上下文管理、设置、provider 目录 | cli / prompts / session_persist / session_store / events_store / context_manager / settings / provider_catalog |

模型接入与选择入口：`provider_catalog.py` 提供公开目录，`server/model_access.py` 保存连接与当前模型，`server/provider_config.py` 管理配置与实例。`server/v2_agents.py` 与 API 共享同一 ProviderBook；前端入口为顶栏模型按钮与 `ModelSettings.tsx`。设计取舍见 [Phase 18](../../dev-notes/v2/phase-18-model-access.md)。

## 五焦点导航（读源码对号入座）

V2 七页作者工作台入口见 [frontend/README.md](../../frontend/README.md) 与 [前端构建笔记](../../dev-notes/v2/phase-17-frontend-workbench.md)。项目身份/正文缓冲在 `frontend/src/state/v2.ts`，业务事件在 `state/v2socket.ts`；作者偏好声明与撤销的 HTTP 适配在 `interfaces/api/author_decisions.py`。

Phase 15 的 V2 变更闭环见 [change-impact.md](../../dev-notes/v2/modules/change-impact.md)：`domain/changes/` 与 `application/workflows/{change_analysis,plan_revision,revision,review,rewrite,audit}.py`；HTTP 在 `interfaces/api/changes.py`，前端在 `frontend/src/components/ChangeCenter.tsx`。服务器通过 `uv run python -m hastings.server --v2-db ./data/v2.sqlite` 显式启用独立仓库，项目直接在 V2 中新建，旧工作区迁移已退役。

Phase 16 的无状态工作者与持久任务见 [orchestration-context.md](../../dev-notes/v2/modules/orchestration-context.md)：`agents/{schemas,roles,coordinator}.py`、`application/contexts/`、`application/workflows/{agent_workflows,worker_job}.py` 与 `infrastructure/agents/tau_runner.py`。Job/事件在 `infrastructure/jobs.py`，API 在 `interfaces/api/agents.py`；界面入口为“变更中心→规划与生成任务”（`AgentWorkbench.tsx`），服务生命周期负责恢复与派发。真模型评测入口为根目录 [eval/v2](../../eval/v2/README.md)，不使用旧 Hastings 工具。

Phase 17 的旧工作区迁移已按[未上线项目的退役决策](../../dev-notes/v2/phase-17-workspace-retirement.md)删除：不再提供迁移面板、slug 迁移接口、导入器或 CLI 工作区映射。旧写路径退役证明在 `tests/v2/server/test_legacy_write_unreachable.py`；V2 业务事件在 `server/v2_ws.py`，备份导出与恢复在 `interfaces/exporters/v2_package_exporter.py`，均保留。

| 想了解 | 入口 |
|---|---|
| 上下文预算与压缩（P10） | [context_manager.py](context_manager.py)：`count_context_tokens` 分层计数（provider usage 锚定 → 字符兜底）+ 两档压缩（确定性剥离 / LLM 摘要）+ 泄压阀；settings 项 `context_window_tokens` |
| 会话生命周期（P10 多会话） | [session_store.py](session_store.py)（sessions.json 索引 + 迁移）、[server/hastings_session.py](server/hastings_session.py)（busy 防护、每轮 `_refresh_system`）、[session_persist.py](session_persist.py) |
| 事件消费点（`harness.subscribe` fan-out 多消费者） | [cli.py](cli.py) 的 `PlainRenderer`、[server/ws.py](server/ws.py)（WS 帧桥）、[events_store.py](events_store.py)（events.jsonl）、[session_persist.py](session_persist.py)（session.jsonl） |
| 工具定义与使用 | [tools/](tools/)（注册与执行）；契约见 [dev-notes/v1/modules/writing-tools.md](../../dev-notes/v1/modules/writing-tools.md) |
| 写作守门装配（P10 硬约束） | [manuscript/guards.py](manuscript/guards.py)（`attach_guards`，一处接线 CLI/eval/Web）：prose_guard（正文直出 steering 纠偏）、advance_guard（`before_tool_call` 回合规速） |

## 深入阅读

- 架构总览与六阶段状态机图：[DESIGN.md](../../dev-notes/v1/DESIGN.md) §2.3、§3 开头的 **as-built 标注块**；
- 各阶段「为什么这样设计」：[dev-notes/README.md](../../dev-notes/README.md) 阶段索引；
- 账本存储 / 工具契约：[dev-notes/v1/modules/](../../dev-notes/v1/modules/README.md)。
