# P4：Agent 创作与修改闭环

本文件保留分批实施和验证记录；最新实现、已关闭事项及验收结果以[收尾记录](P7-implementation-and-acceptance.md)为准。
日期：2026-10-29。状态：**结构工具、核对要求与对象级授权范围已实施；采用（exact 与 assist）在 [P5 记录](P5-story-api.md) 中闭合**。

2026-10-30 补充：构思页任务目标、候选确认与按收据判定完成已接入，见[构思创作路由修复](2026-10-30-story-creation-routing-fixes.md)。讨论轮不开放写入，结构任务不开放模块内容写入，空白方案会实际发送可用的结构语境。

## 1. 本阶段做了什么

| 计划项 | 落地 |
|---|---|
| 结构读取、原文查询、变更提议与提交工具；授权由应用持有 | `story_tools.py`：`read_story`（精确 ID 与修订）、`update_story`（提交语义变更）。基础修订、`operation_id` 与代次由应用填，模型只描述改什么 |
| 结构提交过代次资格 | `StoryStore.commit_change(require_eligible=…)`：在提交决定处再查一次；查重在前，已经提交过的同一请求不会因暂停而丢收据 |
| 工具只在有冻结方案时开放 | `prepare_request_inputs` 按 `RunContext.story` 追加工具；只读用途与讨论轮不给 `update_story` |
| 候选正文核对必需呈现、保留信息、角色知情、作者真相与必要前提 | `story_review.py`：由结构生成固定要求（呈现、真相边界、知情、前提），程序能判断的（呈现缺失、真相被直接写出）给出位置与原文；判断不了的写成语义要求，不伪装成已通过 |
| 核对要求进入实际核对阶段 | `_review_after_write` 把要求写进核对系统提示，并落 `story_review_requirements` 事件；模型只能逐条覆盖，不能改要求 |

已做的边界：

- `update_story` 在暂停后不可写：进入工具先查一次资格，提交决定处再查一次。
- 无效变更（字段缺失、ID 不存在、关系成环）返回带错误码的失败，不写任何文件。
- 结构提交失败不会留下「已成功」的回执；成功时返回新修订与改动对象。
- 核对阶段的位置以服务端规范化后的字符区间给出，报告可以直接引用。

## 2. 关键取舍

**模型不填元数据。** 基础修订、操作标识与代次都由应用补齐。模型如果自己写这些字段，既能绕过版本冲突，也能在暂停后伪造一次提交。

**查重先于资格检查。** 已经提交过的同一请求再查一次资格会报「已暂停」，让作者以为没保存。收据比资格更早成立，所以先返回收据。

**程序只判断能判断的。** 「必需呈现是否出现」「作者真相是否被直接写出」可以定位到字符区间；「角色是否说漏」「前提是否被违背」需要读懂上下文，写成语义要求交给核对阶段，并在要求里标明「要判断」。不把语义问题伪装成程序结论。

**结构变更的范围来自作者本轮的话与界面选择。** `story_authorization.py` 把界面冻结的节点范围与作者消息里出现的对象名称合起来，再将直接依存的对象（所属分支、使用线索、正文关联、两端都在范围内的关系、指向这些节点的约定）纳入范围。范围外的已有对象只能读，不能改、不能删；新建对象属于作者要求的创作，允许提交。作者消息里写「把所有线索都改掉」不会扩大范围。文件写入仍沿用逐路径的 scope 控制与读取前提。

## 3. 代码位置

| 文件 | 职责 |
|---|---|
| `src/hastings/workspace/story_tools.py` | `read_story`、`update_story`；代次资格与提交元数据 |
| `src/hastings/workspace/story_review.py` | 核对要求生成、呈现与真相的程序检查、语义要求 |
| `src/hastings/workspace/story_store.py` | 提交前的 `require_eligible`（锁内） |
| `src/hastings/workspace/agent.py` | `RunContext.story / story_guard / operation_id / generation`；按冻结方案追加工具 |
| `src/hastings/workspace/service.py` | 注入守卫与代次；核对阶段生成结构要求并落事件 |
| `tests/workspace/test_story_tools.py` | 7 个用例：读取、提交元数据、暂停不可写、只读无提交工具、非法变更、重试返回收据、一轮真实工具调用改结构 |
| `tests/workspace/test_story_review.py` | 5 个用例：要求生成、真相位置、无问题时为空、空选择、服务层按冻结结构生成 |

## 4. 验证

没有调用在线模型。

| 检查 | 结果 |
|---|---|
| `uv run pytest tests/workspace/test_story_tools.py tests/workspace/test_story_review.py -q` | 12 passed |
| `uv run pytest -q`（全量） | **1416 passed**，379 秒，0 失败 0 错误 |
| `uv run ruff check src/hastings tests` | 通过 |
| `uv run mypy` | 通过，145 个源文件 |

### 覆盖到的边界

| 情形 | 用例 |
|---|---|
| 模型通过一轮工具调用真的改掉结构，事件里有成功记录 | `test_turn_updates_structure_through_the_tool` |
| 暂停之后旧代次调工具拿不到写入 | `test_paused_generation_cannot_commit` |
| 提交带的是应用的操作与代次 | `test_update_story_commits_with_app_metadata` |
| 已提交过的同一请求重试返回收据，不被资格检查挡掉 | `test_commit_checks_eligibility_but_not_on_retry` |
| 非法变更报错且不写文件 | `test_invalid_operations_are_reported_without_writing` |
| 正文直接写出作者真相时报 blocking 并给出位置 | `test_written_truth_is_a_blocking_finding_with_position` |
| 核对要求来自冻结结构，重复生成一致 | `test_service_review_block_is_built_from_the_frozen_structure` |

## 5. 尚未完成（留给后续）

- **候选正文的自动修正循环**：已接入，见[候选流程记录](P4-candidate-workflow.md)。
- **方案采用与局部采用**：已在 [P5 采用记录](P5-story-api.md) 中接通 `exact / assist` 两条路径。
- **正文与结构成组提交**：候选可登记结构变更，核对后与正文一起提交；见[候选流程记录](P4-candidate-workflow.md)。
- **对象级授权的剩余边界**：范围收口已接入；已有线索的作者真相仍拒绝由模型改写或删除后重建，作者明确要求时同样拒绝，这是当时的暂时限制。该限制已由[授权与采用修复](2026-10-30-authorization-adoption-fixes.md)关闭：作者直接命令可以改真相，引文、解释与讨论仍拒绝。
- **分叉、汇合、备选的完整对话闭环**：工具支持这些操作，但没有一组端到端用例覆盖「一句话生成分支与汇合」。
- **界面**：构思页已在 [P5](P5-story-api.md) 接通。

## 6. 下一阶段条件

P5 需要的接口已经就位：结构读写工具、方案与修订身份、核对要求、操作控制与事件。P5 要补的是页面：构思／正文切换、就近详情、方案差异与采用、正文定位，以及故事资料页与旧接口的退出。
