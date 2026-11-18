# 开发文档导航

当前主包与前端版本为 **1.2.0**。带版本号的历史开发文档已归入 [versions/](versions/README.md)。

## 运行追踪（首版已实施，发布版本待定）

- [开发计划](runtime-trace/development-plan.md)涵盖会话与 Turn 导航、可展开的 Loop、事件甘特时间线，以及记录契约、接口和验收条件。

## 评测体系重建

- [设计](evaluation/design.md)：2026-11-18 修订，按三个问题组织——回归评测（已实现，案例 1 条）、质量对比（待实现）、使用信号（待实现）；后续步骤 R1—R7 未开始。工作台重设计见 [workbench-design.md](evaluation/workbench-design.md)（W1–W7 与四入口壳已落地）。
- [阶段 0 · 最小可归因](evaluation/observation-development-plan.md)：确定性与浏览器 e2e 已通过；在线单例待手测。
- [阶段 1 · 拆除旧评测](evaluation/stage1-retire-old-eval.md)：已删除旧 `eval/` 与旧评测测试。
- [阶段 2 · 改稿评测集与工作台](evaluation/stage2-revise-suite.md)：T0–T6 完成；运行说明见 [evaluation/README.md](../evaluation/README.md)。

## 修订学习与作品同步（首批实施，版本待定）

- [开发计划](revision-learning/development-plan.md)：作者直接改稿和按作者要求的模型改稿统一记录为修订。
- [实施记录](revision-learning/notes.md)：入口映射、修订记录与差异、分析接口与待实施项。
- 旧方案归档见 [v1.1/archive-revision-learning](versions/v1.1/archive-revision-learning/README.md)。

## 当前与近版（按版本）

| 版本 | 入口 | 状态 |
| --- | --- | --- |
| 1.4 | [开发计划](versions/v1.4/development-plan.md) · [订阅方案](versions/v1.4/subscription-plan.md) | 账号 / 隔离 / 订阅；待实施验收 |
| 1.3 | [保留记录](versions/v1.3/README.md) | 章节线索暂缓上线 |
| 1.2 | [开发入口](versions/v1.2/README.md) · [产品方案](../product-design/v1.2/README.md) | **当前版本**，已实施并收尾 |
| 1.1 | [开发入口](versions/v1.1/README.md) | 结构装配等已实施 |

1.4 分阶段记录：[P0](versions/v1.4/p0-inventory.md)、[P1](versions/v1.4/notes/P1-email-accounts.md)、[P2](versions/v1.4/notes/P2-isolation.md)、[P3](versions/v1.4/notes/P3-billing.md)、[P4](versions/v1.4/notes/P4-metering.md)、[P5](versions/v1.4/notes/P5-deployment.md)。

## 更早版本

完整索引见 [versions/README.md](versions/README.md)（v1.0.9 → v0.1）。解释现状时优先读专题文档与当前模块说明，历史版本文档只在追查对应问题时使用。

常用沿用契约（实现仍以源码为准）：

- [0.7 模块导航](versions/v0.7/modules/README.md)、[0.6 活动记录](versions/v0.6/modules/activity-log.md)
- [0.5 文件工作区](versions/v0.5/modules/file-workspace.md)、[0.4 模型连接](versions/v0.4/modules/model-connections.md)

## 当前实现入口

| 需要了解 | 入口 |
| --- | --- |
| 后端代码位置 | [后端导航](../src/hastings/README.md) |
| 前端运行与界面 | [前端说明](../frontend/README.md) |
| 评测运行 | [evaluation/README.md](../evaluation/README.md) |
| 产品设计 | [product-design](../product-design/README.md) |

`uv run python -m hastings.server` 默认启动文件工作区应用。v0.1—v1.1 等历史文档中指向旧 `eval/` 的链接已随阶段 1 拆除失效，以当时记录理解即可，不逐份改写。
