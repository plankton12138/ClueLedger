# Phase 3 构建笔记：Web 化（FastAPI WebSocket 桥 + React 前端）

> 对应 [phase-03-web-plan.md](phase-03-web-plan.md)（T1–T11 全部完成）。
> 状态：**R3 blocker 清零，R5 验收通过（13/13），P3 正式关闭，可进入 P4。**

## 1. 交付内容

**后端**（`src/hastings/server/`，全部新建）：

| 模块 | 职责 | 提交 |
|---|---|---|
| [app.py](../src/hastings/server/app.py) | `create_app(session, workspace, static_dir)` 工厂：health + 路由装配 + StaticFiles 托管 dist | f55b9be → 5e39796 |
| [session.py](../src/hastings/server/session.py) | `WritingSession` 协议 + `HastingsSession` 实现（协议无关，纯 asyncio 可单测）；busy 翻译为 `AgentBusyError` | 3e8a92d |
| [ws.py](../src/hastings/server/ws.py) | WS 事件桥：六命令分发、每连接 listener 订阅 + 写锁、响应帧/error 帧 | （T3，CP1） |
| [rest.py](../src/hastings/server/rest.py) | 只读面：`/api/state`、`/api/bible/{type}`、`/api/chapters`；零写入口 | 8b270e9 |
| [__main__.py](../src/hastings/server/__main__.py) | `python -m hastings.server <slug>` 入口；dist 探测托管 | e126784 |

**前端**（`frontend/`，全部新建；Digital Noir 设计语言，[theme.ts](../frontend/src/theme.ts) 单一事实来源）：

| 模块 | 职责 | 提交 |
|---|---|---|
| [api/types.ts](../frontend/src/api/types.ts) | 事件契约 TS 类型：AgentEventFrame ×10 + 嵌套流事件 ×12 + 七 role 消息（wire 别名对齐） | 5bf8622 → 8a24905 |
| [state/reducer.ts](../frontend/src/state/reducer.ts) | `applyEvent`/`applyMessages` 纯函数：流式 buffer、工具卡片生命周期、错误卡片、未知事件上报 | 8a24905 |
| [api/socket.ts](../frontend/src/api/socket.ts) | 连接管理：自增 id + 响应配对 Promise、超时泄压、快照拉取、手动重连 | 488e4f2 |
| components/ChatStream / Composer | 消息气泡、工具卡片折叠（render 单行摘要 + details JSON 展开）、三态发送（prompt/steer/follow_up）+ abort | 488e4f2 |
| [components/BiblePanel.tsx](../frontend/src/components/BiblePanel.tsx) | 五账本 Tab 表格、伏笔未回收红点、D7 精准 refetch | 5e39796 |
| [components/DebugView.tsx](../frontend/src/components/DebugView.tsx) | 原始事件帧滚动 + 类型徽章 + 过滤 + unhandled 高亮 | 5e39796 |

**依赖变更**：`+fastapi +uvicorn +websockets`（第三个是 T10 验收发现 D1 误裁，见 §4）。

## 2. 关键决策（D1–D10 落锤）

- **D1 依赖**：fastapi + uvicorn（不带 `[standard]`）+ websockets（修正项）。前端依赖全部隔离在 `frontend/package.json`。
- **D2 进程模型**：单进程单工作区，独立 `__main__.py` 入口，CLI 零改动。
- **D3 WS 协议**：照抄 rpc.py 六命令语义（只抄模式不 import）；`id` 回显做请求-响应配对；事件帧 `model_dump(by_alias=True)`，T3 有测试钉住 `assistantMessageEvent` 别名保真。
- **D4 连接模型**：每连接一个 listener，事件 fan-out 免费获得多标签页同步；历史靠显式 `get_messages` 快照（刷新恢复 = 重连 + 快照）。
- **D5 持久化边界**：进程内历史 + 快照拉取；JSONL storage 推迟到 P5 一并评估。
- **D6 REST 只读面**：state/bible/chapters 三组，零写入口；损坏 YAML → 422 + 路径指引。
- **D7 Bible 数据流**：REST 加载 + 写工具成功事件精准 refetch；refetch 名单是呈现策略，不是业务校验。
- **D8 前端架构**：Zustand 薄壳 + 纯函数 reducer（adapter.py 的 TS 等价物，去掉 session 层分支）。
- **D9 设计语言**：Digital Noir（submission 视觉稿）——theme.ts 令牌 → tailwind.config.js 消费，theme.test.ts 钉色值。
- **D10 测试**：vitest 剧本测 reducer/socket（FakeProvider 哲学在前端的对应物）；前端三门 = tsc + vitest + build。

