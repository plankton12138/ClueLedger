# 1.0.1 开发入口：构思结构与连续写作

日期：2026-10-29。状态：**P0—P5 已完成（P4 剩候选正文修正循环，P5 的 assist 采用明确拒绝待接）；运行版本仍为 `1.0.0`，按计划在 P7 统一升级。**

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
| P1 结构与一致性基础 | 结构层已实施 | [结构层记录](notes/P1-structure.md) |
| P2 统一模型操作与恢复 | 写作轮次已完成 | [操作记录](notes/P2-operations.md) |
| P3 长期连续性与实际输入 | 装配已接入 | [实际输入记录](notes/P3-context.md) |
| P4 Agent 创作与修改闭环 | 工具与核对已实施 | [工具记录](notes/P4-tools.md) |
| P5 页面接通并移除故事资料 | 已完成（assist 采用待接） | [接口与页面记录](notes/P5-story-api.md)、[资料退出记录](notes/P5-materials-removal.md) |
| P6 真实使用与在线评测 | 在线浏览器链路已接线并首轮运行；语义评测待跑 | [在线评测记录](notes/P6-online.md) |

实施记录以后按阶段写入 `notes/Px-主题.md`，证据写入 `artifacts/`；不创建空记录或伪造验证结果。桌面端、旧数据迁移和 1.1 修订学习不进入本版；`mynotes/` 只读，根目录 README 仅在用户明确要求时修改。

计划编写不改变 [1.0 实际验证记录](../v1.0/notes/S8-web-delivery.md)的结论；运行版本在本版实现与验证完成后更新。
