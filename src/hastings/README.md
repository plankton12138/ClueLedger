# Hastings 后端导航

Hastings 提供悬疑小说写作工作台。默认启动是 0.4 文件工作区：作品为本地 Markdown 目录，接口前缀 `/api/file-workspaces`。

`python -m hastings.server` 只装配文件工作区应用（`server/file_app.py`），不打开旧工作台数据库、不挂 `/api/v2` 与旧业务路由；`--workspace <目录>` 用于启动时预先打开作品。模型能力是应用级的一组 `/api/model-*` 接口：供应商预设 → 连接（地址、协议、凭据引用、模型）→ 当前选择，写作轮次在受理时固定这一轮的连接与模型。写作 Agent 直接读写作品文件，历史由作品目录内的真实 Git 仓库记录。前端已接新接口（见[前端说明](../../frontend/README.md)）；模型连接已用真实 DeepSeek 在接口层取证，界面渲染由构建与状态测试覆盖。

0.3 的模块与测试已在 V04-5 删除，只剩少量仍在用的文件（见「包职责」）。0.3 文档作为历史记录保留，其中描述的代码已不存在，不要据此推断可用能力。解释现状时先读[0.4 模块导航](../../dev-notes/v0.4/modules/README.md)，运行与验收口径见[0.4 运行说明](../../dev-notes/v0.4/release.md)。

## 当前代码入口

以下路径相对本目录。

| 要修改的行为 | 主要位置 | 当前说明 |
|---|---|---|
| 服务入口、静态目录与退出码 | `server/__main__.py`、`server/file_app.py` | [运行与验收](../../dev-notes/v0.4/release.md) |
| 文件规则、读写指纹、搜索、导入 | `workspace/files.py` | [文件工作区](../../dev-notes/v0.4/modules/file-workspace.md) |
| 新作品的默认位置与位置选择 | `workspace/locations.py`、`workspace/directory_dialog.py` | [文件工作区](../../dev-notes/v0.4/modules/file-workspace.md) |
| 最近目录、会话与事件 | `workspace/state.py` | [写作 Agent 与对话](../../dev-notes/v0.4/modules/agent-and-chat.md) |
| Git 历史、提交与撤销 | `workspace/git.py` | [Git 历史](../../dev-notes/v0.4/modules/git-history.md) |
| 一轮请求的执行与记录 | `workspace/service.py` | [写作 Agent 与对话](../../dev-notes/v0.4/modules/agent-and-chat.md) |
| 文件工具与系统提示 | `workspace/agent.py`、`workspace/context.py` | [写作 Agent 与对话](../../dev-notes/v0.4/modules/agent-and-chat.md) |
| HTTP 接口（文件、Git、对话、事件） | `workspace/api.py` | [模块导航](../../dev-notes/v0.4/modules/README.md) |
| 模型档案、provider 与凭据 | `server/provider_config.py`、`infrastructure/credentials.py` | [模型连接](../../dev-notes/v0.4/modules/model-connections.md) |
| 供应商预设、协议构造与实例关闭 | `server/provider_catalog.py`、`server/provider_runtime.py` | [模型连接](../../dev-notes/v0.4/modules/model-connections.md) |
| 连接增删改、凭据提交与选择 | `server/model_connections.py` | [模型连接](../../dev-notes/v0.4/modules/model-connections.md) |
| 模型发现与连接测试 | `server/model_probe.py` | [模型连接](../../dev-notes/v0.4/modules/model-connections.md) |
| 模型设置接口 | `server/model_api.py` | [模型连接](../../dev-notes/v0.4/modules/model-connections.md) |
| 正文导入解析（遗留，仍在使用） | `interfaces/importers/manuscript_text.py` | [文件工作区](../../dev-notes/v0.4/modules/file-workspace.md) |

前端壳层在 `frontend/src/components/workspace/`，状态与客户端在 `frontend/src/{state,api}/workspace.ts`。详见[写作台界面](../../dev-notes/v0.4/modules/desk.md)。

## 包职责

| 包 | 职责 |
|---|---|
| `workspace/` | 0.4 文件工作区：目录规则、读写指纹、搜索、导入、最近目录、Git 历史、写作 Agent、对话与事件 |
| `server/` | 启动入口、文件应用装配、供应商预设与连接配置、模型设置接口、协议构造与连接测试 |
| `infrastructure/` | 凭据存储（系统凭据存储优先，回落内存） |
| `domain/` | 仍在使用的遗留文件：`manuscript/diff.py`（导入切段）、`repository/serialization.py`（内容散列） |
| `application/` | 错误基元与凭据端口 |
| `interfaces/` | 正文导入解析 |

`packaging/` 是 sidecar 打包规格，入口 `sidecar_entry.py` 仍有效。

## 历史材料

0.3 的行为与决策见[0.3 文档](../../dev-notes/v0.3/README.md)与[历史记录入口](../../dev-notes/README.md)；这些文档描述的实现已被删除，只用于理解过去的决策，不作为当前能力依据。
