# 开发文档导航

当前运行实现为 **0.5 文件工作区**，代码包版本为 `0.5.0`。人物小传按角色分文件，接入读写、搜索、Git、模型按需读取与界面分组；对话已支持流式回复、默认折叠的思考与工具过程、Markdown 阅读与复制展开；章节显示章序章名，人物带身份标签；已有模型连接和会话管理继续使用。当前验证范围见 [0.5 运行说明](v0.5/release.md)。

## 0.5 当前入口

- [0.5 开发入口](v0.5/README.md)：当前模块、运行说明与阶段记录。
- [文件工作区与角色小传](v0.5/modules/file-workspace.md)：受管路径、接口、模型材料、Git 与角色列表。
- [对话展示与流式回复](v0.5/modules/chat-display.md)：事件、合批保存、追尾推送、折叠过程、Markdown、复制与展开、停止与中断的当前实现。
- [V05-3 上下文管理阶段记录](v0.5/notes/v05-3-context-management.md)：章序、显示名与人物标签的改动、取舍与验证。
- [模型对话框开发计划](v0.5/chat-experience-plan.md)：已交付范围与消息契约；预选项与专用可视化组件仍不在范围内。
- [上下文与作品信息开发计划](v0.5/context-management-plan.md)：待实施的章节名称、人物标签、本轮参考材料、章节索引、来源版本核对与冲突处理；会话压缩另行设计。

## 0.4 历史开发入口

| 需要了解 | 入口 |
|---|---|
| Markdown 工作目录、两块内容与直接修改流程 | [0.4 产品方案](../product-design/v0.4/README.md) |
| 新版本文档分工 | [0.4 开发入口](v0.4/README.md) |
| 已实现的目录规则、读写、搜索、导入与接口 | [0.4 模块导航](v0.4/modules/README.md) |
| 写作 Agent、对话与事件 | [写作 Agent 与对话](v0.4/modules/agent-and-chat.md) |
| 会话边界、草稿、任务归属与恢复 | [会话与草稿](v0.4/modules/conversations.md) |
| Agent 与对话的改动与验证 | [V04-3 阶段记录](v0.4/notes/v04-3-agent-and-chat.md) |
| 会话管理的改动与验证 | [V04-10 阶段记录](v0.4/notes/v04-10-conversation-management.md) |
| Git 版本闭环的改动与验证 | [V04-2 阶段记录](v0.4/notes/v04-2-git-history.md) |
| 前端接线的改动与验证 | [V04-4 阶段记录](v0.4/notes/v04-4-frontend.md) |
| 旧代码清理与运行验收说明 | [V04-5 阶段记录](v0.4/notes/v04-5-cleanup.md) |
| 默认切换与旧数据库删除 | [默认启动切换记录](v0.4/notes/v04-5-default-switch.md) |
| 模块调整、示例、实施顺序与验收 | [0.4 修改计划](v0.4/development-plan.md) |
| 供应商连接、模型设置与选择的当前行为 | [模型连接](v0.4/modules/model-connections.md) |
| 供应商连接的交付范围与验收口径 | [供应商连接开发计划](v0.4/model-connections-plan.md) |
| 已实施的会话管理计划 | [会话管理开发计划](v0.4/conversation-management-plan.md) |

`uv run python -m hastings.server` 默认启动文件工作区应用，可用 `--workspace <目录>` 预先打开作品，`--config` / `--model` 指定模型配置与启动模型。旧参数（`--v2-db`、`--stories-root` 等）已退出，传入按参数错误处理。

## 当前实现

| 需要了解 | 入口 |
|---|---|
| 0.5 模块职责、接口与限制 | [0.5 模块导航](v0.5/modules/README.md) |
| 后端代码位置 | [后端导航](../src/hastings/README.md) |
| 前端运行与界面入口 | [前端说明](../frontend/README.md) |
| 0.3 历史实现与验收材料 | [0.3 文档](v0.3/README.md) |
| 评测方法与报告 | [评测导航](../eval/README.md) |

解释现状或修改代码时，先读相关模块说明，再核对源码。前端可用、仅有后端接口、尚未实现和未验证分别记录，不以计划或历史测试结果推断交付。

## 产品范围与实施方案

- [产品设计](../product-design/README.md)：当前需求与交互约束，未来方案有独立入口。
- [0.4 修改计划](v0.4/development-plan.md)：历史文件工作区改造目标。
- [0.3 开发计划](v0.3/development-plan.md)：旧版目标，不作为当前契约。
- [0.3 阶段执行指导](v0.3/stage-execution-guide.md)：旧版实施顺序与取证方法。
- [灵感池](ideas.md)：未排期想法，不作为当前需求。

## 历史记录

| 文档集 | 内容 |
|---|---|
| [0.3 阶段交付记录](v0.3/stage-delivery-record.md) | 阶段改动、当时的检查结果及构建笔记索引 |
| [0.2 文档](v0.2/README.md) | 旧架构目标与交付材料；复用契约按当前模块导航的具体链接读取 |
| [0.1 文档](v0.1/README.md) | 早期实现、阶段计划与验收材料 |

历史资料只在追查对应问题时读取，不能覆盖当前模块说明。
