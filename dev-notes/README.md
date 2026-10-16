# dev-notes

设计文档与开发过程笔记总入口。

## 版本导航

| 文档集 | 定位 | 入口 |
|---|---|---|
| V2 | Phase 11–18 设计与交付记录，含作者工作台与模型接入 | [v2/README.md](v2/README.md) |
| V1 | 当前运行系统的 as-built 说明、P0–P10 历史计划、构建笔记和验收材料 | [v1/README.md](v1/README.md) |
| 灵感池 | 跨版本、未排期的产品与 Agent 想法 | [ideas.md](ideas.md) |
| 真模型评测 | 走查用例、运行方法和历史报告 | [../eval/README.md](../eval/README.md) |

## 如何判断“目标”和“现状”

- 讨论后续产品与架构设计时，读取 [V2 总设计](v2/DESIGN.md)；
- 调试当前代码、解释现有工具和数据格式时，读取 [V1 DESIGN](v1/DESIGN.md) 及其中的 as-built 标注；
- 执行 V2 阶段计划时，同时读取相关 V2 模块契约和 V1 迁移对照；
- V2 某阶段完成后，在 V2 构建笔记和模块文档中更新 as-built，不能继续用目标设计代替交付证据。

## 当前入口

- 当前阶段：[Phase 19：常驻对话侧栏与显式创作周期](v2/phase-19-chat-workbench.md)，主体已交付；[代码审查修复](v2/phase-19-review-fixes.md) 已补真实 HTTP/SSE 与浏览器闭环，真模型 A13 仍待验收。

- 最新模型接入：[Phase 18：模型接入与切换](v2/phase-18-model-access.md)，含国内 API / Coding Plan 预设与本地模型。
- 当前目标设计：[v2/DESIGN.md](v2/DESIGN.md)
- 当前前端交付（2026-10-15）：[V2 七页作者工作台](v2/phase-17-frontend-workbench.md)，运行与验证见 [frontend/README.md](../frontend/README.md)
- 当前旧工作区处置：[迁移功能退役](v2/phase-17-workspace-retirement.md)；不再维护 V1 → V2 数据迁移。
- 历史迁移与旧链路退出记录：[v2/phase-17-migration-cleanup.md](v2/phase-17-migration-cleanup.md)；原计划见 [phase-17-migration-cleanup-plan.md](v2/plans/phase-17-migration-cleanup-plan.md)
- 上一阶段 as-built：Agent 编排与任务上下文 [v2/modules/orchestration-context.md](v2/modules/orchestration-context.md) + 构建笔记 [v2/phase-16-agent-workflows.md](v2/phase-16-agent-workflows.md)
- Phase 15 as-built：影响分析与系统修复 [v2/modules/change-impact.md](v2/modules/change-impact.md) + 构建笔记 [v2/phase-15-change-impact.md](v2/phase-15-change-impact.md)
- Phase 14 as-built：正文版本与作者模型 [v2/modules/manuscript-author-model.md](v2/modules/manuscript-author-model.md) + 构建笔记 [v2/phase-14-manuscript-author-model.md](v2/phase-14-manuscript-author-model.md)
- Phase 13 as-built：Story 领域内核 [v2/modules/story-domain.md](v2/modules/story-domain.md) + 构建笔记 [v2/phase-13-story-domain.md](v2/phase-13-story-domain.md)
- 仓库内核 as-built：[v2/modules/repository-kernel.md](v2/modules/repository-kernel.md) + 构建笔记 [v2/phase-12-repository-kernel.md](v2/phase-12-repository-kernel.md)
- 当前代码导航：[../src/hastings/README.md](../src/hastings/README.md)
- 当前 V1 工具契约：[v1/modules/writing-tools.md](v1/modules/writing-tools.md)
