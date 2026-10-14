# Hastings V2 文档入口

> 状态：**目标架构已立项，代码尚未迁移**。
> 本目录描述 Hastings 下一版的目标形态；当前已经运行的系统仍以
> [`src/hastings/README.md`](../../src/hastings/README.md) 和旧版
> [`dev-notes/v1/DESIGN.md`](../v1/DESIGN.md) 的 as-built 标注为准。

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

| 想了解 | 先读 |
|---|---|
| 产品目标、整体架构、核心流程 | [DESIGN.md](DESIGN.md) |
| 模块依赖、公开契约和不变量 | [modules/README.md](modules/README.md) |
| 全部重建阶段、依赖和里程碑 | [plans/README.md](plans/README.md) |
| 最近完成阶段（as-built） | [phase-13-story-domain.md](phase-13-story-domain.md)（Story 领域内核；模块 as-built 见 [modules/story-domain.md](modules/story-domain.md)）。上一阶段：[phase-12-repository-kernel.md](phase-12-repository-kernel.md)（仓库内核） |
| 旧系统如何迁移 | [DESIGN.md §11](DESIGN.md#11-旧系统迁移边界) |

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
├── plans/                        # Phase 11–17 详细实施计划
├── phase-11-architecture-baseline.md  # Phase 11 构建笔记
├── phase-12-repository-kernel.md      # Phase 12 构建笔记
└── phase-13-story-domain.md           # Phase 13 构建笔记
```

