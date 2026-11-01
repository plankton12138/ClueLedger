# P1 数据契约、选段解析与三状态投影

日期：2026-11-01。代码版本：`d1f8238` 基础上未提交改动。阶段范围：M2 公共类型、来源规范化、状态投影与读写回执。

## 1. 完成内容

| 落点 | 变化 |
|---|---|
| `story_models.py` | 新增 `ClueKind`（`truth / mislead`）、`StoryEventSpan`、`ClueRelation`、`AttentionReceipt`；节点增加 `planned_chapter_number / event_span`；方案增加 `time_origin_label / clue_relations / attention_receipts`；`ProseBinding` 的 `node_id` 可空、`contract_digest` 可为 `None`、移除 `waived_digest / waived_note`；格式版本升为 2 |
| `story_changes.py` | 新增 5 个操作：`upsert_clue_relation`、`remove_clue_relation`、`acknowledge_attention`、`clear_attention_receipt`、`set_time_origin`、`confirm_alignment`；对称关系写入时按端点排序；反向操作覆盖关系、暂缓记录与时间基准 |
| `story_bindings.py` | 新增：`unique_span`、`resolve_selection`、`locate_saved_span`、`anchor_state`、`build_binding`；界面与模型共用同一套坐标与失败分类 |
| `story_attention.py` | 新增：`node_status`、`warning_fingerprint`、`reminder_visible`、`node_attention`；状态、原因与提醒可见性分开返回 |
| `story_sync.py` | 读模型改用新的提醒指纹判断暂缓；`defer` 写 `acknowledge_attention` 而不是改绑定字段；只关联线索的绑定不参与节点与章节的构思聚合 |
| `story_store.py` | 只接受格式 2；旧格式给出明确的版本说明与处理建议 |
| `story_clue_commit.py` | 临时线索标识的替换覆盖计划、节点用途、正文关联与关系两端 |
| `story_authorization.py` | 新增操作的范围判断：节点级提醒与确认、两端点线索关系、无范围操作的时间基准 |
| `story_prose_marks.py`、`clue_marks.py` | 允许只关联线索的绑定；线索出处不占用构思节点范围 |

## 2. 关键取舍

- **一个指纹**：删除旧 `sync_fingerprint`，暂缓与提醒都用 `warning_fingerprint`。指纹不含标题、绝对偏移、暂缓记录本身；`exact` 与 `relocated` 归一为有效定位。
- **状态在节点上聚合**：`ready / draft / attention` 只按该节点的关联与核对问题计算，不看别的章节是否还有未归属正文。
- **作品身份**：指纹里的作品标识取受管目录本身。移动作品目录会重新提醒一次，换取不依赖会话或宿主机时间。
- **关系只做结构校验**：端点是否真实存在由带账本的变更服务验证（P4），模型层只保证不同端点、非空说明与对称去重。
- **仅线索绑定**：章节聚合、构思出处、节点授权都显式跳过 `node_id is None` 的关联，不把它们算作已归属构思。

## 3. 实际命令与结果

```
CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q tests/workspace/test_story_sync.py \
  tests/workspace/test_story_store.py tests/workspace/test_story_contracts.py \
  tests/workspace/test_story_joint_commit.py tests/workspace/test_clue_marks.py \
  tests/workspace/test_story_api.py tests/workspace/test_story_candidate.py \
  tests/workspace/test_story_tools.py tests/workspace/test_story_context.py \
  tests/workspace/test_story_remediation.py tests/workspace/test_story_projection.py \
  tests/workspace/test_story_bindings.py tests/workspace/test_story_attention.py \
  tests/workspace/test_clue_relations.py tests/workspace/test_clue_views.py \
  tests/workspace/test_clue_ledger.py tests/workspace/test_story_clue_observations.py \
  --basetemp data/test-tmp/v107-p1g -o tmp_path_retention_count=50
```

结果：296 passed，102 秒。补完临时标识替换与授权分支后，同一组用例加 `test_story_routing.py` 复跑为 328 passed。

```
uv run ruff check src/hastings tests/workspace   # All checks passed
uv run mypy                                      # 169 files, no issues
CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --basetemp data/test-tmp/v107-full \
  -o tmp_path_retention_count=100                # 1614 passed, 1 failed（见下）
```

全量里唯一的失败是 `test_git.py::test_merge_conflict_blocks_writes`：写 `正文/0001 雨夜.md` 时报 `FILE_WRITE_FAILED`，拒绝访问。单独复跑该用例通过，是 Windows 文件占用引起的偶发失败，与本次结构改动无关。

新增用例：`test_story_bindings.py`（17）、`test_story_attention.py`（17）、`test_clue_relations.py`（10），并补 4 条契约用例。

## 4. 未覆盖与下一步

- 未改前端：绑定类型与三状态读模型没有消费者，等 P2 的选段标记界面一起接。
- 未改工具 Schema 与提示词：`bind_prose` 的 `contract_digest` 语义变化后，工具仍按旧字段自行拼接绑定，P2 统一切到 `build_binding`。
- 新操作已能通过 `/story/changes` 提交（请求体与存储共用同一个联合类型），但关系与提醒的界面入口、读投影分别在 P4、P5 才接；`confirm_alignment` 的指纹与依据校验也留到 P5 的服务层。
- 关系端点是否真实存在由带账本的变更服务校验，P1 只做结构约束（不同端点、非空说明、对称去重）。
- 未跑前端单测与 E2E：本阶段没有前端改动。
