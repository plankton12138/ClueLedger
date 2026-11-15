# V03-0.1 全链路 API 与调用点清单

> 状态：as-inventoried（盘点基线），待实施。生成：2026-10-17，基线 commit `90e5f75`，分支 `v03-0-baseline`。
> 方法：不凭模块名推断。后端逐条读路由注册点（`server/rest.py`、`server/model_access.py`、`server/v2.py`、`interfaces/api/{conversations,agents,changes,author_decisions}.py`），前端逐条读 `frontend/src/api/*` 与 `state/*` 调用点并交叉核对，任务启动逐条追 `JobStore.create` / 模型运行入口与恢复路径。
> 处置列含义：**保留**=原样进入 0.3；**演进**=同路径改语义/加参数；**适配**=接到统一发布管线；**隔离**=0.3 装配不再启动、数据保留只读；**退役**=替代闭环通过后移除（V03-8）。

## 1. V1 REST 面（`server/rest.py`，前缀 `/api/{slug}/`，按 slug 寻址工作区）

| 路径/动作 | 现状 | 0.3 处置 | 替代/目标 | 处置阶段 |
|---|---|---|---|---|
| `GET /api/workspaces` | 工作区列表 | 保留 | 作品选择入口数据源 | V03-1/8 |
| `POST /api/workspaces` | 新建工作区 | 保留 | — | V03-1 |
| `DELETE /api/workspaces/{slug}` | 删除工作区 | 保留（危险操作，注意删除边界） | — | V03-8 |
| `GET /api/provider-catalog` | 厂商目录 | 保留 | 模型引导只读数据 | V03-1 |
| `GET/POST /api/{slug}/sessions` 等 5 个 | V1 会话管理 | 保留（V1 兼容） | 与 0.3 创作记录并存的旧入口 | V03-8 |
| `POST /api/{slug}/session/compact` | V1 会话压缩 | 保留（V1 兼容） | 同上 | V03-8 |
| `GET /api/{slug}/state` | V1 聚合状态 | 保留（V1 兼容） | 同上 | V03-8 |
| `GET /api/{slug}/bible/{entity_type}` | V1 设定查询 | 保留（V1 兼容） | 0.3 账本查询不经过它 | V03-8 |
| `GET /api/{slug}/stats` | V1 统计 | 保留（V1 兼容） | — | V03-8 |
| `GET /api/{slug}/story` | V1 故事视图 | 保留（V1 兼容） | 0.3 图谱/账本查询替代 | V03-6 |
| `GET /api/{slug}/audit/{chapter_id}` | V1 一致性审计 | **隔离** | 本版排除项；历史可读、无启动入口 | V03-0/8 |
| `GET /api/{slug}/chapters`、`/chapters/{chapter_id}` | V1 章节读取 | 保留（V1 兼容） | 0.3 正文读走 `/api/v2` manuscript | V03-8 |
| `GET /api/{slug}/events`、`/events/export` | V1 事件流 | 保留（V1 兼容） | 0.3 执行日志是独立事实源 | V03-8 |
| `GET /api/{slug}/export` | V1 导出 | 保留（V1 兼容） | 0.3 阅读导出/备份替代 | V03-2/8 |
| `GET/PUT /api/{slug}/settings` | V1 设置 | 保留（V1 兼容） | — | V03-8 |
| `GET/POST /api/corpus`、`DELETE /api/corpus/{name}`、`GET /api/corpus/style-profile` | 语料库/风格档案 | **隔离** | 本版排除项（材料库/RAG）；数据保留、无 0.3 入口 | V03-0/8 |
| `WS /ws/{slug}` | V1 工作区 WebSocket | 保留（V1 兼容） | 0.3 业务 WS 走 `/ws/v2/{project_id}` | V03-8 |

## 2. 服务级模型接入面（`server/model_access.py` + `rest.py` 内 ollama/provider 段）

