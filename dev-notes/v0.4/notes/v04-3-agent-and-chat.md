# V04-3：写作 Agent 与对话

日期：2026-10-20。范围：计划 M03、M04 与 M05 的轮次部分——文件工具、材料装配、单 Agent、对话与事件、停止与中断。前端接线不在本阶段。

## 目标

作者一句「写第一章」就能直接产生文件与 Git 记录，中间没有候选、采用或发布确认；讨论可以完全不写文件；停止与失败如实反映磁盘与历史状态。

## 实际改动

新增：

- `src/hastings/workspace/context.py`：系统提示（写作要求、目录、设定预算装入）。
- `src/hastings/workspace/agent.py`：九个工具（列表、读取、搜索、写入、局部修改、移动、删除、差异、历史）与 `build_harness`。
- `tests/workspace/test_agent.py`、`tests/workspace/test_chat.py`：工具行为与轮次行为。
- `dev-notes/v0.4/modules/agent-and-chat.md`：模块说明。

修改：

- `src/hastings/workspace/state.py`：新增 `ConversationStore`（会话登记 + JSONL 事件，自增 `seq`）。
- `src/hastings/workspace/service.py`：新增 `TurnService`（串行锁、事件翻译、停止、中断标记、请求键）。
- `src/hastings/workspace/api.py`：会话、轮次、停止与 SSE 事件端点。
- `src/hastings/server/file_app.py`：装配 provider 工厂（按当前档案取 provider，未配置时给三要素错误）。
- `src/hastings/server/__main__.py`：重新读取 `config.yaml`/环境变量，恢复 `--config` 与 `--model`；仍不打开旧数据库、不挂旧路由。

## 契约与数据影响

- 新接口：`/conversations`（列表/新建）、`/conversations/{cid}/messages`、`/conversations/{cid}/turns`（202 + `turn_id`）、`/turns/{tid}/stop`、`/events`（SSE，`after` 游标）。
- 新事件类型：`turn_started`、`tool_started`、`tool_finished`、`assistant_message`、`error`、`run_finished`。
- 新错误码：`MODEL_NOT_CONFIGURED`、`WORKSPACE_BUSY_CONFLICT`、`TURN_NOT_FOUND`、`CONVERSATION_NOT_FOUND`、`CONVERSATION_EXISTS_CONFLICT`、`EDIT_MATCH_NOT_FOUND`、`EDIT_MATCH_AMBIGUOUS`、`TOOL_INPUT_INVALID`。
- 应用状态新增 `~/.hastings/workspaces/<绑定 ID>/`：会话登记与事件日志；不含正文副本。
- 模型来源重新成为启动依赖（仅连接与模型名），`config.yaml` 损坏按环境故障退出 1。

## 实现取舍

- **没有 token 级流式**：回复按整条消息写入事件，模型结束后一次显示。理由是先把「工具调用 → 文件 → Git → 事件」的闭环做对；流式增量属于前端体验，留到 V04-4 与 SSE 一起处理。
- **事件只记定位信息**：工具参数只保留路径、查询词与分页，结果只留 240 字摘要，避免把整章正文写进日志形成第二份稿件。
- **停止依赖 provider 遵守取消信号**：`harness.cancel()` 取消的是当前模型流；真实 provider 需要响应 `signal`，本阶段只用遵守信号的假 provider 验证。
- **中断在首次访问时补记**：进程重启后不扫描全部作品，而是在打开某个作品时检查最后一条事件；避免启动时写状态目录。
- **写入前就拒绝不可写的仓库**：仓库已初始化但预暂存/冲突/分离头时，在调用模型前结束这一轮，不消耗模型调用。
- **模型能力只保留一条复用**：不迁入 `/api/providers` 与模型选择路由，配置仍走 `config.yaml`；设置界面属于 V04-4。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 工具与轮次 | `uv run pytest tests/workspace/test_agent.py tests/workspace/test_chat.py -q` | 18 项通过 |
| 文件工作区整体 | `uv run pytest tests/workspace -q` | 126 项通过 |
| 完整后端回归 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --junitxml=data/test-tmp/v04-3-full.xml` | 2101 项通过，0 失败 0 错误 0 跳过（419 秒） |
| 静态检查 | `uv run ruff check src/hastings tests/workspace` | 通过 |
| 类型检查 | `uv run mypy src/hastings` | 272 个文件无问题 |

覆盖：工具列表与读取分页、写入必须带指纹、局部替换唯一匹配、过期指纹拒绝、路径越界拒绝、移动删除、Git 工具只读；轮次侧覆盖写入后形成提交、纯讨论无提交、并发第二轮 409、停止保留文件并记「已停止」、未配置模型失败、重启补记中断、请求键幂等、SSE 回放。

## 偏差与未完成

- **真实模型未取证**：所有轮次测试都用脚本化假 provider；真实厂商的连接、超时与工具调用格式需要单独用真实凭据与临时作品验证，不能由本阶段结果替代。
- **流式增量未实现**：SSE 只推送整条消息与工具事件。
- **模型设置路由未迁入**：`/api/providers`、`/api/model-selection` 仍只在旧代码中，新应用没有设置界面。
- **事件不做跨进程广播**：SSE 每 200ms 轮询事件文件；多进程同时写同一作品不在本版范围。
- **工具并行度**：全部工具设为顺序执行，避免同一轮内两个写入互相踩指纹。
