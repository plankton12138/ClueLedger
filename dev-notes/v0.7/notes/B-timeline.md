# B：时间线完整流程

目标：先让一个资料域走通「作者视图 → 明确范围 → 受理 → 进度 → 发布 → 覆盖 → 重读」。

## 实际改动

- 新增 `workspace/story_materials.py`：
  - `CoverageStore`：`materials-coverage.json`，键为「域 + 正文路径 + 人物路径」，带提交
    时的正文指纹；`status()` 按当前磁盘算有效、失效或来源缺失。
  - `author_view()`：六域统一入口，输出条目、证据、覆盖统计、实际时点、章节与人物清单；
    `entry_detail()` 按稳定散列 ID 取条目。
  - 时间线条目按章生成：有锚点显示原文时间、跨度与地点；没有锚点显示「时间未知」；
    没有覆盖记录显示「未整理」。
- 新增 `workspace/material_tasks.py`：
  - `MaterialTaskScope`（Pydantic）与 `freeze_material_scope()`：去重、真实路径、章序唯一、
    截止章、文件存在性、观测指纹。
  - `material_instructions()`、`scope_brief()`：按域生成任务要求与范围说明；已有记录标为
    待核对。
  - `publish_material_result()`：域与目标校验、全文读取、指纹复核、分域发布、覆盖写入；
    空结果只写覆盖。
  - `task_list()`、`task_view()`：从会话事件与覆盖记录还原任务、进度与未完成项。
- `state.py`：`AcceptedTurn` 增加 `material_scope` 与 `origin`；受理时写入 `turn_started`。
- `service.py`：新增用途 `organize_materials`；受理时冻结范围；执行时装配 `materials` 档、
  只读工具与一个提交工具；结束后按服务端目标清单计算完成度，未提交目标记为部分完成。
- `assembly.py` / `context.py`：新增 `materials` 装配档（任务要求 + 目录 + 本次范围），
  不装设定集、谜底、作者计划与旧索引摘要。
- `agent.py`：新增工具模式 `materials`，装配 `list_files`、`read_file`、`search_text` 与
  `publish_material_result`；工具参数按域收窄。
- `api.py`：新增作者视图、条目详情、任务受理、任务列表与任务详情五个端点；写作会话
  列表过滤资料任务会话。
- 前端：`api/storyMaterials.ts`、`state/storyMaterials.ts`、`state/materialTasks.ts`、
  时间线列表与详情、`ChapterRangePicker`、`MaterialTaskBar`、`EvidencePane`。

## 接口与数据影响

- 新增 `/story-materials/{domain}`、`/story-materials/{domain}/entries/{id}`、
  `/material-tasks`（POST/GET）、`/material-tasks/{turn_id}`。
- 新增 `turn_started.material_scope`、`turn_started.origin`、`material_target_published`、
  `material_task_finished` 事件字段；旧事件缺这些字段时按写作会话与无范围处理。
- 新增状态文件 `materials-coverage.json`，不进作品 Git。

## 取舍

- 覆盖记录按「目标」只保留最近一次，而不是追加历史：作者需要知道「现在是否可用」，
  历史可以看活动记录。代价是同一目标后来的任务会替换前一次的覆盖。
- 时间线不做日期解析：`when` 保存原文说法，跨章顺序按章节号而不是故事时间。

## 验证

- `tests/workspace/test_story_materials.py` 中的范围、发布、覆盖、失效、空结果、任务闭环
  与同键复用用例。
- 在线模型 `deepseek-flash`：时间线「有发现」与「无发现」两个样例，证据在
  `artifacts/online-timeline-found.json`、`artifacts/online-timeline-empty.json`。
- 走查 `02-timeline.png`、`06-evidence.png`、`07-as-of-chapter.png`。

## 未完成

- 停止与断线恢复只有确定性测试；浏览器没有在任务运行中停止取证。
