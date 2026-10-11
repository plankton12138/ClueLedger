# hastings — 悬疑小说写作 Agent 应用层

tau 内核之上的**薄应用层**：装配（工具注册、system prompt、事件订阅）与落盘（Bible / 正文 / 审计）全部在此完成，`tau_agent` / `tau_ai` 零改动。设计说明见 [dev-notes/DESIGN.md](../../dev-notes/DESIGN.md)，AI 检索入口见 [dev-notes/README.md](../../dev-notes/README.md)。

## 子包职责

| 路径 | 职责 | 关键文件 |
|---|---|---|
| `bible/` | 账本域：数据模型与状态机、工作区六阶段生命周期、派生统计与审计、时间线检测 | models / store / state_machine / story_state / stats / audit / timeline_check |
| `tools/` | 小说写作工具集（**硬约束所在层**）：账本读写、正文、手法锁、时间线、阶段机 | bible_tools / chapter_tools / method_tools / timeline_tools / story_tools / errors |
| `manuscript/` | 正文 SSOT 与保存不变量：段落 diff、revision 归档、write_guard 守门、写作守门（正文直出纠偏 / 回合规速） | store / diff / write_guard / guards / prose_guard / advance_guard |
| `server/` | FastAPI REST + WebSocket 桥、会话装配、工作区注册表、对账队列 | app / ws / rest / hastings_session / session / registry / reconcile |
| `rag/` | 语料与文风：确定性文风档案 + 语料库存储（向量检索待交付） | style_profile / corpus_store |
| `eval/` | 评测 harness 占位（P13 待交付） | — |
| 顶层模块 | CLI 与渲染、system prompt 组装、会话/事件持久化、多会话索引、上下文管理、设置、provider 目录 | cli / prompts / session_persist / session_store / events_store / context_manager / settings / provider_catalog |

## 五焦点导航（读源码对号入座）

| 想了解 | 入口 |
|---|---|
| 上下文预算与压缩（P10） | [context_manager.py](context_manager.py)：`count_context_tokens` 分层计数（provider usage 锚定 → 字符兜底）+ 两档压缩（确定性剥离 / LLM 摘要）+ 泄压阀；settings 项 `context_window_tokens` |
| 会话生命周期（P10 多会话） | [session_store.py](session_store.py)（sessions.json 索引 + 迁移）、[server/hastings_session.py](server/hastings_session.py)（busy 防护、每轮 `_refresh_system`）、[session_persist.py](session_persist.py) |
| 事件消费点（`harness.subscribe` fan-out 多消费者） | [cli.py](cli.py) 的 `PlainRenderer`、[server/ws.py](server/ws.py)（WS 帧桥）、[events_store.py](events_store.py)（events.jsonl）、[session_persist.py](session_persist.py)（session.jsonl） |
| 工具定义与使用 | [tools/](tools/)（注册与执行）；契约见 [dev-notes/modules/writing-tools.md](../../dev-notes/modules/writing-tools.md) |
| 写作守门装配（P10 硬约束） | [manuscript/guards.py](manuscript/guards.py)（`attach_guards`，一处接线 CLI/eval/Web）：prose_guard（正文直出 steering 纠偏）、advance_guard（`before_tool_call` 回合规速） |

## 深入阅读

- 架构总览与六阶段状态机图：[DESIGN.md](../../dev-notes/DESIGN.md) §2.3、§3 开头的 **as-built 标注块**；
- 各阶段「为什么这样设计」：[dev-notes/README.md](../../dev-notes/README.md) 阶段索引；
- 账本存储 / 工具契约：[dev-notes/modules/](../../dev-notes/modules/README.md)。
