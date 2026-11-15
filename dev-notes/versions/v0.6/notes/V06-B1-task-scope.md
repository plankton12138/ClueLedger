# V06-B1 任务范围基础

> 实施日期：2026-10-22。计划见[上下文与文件操作开发计划](../context-and-file-operations-plan.md)。
> 本阶段只做「范围登记与操作检查」，自然语言语义验收与事件展示在 B2。

## 目标

普通任务起初没有作品写权限；模型定位目标后登记本轮任务理解、作者授权引文、允许的操作与
禁止项，写工具才可能通过。拒绝时不留文件、Git 或目录副作用。

## 实际改动

| 位置 | 改动 |
|---|---|
| `workspace/task_scope.py`（新增） | `Mutation`、`WriteScope`、`ScopeDraft`、`TaskScope`、`ScopeController`；结构与引文校验 |
| `workspace/agent.py` | `RunContext.scope` 与 `author_messages`；新增 `set_task_scope` / `extend_task_scope` 工具；四个写工具统一走 `before_mutation` 并传入变更对象 |
| `workspace/service.py` | 执行一轮时把 `turn_started` 的作者原文与事件序号交给 `RunContext`，助手回复与作品内容不进入授权来源 |
| `workspace/context.py` | 系统提示新增「修改范围」：什么时候登记、固定范围与可发现范围的区别、删除不默认包含在修改里 |

## 接口与数据影响

- 新工具 `set_task_scope`：`mode`、`summary`、`authorization`、`authorization_seq`、
  `carried_from_seq`、`grants`、`denied_paths`、`required_references`、`extendable`、
  `extend_actions`、`note`；返回范围版本与登记内容。
- 新工具 `extend_task_scope`：`version`、`grants`、`evidence`；返回新版本。
- 新增错误：`TASK_SCOPE_REQUIRED_CONFLICT`、`TASK_SCOPE_DENIED_CONFLICT`、
  `TASK_SCOPE_EVIDENCE_INVALID`、`TASK_SCOPE_STALE_CONFLICT`。
- 行为变化：没有登记范围的 `write_file` / `edit_file` / `move_file` / `delete_file` 一律拒绝。
  旧测试改为先登记范围再写入（`test_agent.py`、`test_characters.py`、`test_chat.py`、
  `test_materials.py`）。

## 取舍

- **范围用确切路径，不支持通配符或目录前缀**：范围检查能证明「没有超出已记录的操作」，
  用前缀会把这个证明变成猜测。
- **授权只来自作者消息**：`turn_started` 是唯一来源；助手计划、作品内容与旧范围都不能单独
  构成授权。`extend` 还要求给出实际读到的关联证据，但证据存在不等于语义正确。
- **`extend` 只追加、不替换**：原禁止项与操作类别限制继续有效；旧版本号不能覆盖新版本。
- **删除与移动单独授权**：`update` 不隐含 `delete`，避免「修改」被扩大成删除。
- **预检顺序**：范围 → 目标现状 → 读取前提 → 参考完整性 → 依赖版本 → 准备 Git → 复核。
  前五步失败都不会创建文件或提交基线。

## 验证证据

`uv run pytest tests/workspace/test_task_scope.py -q`：19 项通过。覆盖允许/拒绝判定、两端移动检查、
只读范围、非受管与通配路径、目标字段误用、授权引文来源、无范围拒绝且无文件与 Git 副作用、
越界与禁止路径、版本推进与旧版本追加、固定范围拒绝扩展、追加后保留限制、证据必填、参数结构错误。

## 偏差与未完成项

- 事件展示（`task_scope_set` / `task_scope_extended`）尚未写入会话事件；界面看不到范围详情。
- 自然语言语义（「只改」「同步」是否被正确解释）没有验收，属于 B2。
- 范围缩小没有单独接口：目前只能重新登记一份新版本。
- 包版本仍为 `0.5.0`。
