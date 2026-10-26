# 开发文档导航

当前运行实现为 **0.8**，代码包版本为 `0.8.0`。本版增加应用级模块库与作品装配、统一章节与
事件索引、按章贡献合并、有界检索与任务卡；0.7 的故事资料页、0.6 的材料记录与修改范围、
只读检查与状态层继续沿用。灵感页面、对话参考与 agent 工具已接入。本次实际
运行与未覆盖范围见 [0.8 运行说明](v0.8/release.md)。

## 1.0 计划入口

- [1.0 开发入口](v1.0/README.md)：作者指定目标版本 1.0.0；正文手工修改、版本差异、自动分析、确认偏好与后续写作，计划已制定、尚未实施。
- [详细开发计划](v1.0/development-plan.md)：G0–R1 阶段、当前代码基线、依赖、文件职责与发布。
- [数据与恢复](v1.0/data-and-recovery.md)、[接口与前端](v1.0/api-and-frontend.md)：草稿、幂等、保存恢复、偏好决策、忘记与请求级使用。
- [分模块实现示例](v1.0/implementation-examples.md)、[验收与在线评测](v1.0/acceptance.md)：可审查代码和待执行门槛。
- [1.0 产品方案](../product-design/v1.0/README.md)：作者流程、学习边界、范围与界面；不改变当前运行版本。

## 0.9 计划入口

- [0.9 开发入口](v0.9/README.md)：目标版本 `0.9.0`，计划已制定、尚未实施；先完成上下文占用与累计用量，再同版交付跨轮与轮内压缩。
- [详细开发计划](v0.9/development-plan.md)：统计口径、模型容量、请求投影、压缩记录、前后端接口及 U0–R1 阶段任务。
- [分模块实现示例](v0.9/implementation-examples.md)、[验收与在线评测](v0.9/acceptance.md)：设计示例与待执行矩阵，不代表已交付。
- [0.9 产品方案](../product-design/v0.9/README.md)：两个轻量用量入口、整理交互、未知数据和任务边界。

## 0.8 当前入口

- [0.8 开发入口](v0.8/README.md)：本版方案、阶段记录与交付状态。
- [0.8 前端体验与结构修复计划](v0.8/frontend-experience-plan.md)：H1–H6 已实施并完成确定性回归，在线长篇与桌面打包另列未执行；页面与操作层级、视觉规则、编辑可靠性、阶段任务与验收，版本保持 0.8.0。
- [0.8 灵感全流程改造计划](v0.8/ideas-experience-plan.md)：I0–I4 已实施（[基线](v0.8/notes/I0-baseline.md)、[正文优先](v0.8/notes/I1-body-first.md)、[悬浮面板](v0.8/notes/I2-surface.md)、[固定版本引用](v0.8/notes/I3-references.md)、[只读展开](v0.8/notes/I4-expand.md)），I5–I6 待实施；记录、面板、修改删除、引用与选段、一次点击只读展开已完成，实际使用关联仍待做；附[分模块实现示例](v0.8/ideas-experience-examples.md)，版本保持 0.8.0。
- [0.8 模块导航](v0.8/modules/README.md)：当前职责、真实行为与未实现项。
- [全局模块库与作品装配](v0.8/modules/library-and-assembly.md)：七种类型、不可变版本、精确绑定、装卸与派生。
- [统一章节与事件索引](v0.8/modules/story-index.md)：双视图、事件观察、按章贡献与旧记录兼容。
- [检索与上下文](v0.8/modules/retrieval-and-context.md)：L0／L1／L2、装配预算、排除记录与任务卡。
- [0.8 运行与验证](v0.8/release.md)：启动方式、行为变化、本次验证与未覆盖项。
- [详细实施计划](v0.8/development-plan.md)：阶段依赖、任务清单与交付门槛。
- [验收与评测](v0.8/acceptance.md)：确定性、浏览器、在线语义及成本对照；整份矩阵尚未执行。
- [数据与存储](v0.8/data-and-storage.md)、[索引与上下文设计](v0.8/context-and-index.md)、[接口与前端](v0.8/api-and-frontend.md)：实施前的契约与代码示例。
- [0.8 产品方案](../product-design/v0.8/README.md)：作者旅程、模块范围、时间线双视图与操作边界。

## 0.7 沿用入口

- [0.7 开发入口](v0.7/README.md)：本版方案与阶段记录导航。
- [故事资料](v0.7/modules/story-materials.md)：五页、六域、作者视图、覆盖与失效、任务与工具权限。
- [0.7 模块导航](v0.7/modules/README.md)：本版职责与沿用的旧版契约。
- [0.7 运行与验证](v0.7/release.md)：启动方式、行为变化、本次验证与未验证项。
- [故事资料开发计划](v0.7/story-materials-plan.md)：原计划与验收口径；实际交付以模块说明为准。

