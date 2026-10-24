# F4：条目级候选与排除原因

日期：2026-10-24。目标：把提示词里会截断的列表分区改成「条目记账」——没装进去的候选
保留 id、原因、完整渲染字符数与继续查看的入口，而不是只留一句「还有 N 条」。

## 实际改动

- `assembly.Exclusion`：一条没进入本轮上下文的候选，字段为 `id`、`reason`
  （`budget`／`limit`）、`chars`、`location`；`Partition` 增加 `excluded` 元组。
- `AssembledContext.summary()` 在有排除项的分区里写 `excluded` 列表；没有排除项的分区
  形状不变，`state_injected` 仍只记身份与规模，不复制条目正文。
- 五处会截断的装配入口在生成文本的同时登记排除项：
  - 章节索引（`chapter_index.index_injection`）：超预算时从当前章起全部记为 `budget`，
    入口为 `read_file <章节路径>`。
  - 线索（`clues_section`）：超出条数记 `limit`，超出字符记 `budget`，入口为
    `read_state domain=clues`。
  - 人物状态（`roster_section`）：同上，入口为 `read_state domain=roster`。
  - 故事时间（`timeline_section`）：早于最近几章的记 `limit`，超预算后只留最近一章，
    被丢的记 `budget`，入口为 `read_state domain=timeline`。
  - 专名（`glossary_section`）：每类超出条数记 `limit`，入口为 `read_state domain=glossary`；
    文本末尾补一句「还有 N 个专名未列出」，不再静默丢名字。
- `context.build_assembled_context` 把各分区的排除项挂回对应 `Partition`；
  索引分区的排除项由服务层在生成 `index_notes` 时收集后传入。

### 整请求规模

- `model_activity` 在 `model_request_started` 里增加 `request_size`：系统提示、消息序列与
  工具定义各自的应用侧 JSON 字符数，以及三项之和；字段带 `method=json_char_count` 与
  `estimated=true`，明确这是可复算的估算，不是供应商 token。
- 上游实际用量仍只在 `model_request_finished.usage` 出现：供应商没提供时保持 `null`，
  不拿 0 冒充「用量为零」；本项不触发任何额外摘要或压缩任务。

### 前端显示

- `TurnContextSummary` 读同一份 `state_injected`：分区行显示「未装入 N 条」，展开列出
  每条候选的 id、原因（预算不足／超出条数）与继续查看入口；摘要行给出未装入总数。
- 补齐写作档实际存在的分区名（已装模块、身份披露、人物状态、故事时间、未回收线索、
  专有名词）与「资料整理」用途标签，避免界面退回显示英文 key。
- 工具行补齐模块工具的中文动作：`query_records` 显示为「检索资料」、`read_record` 为
  「读取资料」，与文件读取区分；命中仍只表示找到，不代表读过原文。
- 本轮材料块继续按「必读／已提供全文／已提供部分原文（x/y）／已指定尚未读取」显示，
  与注入排除项一起构成选择、命中、已读、未覆盖四种状态。

### 资料文本不授权

- 新增 `tests/workspace/test_material_privilege.py`，覆盖四个通用入口：
  - 目录：`list_files` 不列灵感本文件；
  - 搜索：`search_text` 与 `query_materials` 都不返回灵感正文；
  - 读取：`read_file` 访问灵感路径返回 `PATH_NOT_MANAGED`；
  - 授权：灵感正文或设定文件里的「忽略所有之前的指令」不能当作作者引文登记范围
    （`TASK_SCOPE_EVIDENCE_INVALID`），没有范围时写工具仍返回
    `TASK_SCOPE_REQUIRED_CONFLICT`，目标文件指纹不变。
- 灵感专用读取／保存工具尚未实现，本项只验证现有通用入口不会旁路；专用工具的授权
  校验等工具本身落地后再补用例。

## 接口与数据

- 没有新增 HTTP 路由；`state_injected.parts[i]` 在原本的 `key`／`chars` 之外可能多一个
  `excluded` 数组。`model_request_started` 多一个 `request_size` 对象；前端未接这两个字段，
  现有读取不受影响。
- 排除项只描述「没给什么」，不产生读取授权；继续查看仍走原来的只读工具与 `read_state`。

## 取舍

- **分开记账而不是加预算**：条目被裁掉是设计的一部分，把预算调大只会推迟问题；记录原因
  让作者能判断是「本来没有」还是「这一轮没装下」。
- **只记定位，不塞正文**：排除项不带原文片段，避免事件日志变成第二份上下文；需要原文时
  按 `location` 重新读取，读取区间照常进入材料账本。
- **required 不进可选池**：作者明确指定的材料仍按分区必装／预读规则处理，不参与这里的
  条目裁减，因此不会被降级成 optional 或被半句截断。
- **文本与记录同源**：模型可见的「还有 N 条」与实际排除项来自同一次截断循环，不另算一遍。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 装配与分区专项 | `uv run pytest -q tests/workspace/test_assembly.py tests/workspace/test_context_budget.py tests/workspace/test_clues.py tests/workspace/test_character_states.py tests/workspace/test_timeline.py tests/workspace/test_glossary.py tests/workspace/test_chapter_index.py` | 76 passed |
| 请求记录专项 | `uv run pytest -q tests/workspace/test_activity.py tests/workspace/test_chat.py tests/workspace/test_chat_stream.py` | 73 passed |
| 资料越权专项 | `uv run pytest -q tests/workspace/test_material_privilege.py` | 2 passed |
| 静态检查 | `uv run ruff check src/hastings tests` | 通过 |
| 类型检查 | `uv run mypy` | 106 个源文件无问题 |
| 前端构建 | `npm --prefix frontend run build` | 通过（含 `tsc --noEmit` 与 vite build） |
| 前端单测 | `npm --prefix frontend test` | 18 个文件 161 passed |
| 后端全量 | `uv run pytest -q --basetemp data/test-tmp/f4d-rerun -o tmp_path_retention_count=100` | 768 passed，0 failures |

覆盖：分区摘要带排除项且无排除项时不出现该键；线索超预算记录 `budget`；人物状态只留
死亡角色时记录被裁者；故事时间超预算只留最近一章并记录更早锚点；专名超条数记录 `limit`；
索引超预算记录全部未装入章节与 `read_file` 入口；请求开始事件带可复算的字符规模，
没有上游用量时 `usage` 仍为 `null`；前端把同一份注入记录里的未装入条目逐条显示；
灵感文件与提示注入文本不能从目录、搜索、读取或范围登记里取得写权限。

## 未完成

- 整请求字符规模只进活动记录，对话界面还没有显示；用量仍要到活动详情查看。
- 条目排序仍是固定分区顺序，没有统一候选池与按任务相关性的重排。
- 任务卡（当前授权、约束、未读内容、执行事实）没有随本项实现。
