# P3：长期连续性与实际输入

本文件保留分批实施和验证记录；最新实现、已关闭事项及验收结果以[收尾记录](P7-implementation-and-acceptance.md)为准。
日期：2026-10-29。状态：**已完成。结构按依赖闭包装配，实际输入有记录；试读有独立执行入口、固定问题与空工具范围；超容量任务按节点拆组，单节点仍放不下时停下依赖它的节点**。

## 1. 本阶段做了什么

| 计划项 | 落地 |
|---|---|
| 按任务、方案、位置、约定与依赖装配必需内容 | `workspace/story_context.py`：按选中节点求线索、回收目标、既有呈现、约定与角色知情的闭包；必需项先装，可选项目标后装 |
| 分开作者真相、计划、正文观察、证词与模型判断 | 分区各自带说明：`truth / plan / prerequisites / constraints / knowledge / prose / testimony / judgement`，并写明每种内容不能当成什么 |
| 记录实际发送内容、版本、覆盖与省略理由 | `ContextSnapshot.story` 冻结方案与修订；执行时复核，变了就整轮失败；`story_context` 事件记录覆盖节点、线索、约定、分区大小、省略原因与拆组结果；操作上下文接口一并返回 |
| 换会话、压缩后重装当前结构 | 结构每轮从 `StoryStore` 按冻结身份重新装配，不依赖会话里贴过的内容；用例覆盖「新会话拿到同一份必需结构」 |
| 试读独立历史、工具范围与固定问题 | 新增 `POST /reader-previews` 与 `TurnService.accept_reader`：`origin=reader` 的独立会话、固定读者问题、拒绝作者自由提问与自选材料；执行时只装读者前缀，不带工具、目录、摘要与作者历史，发送前再次自检 |
| 超容量拆分任务；最小单元仍放不下则停止依赖部分 | `split_story_request` 按节点贪心拆组，只装第 1 组并把本轮范围写进请求；单个节点必需内容仍超出容量时，连同依赖它的后继一起停下，全部装不下则报 `STORY_CONTEXT_INCOMPLETE` |

请求侧新增 `context.story`（`variant_id / revision / node_ids / purpose / reader_cutoff`）。客户端只表达意图，写权限仍由受理流程决定。

## 2. 关键取舍

**同一份结构按认知身份拆开，不合成一份「设定」。** 作者的真相、角色的说法、读者的观察与作者的创作目标混在一起，模型就会拿真相去写角色的猜测。分栏之后，每栏都写明「这不是事实」或「这不等于读者已知」。

**不用偶然的拓扑序当成作者的顺序。** 分叉结构没有全序，装配只回答「能不能到达」：角色知情取选中节点的前驱闭包，后面的变化不会注入前章；与当前节点不可比的依赖单独标注，而不是按数组顺序猜。

**必需项不因预算被悄悄省略。** 结构分区在装配层是 `required`，超出预算先拆组；拆不动的节点明确停下并写进本轮范围，不截断、不假装整轮完整。

**试读是另一条执行路径，不是写作请求加个开关。** 读者请求有独立会话、固定问题、空工具清单、单回合与空历史；系统提示只由读者说明和截止前缀组成。这样即使写作侧以后新增工具或目录分区，也不会漏进试读。问题由应用固定，是因为提问本身常写着答案（「管家是不是凶手？」）。

**拆组的结果要同时告诉模型和记录。** 只装第 1 组却在提示里装作整轮，模型会写完一部分并声称完成。拆组说明进入实际请求，`story_context` 事件的 `split` 字段记录分组与停下节点，界面以后直接读这份事实。

**读者阶段不打印线索标题。** 标题经常直接写着答案（「管家是凶手的手套」）。试读稿只给呈现文本与说话人，标题留给作者侧。

## 3. 代码位置

| 文件 | 职责 |
|---|---|
| `src/hastings/workspace/story_context.py` | 冻结与复核、依赖闭包、认知分栏、容量拆组、试读前缀与泄漏自检、读者说明与固定问题 |
| `src/hastings/workspace/materials.py` | `ContextRequest.story`、`ContextSnapshot.story`、受理时冻结、请求键身份 |
| `src/hastings/workspace/context.py` | `story` 分区（`required`），只在写作与核对用途装入 |
| `src/hastings/workspace/service.py` | 运行前复核修订；拆组时只装第 1 组并写 `story_context`；试读独立执行路径（无工具、无历史、固定问题、发送前自检）；`accept_reader` |
| `src/hastings/workspace/api.py` | `context.story` 请求模型；`POST /reader-previews`；写作对话列表不列 `origin=reader` 的会话 |
| `src/hastings/workspace/operation_api.py` | 查看本次依据时一并返回结构注入 |
| `tests/workspace/test_story_context.py` | 17 个用例：长距离回收、未来不注入、备选隔离、知情位置、容量与拆组、试读隔离与执行入口、修订冲突、接线到实际请求 |
| `tests/workspace/test_story_api.py` | 试读接口：独立会话、写入列表隐藏、空工具清单与实际返回 |

