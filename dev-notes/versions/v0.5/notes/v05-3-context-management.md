# V05-3 上下文与作品信息阶段记录

对应计划：[上下文与作品信息开发计划](../context-management-plan.md)。本记录只写已交付的部分，
未开始的阶段明确标注，不用计划推断当前能力。

## V05-3A 作品元信息（2026-10-21 交付）

### 交付内容

| 位置 | 改动 |
|---|---|
| `workspace/metadata.py`（新增） | 章序与显示名解析、中文序号、人物元信息解析与写入前校验 |
| `workspace/files.py` | `FileEntry` 增加显示字段；`catalog()` 整本解析章节；`read()` 增加 `display_text`；新增 `describe()`；导入章名改用同一解析 |
| `workspace/api.py` | `/files`、`/file` 返回 `display_title`、`chapter_number`、`chapter_type`、`roles`、`metadata_warnings` 与 `display_text` |
| `workspace/agent.py` | `list_files` 带显示名、章序、章节类型与身份标签 |
| `workspace/context.py` | 目录按显示名列出，角色附身份；写作要求补元信息格式与「讨论不等于确认」 |
| 前端 `CharacterBadges`、`WorkspaceShell`、`Paper`、`api/workspace.ts` | 目录与稿纸共用显示名与标签；稿纸用 `display_text`，元信息不做正文渲染 |

### 关键取舍

- **章序只认文件名**：四位编号是唯一来源，列表位置、标题里的序号都不参与编号；冲突时显示沿用文件名并给诊断，不重写用户文件。
- **书名回退有诊断**：一级标题等于作品名、或在多章重复时，回退文件名中的章名并写入 `metadata_warnings`；界面和模型都能看到提示。
- **显示名一处生成**：`title` 保留纯章名（复制、工具编辑、搜索定位都用它），`display_title` 才是「第X章 章名」；前端不拼规则。
- **元信息与正文分开**：`text` 保留含 `---` 围栏的完整原文，`digest` 按磁盘字节；`display_text` 只去掉人物开头的元信息。搜索行号因此不偏移。
- **应用写入拒绝坏格式**：未知身份、重复键、未结束或过长的元信息返回 `CHARACTER_METADATA_INVALID`，不落盘；外部编辑器留下的问题按诊断修复，文件仍可读。
- **标签是作者视角**：小传写了 `culprit` 不等于正文已揭露凶手，也不自动写进章节事件。
- **标签颜色类名必须字面量**：构建按源码扫描保留样式类，`role-${role}` 拼接会被当成未使用样式删掉，只剩无颜色的空壳；`CharacterBadges` 用 `ROLE_CLASS` 常量表写完整类名，浏览器用例断言标签背景色非透明防回归。

### 验证

| 检查 | 结果 |
|---|---|
| `uv run pytest tests/workspace/test_metadata.py` | 38 项通过（新增） |
| `uv run pytest tests/workspace` | 333 项通过 |
| `uv run ruff check src/hastings tests/workspace` | 通过 |
| `uv run mypy src/hastings` | 38 个源文件通过 |
| `npm test` / `npm run build` | 85 项通过（含标签类名覆盖表）/ 构建通过 |
| `npm run test:e2e` | 12 项通过（新增「章名带章序，人物标签在目录与稿纸一致」，含标签背景色断言） |

浏览器走查脚本 `frontend/e2e/walkthrough-v05-3a-metadata.mjs` 用夹具作品覆盖：一级标题是书名
（回退为「第一章 暴雪来客」）、缺第二章（仍显示第三章）、序章与尾声、文件号与标题号冲突
（显示第三章并提示）、角色多标签与无标签、稿纸不渲染元信息。截图与逐项观察见
[走查记录](../artifacts/v05-3a-metadata/observations.md)。

### 验收缺陷修复

验收复现出两处与本章规则不符的行为，均已修复并补回归用例：

| 缺陷 | 原因 | 修复 | 回归用例 |
|---|---|---|---|
| 导入时书名回退保留了文件编号 | 一级标题等于作品名时回退到整条文件名，`0001` 被当成章名再拼一次，生成 `0001 0001 暴雪来客.md` | 回退改用 `filename_without_order()`：只去掉四位编号，章名与「序章」这类标记保留 | `test_import_markdown_with_book_title_does_not_repeat_filename_number`、`test_import_keeps_special_marker_when_falling_back` |
| 无编号文件从标题取得章序 | `_scan_chapter` 在文件名没有编号时退回标题里的序号，与「章序只认文件名」的约定相反，且不提示缺编号 | 章序只取文件名编号；标题里的「第三章」仅用于提取章名并给出缺编号诊断 | `test_heading_number_does_not_set_chapter_number` |

复跑（2026-10-21）：`uv run pytest -q` 339 通过（`test_metadata.py` 39 项）；
`npm test` 10 个文件 86 项通过；`npm run test:e2e` 12 项通过；`ruff`、`mypy`（38 个源文件）
与 `npm run build` 通过。真实模型改写人物标签仍无用例，见下节。

### 未验证与未进入

- 只在本地文件与假 provider 上验证；没有让真实模型改写人物标签，因此「模型是否按提示只写确认过的身份」尚无真实用例。
- 章名规则只覆盖计划列出的样例（缺章、重复号、序章/尾声/番外、重复书名、H2 标题、无章名、无编号）；标题里混用「回」「节」、超长章名等实际作品未取证。
- 浏览器走查未覆盖超长角色列表与标签换行。
- V05-3B（本轮材料与指定参考）、V05-3C（章节索引与来源）、V05-3D（检索与写前核对）、V05-3E（界面闭环与验收）均未开始；索引、材料快照、读取记录与冲突规则目前都不存在。
