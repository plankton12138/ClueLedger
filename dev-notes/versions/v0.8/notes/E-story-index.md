# E：统一章节索引与时间线双视图

日期：2026-10-24。目标：一次整理只产出一份章节投影，章节视图与故事视图从同一份数据读；
发布、覆盖、失效与旧版只读兼容都在同一条链路上，不另造第三份可变的时间线正文。

## 实际改动

### 发布链路

- `agent.py` 增加 `story_index` 的结果结构：摘要、事件（表达方式、故事时间、完整模块引用、
  证据）。表达方式限定为叙述、证词、回忆、推测、梦与未知；引用必须是四段完整身份。
- `material_tasks.py` 增加该域的发布分发：校验证据区间与读取账本，交给
  `validate_projection` 复核后发布；证据所属章号与指纹不符时整章失败。
- 一章重建为 `empty` 时只移除这一章的贡献并更新覆盖：其他章的投影、作者安排与其他域的
  记录都不动；`insufficient` 不删除已有投影。
- `StoryIndexStore.remove()` 在同一把锁内重读、删除、原子替换；没有这一章时是空操作。

### 视图与接口

- `story_index.py` 修正两处：`ObservedEvidence` 统一提供 `to_dict()`；故事视图的稳定顺序
  改为「章号 + 来源顺序」，不再用散列标识决定不连通分组的先后。组与组之间仍然没有
  可证明的先后，第二组起标为「先后未确定」，这个标记本身不表达时间结论。
- `workspace/api.py` 新增 `GET /api/file-workspaces/{id}/story-events?order=chapter|story`，
  两种顺序读同一份 `story-index.json`；截止章只影响浏览，不改写投影。
- `story-materials/story_index` 的作者视图保持可用，供整理范围选择与覆盖统计读取。

### 前端

- `state/navigation.ts` 把「章节概览」与「时间线」合并为「章节与事件」，带 `order` 参数；
  旧 `materials/chapters`、`materials/timeline` 路由分别落到章节顺序与故事顺序。
- 新增 `components/materials/StoryEventsPanel.tsx`：章节顺序显示每章摘要、事件、旧版条目与
  旧时间锚点；故事顺序显示可证明的链、未知事件与冲突。证据直接跳到原文，不复制正文。
- `StoryMaterialsPage` 的整理按钮改为 `story_index` 域，范围选择与覆盖统计沿用现有资料任务。

## 接口与数据

- `story-index.json`：一章一条投影，发布只替换该章；`recipe_version` 变化时旧投影按过期处理。
- 事件字段：`text`、`chapter`、`mode`、`story_time`（原文说法、可选标准日期、before/after）、
  `speaker`、`related`、`author_event`、`evidence`；`speaker` 与 `author_event` 只是引用，
  发布工具不能创建作者事件。
- `/story-events` 返回 `mode`、`as_of_chapter`、`chapters` 或 `groups/unknown/conflicts`、
  `warnings`；旧版概览与时间锚点在章节顺序里标为 `legacy`，不伪装成新投影。

## 取舍

- **一个视图，两种顺序**：章节顺序回答「这一章发生了什么」，故事顺序回答「能证明谁先谁后」。
  不连通的事件不排进同一列表，宁可在 UI 上多一个分组，也不伪造全序。
- **空结果的行为按域区分**：统一章节索引的 `empty` 表示「这一章不再有可发布的观察」，
  因此移除该章旧投影；线索与专名的 `empty` 仍只表示这一目标没有发现，不清空其他来源。
- **证据必须来自本章**：故事视图不接受跨章拼证据；回忆与证词不因为写在正文里就升级为事实。
- **旧路由只做映射**：旧链接仍能打开，但页面只维护合并后的入口，不再保留两套展示逻辑。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --basetemp data/test-tmp/post-package -o tmp_path_retention_count=100` | 745 passed，0 failures（作品包用例已随功能取消删除） |
| 静态检查 | `uv run ruff check src/hastings tests`、`uv run ruff format --check <本次改动文件>` | 通过。仓库级 `ruff format --check .` 仍报告历史文件与部分 0.7 服务文件不符合当前 ruff 0.16.10 的格式，属既存差异，本次未批量改写 |
| 类型检查 | `uv run mypy src/hastings` | 68 个源文件无问题（作品包模块已删除） |
| 前端单元测试 | `npx vitest run --no-file-parallelism` | 161 passed（18 个文件） |
| 前端构建 | `npm run build` | 通过 |

新增用例：

- `tests/workspace/test_story_index.py`：补证据序列化、不连通分组的稳定顺序与
  原子替换失败的旧 envelope 保护，19 条通过。
- `tests/workspace/test_story_materials.py`：统一索引发布后两种视图可读；一章 empty 只移除该章；
  整理任务经工具发布后，`/story-events` 的两种顺序返回同一批事件。
- `frontend/src/state/navigation.test.ts`：新章节与事件页、`order` 参数与旧路由映射。

## 未完成

- 线索与专名的「条目＋按来源分组贡献」还没有升级：目前仍按目标与来源记覆盖，没有模块来源
  分组；空结果不会清空其他来源，但也不支持多来源合并展示。属 F 阶段与模块检索一起做。
- 多份时间线／故事线实例的计划叠加与按绑定筛选没有做；当前故事视图只读本作品正文观察。
- 作者事件只能作为完整引用出现；本轮没有创建或更新库中作者事件的工具。
- 故事视图一次返回当前截止章内的全部事件，没有分页；长篇作品需要先按绑定或章区间筛选，
  该筛选依赖 F 阶段的模块与检索范围。
- 未做浏览器走查与在线模型语义验收；现有结论只覆盖确定性后端与前端单元。
