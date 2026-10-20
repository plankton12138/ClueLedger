# Hastings 后端导航

Hastings 提供悬疑小说写作工作区，负责正文、故事账本、创作任务、模型连接与持久化。默认运行仍是 0.3 写作台，业务接口沿用 `/api/v2`。

0.4 的文件工作区已完成基础部分（V04-1）：作品内容改为本地 Markdown 目录，新增 `workspace/` 包与 `--workspace <目录>` 启动参数，接口前缀 `/api/file-workspaces`。该模式不装配旧工作台，只提供目录、读取、搜索与导入；Git、写作 Agent、对话和前端接线尚未实现，默认入口未切换。

先从[当前模块说明](../../dev-notes/v0.3/modules/README.md)理解 0.3 行为与约束，再按下表定位源码。0.4 已实现的行为见[文件工作区](../../dev-notes/v0.4/modules/file-workspace.md)。历史目标不能替代当前实现；运行与验收限制见[0.3 运行说明](../../dev-notes/v0.3/release.md)。

## 当前代码入口

以下路径相对本目录，前端路径相对仓库根目录。

| 要修改的行为 | 主要位置 | 当前说明 |
|---|---|---|
| 服务装配、默认数据库、启动恢复 | `server/__main__.py`、`server/v2.py` | [发布、导入与恢复](../../dev-notes/v0.3/modules/publish-import-recovery.md) |
| 正文发布、导入、操作归属 | `application/services/{publish_manuscript_change,import_service,operation_service}.py`、`application/workflows/operation_projection.py` | [发布、导入与恢复](../../dev-notes/v0.3/modules/publish-import-recovery.md) |
| 手工修改副本、合并、快照与撤销 | `domain/manuscript/{edit_session,merge}.py`、`application/services/{edit_session_service,undo_service}.py` | [修改与撤销](../../dev-notes/v0.3/modules/editing-and-undo.md) |
| 试写候选与预览采用 | `domain/manuscript/drafting.py`、`application/services/drafting_service.py` | [修改与撤销](../../dev-notes/v0.3/modules/editing-and-undo.md) |
| 账本、本书约定、背景、章摘要与更新建议 | `application/services/{ledger_service,ledger_candidate_service,agreement_service,summary_service,suggestion_service}.py`、`application/contexts/` | [账本与模型背景](../../dev-notes/v0.3/modules/context-and-ledger.md) |
| 正文证据与披露边界 | `domain/manuscript/evidence.py`、`application/services/{evidence_service,disclosure_service}.py` | [账本与模型背景](../../dev-notes/v0.3/modules/context-and-ledger.md) |
| 对话、意图、整体修订、预算与覆盖 | `domain/conversation/`、`domain/revision/`、`application/services/{conversation_service,intent_service,revision_work_service,revision_task_service,revision_execution_service,revision_publish_service}.py` | [修订与覆盖](../../dev-notes/v0.3/modules/revision-and-coverage.md) |
| 图谱与时间线 | `domain/story/{graph,timeline}.py`、`application/queries/graphs.py` | [图谱](../../dev-notes/v0.3/modules/graphs.md) |
| 执行日志、历史分页与实时流 | `domain/execution_log.py`、`infrastructure/execution_log.py`、`interfaces/api/execution_log.py` | [执行日志](../../dev-notes/v0.3/modules/execution-log.md) |
| 路由装配与旧功能限制 | `server/v2.py`、`interfaces/api/`、`interfaces/api/_retired.py` | [API 与兼容边界](../../dev-notes/v0.3/modules/api-matrix-and-retirement.md) |
| 模型目录、连接与本地模型任务 | `provider_catalog.py`、`server/{model_access,provider_config,local_model_service,v2_agents}.py` | [前端模型接入](../../frontend/README.md) |
| 0.4 作品目录、读写指纹、搜索与导入 | `workspace/{files,state,api}.py`、`server/file_app.py`、`server/__main__.py` 的 `--workspace` | [文件工作区](../../dev-notes/v0.4/modules/file-workspace.md) |

前端壳层在 `frontend/src/components/desk/`，状态在 `frontend/src/state/desk*.ts`；项目与正文缓冲在 `state/v2.ts`，业务事件在 `state/v2socket.ts`。详见[写作台模块](../../dev-notes/v0.3/modules/desk-surface.md)。

## 包职责

| 包 | 职责 |
|---|---|
| `domain/` | 故事、正文、版本、任务和约束的领域模型 |
| `application/` | 用例、查询、工作流、背景装配与端口 |
| `agents/` | 工作者角色、输入输出结构与协调；生成提案，不直接发布正式内容 |
| `infrastructure/` | 仓库、任务、会话、事件与模型端口的实现 |
| `interfaces/` | HTTP、导入导出等边界适配 |
| `server/` | 配置、连接、运行时与服务装配 |
| `workspace/` | 0.4 文件工作区：目录规则、读写指纹、搜索、导入、最近目录与文件接口 |

版本仓库的基础契约见[仓库内核](../../dev-notes/v0.2/modules/repository-kernel.md)，故事基础类型见[故事领域](../../dev-notes/v0.2/modules/story-domain.md)。两篇为旧版本材料，只按需读取当前模块导航指定的契约。

## 兼容代码

`bible/`、`tools/`、`manuscript/`、`rag/`、旧 CLI 与会话模块仍含早期工作区实现。它们的存在不表示 0.3 提供相应功能；旧写工具限制、模型审计拒绝与语料接口保留的边界见[API 说明](../../dev-notes/v0.3/modules/api-matrix-and-retirement.md)。追查旧行为时从[历史文档入口](../../dev-notes/README.md#历史记录)进入。
