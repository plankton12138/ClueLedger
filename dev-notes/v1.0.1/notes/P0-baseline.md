# P0：基线、格式与调用清单

日期：2026-10-28。状态：**本阶段登记与格式冻结已完成，结构层已按冻结格式实现（P1 同步推进）**。

## 1. 基线

| 项目 | 值 |
|---|---|
| 起点提交 | `ea3088d322c4a23c8a551094e85920a76b9a926a`（`feat(v1.0): 线索工作流、水蓝交互与 1.0 验收材料`） |
| 主包 / 前端版本 | `1.0.0`（本版目标 `1.0.1`，按计划在 P7 统一升级） |
| 工作树 | 29 项未提交改动，见下节；本次未回退、未提交这些改动 |
| 运行平台 | Windows，Git Bash；后端 `uv run`，前端 `npx` |

### 未提交改动的归属

开始实施前工作树里已有他人（作者）的改动，本版不改写它们：

- 灵感展开与「用于写作」的移除：`frontend/src/state/conversations.ts`（-146）、`frontend/src/state/conversations.test.ts`（-172）、`frontend/src/components/ideas/IdeaEditor.tsx`（-53）、`frontend/src/components/ideas/IdeasSurface.tsx`、`frontend/e2e/frontend-experience.spec.ts`（-238）。
- 视觉与原型：`dev-notes/v0.8/artifacts/**` 截图、`product-design/visual-language.md`、`prototypes/wood-classical-20261028/`。
- 文档：`AGENTS.md`、`dev-notes/README.md`、`dev-notes/v1.0*`、`product-design/README.md`、`product-design/v1.0.1/*`。

这批改动与 1.0.1 的“取消故事资料、构思进入写作内视图”方向一致，因此保留；本版成果不把它们的 diff 计入。

### 基线检查

| 检查 | 结果 |
|---|---|
| `uv run ruff check src/hastings tests/workspace` | 通过 |
| `uv run mypy src/hastings` | 通过，104 个源文件 |
| `npx tsc --noEmit`（frontend） | 通过 |
| `uv run pytest -q` | **1367 passed**，801 秒，0 失败 0 错误（含 P1 新增用例；用例明细见 [P1 记录](P1-structure.md)）；结果见 `data/test-tmp/pytest-final.xml` |

1.0 遗留的两处静态检查问题（`test_release_recovery.py` 的 F401、E501）在基线上已修复，本版无需再处理。

以上检查与 P1 的全部用例都不调用模型：开发期间只用确定性测试，在线模型评测集中在 P6，避免消耗作者额度。

## 2. 1.0 剩余体验问题登记

来自[1.0 完成情况检查](../../v1.0/notes/completion-audit.md)，本版承接其中与 1.0.1 相关的问题：

| 编号 | 问题 | 1.0.1 处理 |
|---|---|---|
| AUDIT-01 | 参考文件的预读范围登记为“已提供”，但对应正文没有进入实际请求 | P3 装配“实际输入”时一并解决：只对真正进入请求的区间登记覆盖，并补首轮请求回归 |
| AUDIT-02 | 桌面冒烟失败 | 不在本版范围（桌面端已排除） |
| AUDIT-03 | 状态文档与实现不一致 | P7 统一：入口页、验收页、产品方案按实际状态改写 |
| 人工签收 | 1.0 未完成独立人工签收 | 不在本版范围，保留为 1.0 的历史未闭合项 |

另有 1.0 已记录的模型误报 1 次（线索核对），保留原记录，不在本版重算。

## 3. 冻结的格式

字段与语义的单一出处仍是[数据契约](../data-and-runtime.md)。P0 冻结的是**落地形式**，避免前后端各自解释：

| 内容 | 冻结结果 | 校验位置 |
|---|---|---|
| 结构对象 | `workspace/story_models.py` 的 pydantic 严格模型（`extra="forbid"`、`strict=True`） | 模型自身校验 + `validate_story_variant` |
| 结构变更 | `StoryChangeSet`：`id / base_revision / operations / prose_patches / read_dependencies / requested_scope / operation_id / generation` | `apply_change_set` |
| 错误消息 | `[错误码] 位置：现象。建议：…`，由 `WorkspaceError.message` 统一生成 | `errors.py` |
| 错误码 | `STORY_*`：`STORY_GRAPH_INVALID`(400)、`STORY_REVISION_CONFLICT`(409)、`STORY_REQUEST_CONFLICT`(409)、`STORY_CHANGE_CONFLICT`(409)、`STORY_VARIANT_NOT_FOUND`(404)、`STORY_CHANGE_NOT_FOUND`(404)、`STORY_STATE_FAILED`(500)、`STORY_WRITE_FAILED`(500) | `status_for_code` 尾缀规则，无需改映射表 |
| 提交 | 两阶段：`pending.json` → `pending.decided` → head → 快照 → 提交日志 | `story_store.py` |
| 撤销 | 提交时记录反向操作与“改动对象 → 提交后摘要”；撤销只恢复这些对象 | `story_changes.diff_variant` / `touched_objects` |

### 与契约文档的两处偏差（已确认更安全，记录在此）

1. **线程成员只存一份。** 契约写 `StoryThread.node_ids`，实现改由 `StoryNode.thread_ids` 推导（`StoryVariant.members()`）。两处都存会在编辑后分叉，而分支成员本来就由“这个节点属于哪些线”决定。
2. **修订快照按文档修订命名。** 契约要求保存不可变修订；一次提交可能同时改多个方案，因此快照文件名用文档修订（`story/revisions/<document.revision>.json`），方案修订只出现在变更记录与接口里。

## 4. 共享样例

样例放在 `tests/fixtures/story/`，由[契约测试](../../../tests/workspace/test_story_contracts.py)读取并校验，改动格式时样例先失败：

| 样例 | 覆盖 |
|---|---|
| `blank-mystery.json` | 无正文、无章节，真相与回收都未定 |
| `long-distance.json` | 第 1 个节点埋设、第 24 个节点回收，跨分支依赖与知情 |
| `flashback.json` | 倒叙：`reframe` 从后文指向前文，实际时间与揭示顺序不同 |
| `testimony.json` | 伪造证词：作者真相、角色误信、证词呈现三者分开 |
| `alternative.json` | 备选方案：完整基线、独立修订、不共享可变对象 |
| `emoji-positions.json` | 正文区间：emoji 与 CRLF 下的码点长度 |
| `change-set.json` | 一次包含多类操作的完整变更单元 |
| `error.json` | 统一错误消息与状态码 |

## 5. 模型调用清单

见[全部模型入口](P0-model-entries.md)：从每个界面动作反查到服务入口与模型阶段，并列出没有调用方的服务入口。

## 6. 旧资料域调用方

见[故事资料移除映射](P0-materials-removal-map.md)：每个旧文件、接口、工具与状态的实际调用方，以及它在 1.0.1 的替代归属。

## 7. 完成条件对照

| 条件 | 状态 |
|---|---|
| 每个按钮有服务能力 | 是；唯一例外是界面已移除但服务端仍在的灵感展开，已在清单中标为待删 |
| 每个服务入口有调用方 | 否，存在 4 个无调用方入口（灵感展开、章节索引、身份、状态），处理方式见清单 |
| 普通操作与模型调用可区分 | 是；清单按“不调用模型 / 调用模型”分栏 |
| 没有未归属的旧资料依赖 | 是；映射表逐项给出替代归属，P5 按表执行 |
