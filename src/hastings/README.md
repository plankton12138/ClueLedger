# Hastings 后端导航

Hastings 提供悬疑小说写作工作台。默认启动是 0.7 工作区：作品为本地 Markdown 目录，人物小传一人一文件，接口前缀 `/api/file-workspaces`；本轮材料、修改范围与只读检查按 0.6 契约执行，故事资料页与资料任务按 0.7 契约执行。

`python -m hastings.server` 只装配文件工作区应用（`server/file_app.py`），不打开旧工作台数据库、不挂 `/api/v2` 与旧业务路由；`--workspace <目录>` 用于启动时预先打开作品。模型能力是应用级的一组 `/api/model-*` 接口：供应商预设 → 连接（地址、协议、凭据引用、模型）→ 当前选择，写作轮次在受理时固定这一轮的连接与模型。写作 Agent 直接读写作品文件，历史由作品目录内的真实 Git 仓库记录。前端已接新接口（见[前端说明](../../frontend/README.md)）；模型连接已用真实 DeepSeek 在接口层取证，界面渲染由构建与状态测试覆盖。

0.3 的模块与测试已在 V04-5 删除，只剩少量仍在用的文件（见「包职责」）。0.3 文档作为历史记录保留，其中描述的代码已不存在，不要据此推断可用能力。解释现状时先读[0.7 模块导航](../../dev-notes/v0.7/modules/README.md)，运行与验收口径见[0.7 运行说明](../../dev-notes/v0.7/release.md)；没有变化的会话、连接与执行契约按 0.6/0.5/0.4 模块说明的具体链接读取。

## 当前代码入口

以下路径相对本目录。

| 要修改的行为 | 主要位置 | 当前说明 |
|---|---|---|
| 作品活动索引、持续订阅、模型请求诊断、详情保留与导出 | `workspace/activity.py`、`workspace/model_activity.py`、`workspace/api.py` | [活动记录](../../dev-notes/v0.6/modules/activity-log.md) |
| 服务入口、静态目录与退出码 | `server/__main__.py`、`server/file_app.py` | [运行与验收](../../dev-notes/v0.5/release.md) |
| 文件规则、读写指纹、搜索、导入 | `workspace/files.py` | [文件工作区](../../dev-notes/v0.5/modules/file-workspace.md) |
| 新作品的默认位置与位置选择 | `workspace/locations.py`、`workspace/directory_dialog.py` | [文件工作区](../../dev-notes/v0.5/modules/file-workspace.md) |
| 最近目录、会话索引、事件、追尾读取与界面草稿 | `workspace/state.py` | [会话与草稿](../../dev-notes/v0.4/modules/conversations.md) |
| 会话上下文回放与执行事实 | `workspace/conversation_context.py` | [会话与草稿](../../dev-notes/v0.4/modules/conversations.md) |
| Git 历史、提交与撤销 | `workspace/git.py` | [Git 历史](../../dev-notes/v0.4/modules/git-history.md)，受管路径见[当前文件规则](../../dev-notes/v0.5/modules/file-workspace.md) |
| 一轮请求的受理、执行与记录 | `workspace/service.py` | [写作 Agent 与对话](../../dev-notes/v0.4/modules/agent-and-chat.md)，回复展示见[对话展示](../../dev-notes/v0.5/modules/chat-display.md) |
| 模型增量到会话事件的桥接、合批与中断修复 | `workspace/chat_stream.py` | [对话展示与流式回复](../../dev-notes/v0.5/modules/chat-display.md) |
| 章序、章节显示名与人物身份标签的解析和写入检查 | `workspace/metadata.py` | [文件工作区与角色小传](../../dev-notes/v0.5/modules/file-workspace.md) |
| 文件工具与系统提示 | `workspace/agent.py`、`workspace/context.py` | [人物按需读取与文件工具](../../dev-notes/v0.5/modules/file-workspace.md) |
| 材料来源、读取区间、受理快照与有界输出 | `workspace/materials.py` | [V06-A1 读取与材料基础](../../dev-notes/v0.6/notes/V06-A1-materials.md)、[V06-A2 可选参考闭环](../../dev-notes/v0.6/notes/V06-A2-references.md) |
| 章节索引的生成、发布与状态 | `workspace/chapter_index.py` | [V06-A3 章节索引](../../dev-notes/v0.6/notes/V06-A3-chapter-index.md) |
| 故事资料的作者视图、证据与覆盖记录 | `workspace/story_materials.py` | [故事资料](../../dev-notes/v0.7/modules/story-materials.md) |
| L0 短目录、L1 有界查询与来源标签（0.8 F1） | `workspace/retrieval.py` | [F1 检索基础](../../dev-notes/v0.8/notes/F1-retrieval.md) |
| 资料任务的范围、域权限、发布与完成度 | `workspace/material_tasks.py` | [故事资料](../../dev-notes/v0.7/modules/story-materials.md) |
| 只读检查、报告与失效 | `workspace/review.py` | [V06-C1 只读检查](../../dev-notes/v0.6/notes/V06-C1-review.md) |
| 本轮修改范围、操作检查与承接规则 | `workspace/task_scope.py` | [V06-B1 任务范围基础](../../dev-notes/v0.6/notes/V06-B1-task-scope.md)、[V06-B2 范围事件](../../dev-notes/v0.6/notes/V06-B2-scope-events.md) |
| HTTP 接口（文件、Git、对话、事件） | `workspace/api.py` | [模块导航](../../dev-notes/v0.5/modules/README.md) |
| 模型档案、provider 与凭据 | `server/provider_config.py`、`infrastructure/credentials.py` | [模型连接](../../dev-notes/v0.4/modules/model-connections.md) |
| 供应商预设、协议构造与实例关闭 | `server/provider_catalog.py`、`server/provider_runtime.py` | [模型连接](../../dev-notes/v0.4/modules/model-connections.md) |
| 连接增删改、凭据提交与选择 | `server/model_connections.py` | [模型连接](../../dev-notes/v0.4/modules/model-connections.md) |
| 模型发现与连接测试 | `server/model_probe.py` | [模型连接](../../dev-notes/v0.4/modules/model-connections.md) |
| 模型设置接口 | `server/model_api.py` | [模型连接](../../dev-notes/v0.4/modules/model-connections.md) |
| 正文导入解析（遗留，仍在使用） | `interfaces/importers/manuscript_text.py` | [文件工作区](../../dev-notes/v0.5/modules/file-workspace.md) |

