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
- 三个工具只在配置了模块库的写作、检查与资料任务里出现；索引与状态任务不装。

### 按用途收窄

- 资料任务调用 `query_records` 时强制 `include_plans=false`：整理任务只核对正文观察，不能把
  作者计划当成已经发生的事实。
- 写作用途仍可查计划；正文观察按本轮截止章过滤。
- 每次查询把查询条件与 `metrics` 写入活动事件 `records_queried`，每次模块读取写 `record_read`；
  界面与复盘能看到扫了什么、读的是哪一版。

### 系统提示

- 已装模块短目录末尾补一句工具入口：`list_modules` 看装配、`query_records` 检索、
  `read_record` 读原文；命中不等于读过。没有模块库时不出现这句，避免提示不存在的工具。

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
| 工具专项 | `uv run pytest -q tests/workspace/test_retrieval_tools.py` | 4 passed |
| 静态检查 | `uv run ruff check src/hastings tests` | 通过 |
| 类型检查 | `uv run mypy src/hastings` | 70 个源文件无问题 |
| 后端全量 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --basetemp data/test-tmp/f3-full -o tmp_path_retention_count=100` | 761 passed，0 failures |

覆盖：没有模块库时工具不出现；只列已装模块；未装模块查询只警告；正文观察来源与截止章；
未装版本读取返回 403；已装版本读取返回原文与 `module_ref`。

## 未完成

- `search_module_library` 与「作者明确请求全局选模块」的授权还没有；当前不开放跨库搜索。
- `task_scope` 的 module／assembly 资源项、模块发布与装配更新工具都没有。
- 简单关联的一跳扩展、条目级 required／optional 预算与排除原因还没有。
- 界面没有把「找到／命中／已读／未覆盖」分开显示；活动事件已有数据，前端未接。
- 灵感工具（`search_ideas`／`read_idea`／`save_idea`／`link_idea_use`）与整请求规模记录仍未做。
