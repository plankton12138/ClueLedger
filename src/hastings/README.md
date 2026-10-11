# hastings — 悬疑小说写作 Agent 应用层

tau 内核之上的**薄应用层**：装配（工具注册、system prompt、事件订阅）与落盘（Bible / 正文 / 审计）全部在此完成，`tau_agent` / `tau_ai` 零改动。设计说明见 [dev-notes/DESIGN.md](../../dev-notes/DESIGN.md)，AI 检索入口见 [dev-notes/README.md](../../dev-notes/README.md)。

## 子包职责

| 路径 | 职责 | 关键文件 |
|---|---|---|
| `bible/` | 账本域：数据模型与状态机、工作区六阶段生命周期、派生统计与审计、时间线检测 | models / store / state_machine / story_state / stats / audit / timeline_check |
| `tools/` | 小说写作工具集（**硬约束所在层**）：账本读写、正文、手法锁、时间线、阶段机 | bible_tools / chapter_tools / method_tools / timeline_tools / story_tools / errors |
| `manuscript/` | 正文 SSOT 与保存不变量：段落 diff、revision 归档、write_guard 守门 | store / diff / write_guard |
| `server/` | FastAPI REST + WebSocket 桥、会话装配、工作区注册表、对账队列 | app / ws / rest / hastings_session / session / registry / reconcile |
| `rag/` | 语料与文风：确定性文风档案 + 语料库存储（向量检索待交付） | style_profile / corpus_store |
| `eval/` | 评测 harness 占位（P13 待交付） | — |
| 顶层模块 | CLI 与渲染、system prompt 组装、会话/事件持久化、设置、provider 目录 | cli / prompts / session_persist / events_store / settings / provider_catalog |

## 五焦点导航（读源码对号入座）

| 想了解 | 入口 |
|---|---|
| 上下文管理（靠架构省 token，非压缩） | [prompts.py](prompts.py) 的 `build_system_prompt` / `build_reconcile_message`；[bible/stats.py](bible/stats.py) 的 `build_workspace_status` |
| 会话生命周期 | [server/hastings_session.py](server/hastings_session.py)（busy 防护、每轮 `_refresh_system`）、[server/session.py](server/session.py)、[session_persist.py](session_persist.py) |
| 事件消费点（`harness.subscribe` fan-out 四消费者） | [cli.py](cli.py) 的 `PlainRenderer`、[server/ws.py](server/ws.py)（WS 帧桥）、[events_store.py](events_store.py)（events.jsonl）、[session_persist.py](session_persist.py)（session.jsonl） |
| 工具定义与使用 | [tools/](tools/)（注册与执行）；契约见 [dev-notes/modules/writing-tools.md](../../dev-notes/modules/writing-tools.md) |
| 上下文压缩 | 应用层**零接线**；内核有 `CompactionEntry` 原语，参考实现 `src/tau_coding/context_window.py`（长篇 50+ 章窗口溢出风险见 [DESIGN.md](../../dev-notes/DESIGN.md) §3） |

## 深入阅读

- 架构总览与六阶段状态机图：[DESIGN.md](../../dev-notes/DESIGN.md) §2.3、§3 开头的 **as-built 标注块**；
- 各阶段「为什么这样设计」：[dev-notes/README.md](../../dev-notes/README.md) 阶段索引；
- 账本存储 / 工具契约：[dev-notes/modules/](../../dev-notes/modules/README.md)。