| 路径/动作 | 现状 | 0.3 处置 | 替代/目标 | 处置阶段 |
|---|---|---|---|---|
| `GET/PUT /api/model-selection` | 当前模型选择 | 保留 | 连接同源后仅返回非敏感快照 | V03-1 |
| `POST /api/providers/connect` | 保存连接（Key 进配置文件） | **演进** | 桌面凭据存储端口，元数据只存 `credential_ref`；禁止普通流回退环境变量 | V03-1 |
| `GET /api/providers` | 连接列表 | 演进 | 掩码显示，不回显 Key | V03-1 |
| `GET /api/providers/{name}/models` | 发现模型 | 保留 | — | V03-1 |
| `PUT /api/providers/{name}`、`DELETE /api/providers/{name}` | 改/删连接 | 演进 | 删连接后新 attempt 明确要求重连 | V03-1 |
| `POST /api/providers/test` | 测试连接 | **演进** | 优先收 `connection_id/revision + model`，实际使用已保存连接；同一连接才更新 verified | V03-1 |
| `GET /api/providers/{name}/model-choices` | 可选模型 | 保留 | — | V03-1 |
| `GET /api/ollama/status`、`/catalog`、`/models` | 本地环境检测 | 保留 | 区分未安装/未运行/不可达/无模型 | V03-1 |
| `POST /api/ollama/pull` | 本地模型下载（进度流） | **已删除（V03-1.4 收敛）** | 由 `/api/local-model-tasks` 任务记录取代，离开后可恢复真实阶段 | ✅ V03-1.4 |
| `DELETE /api/ollama/models/{name}` | 删除本地模型 | 保留 | — | V03-1 |

## 3. V2 项目面（`server/v2.py`，前缀 `/api/v2/projects/{project_id}`）

| 路径/动作 | 现状 | 0.3 处置 | 替代/目标 | 处置阶段 |
|---|---|---|---|---|
| `POST /api/v2/projects` | 创建作品 | 保留 | 无作品入口预约后创建 | V03-1 |
| `GET .../manuscript` | 全文读取 | 保留 | 读请求带 `at_commit_id` | V03-2 |
| `POST .../chapters/import` | 单章文本导入（旧语义） | **适配** | 改走预览 + 统一发布；旧语义拒绝或迁移 | V03-2 |
| `GET .../chapters/{chapter_id}` | 单章读取 | 保留 | — | V03-2 |
| `POST .../chapters/{chapter_id}/save` | 单章正式保存 | **适配** | 手改副本 + finish 统一发布；旧路径不再直接动正式稿 | V03-2 |
| `POST .../chapters/{chapter_id}/restore` | 指定版本恢复 | **适配** | 复用 `PublishManuscriptChange`（新操作、不移动 head） | V03-2 |
| `GET .../author-model/preferences`、`/observations`、`/preferences/{id}/evidence` | 作者模型资料 | **隔离** | 本版排除项；历史可读、无注入、无启动 | V03-0/8 |
| `GET .../export` | 版本包导出 | **演进** | 增加 0.3 元数据与恢复验证；明确 format 参数 | V03-8 |

## 4. 对话与任务卡面（`interfaces/api/conversations.py`，前缀 `/api/v2/projects/{project_id}`）

| 路径/动作 | 现状 | 0.3 处置 | 替代/目标 | 处置阶段 |
|---|---|---|---|---|
| `GET /conversations` | 讨论列表 | 保留 | 多讨论历史 | V03-1 |
| `POST /conversations` | 新建讨论 | 保留 | — | V03-1 |
| `GET /conversations/{id}/messages` | 消息历史 | 保留 | 补无模型消息（`not_requested`） | V03-1 |
| `POST /conversations/{id}/turns` | 发送并运行模型 | **演进** | 发送前先完成作者消息落库；启动凭据校验后运行 | V03-1 |
| `POST /conversations/{id}/turns/{turn_id}/cancel`、`/retry` | 停止/重试 | 保留 | 重试创建新 attempt | V03-1 |
| `POST /task-drafts` | 创建任务草稿（含节拍检查） | **演进** | 起草缺失项改为正文目标/作者要求/范围；删 Beat 前置 | V03-3 |
| `GET/PATCH /task-drafts/{draft_id}`、`POST /task-drafts/{draft_id}/abandon` | 草稿读写/放弃 | 保留 | — | V03-3 |
| `POST /task-drafts/{draft_id}/start` | 启动任务 | **演进** | 统一 ExecutionGrant 校验；无凭据旧 Job 不派发 | V03-0/3 |
| `POST /conversations/{id}/messages/{message_id}/accept` | 采用候选（经审阅规则） | **演进** | 采用携带预览版本与哈希，走统一发布 | V03-3 |
| `GET /conversations/{id}/events`（SSE） | 会话事件流 | 保留 | 与执行日志 SSE 分流；cursor 补齐 | V03-1 |

## 5. 变更中心面（`interfaces/api/changes.py`，前缀 `/api/v2/projects/{project_id}`）

| 路径/动作 | 现状 | 0.3 处置 | 替代/目标 | 处置阶段 |
|---|---|---|---|---|
| `GET/POST /changes`、`GET /changes/{cs_id}` | 变更集 CRUD | 保留（内部能力） | 0.3 UI 不直接暴露；整体任务内部使用 | V03-7 |
| `POST /changes/{cs_id}/analyze`、`/plan`、`/approve`、`/execute`、`/review`、`/merge` | 变更审批流 | **隔离** | 0.3 不保留逐章审批；底层能力按用例复用 | V03-7/8 |
| `POST /audit` | 全书一致性审计 | **隔离** | 本版排除项；请求完整审计须明确说明范围 | V03-0/8 |
| `POST /rewrites`、`GET /rewrites/{run_id}/compare`、`/execute`、`/{action}` | 修订工作流 | **隔离** | 进行中对象可打开/继续或明确转入历史 | V03-7/8 |