## 3. Agent 设计经验（四透镜）

### 3.1 错误消息是喂给模型的 API——本阶段的「模型」换成了前端

P1/P2 的错误回喂对象是 LLM，P3 多了一类读者：**浏览器**。同一原则直接迁移——协议 error 帧保留三要素原样透传，前端不做人话翻译：

- busy 拒绝：`AgentBusyError` 文案自带「用 steer 在当前流中插话、用 follow_up 排队、或先 abort」指引，经 error 帧到 Composer 就地显示——用户看到的和 LLM 看到的是同一条消息；
- 未知命令：error 帧列出合法命令全集（`AVAILABLE_COMMANDS`）；
- 帧校验：pydantic 的 `loc: msg` 原样进帧，不做字段名翻译。

发现：**对 LLM 的回喂与对 UI 的透传是同构的**——「哪里错、为什么错、怎样算对」对人同样是最短恢复路径。前端唯一做的是排版（错误卡片/横幅），不是解释。

### 3.2 硬约束放工具层，prompt/UI 只负责解释世界

- **REST 零写入口**：写路径唯一在工具层（P2 守门人原则），HTTP 面只读。前端再怎么被操纵也绕不过账本校验。
- **Composer 三态只是引导，不是约束**：按钮按 `is_running` 切换 prompt/steer/follow_up，但真正的并发防护是服务端 busy 拒绝——UI 可骗，协议不可骗。实测：连发两个 prompt，第二个收到带指引的 error 帧。
- **reducer 零 Agent 逻辑**：前端是「事件的另一个消费者」，与 TUI 完全同构。这是 Tau 红利的直接验证：P3 全程 `tau_agent`/`tau_ai` diff 为空，换前端零内核改动。

### 3.3 泄压阀：三情形在三个层次各有落点

| 情形 | 工具层（P2 已有） | HTTP 层（P3 新增） | WS/前端（P3 新增） |
|---|---|---|---|
| 违规 | is_error + 修正指引 | 422 + 可读中文 + 路径指引 | error 帧 + Composer 就地显示 |
| 合法但空 | 正常返回 + 指明下一步 | 200 空数组（空账本/空章节不是错误） | 空态文案（"该账本暂无条目（用 bible_upsert 工具登记）"） |
| 环境故障 | 明确归因 | 500 带文件系统错误归因 | WS 断开 = 状态徽章 + 重连按钮（**手动**，不做自动重连风暴）；命令超时 = 15s reject 带三要素 |

关键取舍：断线重连做成显式动作而非指数退避——自动重连风暴在服务重启场景会放大故障；连接状态本身是可观测的（三色徽章），把「何时重连」的判断留给用户。

### 3.4 可观测性是事件契约的一部分

- **未知事件显式上报**：TS reducer 的 `unhandledEventTypes` 照搬 PlainRenderer 惯例，调试视图红色高亮——协议演化时前端不会静默吞掉新事件类型；
- **同一事件流服务两类读者**：ChatStream 渲染「人话」（气泡/卡片），DebugView 滚动原始帧（102 帧/轮验收流程）——一个事件契约，零额外推送；
- **details 直通**：`AgentToolResult.details`（如 timeline_check 的 conflicts）随事件帧自然到达前端，调试视图展开即见——P3 不发明任何「账本推送」第二契约；
- **服务端兜底**：listener 投递失败打 stderr 并退订，死连接不静默吞。

## 4. 踩过的坑

