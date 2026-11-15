# 0.7 运行与验证

日期：2026-10-23。运行版本 `0.7.0`。本文件只记录本次实际运行方式与验证结果；未执行
或只做确定性测试的项目单独列出，不用历史结论替代。

## 启动

```bash
uv run python -m hastings.server                 # 默认端口 8000
uv run python -m hastings.server --state-dir <目录> --port 8140
```

前端产物在 `frontend/dist`（`cd frontend && npm run build`）；服务存在该目录时一并托管。
应用状态目录由 `--state-dir` 指定，不读进程环境变量。

## 本版行为变化

- 顶栏主导航改为**写作 / 故事资料**；「索引」「状态」两个抽屉及其组件已删除。
- 「刷新」改为作品操作中的「重新读取文件」；资料页另有局部「重新读取资料」。
- 新增故事资料五页（章节概览、人物、时间线、线索与伏笔、名词表）、章节范围选择、
  任务条与同页证据栏。
- 新增作者视图接口与资料任务接口；新增用途 `organize_materials` 与工具模式 `materials`。
- 新增整理覆盖记录 `materials-coverage.json`；空结果带指纹，来源改动后同样失效。
- 资料任务使用专用会话（`origin=materials`），不出现在写作会话列表。
- 资料任务上下文使用独立装配档，不注入设定集、谜底、作者计划与旧索引摘要。

## 本次验证

### 确定性测试

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `uv run pytest -q --junitxml=…` | 578 passed，0 failures，0 errors |
| 后端静态检查 | `uv run ruff check src/hastings tests/workspace`、`uv run mypy src/hastings` | 通过 |
| 前端单元测试 | `npx vitest run` | 126 passed（15 个文件） |
| 前端构建 | `npm run build`（`tsc --noEmit && vite build`） | 通过 |

新增的后端用例在 `tests/workspace/test_story_materials.py`（15 条），覆盖：人物域必须
带小传、章序重复拒绝、目标超出截止章拒绝、观测指纹不符拒绝、域与目标越界拒绝、
有结果/空结果的结构约束、覆盖写入与作者视图、空结果随来源失效、无核对记录时生死未知、
任务无提交记为部分完成、资料会话不出现在写作列表、受理→发布→完成闭环、同键不重复建任务。

新增的前端用例：`state/navigation.test.ts`（路由解析与往返）、`state/storyMaterials.test.ts`
（按截止章缓存、已有缓存不重复请求、切作品后丢弃迟到响应）、`state/materialTasks.test.ts`
（请求键复用与拒绝后重置、事件进度与结束后回读详情）。

### 浏览器走查（不调用模型）

脚本 `frontend/e2e/walkthrough-v07-materials.mjs`，服务以空状态目录启动，脚本经真实接口
建立作品与派生记录后只用界面操作。截图在 `artifacts/walkthrough-v07/`：

1. `01-chapters.png`：章节概览列出三章，未整理的章显示摘要缺失。
2. `02-timeline.png`：时间线显示第 1 章「10月21日深夜｜旅馆前台（有结果）」、第 3 章「无发现」。
3. `03-characters.png`：人物页一人一行，未核对时显示「生死未知」。
4. `04-clues.png`、`05-glossary.png`：空分组与声明词条正常显示。
5. `06-evidence.png`：选中条目后右侧显示详情与原文依据，含「打开原文」「带到对话」。
6. `07-as-of-chapter.png`：切换「查看截至第 2 章」后列表按截止章收敛。
7. `08-1024.png`、`09-narrow.png`：1024 与 760 宽度下无全页横向滚动，导航改为横排。

脚本同时断言：从资料页返回写作后草稿原文仍在；页面没有「检查报告」「章节索引」
「状态面板」等未开放入口。

### 浏览器回归用例

- `frontend/e2e/workspace.spec.ts` 中三处「刷新」按钮改为「重新读取文件」；其余断言未删。
- `frontend/e2e/model-flows.spec.ts` 的旧「索引面板」用例迁移为「故事资料整理、外部改源
  失效与重新读取」：改为从资料页发起整理、按 `/material-tasks` 轮询、断言没有写文件
  工具、改源后显示「来源已修改」、回到写作页后正文仍可修改，并检查作者视图的
  `coverage=found` / `freshness=stale`。该用例已通过类型检查，**本次没有执行**（需要
  在线模型与 Playwright 服务）。

### 在线模型（`deepseek-flash`）

使用 `config.yaml` 当前选择（`active_model: deepseek-flash`）对真实接口发起资料任务，
不打印凭据。三次样例均只使用 `list_files`、`read_file`、`publish_material_result`：

| 样例 | 观察结果 | 证据 |
|---|---|---|
| 1 章时间线（含「10月21日深夜」） | 完成 1/1；原文时间说法保留，跨度与地点有依据，引文与原文一致 | `artifacts/online-timeline-found.json` |
| 1 章时间线（没有任何时间词） | 完成 1/1；提交 `empty`，覆盖为「无发现」，没有编造时间 | `artifacts/online-timeline-empty.json` |
| 1 章章节概览 | 完成 1/1；摘要与事件、人物、地点、线索均可定位到本次读取的版本 | `artifacts/online-chapter-index.json` |

在线样例只说明这三类输入在上述作品上可用，不代表任意作品与任意章节都能正确理解。

## 未验证或未执行

- **人物身份、人物状态、线索、名词的在线语义验收**：只有确定性测试（工具权限、证据、
  域限制、空结果与失效），没有在线模型样例。
- **任务途中停止、断线重连、服务重启中断、预算耗尽**：后端只有确定性测试，未做浏览器
  或在线模型取证。
- **多窗口并发受理**：服务端互斥由既有轮次测试覆盖，未做双窗口浏览器取证。
- **150% 字号**：走查只覆盖 100%；150% 下操作区换行与详情切换未取证。
- **资料任务的部分完成与失败展示**：确定性测试覆盖状态与未完成项；浏览器只验证了
  无任务与已完成数据，没有在界面里跑一次失败任务。
- **既有检查报告回看、活动记录、版本历史**：既有测试全部通过，但没有重新做在线模型
  的检查流程。
