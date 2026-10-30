# 1.0.1 开发入口：构思结构与连续写作

日期：2026-10-30。状态：**六项缺口已修复，六项复现、受影响回归、后端全量与浏览器复验通过；构思创作路由修复的受影响回归、后端全量与本地构思浏览器用例通过，在线受影响场景和作者体验待执行；运行版本 `1.0.1`。** 修复记录见[授权与采用修复](notes/2026-10-30-authorization-adoption-fixes.md)；修复前的判断保留在[阶段质量检查](notes/2026-10-30-quality-audit.md)。

2026-10-30 补充：修复真实会话中“写入构思并构建线索”只保存模块、未建立构思结构的问题。受理时记录任务目标与候选来源，结构任务不再开放模块内容写入；任务结束按实际收据判定，模块保存或文字回复不能代替结构提交。记录见[构思创作路由修复](notes/2026-10-30-story-creation-routing-fixes.md)。

同日后续复核：普通对话未携带构思信息时仍有入口缺口，不能把原批次通过视为所有入口已修复，见[路由计划](notes/2026-10-30-story-creation-routing-plan.md)。空白构思现通过独立创建表单明确目标，已完成在线保存节点、线索与连接及重载验证，见[创建流程](notes/2026-10-30-story-empty-entry.md)；其余在线场景与作者体验仍待验收。

产品方向统一记录在[1.0.1 产品方案](../../product-design/v1.0.1/README.md)：先构思真假线索、误导与结局组成的分支时间线，再展开正文；故事内分支与创作备选分别处理。

已补充[线索结构与 Agent 全流程讨论稿](../../product-design/v1.0.1/structure-and-agent-lifecycle.md)，涵盖数据职责、实际模型输入、长期连续性、对话修改、暂停恢复与故事资料移除范围。字段与策略仍属建议。

整体方向获作者认可后，继续补充[构思与正文的页面设计](../../product-design/v1.0.1/page-design.md)。页面与字段均未冻结，示意不代表已经实现。

| 文档 | 用途 |
|---|---|
| [开发计划](development-plan.md) | 范围、代码基线、模块、阶段依赖、移除清单与完成条件 |
| [数据、输入与恢复](data-and-runtime.md) | 数据对象、方案隔离、实际模型输入、提交、失效与恢复 |
| [接口与页面契约](api-and-frontend.md) | 前后端对应、事件、页面状态与全部模型入口 |
| [分模块代码示例](implementation-examples.md) | 校验、提交、上下文、暂停、来源检查与前端归并 |
| [验收与在线评测](acceptance.md) | 确定性测试、真实浏览器、在线模型与交付证据 |
| [阶段完成质量检查](notes/2026-10-30-quality-audit.md) | 修复前的六项复现、基础回归与阶段判断 |
| [授权与采用修复](notes/2026-10-30-authorization-adoption-fixes.md) | F1—F6 的统一规则、逐项修复、取舍与本轮验证 |
| [构思创作路由修复](notes/2026-10-30-story-creation-routing-fixes.md) | 任务目标、候选确认、空白方案输入、收据判定与页面结果 |
| [空白构思创建流程](notes/2026-10-30-story-empty-entry.md) | 实际创建表单、明确动作、结构保存结果与隔离在线证据 |

## 阶段记录

| 阶段 | 状态 | 记录 |
|---|---|---|
| P0 基线、格式与调用清单 | 已完成 | [基线与格式](notes/P0-baseline.md)、[模型入口](notes/P0-model-entries.md)、[资料移除映射](notes/P0-materials-removal-map.md) |
| P1 结构与一致性基础 | 已修复 F5，全量复验通过 | [结构层记录](notes/P1-structure.md)、[联合提交补充](notes/P1-joint-commit.md)、[修复记录](notes/2026-10-30-authorization-adoption-fixes.md) |
| P2 统一模型操作与恢复 | 开发验收完成；写作轮次、压缩与连接测试共用同一张操作表，可读取、暂停、继续，连接测试插话可调整测试目标 | [操作记录](notes/P2-operations.md)、[重启恢复补充](notes/P2-restart-recovery.md) |
| P3 长期连续性与实际输入 | 已修复 F6，全量复验通过 | [实际输入记录](notes/P3-context.md)、[修复记录](notes/2026-10-30-authorization-adoption-fixes.md) |
| P4 Agent 创作与修改闭环 | 已修复 F1、F2，全量复验通过；构思创作路由补充已实施 | [工具记录](notes/P4-tools.md)、[候选流程](notes/P4-candidate-workflow.md)、[修复记录](notes/2026-10-30-authorization-adoption-fixes.md)、[路由修复](notes/2026-10-30-story-creation-routing-fixes.md) |
| P5 页面接通并移除故事资料 | 已修复 F3、F4，全量复验通过 | [接口与页面记录](notes/P5-story-api.md)、[资料退出记录](notes/P5-materials-removal.md)、[修复记录](notes/2026-10-30-authorization-adoption-fixes.md) |
| P6 真实使用与在线评测 | 确定性反例已关闭；在线受影响场景与作者体验待执行，既有矩阵保留 33/36 及其批次身份 | [在线评测记录](notes/P6-online.md)、[修复记录](notes/2026-10-30-authorization-adoption-fixes.md) |
| P7 文档、版本与交付 | 版本与文档已同步；全量与浏览器通过，在线与作者体验待执行 | [开发计划](development-plan.md)、[修复记录](notes/2026-10-30-authorization-adoption-fixes.md) |

实施记录以后按阶段写入 `notes/Px-主题.md`，证据写入 `artifacts/`；不创建空记录或伪造验证结果。桌面端、旧数据迁移和 1.1 修订学习不进入本版；`mynotes/` 只读，根目录 README 仅在用户明确要求时修改。

计划编写不改变 [1.0 实际验证记录](../v1.0/notes/S8-web-delivery.md)的结论；本版主包、前端与锁文件已统一为 `1.0.1`。
