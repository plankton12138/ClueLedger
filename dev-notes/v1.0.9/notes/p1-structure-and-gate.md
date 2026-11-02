# P1 实施记录：当前格式与章节门槛

日期：2026-11-02。状态：**已实施（P1 领域与门槛）**。未改前端，未改版本号；P6 前运行版本仍为 1.0.8。

## 1. 实际改动

| 文件 | 改动 |
|---|---|
| `src/hastings/workspace/story_models.py` | 新增 `BookIntent / ChapterPlan / InformationThread / InformationMove`、`FORMAT_VERSION`、`chapter_content_revision`；`ProseBinding` 增加 `move_id` 与新来源值；`StoryVariant` 增加 `format_version` 与当前格式集合，旧字段保留为过渡读取；新增 12 个当前格式操作并扩展联合类型；新增章节查找、可开始判断与修订校验 |
| `src/hastings/workspace/story_changes.py` | 新增当前格式操作的应用分支；旧操作与当前操作按 `format_version` 互斥；`diff_variant` 为当前格式生成反向操作；`variant_digests` 覆盖作品意图、章节、信息线与信息变化 |
| `src/hastings/workspace/story_store.py` | 新建文档时可指定格式版本；备选复制当前格式对象；新增 `receipt_for`；内部联合提交写正文时跳过章节策略，避免持锁重入 |
| `src/hastings/workspace/chapter_write_policy.py` | 新增统一写入策略：按路径找计划、核对确认状态与章修订；已开始章节禁止直接改名 |
| `src/hastings/workspace/chapter_writing.py` | 新增 `start_chapter` 与 `save_chapter_prose`：开始事务在锁内确认构思、分配路径并创建初始文件；整章保存按摘要与章修订校验，可幂等重试 |
| `src/hastings/workspace/files.py` | `Workspace` 可注入 `ChapterWriteGuard`；`write / check_write_intent / check_delete_intent` 统一调用；`create_chapter` 与导入显式允许未挂计划创建；新增 `check_chapter_intent` 与 `reserve_chapter_path` |
| `src/hastings/workspace/agent.py` | `edit_file` 在 Git 基线前增加章节门槛预检，指纹冲突仍按原有材料顺序报告 |
| `src/hastings/workspace/state.py` | `RecentWorkspaces.workspace()` 在读取故事状态后注入 `ChapterWritePolicy` |
| `tests/workspace/test_chapter_writing.py` | 新增 11 个用例：格式 round-trip、修订范围、旧操作拒绝、开始幂等、内容门槛、策略拦截、导入只读、摘要与修订冲突、保存重试、附加正文、撤销与失败回滚 |
| `tests/workspace/test_story_bindings.py`、`test_story_joint_commit.py` | 按新的绑定目标提示与 `Workspace.write` 参数调整测试替身；旧格式行为不变 |

## 2. 关键语义

- **两种格式并存，不是双份真相。** 新作品使用 `format_version=3`；旧作品保持 2。旧操作不能写入格式 3，当前操作不能写入格式 2。P5 删除格式 2 与旧投影。
- **章节修订只由内容计算。** 标题、顺序、确认状态和正文路径不参与；改标题不会让已确认章节失效，改其他章不会改变本章修订。
- **开始写作是一笔事务。** 确认修订、写入 `prose_path`、创建初始正文在同一个 `commit_change` 中完成；失败不留半状态，撤销会同时移除确认与初始文件。
- **门槛在文件层。** 应用内正文写入都经过 `Workspace` 的策略钩子；Agent 的 `write_file / edit_file` 无例外。导入允许创建未挂计划正文，但之后的应用内编辑会被拒绝，直到附加章节计划。
- **内部联合提交有唯一例外。** `StoryStore` 在持锁状态下写正文时显式跳过策略；授权由上层开始/保存事务给出，否则会重入死锁。这不是绕过：跳过只发生在已经完成章节校验的提交内部。

## 3. 验证

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/workspace/test_chapter_writing.py -q` | 11 passed |
| `uv run pytest tests/workspace/test_story_contracts.py test_story_store.py test_story_bindings.py test_story_joint_commit.py -q` | 92 passed |
| `uv run pytest tests/workspace/test_story_api.py ... test_story_tools.py -q` | 219 passed |
| `uv run pytest tests/workspace/test_files.py test_agent.py test_chapter_writing.py -q` | 71 passed |
| `uv run ruff check <本次改动文件>` | All checks passed |

未运行：后端全量、前端测试与浏览器测试；本阶段没有前端改动。按项目约定，等正文保存与工作台投影接通后再做一次必要的广泛回归。

## 4. 未完成项（进入 P2）

1. 工作台只读投影与 `/story/workbench` 等 HTTP 路由尚未接线。
2. Agent 写入尚未从材料快照取得 `expected_chapter_revision`，P1 只强制「已确认构思」；修订绑定在 P2 完成。
3. 正文保存响应里的确定性索引仍是占位 `index_revision`，`analysis_job_id` 为空；P2 接锚点，P3 接语义分析。
4. 前端没有任何新入口；旧构思/线索页仍在，读取格式 3 作品时会显示空的旧结构。P4 替换，P5 撤除。
5. 没有为新格式准备长期样本；当前用例在 `tmp_path` 中创建隔离新作品。
