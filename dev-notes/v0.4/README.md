# Hastings 0.4 开发入口

0.4 将作品改为 Markdown 工作目录，版本历史使用 Git；Agent 直接修改文件，作者随后查看差异或撤销。内容只有按章组织的正文，以及角色小传、线索、真相三类设定。

**当前进度：文件工作区（V04-1）、Git 版本闭环（V04-2）与写作 Agent/对话（V04-3）已实现，默认启动已是 0.4，包版本 `0.4.0`。** 目录读写、真实 Git、单 Agent 直接写文件、对话与事件流可用；前端接线尚未实现，浏览器界面仍是 0.3 构建、当前不可用。真实模型调用未取证。

| 要了解 | 入口 |
|---|---|
| 产品范围、内容结构和交互 | [0.4 产品方案](../../product-design/v0.4/README.md) |
| 模块调整、代码示例、实施顺序与验收 | [修改计划](development-plan.md) |
| 已实现行为：目录规则、读写、搜索、导入、接口与启动 | [文件工作区](modules/file-workspace.md) |
| 已实现行为：仓库边界、提交、差异、撤销与记录状态 | [Git 历史](modules/git-history.md) |
| 已实现行为：材料装配、文件工具、轮次、事件与停止/中断 | [写作 Agent 与对话](modules/agent-and-chat.md) |
| Agent 与对话的改动与验证 | [V04-3 阶段记录](notes/v04-3-agent-and-chat.md) |
| Git 版本闭环的改动与验证 | [V04-2 阶段记录](notes/v04-2-git-history.md) |
| 默认切换与旧数据库删除 | [默认启动切换记录](notes/v04-5-default-switch.md) |
| 文件基础实现与验证范围 | [V04-1 阶段记录](notes/v04-1-file-workspace.md) |
| 版本开立与分支合并 | [V04-0 版本开立记录](notes/v04-0-version-baseline.md) |
| 0.3 历史实现（已无启动入口） | [0.3 模块说明](../v0.3/modules/README.md) |

后续阶段记录放在 `notes/`，验证材料放在 `artifacts/`；模块说明只写已实现的行为，不复制计划或阶段进度。
