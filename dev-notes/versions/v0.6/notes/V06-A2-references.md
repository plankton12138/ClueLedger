# V06-A2 可选参考闭环

> 实施日期：2026-10-22。计划见[上下文与文件操作开发计划](../context-and-file-operations-plan.md)。
> 本阶段做「受理快照 + 显式参考 + 自然语言登记 + 材料事件 + 草稿引用 + 前端入口」，
> 不含章节索引（A3）与写后检查（C1）。

## 目标

作者可以不选文件直接讨论；也可以从当前作品里多选参考。两条路径进入同一份材料记录：
受理时冻结版本，执行时按预算预读，界面能看到「已提供全文 / 部分原文」，写作品前
指定的参考必须读完。

## 实际改动

| 位置 | 改动 |
|---|---|
| `workspace/materials.py` | 新增 `ContextRequest` / `ContextSnapshot` / `freeze_context` / `verify_context` / `require_author_quote`；`ContextSnapshot.identity()` 用于请求键查重 |
| `workspace/agent.py` | `RunContext.emit` 事件出口；`read_file` 写 `material_read` 事件；新增 `register_references` 工具（作者文本要引文与事件定位，发现的材料要先读过并引用文件原文） |
| `workspace/service.py` | `accept(..., context=...)` 冻结材料快照并写入 `turn_started`；执行开始复核快照版本；`_prepare_materials` 登记必需参考、按预算预读并写 `context_prepared` |
| `workspace/context.py` | `materials_section`：当前阅读只用于定位，参考按实际提供范围说明；`build_system_prompt(..., materials=...)` |
| `workspace/state.py` | `DraftContext` 与 `ConversationUi.draft_contexts`（界面状态版本 2，旧文件按空读取）；删除文字草稿时一并删除引用 |
| `workspace/api.py` | `ContextInput` / `TurnInput` 共用受理模型；首次与续聊都接收 `context`；`conversation-ui` 读写草稿参考 |
| `frontend/src/api/workspace.ts` | `ContextInput` / `TurnInput` / `DraftContext`；`createConversationWithText` / `startTurn` 改收完整请求对象；新增三类材料事件 |
| `frontend/src/state/chatMessages.ts` | `ChatTurn.materials` 与区间合并辅助；三类材料事件归并，版本变化不拼接旧区间 |
| `frontend/src/state/conversations.ts` | `draftContexts`；文字与参考同一个 PATCH 保存；发送时接收组件生成的快照 |
| `frontend/src/components/workspace/ReferencePicker.tsx`（新增） | 输入区「添加文件」多选，标签逐个移除；没有选择时不显示标签区 |
| `frontend/src/components/chat/TurnMaterials.tsx`（新增） | 每轮材料详情：必读标记与「已提供全文 / 部分原文（x/y 字符）」 |

## 接口与数据影响

- `POST /{wid}/conversations` 与 `POST /{wid}/conversations/{cid}/turns` 新增 `context`
  （`selected_path`、`reference_paths`、`observed_digests`）；路径必须是作品受管路径，否则 422。
- `conversation-ui` 新增 `draft_contexts`；PATCH 新增 `set_draft_context`、
  `draft_selected_path`、`draft_reference_paths`。
- 新事件：`context_prepared`、`reference_registered`、`material_read`。
- 新错误码：`CONTEXT_INPUT_INVALID`、`REFERENCE_EVIDENCE_INVALID`、`REFERENCE_KIND_INVALID`。
- `turn_started` 增加 `context`（路径、指纹、观察版本）。旧日志没有该字段时按空材料处理，
  同键带新材料不能匹配成旧请求。
- 首次与续聊的请求体统一：前端在发送前生成一次 `TurnInput`，重试复用同一个对象。

## 取舍

- **受理快照不是内容副本**：只冻结路径与服务端指纹；执行开始版本不符即整轮失败，
  提示刷新后重发，不偷偷换成新版本。
- **预读按选择顺序分配**：每份最多 2000 字符、总量 8000；被截断的参考在写入前必须读完，
  模型按 `next_cursor` 继续。
- **工具只登记两种来源**：`author_text` 与 `discovered`。作者选择的文件由应用登记，
  索引与系统设定由各自入口登记，避免模型自己给自己授权。
- **没有参考时不写空事件**：只讨论的轮次事件流保持不变，`context_prepared` 只在确实有
  当前阅读或参考时出现。
- **草稿引用跟文字同生共死**：同一个 PATCH 提交，删除文字时一并删除，避免留下没有内容的
  悬空选择。

## 验证证据

- 后端：`uv run pytest -q` 全量通过；新增用例覆盖 `freeze_context` 去重/缺失/未知观察版本、
  `verify_context` 版本失效、引文校验、`register_references` 三种失败、受理快照与
  参考预读、同键不同材料的冲突、`conversation-ui` 草稿参考往返。
- 前端：`npm test -- --no-file-parallelism` 95 项通过，`npm run build` 通过。新增用例覆盖
  材料区间合并与版本切换、草稿参考同次保存、发送快照与受理后清理。

## 偏差与未完成项

- 执行开始时「参考已被外部改动」的整轮失败只在单元层验证；端到端时序由受理快照复核保证，
  未用真实模型复现该竞态。
- `purpose`（索引 / 检查）字段尚未加入请求模型：等 A3、C1 有真实使用方时再加，避免空接口。
- 索引查询、章节索引面板、写后检查与范围事件展示仍缺（A3 / B2 / C1）。
- 包版本仍为 `0.5.0`，前端与桌面版本声明未升级。
