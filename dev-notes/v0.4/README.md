# Hastings 0.4 开发入口

0.4 将作品改为 Markdown 工作目录，版本历史使用 Git；Agent 直接修改文件，作者随后查看差异或撤销。内容只有按章组织的正文，以及角色小传、线索、真相三类设定。

**当前进度：V04-1～V04-8 已实施，默认启动已是 0.4，包版本 `0.4.0`。** 浏览器经真实后端完成打开目录、阅读、搜索、对话过程、差异、历史与撤销；真实模型已在接口层取证（一句请求 + 一轮写作到章节文件与 Git 提交），界面渲染仍用脚本化假 provider 验证。0.3 代码与测试已清理。

| 要了解 | 入口 |
|---|---|
| 产品范围、内容结构和交互 | [0.4 产品方案](../../product-design/v0.4/README.md) |
| 模块调整、代码示例、实施顺序与验收 | [修改计划](development-plan.md) |
| 已实现行为：目录规则、读写、搜索、导入、接口与启动 | [文件工作区](modules/file-workspace.md) |
| 已实现行为：仓库边界、提交、差异、撤销与记录状态 | [Git 历史](modules/git-history.md) |
| 已实现行为：材料装配、文件工具、轮次、事件与停止/中断 | [写作 Agent 与对话](modules/agent-and-chat.md) |
| 已实现行为：界面布局、稿纸、对话栏与改动历史 | [写作台界面](modules/desk.md) |
| Agent 与对话的改动与验证 | [V04-3 阶段记录](notes/v04-3-agent-and-chat.md) |
| Git 版本闭环的改动与验证 | [V04-2 阶段记录](notes/v04-2-git-history.md) |
| 默认切换与旧数据库删除 | [默认启动切换记录](notes/v04-5-default-switch.md) |
| 文件基础实现与验证范围 | [V04-1 阶段记录](notes/v04-1-file-workspace.md) |
| 版本开立与分支合并 | [V04-0 版本开立记录](notes/v04-0-version-baseline.md) |
| 运行方式、模型来源、已验收与缺口 | [运行与验收说明](release.md) |
| 模型来源单一化与失败可见 | [V04-6 阶段记录](notes/v04-6-model-source.md) |
| 新作品默认存放在文档目录 | [V04-7 阶段记录](notes/v04-7-default-location.md) |
| 位置改由系统文件夹对话框选择 | [V04-8 阶段记录](notes/v04-8-directory-dialog.md) |
| 真实模型验证证据 | [真实模型验证](artifacts/real-model-2026-10-20.md) |
| 旧代码清理的改动与验证 | [V04-5 阶段记录](notes/v04-5-cleanup.md) |
| 0.3 历史实现（代码已删除） | [0.3 文档](../v0.3/README.md) |

后续阶段记录放在 `notes/`，验证材料放在 `artifacts/`；模块说明只写已实现的行为，不复制计划或阶段进度。
