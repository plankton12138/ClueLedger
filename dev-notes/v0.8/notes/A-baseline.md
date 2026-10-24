# A：基础修正与回归基线

日期：2026-10-24。目标：在引入灵感本与全局模块库之前，先修掉会污染后续判断的确定性问题——
设定分区算错预算后整块消失、线索计划被截止章误挡，并把两者的边界固定成回归用例。

## 实际改动

### 装配按渲染尺寸计量

- `workspace/assembly.py`：`Partition` 增加 `size`；预算比较、`Skip.chars`、`state_injected`
  的 `parts[].chars` 统一按渲染后的字符数（含 `## 标题` 与空行）计算。
- 新增 `body_budget(title, budget)`：给自带长度限制的分区预留标题占位。
- 章节索引分区的字面量 `8_000` 提为 `INDEX_BUDGET`，`PROFILE_BUDGETS` 由常量相加得出。
- 注明 `PROFILE_BUDGETS` 是分区字符预算：写作档合计 44,900 字符，只用来解释这一轮装进多少
  材料；它不参与 token 统计，上游实际 token 由用量记录单独上报。

### 设定分区不再整块消失

`workspace/context.py` 重写 `settings_section`：

- 预算先扣掉分区标题，剩余部分才分配给正文；
- 二分求「两份设定都能容纳的同一上限」：短设定整段装入，长设定按同一上限截短，不会把
  预算全给其中一份；
- 截短时写明还剩多少字符、用 `read_file` 继续读；正文一份都放不下时保留标题与读取入口，
  不再返回空串导致整块被跳过；
- 线索、人物状态、时间线三个分区改用 `body_budget()` 生成正文，避免「正文刚好卡满预算、
  加上标题后整块跳过」。

### 线索计划与正文证据分开

`workspace/material_tasks.py`：

- 截止章只约束正文证据：`planted_chapter`、`resolution_chapter` 超出截止章仍然拒绝；
- `due_chapter` 是作者的回收计划，可以落在截止章之后——写到第 8 章登记「计划第 22 章回收」
  不再被拒；
- 「计划早于埋设」继续由 `publish_clues` 的 `_check_entry` 拒绝，那是自相矛盾的作者计划；
- `clues` 域的整理要求补一句：计划回收章可以晚于截止章，埋设与回收的原文必须在截止章以内。

## 接口与数据影响

- 没有新增或修改 HTTP 路由、事件类型与存储格式。
- `state_injected` 的 `parts[].chars`、`skipped[].chars` 由「正文字符」改为「渲染字符」；
  字段名与前端展示不变，前端本就把它们标成字符数。
- 作品文件、全局库与前端代码均未改动；运行版本仍为 `0.7.0`。

## 取舍

- 按渲染尺寸计量：继续按正文字符计量，等于让每个分区各自记住标题占多少，口径会继续分裂。
  改成渲染尺寸后，预算数字与实际提示词长度一致，代价是临界值要重算。
- 设定超预算时截短并保留读取入口：整块跳过的代价是模型既看不到内容、也不知道去读哪个
  文件；截短之后开头以外的部分需要再读一次原文。
- 两份设定按同一上限截短：单份多吃预算会让另一份几乎读不到，按同一上限分配更容易解释，
  也让「还剩多少」可比。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --basetemp data/test-tmp/a-stage2 -o tmp_path_retention_count=100` | 634 passed，0 failures |
| 静态检查 | `uv run ruff check src/hastings tests/workspace`、`uv run ruff format --check` | 通过 |
| 类型检查 | `uv run mypy src/hastings` | 53 个源文件无问题 |

新增用例：

- `tests/workspace/test_context_budget.py`（13 条）：两份设定各 10／2,900／3,000／3,500／
  20,000 字符时的渲染尺寸、是否截断、跳过原因；标题计入预算的临界情况；`used` 与事件摘要
  的口径；44,900 的构成。
- `tests/workspace/test_story_materials.py`（3 条）：第 1 章截止时登记第 22 章计划；
  第 2 章证据在第 1 章截止时被拒；计划早于埋设被拒。

证据：

- `artifacts/baseline-a.json`：修改前的提交 `8b910f9`、60 项工作树改动与关键文件指纹。
- `artifacts/a-budget-check.json`：五档设定长度的实测结果。两份 3,500 字符时渲染正好
  6,000 字符（预算 6,000），各装入 2,939 字符并写明还剩 561 字符，分区未被跳过。

## 未完成

- `state_section`、`glossary_section` 没有长度参数，超限时仍按整块跳过处理；这两类内容规模
  小，留到 F 阶段做检索与装配时统一。
- 只用合成作品验证，没有在线模型或长篇样例的语义结论。
