# P3：长期连续性与实际输入

日期：2026-10-29。状态：**结构装配与试读隔离已实施并接入请求；试读的执行入口尚未接通**。

## 1. 本阶段做了什么

| 计划项 | 落地 |
|---|---|
| 按任务、方案、位置、约定与依赖装配必需内容 | `workspace/story_context.py`：按选中节点求线索、回收目标、既有呈现、约定与角色知情的闭包；必需项先装，可选项目标后装 |
| 分开作者真相、计划、正文观察、证词与模型判断 | 五个分区各自带说明：`truth / plan / prerequisites / constraints / knowledge / prose / testimony / judgement`，并写明每种内容不能当成什么 |
| 记录实际发送内容、版本、覆盖与省略理由 | `ContextSnapshot.story` 冻结方案与修订；执行时复核，变了就整轮失败；`story_context` 事件记录覆盖节点、线索、约定、分区大小与省略原因；操作上下文接口一并返回 |
| 换会话、压缩后重装当前结构 | 结构每轮从 `StoryStore` 按冻结身份重新装配，不依赖会话里贴过的内容；用例覆盖「新会话拿到同一份必需结构」 |
| 试读独立范围，过滤标题、摘要、缓存与问题 | `assemble_reader_input` 只给截止节点的前驱呈现稿，不含真相、计划、约定与核对；`assert_reader_isolated` 用隐藏字符串自检；问题由应用固定，不接收作者自由提问 |
| 超容量拆分任务；最小单元仍放不下则停止 | 必需项超过容量抛 `STORY_CONTEXT_INCOMPLETE`（含需要多少、可用多少），不截断；可选项目标记入省略理由 |

请求侧新增 `context.story`（`variant_id / revision / node_ids / purpose / reader_cutoff`）。客户端只表达意图，写权限仍由受理流程决定。

## 2. 关键取舍

**同一份结构按认知身份拆开，不合成一份「设定」。** 作者的真相、角色的说法、读者的观察与作者的创作目标混在一起，模型就会拿真相去写角色的猜测。分栏之后，每栏都写明「这不是事实」或「这不等于读者已知」。

**不用偶然的拓扑序当成作者的顺序。** 分叉结构没有全序，装配只回答「能不能到达」：角色知情取选中节点的前驱闭包，后面的变化不会注入前章；与当前节点不可比的依赖单独标注，而不是按数组顺序猜。

**必需项不因预算被悄悄省略。** 结构分区在装配层是 `required`，超出预算直接报缺口；`assemble_story_input` 先装必需项，可选的「创作目标」装不下就记省略原因。两条路径都给出可操作的下一步：减少节点或拆分任务。

**试读在装配层完成隔离，但执行入口先拒绝。** 读者阶段需要独立请求、无工具、无目录与摘要；这些还没接通，所以 `purpose=reader_preview` 在运行前直接报「未接通」，不发送一个带着写作工具与目录的请求假装试读。隔离函数与自检已经可用，页面阶段接上执行入口即可。

**读者阶段不打印线索标题。** 标题经常直接写着答案（「管家是凶手的手套」）。试读稿只给呈现文本与说话人，标题留给作者侧。

## 3. 代码位置

| 文件 | 职责 |
|---|---|
| `src/hastings/workspace/story_context.py` | 冻结与复核、依赖闭包、认知分栏、容量、试读前缀与泄漏自检 |
| `src/hastings/workspace/materials.py` | `ContextRequest.story`、`ContextSnapshot.story`、受理时冻结、请求键身份 |
| `src/hastings/workspace/context.py` | 新增 `story` 分区（`required`），只在写作与核对用途装入 |
| `src/hastings/workspace/service.py` | 运行前复核修订、装配结构、写 `story_context` 事件；试读未接通时直接失败 |
| `src/hastings/workspace/api.py` | `context.story` 的请求模型 |
| `src/hastings/workspace/operation_api.py` | 查看本次依据时一并返回结构注入 |
| `tests/workspace/test_story_context.py` | 13 个用例：长距离回收、未来不注入、备选隔离、知情位置、容量、试读隔离、修订冲突、接线到实际请求 |

## 4. 验证

不调用在线模型：全部用受控 provider 与真实结构文件。

| 检查 | 结果 |
|---|---|
| `uv run pytest tests/workspace/test_story_context.py -q` | 13 passed |
| `uv run pytest -q`（全量） | **1404 passed**，465 秒，0 失败 0 错误 |
| `uv run ruff check src/hastings tests` | 通过 |
| `uv run mypy` | 通过，107 个源文件 |

### 覆盖到的边界

| 情形 | 用例 |
|---|---|
| 第 1 个节点埋的线索，在第 24 个节点回收时进入必需内容 | `test_long_distance_clue_reaches_required_content` |
| 后面的节点动作不进入当前位置的输入 | `test_future_nodes_are_not_injected` |
| 备选方案的内容不串进主方案 | `test_other_variant_is_not_injected` |
| 角色知情只取前驱变化 | `test_knowledge_uses_only_earlier_changes` |
| 必需内容超容量时停止而不是截断 | `test_capacity_refuses_to_truncate_required_content` |
| 试读不含真相、未来节点与线索标题，自检能发现泄漏 | `test_reader_preview_hides_truth_and_future_nodes` |
| 修订变化后不按旧结构继续 | `test_frozen_revision_must_still_match` |
| 新会话不依赖历史也能拿到同一份结构 | `test_new_conversation_gets_the_same_structure_without_history` |
| 实际系统提示里确实有必需结构 | `test_turn_sends_required_structure_to_the_model` |

## 5. 尚未完成（留给后续）

- **试读执行入口**：装配与自检已就绪，但执行仍是「拒绝」。接通时要一起做：无工具、无目录与摘要、固定问题、独立历史，以及把实际发送的文本再过一次 `assert_reader_isolated`。
- **失效粒度**：结构任何变化都会让上一轮的核对过期；按「只有被改到的对象才失效」需要变更与作用域的对应关系，随 P4 的核对工具一起做。
- **正文与结构的联合读**：当前结构分区只给正文观察（引文与观察），正文原文仍由模型按需 `read_file`。哪些节点必须先读原文、读多少，属于 P4 的阅读范围控制。
- **接口与页面**：`context.story` 已可用，但还没有界面发起；P5 的构思页会把所选节点与用途接上。
- **压缩摘要与结构的交接**：摘要只整理讨论；结构每轮重装已经覆盖「压缩后不丢结构」，但摘要里如果写了与结构冲突的安排，仍以结构为准——这条规则要在 P4 的写前提示里显式写出来。

## 6. 下一阶段条件

P4 需要的输入已就位：冻结的方案与修订、按位置求出的知情与前提、分栏后的认知边界、可复核的实际输入记录。P4 要补的是工具侧：读取结构、提议变更、候选正文与核对，并让「明确修改」在授权范围内直接执行。