## 4. 验证

不调用在线模型：全部用受控 provider 与真实结构文件。

| 检查 | 结果 |
|---|---|
| `pytest tests/workspace/test_story_context.py -q` | 17 passed |
| `pytest tests/workspace/test_story_api.py tests/workspace/test_story_context.py -q` | 25 passed |
| `pytest tests/workspace/test_api.py tests/workspace/test_chat.py tests/workspace/test_operation_api.py tests/workspace/test_operation_control.py tests/workspace/test_recovery_boundaries.py tests/workspace/test_context_boundaries.py tests/workspace/test_author_questions.py -q` | 130 passed |
| `pytest -q`（全量） | **1406 passed**，357 秒，0 失败 0 错误 |
| `ruff check src/hastings tests` | 通过 |
| `mypy src/hastings` | 通过，111 个源文件 |

### 覆盖到的边界

| 情形 | 用例 |
|---|---|
| 第 1 个节点埋的线索，在第 24 个节点回收时进入必需内容 | `test_long_distance_clue_reaches_required_content` |
| 后面的节点动作不进入当前位置的输入 | `test_future_nodes_are_not_injected` |
| 备选方案的内容不串进主方案 | `test_other_variant_is_not_injected` |
| 角色知情只取前驱变化 | `test_knowledge_uses_only_earlier_changes` |
| 必需内容超容量时停止而不是截断 | `test_capacity_refuses_to_truncate_required_content` |
| 拆组后每组单独放得下；单节点装不下时停下依赖它的节点 | `test_split_groups_fitting_nodes_and_stops_dependents` |
| 超容量的真实轮次只装第 1 组，长真相不进入请求，事件记录拆组 | `test_over_capacity_turn_sends_first_batch_and_records_split` |
| 试读不含真相、未来节点与线索标题，自检能发现泄漏 | `test_reader_preview_hides_truth_and_future_nodes` |
| 试读没有工具、历史与目录，问题固定、来源独立 | `test_reader_preview_runs_without_tools_history_or_catalog` |
| 试读拒绝作者自由提问与自选材料 | `test_reader_preview_rejects_free_question_and_author_materials` |
| 试读接口建独立会话，且不出现在写作列表 | `test_reader_preview_route_keeps_its_own_conversation` |
| 修订变化后不按旧结构继续 | `test_frozen_revision_must_still_match` |
| 新会话不依赖历史也能拿到同一份结构 | `test_new_conversation_gets_the_same_structure_without_history` |
| 实际系统提示里确实有必需结构 | `test_turn_sends_required_structure_to_the_model` |

## 5. 尚未完成（留给后续）

- **失效粒度**：结构任何变化都会让上一轮的核对过期；按「只有被改到的对象才失效」需要变更与作用域的对应关系，随 P4 的核对工具一起做。
- **正文与结构的联合读**：当前结构分区只给正文观察（引文与观察），正文原文仍由模型按需 `read_file`。哪些节点必须先读原文、读多少，属于 P4 的阅读范围控制。
- **拆组后的继续入口**：现在只把「第 1 组 / 其余组」写进事件与请求，作者侧还没有「继续下一组」的按钮；P5 构思页接入试读与拆组时会一起补。
- **试读结果展示**：接口与执行已就位，页面还没有读取读者会话的入口；读者会话目前只在写入列表里隐藏。
- **压缩摘要与结构的交接**：摘要只整理讨论；结构每轮重装已经覆盖「压缩后不丢结构」，但摘要里如果写了与结构冲突的安排，仍以结构为准——这条规则要在 P4 的写前提示里显式写出来。

## 6. 下一阶段条件

P4 需要的输入已就位：冻结的方案与修订、按位置求出的知情与前提、分栏后的认知边界、可复核的实际输入记录、可单独发起的试读请求。P4 要补的是工具侧：读取结构、提议变更、候选正文与核对，并让「明确修改」在授权范围内直接执行。
