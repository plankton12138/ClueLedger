# P4 线索页与关系投影

日期：2026-11-01。范围：M5 的读取、关系登记与生命周期。

## 1. 完成内容

- 新增 `clue_relations.py`：`build_relations` 返回选中线索的一跳关系或受预算约束的全图，带 `remaining_count` 与 `view_revision`。
- 新增 `GET /clue-relations?variant_id=&focus_clue_id=`；未知焦点返回 `CLUE_NOT_FOUND`。
- 线索详情补 `relations`（相对当前线索的方向）与 `occurrences`（同位置合并作者关联与账本观察）。
- 关系写入沿用 `POST /story/changes` 的 `upsert_clue_relation / remove_clue_relation`：支撑保留方向，矛盾与共同误导按端点排序去重，允许成环，自关联与空说明在模型层拒绝。
- 删除线索时仍由现有引用校验阻止静默级联；撤销覆盖关系的新增、改写与删除。

## 2. 取舍

- 关系图是投影：没有关系时显示列表，不按用途自动连线；共同误导不要求两端都是误导线索。
- 端点是否真实存在由带账本的目录校验（`StoryClueCatalog`），模型层只做结构约束。
- 图默认最多 40 个节点、80 条边，焦点视图最多 20 个相邻线索；未展示数量如实返回。

## 3. 验证

```
CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q tests/workspace/test_clue_relations.py \
  --basetemp data/test-tmp/p4b -o tmp_path_retention_count=50
```

结果：15 passed。覆盖方向保留、对称去重、成环、改写、删除与撤销、孤立线索不自动连线、焦点视图的剩余数量、空结果，以及临时线索标识替换覆盖关系与绑定两端。

## 4. 未覆盖

- 未做 S5 规模（300 条关系）的响应体与首屏时间测量。
- 关系图前端在 P6 以列表加一跳 SVG 呈现；键盘可达性用列表保证，未给 SVG 单独做完整焦点环。