前端壳层在 `frontend/src/components/workspace/`，故事资料在 `frontend/src/components/materials/`，对话展示组件在 `frontend/src/components/chat/`，作品状态在 `frontend/src/state/workspace.ts`，页面位置在 `frontend/src/state/navigation.ts`，资料数据与任务在 `frontend/src/state/storyMaterials.ts`、`materialTasks.ts`，会话与草稿在 `frontend/src/state/conversations.ts`，事件归并在 `frontend/src/state/chatMessages.ts`，客户端在 `frontend/src/api/workspace.ts` 与 `api/storyMaterials.ts`。角色分组见[当前文件规则](../../dev-notes/v0.5/modules/file-workspace.md)，其余布局见[写作台界面](../../dev-notes/v0.4/modules/desk.md)。

## 包职责

| 包 | 职责 |
|---|---|
| `workspace/` | 文件工作区与资料层：章节与人物文件、读写指纹、搜索、导入、最近目录、Git 历史、写作 Agent、对话与事件、故事资料作者视图与资料任务 |
| `server/` | 启动入口、文件应用装配、供应商预设与连接配置、模型设置接口、协议构造与连接测试 |
| `infrastructure/` | 凭据存储（系统凭据存储优先，回落内存） |
| `domain/` | 仍在使用的遗留文件：`manuscript/diff.py`（导入切段）、`repository/serialization.py`（内容散列） |
| `application/` | 错误基元与凭据端口 |
| `interfaces/` | 正文导入解析 |

`packaging/` 是 sidecar 打包规格，入口 `sidecar_entry.py` 仍有效。

## 历史材料

0.3 的行为与决策见[0.3 文档](../../dev-notes/v0.3/README.md)与[历史记录入口](../../dev-notes/README.md)；这些文档描述的实现已被删除，只用于理解过去的决策，不作为当前能力依据。