## 6. Agent 任务面（`interfaces/api/agents.py`，前缀 `/api/v2/projects/{project_id}`）

| 路径/动作 | 现状 | 0.3 处置 | 替代/目标 | 处置阶段 |
|---|---|---|---|---|
| `GET /story` | 故事快照 | 保留 | 上下文装配数据源 | V03-4 |
| `POST /coordinate` | 多 Agent 协调入口 | **退役** | 前端无调用点；0.3 不要求新通用 Agent 框架 | V03-8 |
| `POST /agent-jobs`、`GET /agent-jobs`、`GET /agent-jobs/{job_id}`、`GET /agent-jobs/{job_id}/events` | 任务 CRUD/事件 | **演进** | 启动带 ExecutionGrant；任务快照固定连接 | V03-1/3 |
| `POST /agent-jobs/{job_id}/run`、`/cancel` | 手动运行/取消 | 演进 | 运行前校验凭据与预算 | V03-3/7 |
| `POST /agent-jobs/{job_id}/accept` | 采用任务结果 | **演进** | 按预览 hash 采用；走统一发布 | V03-3 |
| `POST /changes/{cs_id}/execute-agent` | 变更集执行 Agent | 隔离 | 随变更审批流处置 | V03-7/8 |

## 7. 作者模型决策面（`interfaces/api/author_decisions.py`）

| 路径/动作 | 现状 | 0.3 处置 | 替代/目标 | 处置阶段 |
|---|---|---|---|---|
| `POST /author-model/preferences` | 申报偏好 | **隔离** | 本版排除项；本书约定（`/agreements`）替代 | V03-4 |
| `POST /author-model/preferences/{id}/revoke` | 撤销偏好 | 隔离 | 同上 | V03-4 |

## 8. 任务启动链路（模型调用入口，逐条追迹）

| 链路 | 现状 | 0.3 处置 | 处置阶段 |
|---|---|---|---|
| `manuscript.chapter.committed` 事件 → `OutboxPublisher` → `AnalysisDispatcher.publish` → `JobStore.create("manuscript.analysis")`（event_id 幂等） | 保存即自动建分析 Job | **切断**：0.3 装配不再注册 dispatcher；历史事件与旧资料保留 | V03-0.4 |
| `worker_loop` → `prepare_analysis_jobs()` → 认领 `manuscript.analysis` → `agent_workflows.style()` → `WorkerJobRunner.submit` | 分析 Job 自动派发风格观察 | **切断**：同上去掉下游派发；旧 Job 写分类报告默认 `historical_paused` | V03-0.4 |
| `worker_loop` 主循环：派发 `queued/retryable_failed` 全部类型（除 `manuscript.analysis`）+ `auditor/style_observer` 的 `analysis_pending` | 自动消费旧队列 | **收紧**：派发前 ExecutionGrant 校验；无凭据不派发 | V03-0.3 |
| `worker_loop` 启动 `jobs.recover()` + lifespan `chat.mark_interrupted()` | 重启恢复：轮次标中断、Job 按 retry_at 重试 | **收紧**：恢复不自动重发模型调用；无法确认完成标 `interrupted` 等作者 | V03-0.3 |
| `POST /task-drafts/{id}/start` → `task_prep_service.start_draft` → `agent_workflows.{draft,plan,audit}` → `worker.submit` | 作者显式启动创作任务 | **演进**：删 Beat 前置；自然语言明确授权可直启；凭据+连接快照 | V03-3/4 |
| `POST /conversations/{id}/turns` → `conversation_service._spawn/_execute` → `runner.stream` | 发送即直接跑模型（不经 JobStore） | **演进**：先落作者消息再启动；零隐式调用 | V03-1 |
| `POST /agent-jobs` → `runtime.worker.submit` | 直接提交 Agent 任务 | **演进**：凭据校验 | V03-3 |
| `agent_workflows.accept` → `owner.jobs.submit`（分析型 Job） | 采用路径派生 Job | 审查：确认 0.3 是否需要保留或随审阅流隔离 | V03-3 |

## 9. 前端调用点交叉核对（`frontend/src/api/*` + `state/*`）

