# dev-notes

设计文档与开发过程笔记（沿用上游 Tau 的 ADR/构建日志习惯）。

## 查什么 → 去哪（AI 检索路由表）

| 想查 | 去哪 |
|---|---|
| 当前架构 / as-built 现状 | [DESIGN.md](DESIGN.md) §2.3、§3 开头的 **as-built 标注块**（含架构图与生命周期状态机图） |
| 工具契约（参数/硬约束） | [modules/writing-tools.md](modules/writing-tools.md) |
| 账本存储契约 | [modules/bible-store.md](modules/bible-store.md) |
| 其他模块契约索引 | [modules/README.md](modules/README.md) |
| 某阶段为什么这样设计 | 对应 `phase-XX.md` 的「关键决策」节 |
| Agent 设计经验（四透镜） | 各 `phase-XX.md` 的「Agent 设计经验」节；样板：[phase-01-minimal-loop.md](phase-01-minimal-loop.md) §3 |
| 已知留白 / 待办 | 各 `phase-XX.md` 的「留白」节；弱模型压测发现见 [../eval/runs/2026-10-11-local/report-local-qwen3.md](../eval/runs/2026-10-11-local/report-local-qwen3.md) |
| 测评 / 真模型走查 | [../eval/README.md](../eval/README.md)（唯一入口） |
| 阶段验收产物（转录/演示） | [artifacts/](artifacts/) |
| 当前活跃计划 | [phase-10-hardening-plan.md](phase-10-hardening-plan.md)（工具层加固，已立项待执行） |

## 阶段索引

| 阶段 | 主题 | 一句话交付 | 笔记 |
|---|---|---|---|
| P0 | 项目骨架 | uv/包结构/CI 七道质量门 | [phase-00-skeleton.md](phase-00-skeleton.md) |
| P1 | 最小闭环 | CLI 写作 agent（build_harness/PlainRenderer/事件流渲染） | [phase-01-minimal-loop.md](phase-01-minimal-loop.md) |
| P2 | 五账本 | 账本模型+状态机+工具层守门+timeline_check | [phase-02-five-ledgers.md](phase-02-five-ledgers.md) |
| P3 | Web 化 | FastAPI WebSocket 桥 + React 前端 | [phase-03-web.md](phase-03-web.md) |
| P4 | 实用化 | 会话持久化+正文读回+多工作区 | [phase-04-usability.md](phase-04-usability.md) |
| P5 | 正文中心 | 创作台反转（手动修订→对账同步 reconcile） | [phase-05-manuscript-center.md](phase-05-manuscript-center.md) |
| P6 | 基础设施 | 设置页可换 LLM+前端可用性+事件流持久化/查询 | [phase-06-infra.md](phase-06-infra.md) |
| P7 | 界面重塑 | 五原型对齐（账本页/审计中心/语料库/事件流页/顶栏） | [phase-07-ui-pages.md](phase-07-ui-pages.md) |
| P8 | 引导式生命周期 | 六阶段状态机+确认门+intake 协议+状态注入 | [phase-08-guided-lifecycle.md](phase-08-guided-lifecycle.md) |
| P9 | 桌面化 | 交互修补+响应式抽屉+Electron 壳与安装包 | [phase-09-ux-desktop.md](phase-09-ux-desktop.md) |
| — | 前端组件库改造 | Radix+lucide 迁移（P9 后的前端基础阶段） | [frontend-ui-foundation.md](frontend-ui-foundation.md) |
| P10 | 工具层加固 | 弱模型压测驱动的硬门收口（**计划中**） | [phase-10-hardening-plan.md](phase-10-hardening-plan.md) |

## 文档角色三分法（防误读）

1. **设计态**：DESIGN.md 未标注 as-built 的章节——立项时的设计意图，与实现可能存在演进差异，**有对比价值，保留不改写**；
2. **as-built**：显式标注「as-built」的块——实际交付现状（截至 P9，P10 计划中）；
3. **历史记录**：`phase-XX-*-plan.md`（立项时冻结的计划）与 `artifacts/`（验收转录）——只读，不再更新。

## 约定

- 命名：`phase-<两位序号>-<短横线主题>.md`；每阶段计划先行（`*-plan.md`），收口后写构建笔记。
- 构建笔记固定结构：交付内容 / 关键决策 / **Agent 设计经验（四透镜，必设）** / 评审记录 / 踩坑 / 留白；顶部带「关键词：」定位行。
- 每篇笔记顶部「关键词：」行供 AI 检索定位；正文只留决策与经验，流水（提交罗列/验收对勾/长转录）压缩或指向 artifacts/。
