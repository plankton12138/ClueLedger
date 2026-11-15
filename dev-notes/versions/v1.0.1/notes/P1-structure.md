# P1：结构与一致性基础

日期：2026-10-28。状态：**结构层已实施，提交协调与撤销可用；正文事务与暂停竞态留待 P2/P4**。

## 1. 本阶段做了什么

| 计划项 | 落地 |
|---|---|
| 新格式初始化、稳定作品身份、方案修订和备选隔离 | `story_models.StoryDocument` + `story_store`：首次提交创建 `main` 方案；作品身份 `workspace_lineage` 在首次写入时生成；备选整份复制并记录 `base_variant_id / base_revision` |
| 节点、线索、关系、知情、约定和正文关联；允许合法未定 | `StoryNode / Clue / StoryEdge / KnowledgeChange / Constraint / ProseBinding`，`author_truth=None` 与 `resolution.intent="undecided"` 是一等状态 |
| 引用与范围校验，推进／实际时间无环校验，重新解释可指回前文 | `StoryVariant` 的引用校验 + `validate_story_variant`；`next` 单独做无环，`reframe/answer` 允许从后文指向前文；`event_times` 独立无环 |
| 工作副本、提交日志、请求去重、故障恢复、失效与撤销 | `commit_change`（幂等收据）、两阶段提交（`pending.json` → `pending.decided` → head → 快照 → `commits.jsonl`）、`undo`（反向操作 + 改动对象摘要） |
| 分离应用提交与 Git 补记 | 结构提交只写状态目录；Git 记录仍由写作轮次在文件改动后处理，两者不共用事务 |

同时把线索账本与构思结构共用的锁与原子写抽成 `workspace/atomic_io.py`，线索账本改为复用（行为不变，23 个用例全绿）。

## 2. 关键取舍

**结构提交与正文提交分开。** 本阶段只保证结构文件的可见性；正文仍是文本文件，两者的联合提交要等 P4 的工作副本与提交协调器。现在假装“结构+正文一次原子替换”会给人一个并不存在的保证。

**删除节点连带关系，不连带作者内容。** 删节点会自动删掉它的边与实际时间关系（这些只是连接），但线索回收、知情变化、正文关联仍指向它时直接报错，要求在同一个变更里一并处理。理由：关系可以重建，作者写下的判断不能静默丢弃。

**撤销按对象而不是按整本。** 提交时记录反向操作与「改动对象 → 提交后摘要」；撤销前逐项重查，只有全部未变才执行。任一对象被后来的编辑改过就返回 409 并列出这些目标，其余无关编辑原样保留。

**修订快照按文档修订命名。** 一次提交可能同时改多个方案（采用、联动修改），快照存整份文档；方案修订只出现在变更记录与接口里。

**线程成员只存一份。** 由 `StoryNode.thread_ids` 推导，`StoryVariant.members(thread_id)` 提供查询。两处都存会在编辑后分叉。

## 3. 代码位置

| 文件 | 职责 |
|---|---|
| `src/hastings/workspace/story_models.py` | 结构对象、操作、变更单元、文档与回执；引用校验 |
| `src/hastings/workspace/story_changes.py` | 应用变更、按依赖顺序生成反向操作、对象摘要与改动范围 |
| `src/hastings/workspace/story_store.py` | 读取、两阶段提交、恢复、备选创建、撤销 |
| `src/hastings/workspace/story_projection.py` | 正文关联与核对的失效判定、当前构思概览；只读，不调模型 |
| `src/hastings/workspace/atomic_io.py` | 跨进程锁、原子写（从线索账本提取，两处共用） |
| `tests/workspace/test_story_store.py` | 22 个用例：空白结构、两类无环、隔离、幂等、崩溃恢复、撤销、级联、跨进程并发 |
| `tests/workspace/test_story_contracts.py` | 13 个用例：共享样例与契约一致 |
| `tests/workspace/test_story_projection.py` | 8 个用例：emoji／CRLF 定位、正文变化、文件缺失、核对过期 |

## 4. 验证

本阶段没有调用任何在线模型：全部用例用受控输入与故障注入，`.env` 未参与。按作者要求，在线实测统一放到全部实现完成后的 P6，避免消耗额度。

| 检查 | 结果 |
|---|---|
| `uv run pytest tests/workspace/test_story_store.py tests/workspace/test_story_contracts.py tests/workspace/test_story_projection.py -q` | 43 passed（全量运行之后又删掉 1 个只断言 `issubclass` 的空用例，该文件复跑 22 passed） |
| `uv run pytest tests/workspace/test_clue_ledger.py -q` | 23 passed（`atomic_io` 提取后行为不变） |
| `uv run ruff check src/hastings/workspace` | 通过 |
| `uv run mypy src/hastings` | 通过，104 个源文件 |
| `uv run pytest -q`（全量） | **1367 passed**，801 秒，0 失败 0 错误 |

### 崩溃注入覆盖

| 注入位置 | 期望 | 用例 |
|---|---|---|
| 写完 pending、未写决定 | 回到原状态，pending 清理 | `test_pending_without_decision_returns_to_original_state` |
| 已写决定、head 未写 | 下次读取补完 head、快照与日志 | `test_decided_commit_is_completed_after_head_write_failure` |
| head 已写、快照与日志失败 | 下次读取补快照与日志，不重复提交 | `test_snapshot_and_log_failure_is_repaired_on_next_read` |
| head 损坏 | 报 `STORY_STATE_FAILED`，不改写文件 | `test_corrupt_structure_is_reported_instead_of_reset` |

## 5. 尚未完成（留给后续阶段）

- **暂停竞态**：结构提交还没有代次资格检查，P2 接入操作代次后补 `D08` 的提交前后用例。
- **正文与结构联合提交**：`prose_patches` 只做形状校验，不写文件；P4 由提交协调器接管。
- **采用方案**：`create_variant` 只复制基线；整案／片段采用需要差异计算与语义整理，放在 P4—P5。
- **容量与拆分**：`STORY_CONTEXT_INCOMPLETE` 与拆分属于 P3。
- **失效粒度**：核对只要结构有任何变化就判过期，是保守做法。按「只有被改到的对象才失效」需要变更与作用域的对应关系，P3 装配实际输入时再做，避免现在给出一个看似精确、实际漏报的结论。
- **历史体积**：`story.json` 保存全部变更记录，`revisions/` 每次提交一份完整快照，长作品会持续变大。清理策略（保留最近 N 次、旧快照归档）与查看改动的分页一起在 P5 定。

## 6. 下一阶段条件

P2 开始前需要的输入已就绪：`StoryChangeSet` 的 `operation_id / generation` 字段、`StoryReceipt` 的 `request_key / change_id`、`StoryStore.commit_change` 的幂等入口。P2 需要补的是：操作存储、代次资格、事件契约与前端控件。
