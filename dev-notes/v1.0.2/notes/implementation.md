# 1.0.2 实施记录（P1—P4）

日期：2026-10-30。状态：**实现完成，待在线验证（§3）与作者体验验收**。

实现入口：`server/model_limits.py` 的 `resolve_model_limits`，全链路共用同一份结果。本文按阶段记录改动与落点。

## P1 容量目录与项目预算

**容量目录**（`server/model_catalog.py`）
- 新增 `deepseek-flash`、`deepseek-v4-pro`：W=1048576、O=393216。
- `catalog_capacity(catalog_id, model)` 按精确模型名查；自定义中转（`catalog_id` 非官方预设）查不到，
  不冒用原厂窗口；同连接上 `deepseek-chat-v2` 不会继承 `deepseek-chat` 的容量。

**项目预算与策略**（`server/provider_config.py`）
- 新增 `ContextPolicy`（仅 `max_input_tokens`，默认 `DEFAULT_PROJECT_INPUT_TOKENS=128000`），
  挂在 `ServerConfig.context_policy`，存读往返通过 `load_server_config` / `save_server_config`。
- `ProviderProfile` 保留 `model_metadata: dict[str, ModelCapacity]`（解析模型列表得到）与
  `model_limits: dict[str, ModelLimitOverride]`（旧手填，仅作兼容读取，解析时不再采用）。
- `book.limits(model)` 与 `snapshot_for` 改用 `resolve_model_limits(..., project_limit=config.context_policy.max_input_tokens)`，
  把项目策略上限带入容量计算。

**统一预算事实**（`server/model_limits.py`）
- `ModelLimits` 字段：`context_window_tokens` / `max_input_tokens` / `max_output_tokens`（仅模型事实）、
  `output_reserve_tokens` / `safety_tokens`（运行时策略）、`project_input_limit_tokens`（项目上限）。
- `input_limit_tokens` / `model_input_limit_tokens` / `input_limit_source` 由 `budget` 属性现算，
  不另存副本，避免「界面按 A、请求按 B」。
- `resolve_input_budget`：纯函数，算出零/负即抛 `CONTEXT_POLICY_INVALID`，**不静默回退到更大上限**
  （A07）。`source` ∈ {project, model, both}，模型容量全未知时只能是 project。
- `revision` 压容量+策略成短标识；`policy_revision` 只描述策略。两者进估算指纹，改即失效。
- 旧手填覆盖（`ModelLimitOverride`）在解析链里被忽略；`source` 不再出现 `override`
  （字段保留仅为旧配置读取不报错）。

## P2 请求与生命周期接入

- `workspace/model_activity.py::request_estimate`：每轮真实请求前计算，写入
  `model_request_started` 的 `request_estimate`，含 `context_window_tokens` / `input_limit_tokens` /
  `output_reserve_tokens` / `safety_tokens` / `capacity_source` / `limits_revision` / `budget`。
- `workspace/context_boundary.py`：压缩触发与「过大请求前拒绝」改用 `limits.input_limit()`；
  窗口未知时退化为项目上限，仍执行自动压缩与请求前检查（A04/A09）。
- `workspace/context_summary.py`：摘要分段按 `input_limit_tokens` 走，未知窗口也按项目预算分段（A10）。
- `server/model_probe.py`：探针输入预算复用 `resolve_model_limits`，与写作轮次同口径，不另立一套。
- `server/__main__.py` + `server/file_app.py`：装配时把 `ContextPolicy` 传入 `book`，模型快照在受理时冻结。

## P3 用量接口与页面

- `workspace/conversation_usage.py::ContextSnapshot`：新增 `input_limit_tokens` / `model_input_limit_tokens` /
  `input_limit_source` / `project_input_limit_tokens` / `policy_revision`；`budget` 由 `input_limit_tokens` 推导，
  窗口未知时仍采用项目预算（A04）。运行中保留冻结值，空闲时读取当前容量；不同连接或模型不复用旧输入估算。
  `context_snapshot` 纯读取，不调模型、不写会话事件、不读密钥（A14）。
- `workspace/api.py::ContextView` + `/usage` 端点：字段与 `ContextSnapshot` 对齐并序列化。
- 前端 `frontend/src/api/contextUsage.ts`：类型与 `contextAmount` / `contextShare` / `autoCompactionNote` /
  `capacitySourceLabel` 全部以 `input_limit_tokens` 为主口径；窗口未知但预算已知时仍显示预算与占比。
- 前端 `frontend/src/components/workspace/ConversationUsageBar.tsx`：明细显示模型窗口、可用输入预算、
  安全余量、容量来源；未知窗口不覆盖项目预算说明。

## P4 验证与交付

见 `artifacts/test-evidence.md`。要点：后端 213 个容量相关用例全绿，前端 328 个单元/构建全绿，
`ruff` / `mypy` 通过。`dev-notes/README.md`（根）按本版范围**不修改**；主包/前端版本号按验收 §4
在「真实后端联调 + 在线关键链路有证据 + 作者体验验收」达成后再同步到 1.0.2，本会话不改。

## 未执行项（如实列出）

- 验收 §3 在线验证：按项目约定（开发期不调在线模型）推迟到 P6，本会话无在线证据。
- 验收 §2：上下文面板的 5 项 Playwright 回归、原会话真实后端与页面显示已完成，
  见[容量刷新修复](2026-10-30-context-window-refresh.md)；其余运行生命周期与在线场景仍按验收计划执行。
- 作者「窗口与预算」显示口径体验验收：待作者执行。
