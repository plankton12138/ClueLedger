# P4 验收记录（T7）

日期：2026-10-09。环境：Windows，`uv run python -m hastings.server`（默认 127.0.0.1:8000），模型 deepseek-flash（真实端点）。
验收方式说明：本会话环境无浏览器自动化工具，Web 侧按 P3 T10 同款许可路径，以 **REST/WS 客户端 transcript + 落盘文件 + 服务端日志** 证据替代截图；浏览器人工联调留给用户日常使用复核。

## DoD 逐条

### 1. Web：杀进程重启 + 重连，对话流完整恢复 ✅

- `POST /api/workspaces {"slug":"p4-demo-a"}` → 201；
- WS `/ws/p4-demo-a` 发真实 prompt「请登记一个角色：波洛，比利时侦探」→ `prompt_ok: true`，332 个事件帧到 `agent_end`；
- **杀掉服务进程 → 重启 → 新 WS 连接 `get_messages`** → 6 条消息完整回放：
  `roles = [user, assistant, toolResult, assistant, toolResult, assistant]`，首条 user 与重启前一致。

### 2. CLI：重进同 slug 会话续上 ✅

- 重启后的服务进程不动，另开 CLI：`"看一下之前的对话，用一句话说出我们登记过的角色名，不要用工具。/quit" | uv run python -m hastings p4-demo-a`
- agent 不经任何工具调用即答：**「我们登记过的角色只有一个：波洛（比利时侦探）。」**——历史来自 session.jsonl 回放而非账本直读。

### 3. agent 可 read_chapter / list_chapters ✅

- 工具清单确认注册（CLI 启动横幅）：`bible_upsert、bible_query、write_chapter、read_chapter、list_chapters、method_lock、method_unlock、timeline_check`；
- 单元 + 集成测试：test_chapter_tools（存在/不存在/泄压阀）、test_writing_loop 剧本（读回第 1 章再写第 2 章）；
- guidelines 已增补使用时机（写新章前读上章结尾、回收伏笔前核对原文）。

### 4. 双工作区并存、隔离、可新建 ✅

- `POST /api/workspaces {"slug":"p4-demo-b"}` → 201；`GET /api/workspaces` → `["demo-story","p4-demo-a","p4-demo-b","web-acceptance"]`；
- 隔离：p4-demo-a 写入 6 条消息后，WS 连 `/ws/p4-demo-b` 拉快照 → `b_messages: 0`；
- 落盘隔离：`stories/p4-demo-a/` 有 session.jsonl + `bible/characters/波洛.yaml` + chapters/；`stories/p4-demo-b/` 无 session.jsonl（未产生对话）；
- 坏 slug 新建：`POST {"slug":"../escape"}` → 422「slug 不合法」（测试钉住）；WS 连 `/ws/CON` → 服务端 1008 关闭。

### 5. 泄压阀 ✅

- 坏 session.jsonl（端到端）：手工写入坏行 → WS 连 `/ws/p4-bad` → `success:true, messages:[]`（空会话继续），文件改名 `session.jsonl.bad-1791511553719844700`；
  注：中文告警走 `print`，服务进程管道缓冲未即时刷出——文案由 test_session_persist 单元测试钉住；
- read 不存在章节 → is_error + 可用章节列表（test_chapter_tools 钉住）。

### 6. 红线 + 七道门 ✅

- `git diff HEAD -- src/tau_agent src/tau_ai` 为空；hastings 零 `import tau_coding`（仅文档字符串提及）；grep `@router.post|@app.post` 仅 rest.py 一处 = `/api/workspaces`；
- 七道门：ruff check ✅ / ruff format ✅（109 files）/ mypy ✅（62 files, 零豁免）/ pytest ✅（257 passed）/ tsc ✅ / vite build ✅（167.72 kB）/ vitest ✅（43 passed）。

## REST 契约变更声明（无兼容层，破坏性）

- `GET /api/state` → `GET /api/{slug}/state`；`/api/bible/{type}`、`/api/chapters[/{id}]` 同理加 slug 前缀；
- `WS /ws` → `WS /ws/{slug}`；
- 新增 `GET/POST /api/workspaces`（POST 为唯一写 REST，资源创建）；
- `GET /api/health` 收敛为全局探活 `{"status":"ok"}`（会话信息移至 `/api/{slug}/state`）；
- CLI：`python -m hastings.server <slug>` 去掉 slug 位置参数（服务进程不再绑定单一故事）。

## 冒烟脚本

临时脚本（不入库）：Temp/p4-smoke.py（setup/prompt/isolation/restore 四步）。
