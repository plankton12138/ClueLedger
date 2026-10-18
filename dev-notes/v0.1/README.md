# Hastings 0.1 文档入口

> 版本名称：0.1（原 V1）；文档目录已按产品版本命名为 `dev-notes/v0.1/`，正文中的历史标识 V1 保留。下一产品版本为 0.3，见[版本约定](../../product-design/README.md#版本约定)。

> 状态：**当前 as-built 与历史记录**。V1 代码仍在运行，但后续目标架构与迁移计划已经转入
> [V2 文档](../v0.2/README.md)。本目录不再承载新的产品设计。

## 1. 查什么 → 去哪

| 想查 | 去哪 |
|---|---|
| V1 当前架构与六阶段生命周期 | [DESIGN.md](DESIGN.md) 的 as-built 标注块 |
| 工具参数和硬约束 | [modules/writing-tools.md](modules/writing-tools.md) |
| Bible 文件存储契约 | [modules/bible-store.md](modules/bible-store.md) |
| V1 模块索引 | [modules/README.md](modules/README.md) |
| 某阶段为何这样设计 | 对应 `phase-XX-*.md` 构建笔记 |
| Agent 设计经验四透镜 | 各构建笔记的“Agent 设计经验”；样板 [phase-01-minimal-loop.md](phase-01-minimal-loop.md) §3 |
| 验收转录与演示 | [artifacts/](artifacts/) |
| 端到端测试说明 | [e2e-testing.md](e2e-testing.md)、[e2e-test-plan.md](e2e-test-plan.md) |
| 旧界面组件库改造 | [frontend-ui-foundation.md](frontend-ui-foundation.md) |

## 2. 阶段索引

| 阶段 | 主题 | 一句话交付 | 构建笔记 |
|---|---|---|---|
| P0 | 项目骨架 | uv/包结构/CI 七道质量门 | [phase-00-skeleton.md](phase-00-skeleton.md) |
| P1 | 最小闭环 | CLI 写作 Agent、工具调用与事件渲染 | [phase-01-minimal-loop.md](phase-01-minimal-loop.md) |
| P2 | 五账本 | 账本模型、状态机、时间线检查 | [phase-02-five-ledgers.md](phase-02-five-ledgers.md) |
| P3 | Web 化 | FastAPI WebSocket 桥与 React 前端 | [phase-03-web.md](phase-03-web.md) |
| P4 | 实用化 | 会话持久化、正文读回、多工作区 | [phase-04-usability.md](phase-04-usability.md) |
| P5 | 正文中心 | 人工修订、写锁与 reconcile | [phase-05-manuscript-center.md](phase-05-manuscript-center.md) |
| P6 | 基础设施 | 模型设置、事件流和工作区导出 | [phase-06-infra.md](phase-06-infra.md) |
| P7 | 界面重塑 | 账本、审计、语料和事件页面 | [phase-07-ui-pages.md](phase-07-ui-pages.md) |
| P8 | 引导生命周期 | 六阶段状态机、确认门与状态注入 | [phase-08-guided-lifecycle.md](phase-08-guided-lifecycle.md) |
| P9 | 桌面化 | 响应式界面、Electron 壳与安装包 | [phase-09-ux-desktop.md](phase-09-ux-desktop.md) |
| — | 前端基础 | Radix 与 lucide 组件库改造 | [frontend-ui-foundation.md](frontend-ui-foundation.md) |
| P10 | 工具层加固 | 弱模型硬门、上下文压缩和多会话 | [phase-10-hardening.md](phase-10-hardening.md) |

每个阶段的冻结计划文件为同名 `*-plan.md`，仅用于理解当时范围和决策，不代表 V2 路线。

## 3. 文档角色

1. `DESIGN.md` 未标 as-built 的章节是 V1 当时的设计意图；
2. 显式 as-built 块描述 P0–P10 实际交付；
3. `*-plan.md`、`artifacts/` 和阶段构建笔记属于历史记录；
4. V1 文档中的未来路线已经失效，以 V2 设计和阶段计划为准；
5. 调整 V1 文档只用于修复链接、补迁移注释或纠正现状事实，不再扩展 V1 产品设计。

## 4. V2 迁移入口

- [V2 总设计](../v0.2/DESIGN.md)
- [V2 模块契约](../v0.2/modules/README.md)
- [V2 Phase 11–17 总览](../v0.2/plans/README.md)
- [V1 → V2 迁移边界](../v0.2/DESIGN.md#11-旧系统迁移边界)
