# P5 之二：故事资料退出

日期：2026-10-29。状态：**资料页、资料接口与资料任务已移除；写作、线索、构思仍然可用**。执行的是 [P0 移除映射](P0-materials-removal-map.md) 的第 1—3 步。

## 1. 删掉的入口

| 层 | 移除内容 |
|---|---|
| 页面 | `components/materials/*`（资料页、浏览栏、条目表、证据栏、章节范围选择、任务栏、事件面板、标签）、`NewClueDialog`（只从资料页发起建线索） |
| 状态与客户端 | `state/storyMaterials.ts`、`state/materialTasks.ts`、`api/storyMaterials.ts` |
| 导航 | 顶栏「故事资料」、壳层资料页挂载、`navigation.ts` 的 `materials` 页面类型与 `#/…/materials/*` 解析、`pagePositions` 的资料位置 |
| 接口 | `GET /story-materials/{domain}`、`GET /story-materials/{domain}/entries/{id}`、`GET /story-events`、`POST/GET /material-tasks`、`GET /material-tasks/{turn_id}`、`POST /turns/{id}/stop` |
| 服务端 | `workspace/material_tasks.py`（1360 行）、`workspace/story_materials.py`（1071 行）、`materials` 工具模式、`publish_material_result`、`MATERIALS_INSTRUCTIONS` 与 `materials` 装配画像、`chapter_view` / `story_view` 两层事件视图与覆盖统计 |
| 用途 | `organize_materials` 从受理、工具模式、装配、终态统计里一并去掉；受理改为**用途白名单**，不认识的用途返回 `TURN_PURPOSE_UNAVAILABLE`，不会被当成普通对话执行 |

旧地址的行为按映射表约定：`#/workspaces/{id}/materials/*` **回落到写作页**，不保留兼容页；接口返回 JSON 的 404（`{"detail":"Not Found"}`），不是静态站点的 200 页面。

## 2. 保留与迁移的能力

- **写作轮的材料账本**（`materials.py`、`MaterialLedger`）不动：它记录这一轮实际读了什么、提供了哪些原文，与「资料页」不是一回事。
- **线索面板**留在写作区：读列表、详情、正文依据定位、带入对话都保留；去掉了「整理本章」与资料任务栏，状态文案从「尚未整理」改为「还没有线索记录」。线索只由写作与核对登记。
- **来源定位**改用「返回原处」：资料页不存在了，返回目标就是原来的阅读位置。
- **活动记录**继续显示历史资料任务（旧日志里的 `origin=materials` 与会话仍按当时的域名称渲染），但不再产生新的这类任务；写作会话列表仍过滤这些旧会话。
- **构思页**是结构信息的唯一入口；`/story/*` 接口与结构工具不受影响。
- **目录新增「设定与人物」**：原先设定集与人物小传折在「原有资料」里，折掉之后作者就找不到自己的设定文件了。现在它们作为普通目录行直接列出（正文／模块／设定与人物三段），不带「资料」这层旧含义；`e2e/workspace` 的目录用例同步改写。

## 3. 验证

| 检查 | 结果 |
|---|---|
| `tests/workspace/test_story_overview_removal.py` | 5 个用例：旧总览 404、资料接口返回 JSON 404 且不在 OpenAPI 里、旧停止入口 404 且暂停仍在、旧用途被拒、旧资料任务不会跑成写作 |
| `tests/workspace/test_clue_ledger.py`、`test_clue_views.py`、`test_source_locations.py`、`test_story_index.py` | 改写为读账本与线索详情；删除资料发布与事件视图用例 |
| `tests/evaluation/test_context_eval.py` | 27 passed（案例 48 个；删除 `material_empty` 并同步套件） |
| `npx vitest run` | 323 passed（30 个文件） |
| `npx tsc --noEmit` / `npm run build` | 通过 |
| `uv run pytest -q` | **1387 passed**，414 秒 |
| `npx playwright test --project=workspace` | 见下方：干净数据目录下除 `text-selection` 外全部通过 |

### 浏览器用例

| 用例 | 处理 |
|---|---|
| `chapter-clues` | 改为在写作区面板里读线索；删除「整理本章」提交范围与冲突的用例（能力已移除），状态文案按新文案断言 |
| `clue-source-navigation` | 证据标识改从 `/clues/{id}` 读取；返回按钮改为「返回原处」；新增「旧资料页地址回落到写作页」 |
| `clue-reference` | 删除「资料页带入线索后回写作页」；面板内带入保留 |
| `clue-design` | 删除：唯一入口（资料页的新建线索设计）已移除 |
| `aqua-layout`、`core-pages`、`workspace`、`frontend-experience` | 删除资料页专属用例；`frontend-experience` 的窄屏矩阵改截构思页，末尾改为验证旧地址回落 |
| `model-flows`、`clue-workflow-online` | 删除资料任务流程（在线用例不重跑，避免消耗额度） |

### 浏览器全量的环境前提

同一个 `data/test-tmp/e2e` 目录连跑多次后，模块库与作品目录会累积，`module-workspace`、`frontend-foundation` 等用例会因残留条目失败（与本次移除无关）。用新的 `HASTINGS_E2E_DATA_DIR` 复跑即恢复：`module-workspace` + `frontend-foundation` + `text-selection` 共 14 个用例 13 passed。

本机批量删除保护仍会拦 playwright 清理输出目录，运行时需要 `CODEBUDDY_SAFE_DELETE_ENABLED=0`（与 pytest 相同）。

第二次用干净目录跑 `core-pages` + `frontend-experience` + `shared-interactions` + `context-usage`：22 passed，4 failed 全部是同一个环境错误——本地文件系统代理拒绝把截图写进 `dev-notes/v0.8/artifacts/frontend-experience/screenshots/*.png`（同一路径用普通脚本可写）。产品断言本身没有失败。

`text-selection.spec.ts:21` 仍红，原因不在本次改动：灵感悬浮按钮压在对话区的「移除选段」上，点击被 `idea-launcher` 拦截。选段浮层已提到 `z-index: 120`（原先 80），但列表里的按钮仍在悬浮按钮之下；这属于灵感浮层默认位置的布局问题，留给那部分一起改，不用放宽断言掩盖。

## 4. 已知取舍

- **线索详情的「作者计划」区仍保留**：agent 的 `link_clue_design` 工具仍可建立关联，详情里也能看到并解除；但界面不再提供新建线索设计。按映射表，计划关联与推理表单要「撤掉」——这一步留到线索详情迁进构思时一起做，避免现在只删一半。
- **`TurnService.stop()` 保留为服务内部能力**：HTTP 入口只剩 `/api/operations/{id}/pause`，两套中断语义在界面上不并存；`stop()` 仍被恢复与单元测试直接调用。
- **`state.py` 仍接受 `material_scope` 字段**：只是读旧日志时容忍，新轮次不再写入。
- **旧数据不迁移**：不读旧的 `materials-coverage.json`、资料域文件或旧资料会话事实来恢复废弃方案。