| 前端模块/函数 | 目标后端 | 核对结果 | 0.3 处置 |
|---|---|---|---|
| `api/v2.ts`：createProject/fetchManuscript/fetchV2Chapter/saveV2Chapter/restoreV2Chapter/importChapter/exportUrl | V2 项目面 | 一一对应 | save/import/restore 适配统一发布（V03-2） |
| `api/v2.ts`：fetchStorySnapshot/listChanges/openChangeSet/fetchChangeDetail/changeAction/runAudit | 变更中心面 | 对应 | UI 收缩为整体任务内部能力（V03-7/8） |
| `api/v2.ts`：fetchPreferences/declarePreference/revokePreference/fetchObservations/fetchPreferenceEvidence | 作者模型面 | 对应 | 排除项；页面退役（V03-8） |
| `api/v2.ts`：submitAgentJob/listAgentJobs/fetchAgentJob/fetchAgentJobEvents/runAgentJob/cancelAgentJob/acceptAgentJob | Agent 任务面 | 对应 | 凭据化演进（V03-3） |
| `api/v2.ts`：coordinate | `POST /coordinate` | **后端多余接口**（前端 0 调用） | 退役（V03-8） |
| `api/chat.ts`：createHttpChatService（14 个对话/任务卡动作 + SSE 订阅） | 对话面 | 逐条对应 | 无模型记录/接着讨论/凭据启动（V03-1） |
| `api/chat.ts`：probeChatCapability | `GET /conversations/{id}/events` 探测 | 对应 | 保留 |
| `api/rest.ts`：bible/stats/workspaces/sessions/chapters/settings/story/events/corpus/providers* | V1 面 | 对应 | V1 兼容保留；corpus 入口隔离（V03-8） |
| `api/models.ts`：fetchModelSelection/selectModel/connectProvider/fetchModelChoices | 模型接入面 | 对应 | 同源连接演进（V03-1） |
| `api/socket.ts`：AgentSocket | `WS /ws/{slug}` | 对应（V1） | V2 WS 并存；0.3 业务 WS 走 `/ws/v2/{project_id}` |
| `api/chatSimulator.ts` + `state/chat.ts#mockEnabled` | 无（本地模拟） | **开发模式默认走模拟器**（`import.meta.env.DEV` 恒真；生产/`chatMock=1` 才探测真实 API） | 0.3 验收必须生产构建或显式关闭 mock；模拟器不作交付证据（V03-1/8） |
| `state/{v2,v2events,v2socket}.ts`、`useAgentSocket.ts` | V2 WS/事件 | 对应 | 执行日志接入后分流（V03-1） |
| 页面：Overview/Studio/Ledger/Changes/Audit/AuthorModel + Corpus/RelationGraph 组件 | 多面 | 七页导航 | 改正文中心工作区；旧入口替代后退役（V03-1/8） |

**缝隙结论**：
- 前端有、后端缺：未发现（0.2 前后端基本对齐；`chatMock` 模拟器是显式例外）。
- 后端有、前端无：`POST /coordinate`（多余接口，退役）；部分 V1 会话/ bible / stats 端点仅旧页使用。
- 语义错位（需 0.3 适配而非新增）：`chapters/{id}/save`、`chapters/import`、`chapters/{id}/restore` 三条写路径仍绕过操作记录与证据核对。

## 10. 本清单支撑的后续决策

1. V03-0.2 冻结四类身份时以 §3/§4/§8 的写入面为界：正式写只经统一发布，副本/候选/任务快照各有 ID 前缀。
2. V03-0.3/0.4 的零模型调用测试覆盖点：§8 全部 8 条链路（重点前 4 条自动链）。
3. V03-0.6 Beat 盘点已定位 9 个引用文件（`domain/changes/{patch_guard,scope}.py`、`domain/conversation/task_drafts.py`、`domain/story/canon/commands.py`、`domain/story/mystery/{models,rules}.py`、`domain/story/narrative/state.py`、`domain/story/views.py`、`server/v2.py`），逐文件分类在 V03-0.6 执行。
4. V03-0.7 配置盘点入口：§2 的凭据存储（`provider_config` 配置文件明文）、导入上限（当前 `chapters/import` 无上限）、调用预算初值、日志源清单（§8 各链 + Outbox + Job events + 对话 SSE）。

## 验证与留白

- 盘点方式为静态阅读路由注册点与前端调用点（2026-10-17，commit `90e5f75`），未运行服务抓包；实施 V03-1 时以运行中 OpenAPI（`/openapi.json`）复核差异并更新本表。
- V1 面逐条「保留（V1 兼容）」的确切退役条件在 V03-8.6 API 调用矩阵中逐项销号，本表不预先判定。
- 前端页面→API 的映射按非测试源码引用计数核对（0 引用判多余），测试文件引用不计入产品链路。