## 0.7 阶段记录

- [A 页面结构](v0.7/notes/A-pages.md)：主导航、hash 位置、资料容器与旧抽屉撤销。
- [B 时间线完整流程](v0.7/notes/B-timeline.md)：作者视图、覆盖、任务受理、域权限与完成度。
- [C 章节概览与人物](v0.7/notes/C-chapters-characters.md)：索引任务接入、身份与状态两个域。
- [D 线索与名词表](v0.7/notes/D-clues-glossary.md)：跨章线索、稳定 ID、冲突与空结果。
- [E 迁移与交付](v0.7/notes/E-delivery.md)：入口清理、版本升级、回归与文档。

## 0.6 模块入口（沿用）

- [活动记录](v0.6/modules/activity-log.md)：事件流、跨会话筛选、模型与操作详情、输入快照、导出和文件版本关联；开发与验证见 [V06-F](v0.6/notes/V06-F-activity-log.md)。
- [0.6 开发入口](v0.6/README.md)：本版方案与阶段记录导航。
- [状态层与装配分层计划](v0.6/state-and-assembly-plan.md)：V06-E 的设计与分阶段记录；当前行为与验收边界见模块说明和运行说明，作者侧状态编辑与真实作品验收未实施。
- [V06-E 审查修复](v0.6/notes/V06-E-review-fixes.md)：状态依据、任务时点、检查接线与回归证据。
- [0.6 模块导航](v0.6/modules/README.md)：材料与参考、修改范围、章节索引、只读检查与状态层的当前实现。
- [0.6 运行与验证](v0.6/release.md)：启动方式、行为变化与本次验证范围（含未验证项）。

## 0.5 历史入口

- [0.5 开发入口](v0.5/README.md)：上一版模块、运行说明与阶段记录。
- [文件工作区与角色小传](v0.5/modules/file-workspace.md)：受管路径、接口、模型材料、Git 与角色列表。
- [对话展示与流式回复](v0.5/modules/chat-display.md)：事件、合批保存、追尾推送、折叠过程、Markdown、复制与展开、停止与中断的当前实现。
- [V05-3 上下文管理阶段记录](v0.5/notes/v05-3-context-management.md)：章序、显示名与人物标签的改动、取舍与验证。
- [模型对话框开发计划](v0.5/chat-experience-plan.md)：已交付范围与消息契约；预选项与专用可视化组件仍不在范围内。
- [0.5 上下文与作品信息计划](v0.5/context-management-plan.md)：章序与人物标签的原计划；未实施部分已由下一版本计划承接。

## 0.6 阶段记录

- [0.6 开发入口](v0.6/README.md)：下一版本方案与阶段记录导航。
- [上下文与文件操作开发计划](v0.6/context-and-file-operations-plan.md)：可选文件参考与自然语言定位、材料来源和版本、本轮修改范围、有原文依据的写后检查；包含模块示例、前后端契约与验收。
- [V06-A1 读取与材料基础](v0.6/notes/V06-A1-materials.md)：材料来源、读取区间、有界工具与写前来源检查（已实施）。
- [V06-A2 可选参考闭环](v0.6/notes/V06-A2-references.md)：受理快照、显式参考、自然语言登记、材料事件与界面入口（已实施）。
- [V06-A3 章节索引](v0.6/notes/V06-A3-chapter-index.md)：索引生成、发布校验、状态与面板（已实施）。
- [V06-B1 任务范围基础](v0.6/notes/V06-B1-task-scope.md)：本轮修改范围、授权引文与操作检查（已实施）。
- [V06-B2 范围与自然语言闭环](v0.6/notes/V06-B2-scope-events.md)：范围事件、界面详情与承接规则（已实施）。
- [V06-C1 只读检查](v0.6/notes/V06-C1-review.md)：检查报告、只读工具集、显式检查与改完检查（已实施）。
- [V06-C2 整体交付](v0.6/notes/V06-C2-delivery.md)：版本升级、模块与运行说明、全量验证与未完成项（已实施）。
- [V06-D1 约束加固与界面收口](v0.6/notes/V06-D1-constraints.md)：重复登记只收窄、必读不降级、证据必须落在已读区间、检查回合预算（已实施）。
- [V06-D2 在线模型验收](v0.6/notes/V06-D2-acceptance.md)：在线任务驱动器、浏览器可见结果、失败修正与验收证据。

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
| 上下文评测的运行、判定与对照报告 | [评测导航](../eval/README.md) |

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
