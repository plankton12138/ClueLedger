# P3 只读故事时间轴

日期：2026-11-01。范围：M4。

## 1. 完成内容

- 新增 `story_timeline.py`：`build_timeline` 产出按披露章节分组的节点、时间位置与三状态。
- 披露章节取有效正文关联的真实章序；没有关联时退到节点的 `planned_chapter_number`，再没有则进「尚未安排章节」组。同一章里既有已绑定节点也有计划节点时，整章按「实际」分组，不拆成两组。
- 时间只接收作者明确给出的相对分钟；`domain` 取所有有效时间的最小 / 最大值并留 5% 边距，只有一个瞬时事件时向两侧各扩 1 分钟，事件本身仍是零时长。
- 新增 `GET /story/variants/{v}/timeline`。
- 新增 `GET /story/variants/{v}/nodes/{node_id}`：详情面板编辑时整份提交，不丢掉没有显示的线索用途与知情。

## 2. 取舍

- 时间轴不提供拖拽、缩放或重排；节点顺序只由 `reveal_order` 与稳定 ID 决定。
- 没有数值时间的节点仍在所属分组里显示，`domain` 为 `null` 时不画任何横轴位置。

## 3. 验证

```
CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q tests/workspace/test_story_timeline.py \
  --basetemp data/test-tmp/p3d -o tmp_path_retention_count=50
```

结果：6 passed。覆盖倒叙（发生时间与披露章节相反）、同章多节点按 `reveal_order`、跨章节点、只有文字时间、瞬时事件、跨午夜连续分钟与失效锚点。

## 4. 未覆盖

- 长篇窗口化（S5 的 200 节点）没有实现；当前一次返回全部节点，P7 记录为已知限制。
- 前端时间轴在 P6 接入。
