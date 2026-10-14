# Phase 3 开发计划：Web 化（FastAPI WebSocket 桥 + React 前端）

> 对应 [DESIGN.md](DESIGN.md) §8 路线图 **P3 Web 化** 行。
> 状态：**✅ 已收口**，构建笔记见 [phase-03-web.md](phase-03-web.md)。

## 0. 已核实的依赖事实（计划的输入）

开工前对 P2 交付代码、tau 内核与环境进行的核查结论：

| 结论 | 证据 | 对 P3 的影响 |
|---|---|---|
| `harness.subscribe(listener)` 返回退订函数，listener 支持 async；事件同时 fan-out 给所有订阅者 | [harness.py](../../src/tau_agent/harness.py#L108-L115) | 每连接订阅/退订零内核改动；多标签页天然同步（D4） |
| harness 运行中再 `prompt()` 抛 `RuntimeError`，错误消息本身已指引"用 steer() 或 follow_up()" | [harness.py](../../src/tau_agent/harness.py#L213-L217) | 并发 prompt 防护免费获得，转成 error 帧即可（D3） |
| 全部 AgentEvent、消息类型与 `AgentToolResult` 均为 pydantic `WireModel` | [events.py](../../src/tau_agent/events.py)、[tools.py](../../src/tau_agent/tools.py#L21-L27) | 序列化统一 `model_dump(mode="json", by_alias=True)`，比 rpc.py 的 `_jsonable` 少 dataclass 分支 |
| `MessageUpdateEvent.assistant_message_event` 带 `serialization_alias="assistantMessageEvent"` | [events.py](../../src/tau_agent/events.py#L39-L44) | `by_alias` 必须有测试钉住，否则前端流式渲染静默失效 |
| rpc.py 命令语义：prompt/steer/follow_up/abort/get_state/get_messages + `{type:"response", success, id?}` / error 帧；运行中 prompt 拒绝并要求 streamingBehavior | [rpc.py](../../src/tau_coding/rpc.py#L189-L244) | D3 协议照抄此子集；**只抄模式不 import**（零 import tau_coding 红线），辅助函数重写进 hastings |
| adapter.py 演示了事件→UI 状态映射：assistant buffer、工具卡片、错误卡片、未知事件不静默 | [adapter.py](../../src/tau_coding/tui/adapter.py#L27-L153) | TS reducer 的蓝本；P3 只消费 `AgentEvent` 子集，无 session 层事件（agent_settled 等） |
| PlainRenderer 的 `unhandled_event_types` 惯例：未识别事件显式暴露不静默 | [cli.py](../../src/hastings/cli.py#L56-L64) | TS reducer 照搬该惯例（P2 制度化的延续） |
| `timeline_check` 的 `details` 已含结构化 conflicts（rule/day/persons/entries/message） | [timeline_tools.py](../../src/hastings/tools/timeline_tools.py#L91-L97) | 结构化数据随事件帧自然到达前端，调试视图直接可用，P3 不再发明账本推送契约 |
| `/bible` 直读入口的 try/except 防崩模式 | [cli.py](../../src/hastings/cli.py#L158-L167) | HTTP 层沿用：损坏 YAML → 可读错误响应，不 500 裸堆栈 |
| httpx 已在运行时依赖 | [pyproject.toml](../../pyproject.toml#L11) | FastAPI TestClient 依赖 httpx，测试基建零额外安装 |
| FakeProvider 剧本 + 事件序列断言范式已成熟（P1 建立、P2 扩展） | [test_writing_loop.py](../../tests/test_writing_loop.py) | WS 集成测试复用同一剧本来源，断言对象从"事件对象"换成"WS 帧" |
| `server/`、`rag/`、`eval/` 仅有空 `__init__.py`；frontend/ 目录不存在 | Glob 核查 | 服务端与前端全部新建 |
| Node v26.0.0 / npm 11.12.1 在场 | 环境核查 | Vite 5 + React 18 + TS 5 工具链无障碍 |

## 1. 目标与验收标准

**目标**：把 P1/P2 的终端 REPL 升级为浏览器体验——FastAPI 做 WebSocket 事件桥（协议照抄 tau_coding/rpc.py 的命令语义），React SPA 做事件的另一个消费者（与 TUI 完全同构：前端不跑任何 Agent 逻辑）。**验证 Tau 架构红利的直接兑现：换前端零内核改动。**

**验收标准**（DESIGN.md §8 原文）：

> 浏览器完成 P1 同样流程；事件流逐条渲染

展开为可检查的七条：

1. WS 集成测试（FakeProvider）：命令帧 → 事件帧序列 → response 帧全链路断言，事件帧与 harness 直发事件在 JSON 层保真（含 `assistantMessageEvent` 别名）；
2. `get_messages` 快照：浏览器刷新后对话流完整恢复（服务端进程内历史 + 重连拉取）；
3. 运行中 prompt 被拒且 error 帧含指引（steer/followUp）；steer / follow_up / abort 在 FakeProvider 剧本下行为正确；
4. REST 只读面：`/api/bible/{type}`、`/api/chapters` 返回结构化数据；损坏 YAML 返回可读错误而非裸 500；
5. 前端：流式 delta 渲染、工具卡片折叠（render_call 单行摘要）、错误卡片；Bible 面板五账本 Tab、伏笔未回收红点；调试视图滚动原始事件帧、未知事件类型显式高亮；
6. 真实端点人工验收：浏览器完成"设定 2 个角色 → 写 1 章"（P1 同流程），截图/transcript 存 `dev-notes/v1/artifacts/`；
7. 红线：`tau_ai`/`tau_agent` 与上游 diff 为空、零 import tau_coding；Python 四门 + 前端三门全绿。

**非目标（本阶段明确不做）**：

- 不做章节阅读器与审计报告视图（P6/P5，DESIGN §8 各行已指派）；章节 REST 端点先行为 curl/后续阶段数据源；
- 不做 RAG、语料上传与管理页（P4）；
- 不做 SSE 降级（DESIGN §5.1 括号里的备位方案，P3 只交付 WebSocket）；
- 不做多故事管理 UI / 多工作区并发（D2：单进程单工作区）；
- 不做鉴权与 CORS 配置（localhost + Vite proxy 开发形态）；
- 不做跨进程会话持久化（JSONL session storage，D5 推迟）；
- 不做 timeline conflicts 的面板级可视化（结构化数据已透传到调试视图，面板可视化归 P5 审计视图）。

## 2. 设计决策（本阶段新增，需评审确认）

### D1 依赖：后端仅加 fastapi + uvicorn，前端独立 package.json

- 后端新增 `fastapi`、`uvicorn`（不带 `[standard]` extras——uvloop/watchfiles 在 Windows 开发机上用不到，依赖最小化）；
- 前端依赖（react/vite/typescript/zustand/tailwind/vitest）全部隔离在 `frontend/package.json`，不进 pyproject；
- 这是 P0 以来首次新增后端依赖，属计划内扩张而非漂移：DESIGN §6 选型表早已锁定 FastAPI。

### D2 进程模型：单进程单工作区，`python -m hastings.server` 独立入口

- 服务启动时绑定一个 `StoryWorkspace` + 一个 `AgentHarness`（slug 作启动参数），与 CLI 一一对应；多故事切换 = 重启服务；
- 新入口 `src/hastings/server/__main__.py`：`uv run python -m hastings.server <slug> [--port 8000]`——**`__main__.py` 的 REPL 入口原样保留**，零 argparse 重构、零存量命令破坏；
- 理由：验收标准就是"浏览器完成 P1 同样流程"，会话注册表/生命周期管理对 P3 是纯开销（学习带宽应花在事件桥与 reducer 上）。

### D3 WS 协议：照抄 rpc.py 命令语义的最小子集

帧方向与结构：

```text
客户端 → 服务端（命令帧）：{"id": 1, "type": "prompt", "message": "..."}
                          {"id": 2, "type": "steer", "message": "..."}
                          {"id": 3, "type": "follow_up", "message": "..."}
                          {"id": 4, "type": "abort"}
                          {"id": 5, "type": "get_state"}
                          {"id": 6, "type": "get_messages"}
服务端 → 客户端（事件帧）：AgentEvent 原样 JSON（type 字段即 tau 事件类型）
服务端 → 客户端（响应帧）：{"type": "response", "command": "prompt", "success": true, "id": 1, "data": {...}}
                          {"type": "response", "command": "prompt", "success": false, "id": 1, "error": "..."}
```

- 命令集六个，不做 model/thinking/compact/session 树等 coding 特化命令（tau_coding 那些命令依赖 CodingSession 编排层，hastings 没有也不需要有）；
- 事件序列化：`model_dump(mode="json", by_alias=True)`（§0 已核实全部为 WireModel）；
- 运行中收到 prompt：拒绝（success=false），error 文案含指引"agent 正在写作中，请用 steer 或 follow_up"——把 harness 的 RuntimeError 语义翻译成协议帧；
- `id` 回显供前端做请求-响应配对；事件帧与响应帧靠 `type` 区分（`response` 不是任何 tau 事件类型，无碰撞）。

### D4 连接模型：每连接一个 listener，共享一个 harness；历史靠显式拉取

- 连接建立 → `harness.subscribe(该连接的推送回调)`；断开 → 调用退订函数（subscribe 的返回值免费获得）；
- 多标签页打开同一故事 = 多个 listener，事件 fan-out 天然同步，无广播代码；
- 连接建立**不自动**推历史：前端连上后发 `get_messages` 拉快照——刷新页面的恢复路径就是"重连 + get_messages"（验收标准 2）；
- 写锁：每连接一把 asyncio.Lock 串行化帧写出（照 rpc.py `_write_lock`），防两帧交错。

### D5 会话持久化边界：进程内历史 + 快照拉取，JSONL storage 推迟

P3 不接 `JsonlSessionStorage`。理由：服务进程存活期间 `harness.messages` 就是完整历史，`get_messages` 快照已覆盖"刷新浏览器"这一主要丢失场景；跨进程重启恢复才真正需要 JSONL 持久化，而它与 session/tree 分支语义耦合（DESIGN §3.4 预留的唯一可能的内核改动点），不在"换前端"的命题内。推迟到审计员阶段（P5 需要独立 session 时）一并评估。

### D6 REST 只读面：bible/chapters/state 三组，零写入口

- `GET /api/state`：slug、model、is_running、工具名列表、账本类型清单；
- `GET /api/bible/{type}`：五账本结构化数据（复用 `entity_summary` 的字段口径，返回对象数组而非渲染文本——前端表格需要字段，不是行文本）；
- `GET /api/chapters` + `GET /api/chapters/{id}`：正文列表与原文（curl 可查，P6 阅读器的数据源）；
- **不做任何写操作 REST**：写入口唯一在工具层（守门人原则，P2 的核心成果不容绕过）；
- 损坏 YAML：直读异常转 422 + 可读中文错误（含路径指引），制度化 `/bible` 的泄压阀模式。

### D7 Bible 面板数据流：REST 加载 + 写工具成功后精准 refetch（不做账本推送）

- 初始加载走 `GET /api/bible/{type}`；收到 `bible_upsert` / `method_lock` / `method_unlock` 的 `ToolExecutionEndEvent` 且 `is_error=false` 后，按 entity_type 精准 refetch 对应账本；
- 边界声明：refetch 触发名单是**呈现策略**（何时刷新视图），不是业务校验（数据正确性仍由工具层守门）——WS 桥与前端均不校验 Bible 内容；
- 不发明 hastings 自己的"账本变更事件"：那是第二套事件契约，维护成本 > 收益，P3 不需要。

### D8 前端架构：Zustand + 纯函数 reducer（adapter.py 的 TS 等价物）

- `applyEvent(state, event) → state` 纯函数：assistant 流式 buffer、工具卡片追加、错误卡片、agent_start/end 的 running 状态——蓝本 [adapter.py](../../src/tau_coding/tui/adapter.py) 但去掉 session 层分支；
- 未识别事件类型进 `unhandledEventTypes` 并在调试视图高亮（PlainRenderer 惯例）；
- 工具卡片：折叠态渲染 `render_call` 单行摘要 + 结果首行；展开显示完整 result text 与 `details` JSON（`timeline_check` 的 conflicts 直通可见）；
- Composer：发送按钮三态（prompt / steer / followUp 由 `get_state` 的 is_running 决定）、abort 按钮（仅运行中可用）。

### D9 页面设计语言：沿用 submission 的"数字推理"（Digital Noir）视觉稿

`submission/product-prototype/p1–p5.png` 已定稿产品视觉：深邃靛蓝底 + 暗金主色（#C8A84D）+ 语义状态色（已计划=蓝/已埋设=绿/逾期=红）+ 衬线中文标题字；三栏创作台（主编对话 / 章节区 / 故事账本）与 Agent 事件流调试视图（类型徽章 + JSON 折叠 + 运行概览 + 事件过滤）。P3 前端（T8/T9）以此为视觉目标，但**范围仍以本计划非目标清单为准**：原型中的章节阅读器、语料库、审计中心、角色关系图等属 P4–P6，只留导航占位不做实现。

### D10 前端测试与质量门：vitest 测 reducer，前端三门

- reducer 是纯函数——"事件序列进、状态断言出"就是 FakeProvider 哲学在前端的对应物；vitest 剧本覆盖：流式渲染、工具卡片、错误帧、未知事件上报；
- 前端三门：`tsc --noEmit` + `vitest run` + `vite build`；Python 四门不变；CI 脚本聚合七门；
- eslint 不引入（tsc + ruff 的纪律已覆盖本项目体量，依赖最小化）。

## 3. 任务分解

### T1 依赖与 app 工厂骨架（测试先行）

- pyproject 加 fastapi/uvicorn；`hastings/server/app.py`：`create_app(session: WritingSession)` 工厂 + `GET /api/health`；
- `WritingSession` 数据结构先行定义（T2 充实）；测试：health 端点 + app 工厂可注入 FakeProvider（依赖注入是 WS 可测性的前提）。

### T2 WritingSession 服务层（测试先行）

- `hastings/server/session.py`：封装 workspace + harness + tools；`prompt(content)`（async generator 透传）、`steer`/`follow_up`/`abort`/`is_running`/`messages`；运行中 prompt 抛带指引的 `AgentBusyError`（翻译 harness RuntimeError）；
- **协议无关**（不 import fastapi），纯 asyncio 可单测：FakeProvider 剧本复用 test_writing_loop 的写章流程；
- 测试：正常 prompt 流、busy 拒绝、steer/follow_up 排队、abort 后消息修复（harness 的 repair 逻辑免费复用）。

### T3 WS 协议层（测试先行）→ CP1

- `hastings/server/ws.py`：命令分发（D3 六命令）、事件订阅桥（subscribe/unsubscribe）、响应帧/error 帧、每连接写锁；
- 序列化辅助 `_wire(model) = model_dump(mode="json", by_alias=True)` + 命令帧字段校验（`_required_string` 等照 rpc.py 模式重写，注明来源，不 import）；
- 测试（TestClient websocket + FakeProvider）：
  - prompt → 事件帧序列断言（顺序 + 类型 + `assistantMessageEvent` 别名保真）→ response 帧配对；
  - 运行中第二连接 prompt → error 帧含 steer 指引；steer 帧生效（剧本断言 steer 消息进入后续 turn）；
  - abort → 后续收到修复消息事件；get_messages 快照与 harness.messages 逐字段一致；
  - 未知命令 / 缺 message 字段 → error 帧三要素。

### T4 REST 只读面（测试先行）

- `hastings/server/rest.py`：state / bible / chapters 三组端点（D6）；
- bible 端点复用 store 层 loader，`ValidationError | LedgerFormatError | YAMLError | OSError` → 422 可读错误（含路径指引）；
- 测试：正常账本、空账本（空数组非错误——泄压阀）、损坏 YAML 422、章节不存在 404。

### T5 serve 入口（测试先行）

- `hastings/server/__main__.py`：slug/port 参数、load_dotenv + `normalize_base_url` + env 检查复用、uvicorn 程序化启动；
- 测试：参数解析、env 缺失时中文指引退出（照 `env_error` 模式）。

### T6 前端骨架

- `frontend/`：Vite 5 react-ts 模板 + Tailwind 3.4 + zustand + vitest（dev 依赖）；`vite.config.ts` proxy `/api`、`/ws` → `localhost:8000`；
- 目录：`api/types.ts`（事件/命令帧 TS 类型）、`state/`、`components/`；
- 验收：`npm run build`（含 tsc）与 `vitest run` 空跑通过——前端三门就此建立。

### T7 事件契约 TS 类型 + reducer（测试先行）

- `api/types.ts`：AgentEvent 判别联合，与 [events.py](../../src/tau_agent/events.py) 一一对应（字段名按 wire 别名：`assistantMessageEvent`）；命令帧/响应帧类型；
- `state/reducer.ts`：`applyEvent` 纯函数（D8）；未识别 type 进 unhandled 列表；
- vitest 剧本：文本 delta 拼接与 MessageEnd 定稿、工具卡片生命周期（start→end、error 卡片）、agent_end 收 running、未知事件上报。

### T8 WS hook + 对话流 UI

- `api/socket.ts`：连接管理、命令发送（自增 id + 响应配对 Promise）、断线提示与手动重连按钮（不做自动重连风暴）；
- 连接建立 → 发 `get_state` + `get_messages`（快照直接喂 reducer）；
- `ChatStream.tsx`（消息气泡、工具卡片折叠、错误卡片）、`Composer.tsx`（三态发送 + abort）；
- 样式按"文章/档案"暗色主题从简（Tailwind 原子类，不引组件库）。

### T9 Bible 面板 + 调试视图 → CP2

- `BiblePanel.tsx`：五账本 Tab 表格（character/clue/foreshadow/method/timeline 各自字段列）；伏笔按状态着色（未回收 = 红点）；D7 refetch 逻辑（写工具成功事件触发）；
- `DebugView.tsx`：原始事件帧 JSON 滚动 + unhandled 高亮（隐藏彩蛋位，学习/演示用）；
- 浏览器联调：FakeProvider 不可用于浏览器（服务端接线是真实 provider），联调用真实端点冒烟一轮 + `npm run build` 产物经 FastAPI StaticFiles 托管验证。

### T10 质量门 + 红线 + 浏览器人工验收

1. Python 四门 + 前端三门全绿（CI 脚本聚合）；
2. 红线重跑：`tau_ai`/`tau_agent` 与上游 diff 为空、零 import tau_coding（grep 复核）、后端 diff pyproject 确认仅 +2 依赖；
3. 真实端点浏览器验收：完成验收标准第 6 条，截图/transcript 存档；暂无 key 则按 P1/P2 惯例记留白。

### T11 构建笔记 + R5 关闭

`dev-notes/v1/phase-03-web.md`：交付物、D1–D10 落锤、「Agent 设计经验」四透镜专节（重点：协议错误帧即 API、前端作为"事件的另一个消费者"的架构验证、WS/HTTP 两层泄压阀落点、调试视图作为可观测性契约的兑现）、七门与红线输出、R3/R5 结论。

## 4. 评审方案

### R1 自动化门禁（每次提交前）

```powershell
uv run ruff check .; uv run ruff format --check .; uv run mypy; uv run pytest
# 前端（T6 起追加）
cd frontend; npm run build; npx vitest run
```

### R2 架构红线评审（阶段验收时）

- `tau_ai`/`tau_agent` 与上游 diff 为空；
- hastings + frontend 零 import `tau_coding`（rpc.py/adapter.py 仅作模式参考）；
- 后端依赖仅 +fastapi +uvicorn（diff pyproject 确认）。

### R3 代码评审（P3 全量 diff 完成后）

TRAE-code-review skill 评审，重点关注清单：

1. WS 桥零业务逻辑：不做 Bible 校验、不解释工具语义（D7 refetch 名单属呈现策略，核对边界未被突破）；
2. 错误帧三要素：哪里错、为什么错、怎样算对（busy 拒绝、未知命令、缺字段三类逐条核对）；
3. 事件序列化保真：`by_alias` 有测试钉住；多连接 fan-out 无交错（写锁）；
4. 前端未识别事件不静默（reducer + 调试视图双落点）；
5. 泄压阀三情形在 HTTP/WS 层落点：损坏 YAML → 422 可读；空账本 → 空数组非错误；服务故障 → 500 带归因；
6. 范围：非目标零带入（无 SSE、无鉴权、无审计/阅读器、无 JSONL 持久化）。

问题分级沿用：blocker 必修，建议记入构建笔记"已知留白"。

### R4 检查点评审（执行中，两个 checkpoint）

- **CP1（T3 完成后）**：WS 帧协议逐字段核对（命令帧/事件帧/响应帧/error 帧）；FakeProvider 剧本在 WS 层回放一遍干跑。
- **CP2（T9 完成后）**：浏览器联调冒烟（真实端点一轮），五账本面板数据与磁盘 YAML 逐项核对。

### R5 阶段验收评审（全部任务完成后）

对照 DoD（§5）逐条打勾留证据；结论写入 T11 构建笔记。**R5 通过 = P3 正式关闭**，才允许进入 P4。

## 5. DoD（Definition of Done）清单

- [ ] 后端依赖仅 +fastapi +uvicorn；前端依赖隔离在 frontend/package.json
- [ ] WS 集成测试全绿：事件帧 JSON 保真（含 assistantMessageEvent 别名）、response 配对、busy 拒绝含指引、steer/follow_up/abort 行为正确
- [ ] get_messages 快照使浏览器刷新后对话流完整恢复
- [ ] REST 只读面三组端点；损坏 YAML → 422 可读错误；空账本 → 空数组
- [ ] 前端：流式渲染、工具卡片折叠（render_call 摘要）、错误卡片、三态 Composer + abort
- [ ] Bible 面板五账本 Tab + 伏笔未回收红点 + 写工具后精准 refetch
- [ ] 调试视图原始事件帧滚动 + 未识别事件高亮
- [ ] reducer vitest 剧本全绿；前端三门（tsc/vitest/build）入 CI
- [ ] Python 四门全绿；mypy strict 无新增豁免
- [ ] 红线：tau_ai/tau_agent diff 为空；零 import tau_coding
- [ ] 真实端点浏览器完成 P1 同流程 + 截图/transcript 存档（或按 T10-3 记留白）
- [ ] R3 评审完成，blocker 清零
- [ ] `dev-notes/v1/phase-03-web.md` 构建笔记发布

## 6. 风险与对策

| 风险 | 概率 | 对策 |
|---|---|---|
| TestClient 的 WebSocket 测试在 portal/事件循环上有坑（sync 上下文驱动 async app） | 中 | T3 第一个用例先做"连接 + health"最小探路；不行则退到 httpx-ws 或 ASGI 直连测试，选型在 CP1 记录 |
| 两 tab 同时 prompt 的竞态窗口（harness._running 检查非原子） | 低 | 竞态结果就是一方收到 busy error 帧，语义安全；不做跨请求锁（过度设计） |
| 长章生成时 MessageUpdate 高频帧导致前端渲染压力 | 低 | reducer 直接吞帧（纯状态合并，廉价）；渲染层按需用 rAF 合帧；正文体量小（单章数千字） |
| 前端 TS 类型与 pydantic wire 模型漂移（无共享 schema） | 中 | types.ts 手写但用 WS 集成测试的帧快照做对照（CP1 核对）；drift 留待 P6 评测阶段评估 openapi/ts 代码生成 |
| mypy strict 与 FastAPI 装饰器兼容问题 | 低 | fastapi 自带类型；个别 endpoint 用 `Annotated` 依赖注入写法即干净 |
| Windows 下 uvicorn/npm 进程管理琐碎 | 低 | 开发用两个终端分别跑；构建笔记记启动命令清单 |
| 范围失控（顺手做阅读器/上传/SSE/自动重连） | 中 | 非目标清单即红线；R3 第 6 条专查 |

## 7. 执行顺序

```text
T1 依赖 + app 工厂骨架（测试先行）
→ T2 WritingSession 服务层（测试先行，协议无关核心）
→ T3 WS 协议层（测试先行，CP1 评审）
→ T4 REST 只读面（测试先行）
→ T5 serve 入口
→ T6 前端骨架（前端三门建立）
→ T7 TS 类型 + reducer（测试先行）
→ T8 WS hook + 对话流 UI
→ T9 Bible 面板 + 调试视图（CP2 浏览器联调）
→ T10 七道门 + 红线 + 浏览器人工验收（需用户 API key 在场）
→ R3 代码评审
→ T11 构建笔记 + R5 验收
```

提交策略沿用 P0–P2：原子提交（feat(server) / feat(frontend) / test / docs 分开），**每笔提交前先经用户批准**。