1. **`uvicorn[standard]` 裁剪误伤（T10 验收发现，最重要）**：D1 以「Windows 用不到 uvloop/watchfiles」为由裁掉 extras，但 extras 里的 `websockets` 是 WS 升级的必需实现——没有它 `/ws` 直接 404（`Unsupported upgrade request`）。REST 全绿、WS 全灭，且服务端只有一行 WARNING。教训：**裁依赖要按「功能必需 vs 平台优化」区分，不能按 extras 打包粒度整体裁**。修正：`uv add websockets`（9ad3a1a）。
2. **busy 必须调用即同步抛**：harness 的 `prompt()` 在运行中调用时同步抛 RuntimeError（不是 async generator 迭代时才抛）。StubSession 必须复刻这一时序，否则 WS 层的拒绝路径测不到。契约照抄要连**抛出时机**一起抄。
3. **瞬发 FakeProvider 下「运行中插命令」必然竞态**：FakeProvider 剧本瞬间跑完，「运行中」窗口趋近于零，steer/abort 的 WS 集成测试会 flaky。解法：这类行为测试用 StubSession 手动控制 running 状态钉确定性，FakeProvider 只测正常流。
4. **`tool_execution_end` 帧不带 args**：args 只在 start/update 上。BiblePanel 的 D7 refetch 需要 `entity_type`，只能从 `tool_execution_start` 帧养一张 `toolCallId → args` 查表。契约类型（types.ts）如实反映——TS 编译器当场拦下了想当然的 `event.args`。
5. **两套字段口径并存**：WS 事件帧 camelCase（WireModel 别名），REST 响应 snake_case（`model_dump()` 未开 by_alias）。是有意的：事件层别名是对外契约需钉住，REST 是内部只读面直接暴露模型。rest.ts 头注已声明，防后人「顺手统一」。
6. **vitest 微任务时序**：响应 resolve 后 onSnapshot 在微任务链里触发，同步断言拿不到——剧本测试需要 `flush()`。类似 Python 测试里「事件循环让一拍」的直觉。
7. **验收工具截图不可用**：浏览器 agent 的 screenshot 接口全程 unavailable。按 P1/P2 留白惯例，以 transcript + DOM 检查 + REST 响应替代（[p3-acceptance.md](artifacts/p3-acceptance.md)）。

## 5. 验证输出存档

**七道门（最终复跑，websockets 加入后）**：

- Python 四门：ruff check ✓ / ruff format ✓（103 files）/ mypy strict ✓（60 files）/ pytest **212 passed**
- 前端三门：tsc + vite build ✓ / vitest **31 passed**（4 files：reducer 13 + socket 10 + rest 4 + theme 4）

**红线（R2）**：

- `tau_ai`/`tau_agent` 与上游 diff 为空（排除 `__pycache__`）✓
- 零 import tau_coding（grep 仅两处文档字符串注明来源，ws.py:3 / cli.py:3）✓
- P3 pyproject 增量 = +fastapi +uvicorn +websockets（修正项，diff c385022..f55b9be + 9ad3a1a）✓

**浏览器人工验收（验收标准 6）**：deepseek-flash 真实端点，`web-acceptance` 工作区——设定 2 角色（波洛/阿斯特兰德船长）→ 写 ch01 全流程通过；agent 自发登记时间线 1 条、伏笔 2 条；工具卡片/账本 refetch/调试视图 102 帧/断线重连横幅均验证。截图留白，transcript 见 [p3-acceptance.md](artifacts/p3-acceptance.md)。

## 6. DoD 对照（计划 §1 验收标准）

| # | 标准 | 结果 |
|---|---|---|
| 1 | WS 集成测试全链路断言（含别名保真） | ✓ test_server_ws.py |
| 2 | 刷新恢复（重连 + get_messages 快照） | ✓ 验收流程覆盖 |
| 3 | 运行中 prompt 拒绝 + steer/follow_up/abort 行为 | ✓（StubSession 钉确定性） |
| 4 | REST 只读面 + 损坏 YAML 422 | ✓ test_server_rest.py |
| 5 | 前端流式渲染/工具卡片折叠/错误卡片/五账本 Tab/红点/调试视图 | ✓ 验收确认 |
| 6 | 浏览器完成 P1 同流程 + 存档 | ✓（截图留白 → transcript） |
| 7 | 红线 + 七门全绿 | ✓ |

**非目标均未越界**（章节阅读器、RAG、多工作区、鉴权、跨进程持久化、conflicts 面板可视化留待 P4–P6）。

## 7. 已知留白（转入后续阶段）

- JSONL 会话持久化（跨进程重启恢复）→ P5 审计员阶段一并评估；
- Conflicts 的面板级可视化（数据已透传调试视图）→ P5 审计视图；
- 移动端布局（<1024px 隐藏账本面板）→ 非目标，桌面优先；
- dist 未进 git（构建产物），部署形态（StaticFiles 单进程）已可支撑演示。

## 8. R3 代码评审记录

**评审范围**：f55b9be^..HEAD（P3 全量，3930 行 / 32 文件），2026-10-09。双验证者独立复核，3 个问题全部 2/2 确认：

