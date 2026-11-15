# E 收口：线索与专名的多来源贡献

日期：2026-10-24。目标：一章重建只能替换这一章的贡献；确认没有发现的章节要移除旧贡献，
不让「无发现但旧记录仍被当当前」。同时保留同一线索被多章各写一份时的最新一份。

## 实际改动

- `ClueEntry` 与 `GlossaryTerm` 增加 `source_chapter`：这条记录来自哪一章的整理结果；
  状态文件的 `record_version` 升到 2，旧结构按空处理，由整理任务重建。
- `ClueStore.replace_source` / `GlossaryStore.replace_source`：先移除该章旧贡献，再写入
  这一批；同一线索或同一专名来自不同章的两份贡献都留在存储里，作者视图按章合并。
- `publish_clues` / `publish_terms` / `publish_timeline` 增加 `source_chapter`：给出时按
  章替换；空批表示这一章确认没有发现，移除该章旧贡献而不是继续沿用。
- `material_tasks` 在 `found` 时把目标章号作为来源；`empty` 时对时间线、线索、专名执行
  按章清除。`insufficient` 只记覆盖，不清除已有记录：信息不足不等于确认没有。
- 读取侧合并规则：
  - 线索按内容键取 `source_chapter` 最新的一份；旧贡献保留在存储里供追溯，视图不重复。
  - 专名按「名称 + 类型」合并：别名取并集，首次出现章取最早，最新章的依据覆盖旧依据。
  - 时间线一行一章，按章直接替换或移除。

## 取舍

- **多份贡献留在存储、视图合并**：删除旧贡献会丢掉「哪一章曾经这样写过」的追溯；
  合并放在读取侧，写入侧只做按章替换。
- **版本升级按空处理**：旧记录结构没有来源章，猜一个归属会把旧数据挂到错误的章节；
  这些文件是派生结果，可以重建。
- **只有 `empty` 清除**：`insufficient` 表示读到了但无法判定，此时清空会丢掉可能仍有效的
  旧记录；保留并在覆盖记录里说明信息不足。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 资料与状态专项 | `uv run pytest -q tests/workspace/test_story_materials.py tests/workspace/test_clues.py tests/workspace/test_glossary.py tests/workspace/test_timeline.py` | 50 passed |
| 静态检查 | `uv run ruff check src/hastings tests` | 通过 |
| 类型检查 | `uv run mypy` | 106 个源文件无问题 |
| 后端全量 | `uv run pytest -q --basetemp data/test-tmp/e-multi-source-rerun -o tmp_path_retention_count=100` | 782 passed，0 failures |

覆盖：第 2 章线索／专名／时间线重建为 empty 后只剩第 1 章贡献；同一线索由两章各写一份时
读取侧只出现最新一份，存储保留两份；旧的空结果仍只记覆盖、不动其他章。

## 未完成

- 作者界面还没有按章展开贡献，也看不出某条记录来自哪一章。
- 卸下模块后旧轮次材料记录「不再适用」的标注仍未做（D 阶段遗留）。
