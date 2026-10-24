# F1：有界检索基础

日期：2026-10-24。目标：先建立可解释的检索入口，回答「这一轮装了哪些模块、正文有哪些章」，
再按显式 ID、人物关联、章区间与关键词返回有界候选；原文仍按版本与区间读取，不用摘要代替。

## 实际改动

### `workspace/retrieval.py`

- `catalog_brief`：L0 短目录。返回已装模块的 `binding_id`、`module_id`、`revision_id`、类型、
  条目数与正文短目录；章节分页，超出部分只给总数与下一页，不一次列出整本书。
- `MaterialQuery`：种类、模块范围、关键词、人物 ID、章路径、截止章、是否包含计划、游标与条数。
  模块范围只能取本轮装配的子集，传入未装模块只产生警告，不会去库里按名字找。
- `query_materials`：L1 查询。作者条目、统一章节索引、旧版章节概览、旧时间锚点、线索与名词
  各自带 `source` 与 `freshness`；过期投影、过期旧版记录不当作当前依据，只在 `warnings` 说明
  跳过了哪一章。
- 每条结果包含 `module_ref`（库／模块／修订／条目）、`matched_by`、章号与摘要长度；列表正文
  最多 240 字符，整页最多 6000 字符。L2 仍走 `ModuleLibrary.read_entry` 与作品 `read_file`，
  由它们返回实际区间与游标，检索模块不复制读取凭据。
- 关键词先查标题、别名、字段与模块名；不命中时再扫正文，单次最多 300 条、20 万字符，超限
  明确写「只扫了部分条目」，不静默漏项。
- 分页游标绑定查询指纹；条件变化后复用旧游标返回 `RETRIEVAL_CURSOR_STALE_CONFLICT`。

## 接口与数据

- 本轮没有新增 HTTP 路由与前端入口；检索还只是服务层能力，尚未接入写作轮次与资料页。
- `MaterialHit.source`：`author`（库内作者内容）、`observed`（本作品观察）、
  `legacy_unverified`（旧版概览／时间锚点，只读兼容）。
- `scope` 随查询返回本轮实际查过的模块与章节；空结果也能说明搜过什么，不返回一个无从判断的 `[]`。

## 取舍

- **正文关键词扫描有上限**：它只用于定位，不宣称读过全文；超限时给出可操作的缩小范围提示。
  不引入向量库，排序仍由显式 ID、关联、章区间、模块与关键词决定。
- **过期观察不参与查询**：作者视图仍能看到「依据已改动」，但查询不会把旧摘要当成当前事实。
- **计划按模块类型排除**：`include_plans=false` 跳过 `clues`／`plot` 模块，以及带
  `basis=plan` 的条目；这不是完整的计划时点模型，后续随模块检索细化。
- **不递归**：当前只读装配内实例；简单关联的一跳扩展留到装配冻结与任务权限接好之后。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 检索用例 | `uv run pytest -q tests/workspace/test_retrieval.py` | 8 passed |
| 静态检查 | `uv run ruff check src/hastings tests` | 通过 |
| 类型检查 | `uv run mypy src/hastings` | 69 个源文件无问题 |
| 后端全量 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --basetemp data/test-tmp/f1-full -o tmp_path_retention_count=100` | 753 passed，0 failures |

覆盖：未装模块不出现、同名人物跨模块不混、旧版概览来源与失效、证词保留表达方式与人物过滤、
计划可排除、游标绑定查询、长条目正文命中与详情完整、L0 模块与章节概览。

## 未完成

- 检索未接入 `service.py`／写作 Agent／HTTP／资料页；受理时的装配冻结、`task_scope` 的
  module／assembly 项、required 与 optional 预算都还没有。
- 简单关联的一跳扩展、未装模块在目录／详情／依赖展开的一致排除、旧 `read_state` 与新查询的
  职责划分，属后续 F2／F3。
- 披露与人物状态两个观察域尚未进入检索；它们目前仍只按装配注入写作上下文。
- 灵感检索（`search_ideas`／`read_idea`／`save_idea`／`link_idea_use`）未实现。
- 没有记录整请求字符规模与实际 token；也没有前端「找到／已读／未覆盖」区分。