| # | 问题 | 严重度 | 修复 |
|---|---|---|---|
| 1 | 手动重连后对话流重复：loadSnapshot 经 applyMessages 纯追加到已有 items，无重置机制 | major | loadSnapshot 重置到 initialUiState 后重放快照（unhandledEventTypes 跨连接保留）；StrictMode 双挂载经验证不触发（socket 在 CONNECTING 即被关） |
| 2 | running 卡 true：`s.running \|\| state.is_running` 使 stale true 短路，与「快照是唯一事实来源」注释矛盾，断线错过 agent_end 后永久运行中形态 | major | 改为 `running: state.is_running` |
| 3 | 被动断线后 pending 命令悬挂 15s 且误报「响应超时」（主动 close 有清理、onclose 没有） | minor | 抽出 failPending 共用；close() 先清未决再关连接（保证主动关闭归因不被 onclose 抢先） |

**评审中排除的候选**（证据不足）：REST 章节 id 校验不一致（write_chapter 固定生成 chNN.md）；双连接瞬发 prompt 竞态（harness.prompt_message 同步置 _running=True，harness.py L148-150）；空闲 steer 语义（入队下轮消费，与上游 rpc.py 一致）。

**修复验证**：新增 store.test.ts（4 剧本）+ socket 被动断线剧本；前端 36 测试全绿，tsc+build 通过。后端零改动。

## 9. R5 验收：DoD 逐条打勾（2026-10-09）

对照 [phase-03-web-plan.md §5](phase-03-web-plan.md) 13 条清单，逐条证据：

| # | DoD | 结果 | 证据 |
|---|---|---|---|
| 1 | 后端依赖仅 +fastapi +uvicorn；前端依赖隔离 | ✅（带修正项） | pyproject 实增 +fastapi +uvicorn +**websockets**——第三个是 T10 验收发现的 WS 必需实现（D1 误裁修正，§4 坑 1），非越界扩张；前端依赖全在 frontend/package.json，pyproject 零前端依赖 |
| 2 | WS 集成测试全绿（别名保真/配对/busy/steer/follow_up/abort） | ✅ | test_server_ws.py 全绿（212 passed 之内）；`assistantMessageEvent` 别名有专门断言 |
| 3 | get_messages 快照刷新后完整恢复 | ✅ | 真实端点验收页面加载即恢复全历史；store.test.ts 钉住快照重置重放语义 |
| 4 | REST 三组端点；损坏 YAML→422；空账本→空数组 | ✅ | test_server_rest.py；验收 curl 佐证（character count=2 / chapters ["ch01"]） |
| 5 | 流式渲染/工具卡片折叠/错误卡片/三态 Composer+abort | ✅ | 验收步骤 4–8：bible_upsert/write_chapter 卡片「完成」、流式渲染、运行中形态切换正常 |
| 6 | Bible 五账本 Tab + 伏笔红点 + 精准 refetch | ✅ | 验收 DOM 读取：角色 2 条自动 refetch 出现；时间线/伏笔/手法 Tab 切换正常；红点逻辑就位 |
| 7 | 调试视图事件帧滚动 + 未识别事件高亮 | ✅ | 验收步骤 10：102 帧滚动 + 类型徽章 + 过滤框；reducer 测试覆盖 unhandled 上报 |
| 8 | reducer vitest 全绿；前端三门入 CI | ✅ | vitest 36 passed（reducer 13）；**本次补 CI frontend job**（tsc/vitest/build 三步，ci.yml） |
| 9 | Python 四门全绿；mypy strict 无新增豁免 | ✅ | 最终复跑：ruff check PASS / format 104 files / mypy 60 files 零 issue / pytest 212 passed；server/ 内 grep `type: ignore` 零命中 |
| 10 | 红线：tau_ai/tau_agent diff 为空；零 import tau_coding | ✅ | T10 核查（--no-index 对 C:\Projects\Agent\tau 排除 pycache 为空；grep 仅 2 处文档字符串注明来源） |
| 11 | 真实端点浏览器 P1 同流程 + 存档 | ✅（按 T10-3 合规留白） | deepseek-flash 完成 2 角色→ch01；截图工具不可用，transcript+DOM+REST 证据替代（[p3-acceptance.md](artifacts/p3-acceptance.md)） |
| 12 | R3 评审完成，blocker 清零 | ✅ | 3 问题（2 major + 1 minor）双验证确认后全部修复（17ebfd7），36 测试全绿 |
| 13 | 构建笔记发布 | ✅ | 本文档（本节即 R5 结论） |

**R5 结论：13/13 通过（2 条带说明：websockets 修正项、截图合规留白），P3 正式关闭，允许进入 P4（RAG 语料库）。**

R1–R4 执行情况：R1 每笔提交前七道门全绿；R2 红线 T10 核查通过；R3 双验证者评审闭环（§8）；R4 CP1（WS 协议逐字段测试钉住）/ CP2（T9 提交时七门全绿+浏览器联调）达成。
