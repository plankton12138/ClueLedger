# Hastings V2 文档入口

> 状态：**Phase 11–18 已完成（2026-10-15）**。Phase 17 经用户决策「项目未上线，V1 直接弃用」完成旧写路径退出；随后删除旧工作区迁移功能，见 [旧工作区迁移退役记录](phase-17-workspace-retirement.md)。
> 本目录描述 Hastings 下一版的目标形态；当前已经运行的系统仍以
> [`src/hastings/README.md`](../../src/hastings/README.md) 和旧版
> [`dev-notes/v1/DESIGN.md`](../v1/DESIGN.md) 的 as-built 标注为准。
> V2 的领域与工作流已有独立实现；Web 服务通过 `--v2-db` 选择性启用；作品直接创建为 V2 项目，旧工作区迁移功能已删除。
> 模型接入与切换已补齐，见 [Phase 18](phase-18-model-access.md)：厂商/套餐预设、服务端当前模型与本地模型。
> 前端已替换为七页作者工作台，交付与边界见 [phase-17-frontend-workbench.md](phase-17-frontend-workbench.md)，运行见 [frontend/README.md](../../frontend/README.md)。
> Phase 19 进行中：19.1 已交付七页常驻对话栏与可控模拟完整流程，见 [phase-19-chat-workbench.md](phase-19-chat-workbench.md)。

## 1. 文档权威顺序

后续模型或开发者开始 V2 工作时，按以下顺序判断：

1. 用户在当前会话中的明确要求；
2. 仓库根 `AGENTS.md` 的开发约定；
3. 本目录的 [`DESIGN.md`](DESIGN.md)；
4. 对应模块契约；
5. 当前阶段计划；
6. 旧版设计和历史阶段笔记，仅用于理解现状与迁移原因。

遇到文档与代码不一致时：

- V2 尚未交付的部分，以阶段计划为**目标**、以现有代码为**现状**；
- 阶段交付后，必须在构建笔记中写明实际结果，并把对应模块文档更新为 as-built；
- 不得为了让代码“看起来符合文档”而跳过迁移、数据兼容或测试。

## 2. 阅读路线

下一阶段计划：[Phase 19：常驻对话侧栏与显式创作周期](plans/phase-19-chat-workbench-plan.md)（进行中，19.1 已交付）。按前端原型、状态契约、真实流式对话、上下文与任务准备、创作审阅接入、整体验收推进。

| 想了解 | 先读 |
|---|---|
| 产品目标、整体架构、核心流程 | [DESIGN.md](DESIGN.md) |
| 模块依赖、公开契约和不变量 | [modules/README.md](modules/README.md) |
| 全部重建阶段、依赖和里程碑 | [plans/README.md](plans/README.md) |
| 最新交付 | [Phase 18：模型接入与切换](phase-18-model-access.md) |
| 进行中阶段 | [phase-19-chat-workbench.md](phase-19-chat-workbench.md)（19.1 常驻对话栏 + 可控模拟；19.2–19.6 待实施） |
| 最新清理 | [旧工作区迁移退役记录](phase-17-workspace-retirement.md)（迁移入口、导入器、专用夹具退出，V2 导出保留） |
| 最近阶段（已完成） | [phase-17-migration-cleanup.md](phase-17-migration-cleanup.md)（历史交付记录，迁移功能现已退役：扫描/幂等导入/校验/不可达证明/迁移产品流/CLI+WS 切换/导出/演练与测量；V1 弃用收尾见 §2.11）；上一阶段：[phase-16-agent-workflows.md](phase-16-agent-workflows.md)（六角色、任务上下文、持久 Job 与真模型走查） |
| 旧工作区处理 | [旧工作区迁移退役记录](phase-17-workspace-retirement.md)；项目未上线，不再提供 V1 数据迁移 |

## 3. V2 核心词汇

| 词汇 | 含义 |
|---|---|
| Artifact | 可独立版本化的项目对象，例如角色、世界规则、节拍或章节正文 |
| Revision | 某个 Artifact 的不可变版本 |
| Commit | 一组原子生效的 Revision 与关系变化 |
| Branch | 指向一条 Commit 历史的可移动引用，用于试写和系统修复 |
| Canon | 作者当前认可为真的故事事实集合 |
| Narrative Plan | 系列、单本、故事弧、章节、场景和节拍组成的叙事计划 |
| Dependency Edge | 一个事实、计划或正文单元依赖另一个对象的显式关系 |
| Change Set | 一次作者意图的完整载体：原因、范围、影响、补丁、审批与提交结果 |
| Patch | 针对指定基础版本提出的结构化修改，不代表已经生效 |
| Author Model | 有证据、范围和置信度的作者写作及协作偏好 |
| Context Package | 针对单个任务、从指定仓库版本构建的有限上下文 |
| Proposal | Agent 产生的候选结果；必须经过守卫和提交管线才能写入仓库 |

## 4. 面向大模型的执行规则

后续任何模型执行阶段计划前必须：

1. 读取本页、总设计、相关模块文档和当前阶段计划；
2. 运行 `git status --short`，保护用户已有改动；
3. 只做当前阶段范围，不提前实现后续模块；
4. 核心行为先写失败测试，再写实现；
5. 所有领域写入都走当前阶段规定的统一入口；
6. 错误消息必须说明“哪里错、为什么错、怎样算对”；
7. 区分违规、合法空结果和环境故障，三者不得共用同一错误路径；
8. 每个提交只完成一个可说明的目标；
9. 阶段结束时运行计划列出的质量门，补构建笔记，并在“Agent 设计经验”中使用四透镜复盘；
10. 未满足阶段退出条件时，不得把计划状态改为完成。

## 5. 文档目录

```text
dev-notes/v2/
├── README.md                     # V2 总入口与执行规则
├── DESIGN.md                     # 产品与总体架构
├── decisions/                    # ADR-001~007 与路由开关设计（Phase 11 冻结）
├── current-system-inventory.md   # 现状系统迁移清单（as-built 快照）
├── modules/                      # 模块职责、契约和不变量
├── plans/                        # Phase 11–19 详细实施计划
├── phase-11-architecture-baseline.md  # Phase 11 构建笔记
├── phase-12-repository-kernel.md      # Phase 12 构建笔记
├── phase-13-story-domain.md           # Phase 13 构建笔记
├── phase-14-manuscript-author-model.md  # Phase 14 构建笔记
├── phase-15-change-impact.md          # Phase 15 构建笔记
├── phase-16-agent-workflows.md       # Phase 16 构建笔记
├── phase-17-migration-cleanup.md     # Phase 17 构建笔记（已完成，含 V1 弃用收尾 §2.11）
├── phase-18-model-access.md          # Phase 18 构建笔记（模型接入与切换）
└── phase-19-chat-workbench.md        # Phase 19 构建笔记（进行中，19.1 已交付）
```
