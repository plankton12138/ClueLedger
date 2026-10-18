# dev-notes

设计文档与开发过程笔记总入口。

## 版本导航

| 文档集 | 定位 | 入口 |
|---|---|---|
| 0.3（V03-0–V03-3 已交付，V03-4 进行中；节拍类型退出随 V03-8） | 正文中心工作区、跨章修改、模型上下文与三批开发验收 | [0.3 开发文档](v0.3/README.md) |
| 0.2（原 V2） | Phase 11–19 设计与交付记录，含作者工作台与模型接入 | [0.2 文档](v2/README.md) |
| 0.1（原 V1） | 早期系统的 as-built 说明、P0–P10 历史计划、构建笔记和验收材料 | [0.1 文档](v1/README.md) |
| 灵感池 | 跨版本、未排期的产品与 Agent 想法 | [ideas.md](ideas.md) |
| 产品设计 | 产品方案、用户旅程、交互与可视化设计 | [../product-design/README.md](../product-design/README.md) |
| 真模型评测 | 走查用例、运行方法和历史报告 | [../eval/README.md](../eval/README.md) |

产品版本统一使用 `0.x`，下一版本为 **0.3（开发中）**，目标见[产品设计](../product-design/README.md)。
代码包版本统一为 `0.3.0`，不表示下一版功能已经交付；现有目录与接口版本标识继续用于兼容，历史 V1/V2 分别对应 0.1/0.2。

## 如何判断“目标”和“现状”

- 讨论0.3产品与范围时，先读[产品设计](../product-design/README.md)；理解既有架构再读 [V2 总设计](v2/DESIGN.md)，不能把0.2能力反推为0.3需求；
- 调试当前代码、解释现有工具和数据格式时，读取 [V1 DESIGN](v1/DESIGN.md) 及其中的 as-built 标注；
- 执行 V2 阶段计划时，同时读取相关 V2 模块契约和 V1 迁移对照；
- V2 某阶段完成后，在 V2 构建笔记和模块文档中更新 as-built，不能继续用目标设计代替交付证据。

## 当前入口

- 0.3 开发计划：[完整开发计划](v0.3/development-plan.md)（2026-10-17 编写）：现状差距、14个模块及示例、前后端契约、三批任务、数据升级和33项产品验收映射。
- 0.3 当前进度：**V03-0、V03-1、V03-2、V03-3 已交付，V03-4 进行中**（导入、手工修改、历史恢复均接入统一发布器；2.6 的启动入口随 V03-4；3.2 提供账本直接保存与设定候选成组，试写 → 只读预览 → 按预览哈希采用已跑通；4.1 本书约定、4.2 背景装配（装配器、只读接口、试写/对话接线）、4.3 显式章摘要（第一刀）已交付——主题维度冲突、待选择状态、按 Commit 冻结的快照、五层背景与裁剪/覆盖记录，两条路径共用同一份背景，摘要来源冻结且迟到结果不落盘；前端入口待定；起草路径与七页侧栏已脱离节拍，`narrative.beat` 类型退出随 V03-8）。质量门基线 1647 passed，交付证据、偏差与留白见 [v0.3/README](v0.3/README.md)。
- 当前产品方案：[Agent 主工作区与故事账本](../product-design/agent-first-workbench-product-design.md)（2026-10-17 评审决定已确认，待实现）：人物、线索与正文中心的创作闭环，包含首次引导、可视化账本与执行日志。
- 修改体验详案：[正文修改与版本流程](../product-design/manuscript-editing-design.md)：随时开启手工修改支线、跨章工作副本、修改完成后的统一版本与后续处理，以及对话整体调整。
- 常驻上下文：[模型上下文与正文摘要](../product-design/context-and-summary-design.md)：账本与摘要提供稳定背景，精确任务按需读原文，摘要版本与失效状态可追溯。
- 后续保留、0.3不实现：[作者模型](../product-design/future/author-model-design.md)、[全文/章节模型一致性审计](../product-design/future/consistency-audit-design.md)、[参考材料与风格语料](../product-design/future/reference-material-design.md)；RAG 同样留待后续，已有正文导入继续保留。

- 0.2 收尾阶段：[Phase 19：常驻对话侧栏与显式创作周期](v2/phase-19-chat-workbench.md)，主体已交付；[代码审查修复](v2/phase-19-review-fixes.md) 已补真实 HTTP/SSE 与浏览器闭环，真模型 A13 仍待验收。

- 最新模型接入：[Phase 18：模型接入与切换](v2/phase-18-model-access.md)，含国内 API / Coding Plan 预设与本地模型。
- 既有架构背景：[v2/DESIGN.md](v2/DESIGN.md)；0.3范围以[产品设计](../product-design/README.md)为准。
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
