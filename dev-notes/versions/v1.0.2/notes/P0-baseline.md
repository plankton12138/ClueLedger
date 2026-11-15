# P0 基线、口径与调用清单

日期：2026-10-30。状态：**已完成**，作为后续阶段的事实基准。

## 1. 官方容量复核

DeepSeek 官方模型列表（`GET https://api.deepseek.com/models`，见 api-docs.deepseek.com）只返回
`id`/`object`/`created`/`owned_by`，**不返回 `context_window` 或 `max_output_tokens`**。
因此「官方容量」必须走内置目录（`model_catalog.py`），不是解析模型列表。

复核后补齐的目录条目（2026-10-30）：

- `deepseek-chat`：W=131072，O=8192
- `deepseek-reasoner`：W=131072，O=65536
- `deepseek-flash`：W=1048576，O=393216
- `deepseek-v4-pro`：W=1048576，O=393216

依据：api-docs.deepseek.com 的「列出模型」与「价格」页给出上述上下文长度与最大输出 token；
价格页同时给出 `deepseek-chat` 输入/输出单价，与目录无关，本版不估算金额。

## 2. 模型调用入口（真正发请求的地方）

- 写作/探针统一经 `ManagedModelProvider.stream_response`（`server/provider_runtime.py`）。
- 探针：`server/model_probe.py` 的 `run_response_probe` / `run_tool_probe`，经 `model_connections.py`
  的 `_fresh_probe_profile` + `_probe_factory` 构造 provider。
- 两轮前的预算冻结点：`workspace/model_activity.py` 的 `request_estimate`（每轮真实请求前计算并写入
  `model_request_started` 事件的 `request_estimate`），是「发出前预算检查」与「页面占比」共用的同一份数字。

## 3. 用量读取入口

- `workspace/conversation_usage.py::context_snapshot(events)`：纯函数，只从事件还原当前上下文，
  不装配、不读文件、不调模型。返回 `ContextSnapshot`（status / estimated_active_tokens /
  context_window_tokens / input_limit_tokens / source / budget …）。
- `workspace/api.py` 的 `/conversations/{id}/usage`：读取 `context_snapshot` 包成 `ContextView` JSON。
- 前端 `frontend/src/api/contextUsage.ts`（类型与 `contextShare` / `contextAmount` / `autoCompactionNote`
  等纯函数）+ `frontend/src/components/workspace/ConversationUsageBar.tsx`（展示）。

## 4. 预算实现唯一性

全仓只有一套预算口径，来源是 `server/model_limits.py::resolve_model_limits`：

- 旧的 `workspace/context_usage.py::budget_for_limit` 已删除，改为直接消费 `ModelLimits`
  （`ModelLimits.input_limit_tokens` / `budget` 属性）。
- `model_activity` / `context_boundary` / `conversation_usage` / `api` / 探针全部走 `resolve_model_limits`，
  不存在第二套「界面一套、请求一套」的计算。

## 5. 口径结论

- 模型容量 = 供应商事实（目录/元数据），项目输入上限 = 应用策略（默认 128000）。
- 有效输入上限 = min(项目上限, 模型输入上限)，模型容量未知时退化为项目上限。
- 未知窗口不再让整轮预算停摆：占比不显示，但输入上限、压缩触发、请求前检查照常执行。
- 回复预留对齐实际发送值 4096（写作用途），不显示 8K 而实际只允许 4K。
