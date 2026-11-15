# P2 正文选段、类型与出处

日期：2026-11-01。范围：M3 与类型编辑的垂直链路。

## 1. 完成内容

- `clue_marks.py` 重写：标记只来自两处——作者登记的正文本关联（`ProseBinding.clue_id`）与账本里仍能对上当前正文的观察。删掉按线索用途分词猜测位置的回退；引文找不到或出现多次时返回 `invalid` 条目与原因，不再给猜测位置。
- 标记带 `kind`（`truth / mislead / null`），同一区间来自两种来源时合并成一条并保留 `sources`。
- `story_bindings.normalize_bindings`：`POST /story/changes` 的 `bind_prose` 一律由服务端重算 `document_id`、区间、引文、章节摘要与核对基准；客户端只给引文与当前摘要，摘要不符返回 `PROSE_SOURCE_STALE`，不写入。
- `ProseBinding` 的 `document_id / digest` 允许输入侧留空，持久化时由 `StoryVariant` 校验非空，避免界面被迫伪造章节标识。
- `/clue-marks` 支持 `variant_id`；线索详情新增 `kind`、`occurrences`（作者关联与账本观察按位置合并）与 `relations`。

## 2. 取舍

- 账本的角色（埋设 / 提及 / 回收）不再决定正文样式；用途以作者计划为准，出现过程仍保留给写作核对。
- 读取时唯一重定位只改变显示位置，不改写绑定；正式锚点在确认或重新关联时才更新。

## 3. 验证

```
CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q tests/workspace/test_clue_marks.py \
  tests/workspace/test_story_bindings.py tests/workspace/test_clue_views.py \
  --basetemp data/test-tmp/p2-marks4 -o tmp_path_retention_count=50
```

结果：33 passed（标记 8、绑定 17、线索视图 8）。接口用例覆盖：选段登记后能读到同一 `clue_id` 与 `kind`；正文改动后仍用旧摘要提交时返回 `PROSE_SOURCE_STALE` 且不落盘。

## 4. 未覆盖

- 未做同章多次出现的批量回归（S2），留给 P7 的浏览器流程。
- 前端接入在 P6，本阶段只保证接口与读模型。
