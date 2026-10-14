# P3 浏览器人工验收记录（T10，验收标准第 6 条）

> 日期：2026-10-09｜端点：`http://127.0.0.1:8000/`（`uv run python -m hastings.server web-acceptance`）
> 模型：deepseek-flash（真实端点）｜前端：`npm run build` 产物经 FastAPI StaticFiles 托管
> 流程：P1 同款「设定 2 个角色 → 写 1 章」
>
> **截图留白**：验收所用浏览器工具的截图接口不可用（多次返回 unavailable），
> 按 P1/P2 惯例记留白；以 transcript、DOM 检查与 REST 响应作为替代证据。
> 工作区落盘文件（`stories/web-acceptance/`，gitignored）为原始证据。

## 流程结果

| 步骤 | 结果 | 证据 |
|---|---|---|
| 打开页面，状态「已连接」 | PASS | 标题「黑斯廷斯备忘录」、头部状态徽章、五 Tab 账本面板（DOM 中 5 个按钮） |
| 发送「设定两个角色档案：波洛 + 阿斯特兰德船长」 | PASS | bible_upsert 工具卡片出现，状态「完成」；「写作中」徽章消失 |
| 账本面板 refetch（D7） | PASS | 角色 Tab 出现 2 条（DOM 读取：赫尔克里-波洛 / 阿斯特兰德船长，含身份/动机/秘密/弧光） |
| 发送「写第一章：波洛午夜抵达伊斯坦布尔旅馆…」 | PASS | write_chapter 卡片「完成」；助手回复确认第一章完成 |
| agent 自发登记时间线 | PASS | 时间线 Tab 1 条：「伊斯坦布尔旅馆初次交锋」第 1 天 00:00–00:50 |
| agent 自发登记伏笔 | PASS | 伏笔 Tab 2 条（未回收红点逻辑就位） |
| 手法账本空态 | PASS | 空表（合法空结果，非错误） |
| 调试视图 | PASS | 102 个事件帧，类型徽章含 agent/message_update/tool_execution_end 等，过滤框在位 |

## REST 佐证（验收后 curl）

- `GET /api/bible/character` → `count=2`（波洛：比利时籍大侦探…；阿斯特兰德：东方快车船长…，均 arc=flat）
- `GET /api/chapters` → `{"chapters":["ch01"]}`；`ch01.md` 落盘于 `stories/web-acceptance/chapters/`
- `GET /api/bible/timeline` → `count=1`（与前端面板一致——快照与文件同源）

## 过程中发现并修复的问题

**uvicorn 缺 WebSocket 实现（D1 依赖决策的修正）**：首次启动后页面显示「已断开」，
服务端日志 `WARNING: No supported WebSocket library detected`，`/ws` 升级请求 404。
根因：D1 为依赖最小化去掉 `uvicorn[standard]`，但该 extras 里的 `websockets`
不是可有可无的性能项，而是 WS 协议的必需实现。修正：`uv add websockets`
（uvicorn 自动探测，无需改代码）。教训归档于 phase-03-web.md §Agent 设计经验。

## 环境注记

- 验收浏览器视口 624px 宽，账本面板按设计在 `lg:`（≥1024px）断点显示；
  DOM 检查确认面板结构与数据在窄视口下正常存在（display:none），非缺陷。
