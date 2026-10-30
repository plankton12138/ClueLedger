# 1.0.1 开发入口：构思结构与连续写作

日期：2026-10-30。状态：**开发完成，待体验验收；运行版本 `1.0.1`。** 结果见[实施与验收](notes/P7-implementation-and-acceptance.md)与[验收证据索引](artifacts/acceptance-matrix.md)。历史进度检查与修复记录保留在 `notes/`，不代表当前未完成项。

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

## 阶段记录

| 阶段 | 状态 | 记录 |
|---|---|---|
| P0 基线、格式与调用清单 | 已完成 | [基线与格式](notes/P0-baseline.md)、[模型入口](notes/P0-model-entries.md)、[资料移除映射](notes/P0-materials-removal-map.md) |
| P1 结构与一致性基础 | 已完成 | [结构层记录](notes/P1-structure.md)、[联合提交补充](notes/P1-joint-commit.md) |
| P2 统一模型操作与恢复 | 开发验收完成；写作轮次、压缩与连接测试共用同一张操作表，可读取、暂停、继续，连接测试插话可调整测试目标 | [操作记录](notes/P2-operations.md)、[重启恢复补充](notes/P2-restart-recovery.md) |
| P3 长期连续性与实际输入 | 开发验收完成；依赖闭包装配、实际输入记录、独立试读入口与容量拆组已接入 | [实际输入记录](notes/P3-context.md) |
| P4 Agent 创作与修改闭环 | 开发验收完成；候选核对／有限修正、对象级授权、结构联动与 assist 采用已接入 | [工具记录](notes/P4-tools.md)、[候选流程](notes/P4-candidate-workflow.md) |
| P5 页面接通并移除故事资料 | 开发验收完成；页面与采用（exact 与 assist）已实施，旧计划／推理入口、接口和工具已退出 | [接口与页面记录](notes/P5-story-api.md)、[资料退出记录](notes/P5-materials-removal.md) |
| P6 真实使用与在线评测 | 开发验收完成；逐案最终适用证据 33/36 达成，每案至少 2/3 | [在线评测记录](notes/P6-online.md) |
| P7 文档、版本与交付 | 文档与版本已对齐；待作者体验确认 | [开发计划](development-plan.md) |

实施记录以后按阶段写入 `notes/Px-主题.md`，证据写入 `artifacts/`；不创建空记录或伪造验证结果。桌面端、旧数据迁移和 1.1 修订学习不进入本版；`mynotes/` 只读，根目录 README 仅在用户明确要求时修改。

计划编写不改变 [1.0 实际验证记录](../v1.0/notes/S8-web-delivery.md)的结论；本版主包、前端与锁文件已统一为 `1.0.1`。
