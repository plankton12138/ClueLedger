# P4：Agent 创作与修改闭环

日期：2026-10-29。状态：**结构工具与核对要求已实施；候选正文的自动修正循环与方案采用尚未完成**。

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

**结构变更的范围只到方案与用途。** 文件写入有逐路径的 scope 控制与读取前提；结构还没有细到「只能改选中节点」的授权模型。当前边界是：这一轮受理时带着哪个方案、用途是不是修改；工具描述要求模型只改作者提到的对象。细化到对象级授权需要变更与作用域对应关系，和失效粒度一起做。

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

- **候选正文的自动修正循环**：现在只有作者明确要求检查时进入核对阶段；「一次局部修正 + 一次复核，仍有关键问题保留草稿并提问」还没有实现。
- **方案采用与局部采用**：`exact / assist` 两种路径、差异计算与跨边界依赖都未做。
- **正文与结构成组提交**：结构提交与 Git 记录仍是两条（P1 有意分开），联合提交的协调器没有接。
- **对象级授权**：如上文所述，结构变更目前只细分到方案与用途。
- **分叉、汇合、备选的完整对话闭环**：工具支持这些操作，但没有一组端到端用例覆盖「一句话生成分支与汇合」。
- **界面**：构思页在 P5；当前只有接口与工具，没有页面入口。

## 6. 下一阶段条件

P5 需要的接口已经就位：结构读写工具、方案与修订身份、核对要求、操作控制与事件。P5 要补的是页面：构思／正文切换、就近详情、方案差异与采用、正文定位，以及故事资料页与旧接口的退出。
