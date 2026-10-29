# 故事资料移除映射

日期：2026-10-28。状态：**P0 登记完成，P5 执行**。本文件回答两件事：现在谁在用这些东西，删掉之后各自归谁。删除顺序不能反：先拆共享依赖，再删入口，最后验证“旧接口退出但写作仍正常”。

## 1. 页面与壳层

| 当前位置 | 实际调用方 | 1.0.1 处理 |
|---|---|---|
| `components/workspace/AppHeader.tsx:16-23` 资料导航 | 顶栏 | 删除；构思入口移入写作内视图 |
| `components/workspace/WorkspaceShell.tsx:10,31,72,113,120-121` 资料页挂载与返回 | 壳层 | 删除挂载；`source.returnPage` 只保留写作与构思 |
| `components/workspace/WorkspaceToc.tsx:170` “原有资料”折叠 | 目录栏 | 删除 |
| `state/navigation.ts:37,104,123,150` 的 `materials` 页面类型与 `#/…/materials/…` 解析 | AppHeader、Shell、来源返回 | 删除；未知旧 URL 回落到写作页，不做兼容页 |
| `state/pagePositions.ts` 的 `materials` 位置记忆 | 壳层 | 删除 |
| `components/materials/*`（`StoryMaterialsPage`、`MaterialBrowserBar`、`MaterialEntries`、`EvidencePane`、`ChapterRangePicker`、`MaterialTaskBar`、`OrganizeActions`、`StoryEventsPanel`、`labels.ts`） | 资料页自身 | 删除；`ChapterRangePicker` 与 `EvidencePane` 的定位能力先迁到构思详情与正文高亮，再删文件 |
| `state/storyMaterials.ts`、`api/storyMaterials.ts` | `StoryMaterialsPage` | 调用方删除后一并删除，不留空客户端 |
| `state/materialTasks.ts` | `StoryMaterialsPage`、`ChapterClues` | 同上；任务状态改由统一操作读取 |
| `components/clues/ChapterClues.tsx` 的“整理本章” | 写作区 | 转为“当前构思”与明确启动的核对任务；不再从资料任务入口发起 |

## 2. 接口

| 接口 | 调用方 | 处理 |
|---|---|---|
| `GET /{id}/story-materials/{domain}` | 资料页 | 删除 |
| `GET /{id}/story-materials/{domain}/entries/{entry_id}` | 资料页详情 | 删除 |
| `GET /{id}/story-events` | 资料页时间线 | 删除；事件改由构思节点与实际时间表达 |
| `POST /{id}/material-tasks` | 资料页、本章线索 | 删除；能力并入写作／核对任务 |
| `GET /{id}/material-tasks`、`GET /{id}/material-tasks/{turn_id}` | 资料页任务栏 | 删除；改由 `/api/operations/*` 读取 |
| `/clues*`（`clue_api.py` 全部端点） | 线索面板、核对、来源定位 | 定位与核对能力接到新结构；计划关联与推理表单撤掉 |
| `POST /{id}/turns/{tid}/stop` | 对话面板 | P2 迁到 `/api/operations/{id}/pause` 后删除 |

旧接口删除的判定不是“返回 404 页面”，而是返回 JSON 的 404：静态站点回退的 200 HTML 不能算已移除（见[代码示例](../implementation-examples.md#8-移除验证旧接口退出且新能力正常)）。

## 3. 服务端模块

| 文件 | 被谁引用 | 处理 |
|---|---|---|
| `workspace/material_tasks.py`（1360 行） | `api.py`（受理、列表、详情）、`service.py`（范围冻结、指令、目标）、`agent.py`（`publish_material_result`） | 删除旧入口；`freeze_material_scope` 的“冻结范围”思路迁到构思任务受理，`targets()` 的“未完成项”迁到操作结果 |
| `workspace/story_materials.py`（1071 行） | `story_index.py`（`CoverageStore` ×2）、`service.py`、`agent.py`、`activity.py`（`DOMAIN_LABEL`）、4 个测试文件 | 不能整文件删：`CoverageStore` 的覆盖与失效逻辑先拆到来源／投影职责，再删旧装配 |
| `workspace/materials.py` 的工具模式 | `agent.py` 的工具注册 | 提取“读取有界、来源冻结、必读区间”三项能力给新任务，删除资料专用工具集 |
| `workspace/clue_*.py`（`clue_api`、`clue_views`、`clue_designs`、`clue_reviews`、`clue_context`、`clues.py`） | 线索面板与核对 | 稳定身份与来源校验的做法沿用到构思；表单式计划关联、推理目标编辑撤掉 |
| `workspace/character_states.py`、`disclosure.py`、`glossary.py`、`chapter_index.py` | 写作轮的内部能力 | 按真实写作调用保留必要读取；统一来源与失效，不再重复注入旧推测 |
| `workspace/agent.py` 的 `materials` 工具模式 | `service._tool_mode` | 随资料任务一起删除；新流程用 `story_*` 工具 |

## 4. 前端工具与状态

| 内容 | 处理 |
|---|---|
| `api/clues.ts`、`state/clues.ts`、`components/clues/*` | 定位与核对能力迁到构思详情；表单式推理编辑删除 |
| `api/workspace.ts` 的 `purpose` 联合类型 | 增加 `discuss / design / write / revise / review / reader_preview`，删除 `expand_idea`、`update_disclosure`、`update_state`、`update_chapter_index` |
| 帮助文案与提示 | 改到新流程；历史版本记录保留原文 |

## 5. 浏览器用例

| 用例 | 处理 |
|---|---|
| `chapter-clues.spec.ts`、`clue-design.spec.ts`、`clue-reference.spec.ts`、`clue-review.spec.ts`、`clue-source-navigation.spec.ts`、`clue-workflow.spec.ts` | 按产品变化改写，保留来源、授权与恢复保护；不能只删用例消除失败 |
| `story-overview-removal.spec.ts` | 保留，继续验证旧页退出 |
| 新增 | `story-structure.spec.ts`、`story-operations.spec.ts`、`story-removal.spec.ts`、`story-workflow-online.spec.ts`，登记进 `playwright.config.ts` 的 workspace／online 白名单 |
| `frontend-experience.spec.ts` | 已由作者改动移除灵感展开部分，保留其余 |

## 6. 执行顺序

1. 拆共享依赖：`CoverageStore`、`DOMAIN_LABEL`、`ChapterRangePicker`、`EvidencePane`。
2. 接新能力：构思详情、统一操作、正文定位。
3. 删入口：页面 → 状态／API → 接口 → 工具注册。
4. 验证：旧 JSON 接口 404、旧 hash 回落、写作／灵感／模块／普通对话仍正常。
5. 收尾：删除已无调用方的旧测试文件，保留改写后的新用例。

## 7. 不做的事

- 不迁移旧数据：新格式不读旧账本、旧资料域或旧会话事实来恢复废弃方案。
- 不保留“只读兼容页”：未知旧 URL 回落到写作页，避免两套入口长期并存。
- 不后台重抽取：作者明确要求分析已有正文时，按新任务受理并可见地调用模型。
