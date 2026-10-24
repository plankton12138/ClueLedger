# F3：检索工具与按用途收窄

日期：2026-10-24。目标：把 F1 的检索接到写作、检查与资料任务里，只开放本轮已装精确版本；
命中只用于定位，原文仍要按版本与区间读取并进入读取账本。

## 实际改动

### 三个只读工具

- `list_modules`：返回本轮装配的实例、精确版本、类型与条目数；未装模块不出现在结果里。
- `query_records`：按关键词、条目种类、模块、人物 ID、章路径查询；服务端固定本轮的截止章，
  返回来源、`module_ref`、`match_by`、`scope`、`metrics` 与 `warnings`。未装模块只警告，不按名字去库里找。
- `read_record`：按四段完整引用读取模块原文；模块与版本必须同时出现在本轮装配里，否则返回
  `MODULE_ACCESS_DENIED`。读取区间进入 `MaterialLedger`，`truncated` 时按 `next_cursor` 继续。
- `query_records` 的显式模块范围允许**一跳关系**：装配清单里的简单连接会带入相邻实例，
  命中标为 `relation`；只走一跳，不递归，也不会进入未装模块。再次扩展由模型重新指定模块。
- `search_module_library`：仅在写作用途出现，且要求 `task_scope.library_search=true`；
  该许可只有在作者消息明确出现「模块库／库里／全局」等短语时才能登记。搜索返回未装候选与
  精确版本，不自动装入、升级或改库。
- 三个只读工具在配置了模块库的写作、检查与资料任务里出现；索引与状态任务不装。

### 按用途收窄

- 资料任务调用 `query_records` 时强制 `include_plans=false`：整理任务只核对正文观察，不能把
  作者计划当成已经发生的事实。
- 写作用途仍可查计划；正文观察按本轮截止章过滤。
- 每次查询把查询条件与 `metrics` 写入活动事件 `records_queried`，每次模块读取写 `record_read`；
  界面与复盘能看到扫了什么、读的是哪一版。

### 系统提示

- 已装模块短目录末尾补一句工具入口：`list_modules` 看装配、`query_records` 检索、
  `read_record` 读原文；命中不等于读过。没有模块库时不出现这句，避免提示不存在的工具。

### 装配与库发布工具

- `task_scope` 增加两类资源项：`module_grants`（`attach` 精确到模块与版本，`detach` 精确到
  绑定 ID）与 `library_publish`。只读范围不能登记；重复登记只能收窄，不能中途新开装卸或发布。
- 作者的授权原话里还要出现对应动词：装入（装入／装上／装进／放进／加载／挂上／装配／绑定）、
  卸下（卸下／移除／拆下／卸载／解绑）、发布（发布／存成模块／保存到模块库等）。
  库搜索或一般写作授权不能代替装卸与发布指令。
- `attach_module`：只写本作品装配清单；按调用时的装配指纹做 CAS，返回绑定与新指纹，
  然后结束本轮（`terminate`）。同轮后续工具不会按旧装配继续执行。
- `detach_module`：按绑定 ID 卸下；后续默认查询不再包含该实例，已写进正文的事实仍保留。
- `publish_module`：已有模块基于当前 head 发布新版本，或用 `kind` + `name` 新建实例；
  默认以调用时的 head 做 CAS，期间别人发布过就返回冲突。发布不改任何作品绑定。
- 三个工具只在写作用途、且配置了模块库时出现；资料整理与检查任务没有装卸与发布入口。

## 接口与数据

- 没有新增 HTTP 路由与前端入口。
- 工具返回沿用统一结构；`read_record` 额外带 `module_ref` 与 `binding_id`。
- `MODULE_ACCESS_DENIED` 映射 403；`TOOL_RESULT_TOO_LARGE` 与 `read_file` 共用同一单次输出上限。

## 取舍

- **只读本轮装配**：不提供跨库按名字搜索；作者明确要求全局选模块的入口还没有，先不做半套权限。
- **精确版本而不是 head**：`read_record` 拒绝当前库里已有但不属于本轮绑定的版本，避免同一轮使用两个版本。
- **命中与读取分开**：`query_records` 不把正文塞满结果，长条目走 `read_record`；摘要只用于定位。
- **资料任务强制排除计划**：宁可让整理任务「查不到计划」，也不让它把未来安排写成当前事实。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 工具专项 | `uv run pytest -q tests/workspace/test_retrieval_tools.py tests/workspace/test_task_scope.py` | 34 passed |
| 装配与发布专项 | `uv run pytest -q tests/workspace/test_module_tools.py tests/workspace/test_task_scope.py` | 38 passed |
| 静态检查 | `uv run ruff check src/hastings tests` | 通过 |
| 类型检查 | `uv run mypy` | 106 个源文件无问题 |
| 后端全量 | `uv run pytest -q --basetemp data/test-tmp/f3-module-tools -o tmp_path_retention_count=100` | 778 passed，0 failures |

覆盖：没有模块库时工具不出现；只列已装模块；未装模块查询只警告；正文观察来源与截止章；
未装版本读取返回 403；已装版本读取返回原文与 `module_ref`；库搜索无作者许可被拒，
登记后只返回候选且装配不变；没有库搜索短语时不能登记许可；显式模块范围带出一跳关联，
无关实例不进入结果；装入只接受登记过的精确版本并结束本轮；卸下按登记绑定移除；
发布只在作者明确要求时可用，新建或追加版本都不改装配。

## 未完成

- 装配工具只支持单个精确版本装卸，不支持一次提交多模块排序或升级；批量调整仍走装配接口。
- 库发布的条目由模型给出，没有与作品文件逐条比对来源；发布内容的语义正确性仍由作者复核。
- 灵感工具（`search_ideas`／`read_idea`／`save_idea`／`link_idea_use`）仍未做。
- 库搜索许可只检查作者消息里是否出现「模块库／库里／全局」等短语，不解析否定语气；
  「不要从模块库找」仍可能被登记。否定句需要后续与任务理解一起处理。
