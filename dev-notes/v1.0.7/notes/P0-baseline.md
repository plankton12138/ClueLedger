# P0 基线核对与范围冻结

日期：2026-11-01。代码版本：`d1f8238 feat(v1.0.6): 段落级正文锚点与正文出处标注`，主包与前端均为 `1.0.6`。

## 1. 工作树

开工前跟踪文件有三处修改：`dev-notes/README.md`、`product-design/README.md`（本版文档入口）和 `src/hastings/workspace/story_models.py`。第三处是一次未完成的枚举替换：新增 `ClueType = red_herring / truth_clue` 与 `OldClueRole`，但 `ClueUse.roles` 仍标注旧名 `ClueRole`。该文件导入可用、首次实例化即失败：

```
pydantic.errors.PydanticUserError: `StoryNode` is not fully defined;
you should define `ClueRole`, then call `StoryNode.model_rebuild()`.
```

失败影响 `test_story_store.py` 等 123 个用例。该改动的命名（`red_herring / truth_clue`）与保留旧角色别名的做法都与冻结契约相反，本阶段把它退回 HEAD 内容，按 `truth / mislead` 重新实现。工作树其余未跟踪文件（`temp-design-preview.html`、`temp-simplified-architecture.md`、根目录包文件、`release/`、`dev-notes/v1.0.6/anchor-model.html`）原样保留。

## 2. 定向基线

```
CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q tests/workspace/test_story_sync.py \
  tests/workspace/test_story_store.py tests/workspace/test_story_contracts.py \
  tests/workspace/test_story_joint_commit.py tests/workspace/test_clue_marks.py \
  tests/workspace/test_story_api.py tests/workspace/test_story_candidate.py \
  --basetemp data/test-tmp/v107-p0c -o tmp_path_retention_count=50
```

结果：139 passed，72 秒。退回枚举改动后无既有失败，本版改动都以这组用例为回归基线。

## 3. 冻结的设计决定

| 事项 | 决定 | 依据 |
|---|---|---|
| 线索用途枚举 | `ClueKind = Literal["truth", "mislead"]`，空值表示未定 | 接口、存储、界面共用一个词；不保留 `truth_clue`、`red_herring` 别名 |
| 旧角色字段 | `ClueUse.roles` 与账本的出现过程继续供写作核对使用，撤除默认界面 | 出现过程不是用途，不能机械换算 |
| 构思格式 | `StoryDocument.schema_version` 为 2，只读写新格式 | 不做旧数据迁移，旧文件按格式错误处理 |
| 无核对基准 | `ProseBinding.contract_digest` 用 `null`，仅线索关联同样为 `null` | 空串与「未核对」在旧格式里混在一起 |
| 暂缓记录 | 从 `ProseBinding` 移除 `waived_digest / waived_note`，改由 `StoryVariant.attention_receipts` 保存 | 提醒决定只有一处 |
| 选段解析 | 新增 `src/hastings/workspace/story_bindings.py` | 与拟新增测试 `test_story_bindings.py` 同名；`story_binding_service` 名称过长 |
| 三状态投影 | 新增 `src/hastings/workspace/story_attention.py`，暂与 `story_sync.py` 并存 | 旧同步视图仍被当前界面使用，P6 撤除后再收敛为唯一读模型 |
| 前端入口 | 线索进入主导航，模块库移入全局工具区，`#/workspaces/{id}/ideas` 保持灵感语义 | 路由语义不复用 |

## 4. 页面动作与落点

| 作者动作 | 接口 | 存储 | 验收编号 |
|---|---|---|---|
| 选段标记真相／误导 | `POST /story/changes`（P2 接入界面） | 账本身份 + `clue_plans.kind` + 精确 `bindings` | A01、A02、A05、A19 |
| 在构思页查看时间轴 | `GET /story/variants/{v}/timeline`（P3） | `nodes.event_span / planned_chapter_number`、`variants.time_origin_label` | A09—A12 |
| 查看与登记线索关系 | `GET /clue-relations`、`POST /story/changes`（P4） | `variants.clue_relations` | A13—A16 |
| 查看提醒与稍后处理 | `GET /story/variants/{v}/sync`（改造，P5） | `variants.attention_receipts` | A17、A23、A24 |
| 主动讨论后调整 | 现有会话与回合接口（P5） | 现有 `assessments` 与正文补丁 | A20、O01—O06 |

P0 只确定落点，不实现这些接口；表中编号沿用[验收计划](../acceptance.md)。

## 5. 统一样本的落点

S1—S6 不新建独立数据目录，按用途分散到测试夹具：S1、S2 在 `test_story_bindings.py`、`test_clue_relations.py` 里以显式节点与绑定构造；S3 在 `test_story_bindings.py` 用中文、emoji、CRLF、强调与重复句逐项断言；S4 在 `test_story_timeline.py`；S5 的规模数据在 P7 用脚本生成；S6 的空作品复用现有 `main/empty` 首次提交路径。夹具只保存被断言的事实，不复制整份预览内容。

## 6. 未覆盖与下一步

- 未跑后端全量、未跑前端、未调用在线模型：本阶段只核对静态基线。
- 前端 `npm test` 与 E2E 在 P2 接入界面后按受影响范围补跑。
- 下一步为 P1：公共类型、选段解析、三状态投影、关系与提醒操作的读写回执，先补失败用例。
