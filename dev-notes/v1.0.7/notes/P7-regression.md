# P7 回归与验收记录

日期：2026-11-01。代码版本：`1.0.7`（工作树未提交）。范围：受影响回归、浏览器流程与未执行项说明。

## 1. 后端

```
CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --basetemp data/test-tmp/v107-full2 \
  -o tmp_path_retention_count=100
```

结果：**1626 passed**，9 分 33 秒，无失败。上一阶段记录里偶发的 `test_git.py::test_merge_conflict_blocks_writes` 本次未复现。

定向组（构思、线索、时间轴、关系、提醒）在 P2—P5 各自阶段运行，最后一次合计 328 passed；新增用例 73 条：

| 文件 | 条数 | 覆盖 |
|---|---|---|
| `test_story_bindings.py` | 17 | 唯一引文、区间严格比对、过期正文、重定位、码点、仅线索关联 |
| `test_story_attention.py` | 17 | 三状态、原因、指纹稳定性、暂缓记录与撤销 |
| `test_clue_relations.py` | 15 | 方向、对称去重、成环、生命周期、关系投影、临时标识 |
| `test_story_timeline.py` | 6 | 披露分组、同章排序、未定时间、瞬时事件、跨午夜、失效锚点 |
| `test_story_sync.py` | 8 | 改写：三状态读模型、保存不再需要确认、暂缓、复核恢复、过期讨论 |
| `test_clue_marks.py` | 8 | 改写：精确出处、用途、重叠、失效说明、选段登记接口 |

静态检查：`uv run ruff check src/hastings tests/workspace` 通过；`uv run mypy` 171 个文件无问题。

## 2. 前端

```
cd frontend
npx tsc --noEmit                      # 无输出
npx vitest run --no-file-parallelism  # 32 个文件、346 passed
npm run build                         # 成功
```

## 3. 浏览器流程

```
cd frontend
CODEBUDDY_SAFE_DELETE_ENABLED=0 npx playwright test --project=workspace \
  --output=../data/test-tmp/e2e-workspace-clean
```

结果：**96 passed、9 failed、1 skipped**，14 分 23 秒。失败全部集中在模块库与长文发布相关的用例（`core-pages`、`frontend-experience` 4 条、`module-workspace`、`frontend-foundation`），以及一条主导航计数的旧断言（已改为 4 个入口）。这些文件本版没有修改，失败也没有定位到与本版改动相关的路径；但没有在 HEAD 上复跑证明它们属于既有问题，因此不计为通过。

随后单独复跑相关用例：

- `aqua-layout.spec.ts` 的导航计数改为 4；该文件其余断言未改。
- `clue-reference.spec.ts`、`clue-source-navigation.spec.ts` 改为从主导航进入线索页：11 passed。
- `clueledger-workspace.spec.ts`：3 passed。
- `story-structure.spec.ts`：7 passed。
- `text-selection.spec.ts`：2 passed。
- `module-workspace.spec.ts` 单独复跑仍有 1 条窄窗口用例失败（6 passed、1 failed），与本版改动无交集。

新增 `e2e/clueledger-workspace.spec.ts` 三条：

1. 正文选段 → 标记线索 → 选真相 → 登记；标记按用途着色，刷新后仍在；线索页列出同一线索与用途。
2. 改构思内容 → 保存成功且状态变为需核对 → 稍后处理只隐藏提醒 → 刷新后状态仍是需核对。
3. 线索页选中线索 → 关系列表显示支撑与说明 → 点击后同一详情区切换到另一条线索。

`playwright.config.ts` 的 `workspace` 项目已登记该文件；`story-structure.spec.ts` 删掉了依赖悬疑视图与旧线索栏的两条用例，其余断言改到时间轴列表。

## 4. 未执行项

- **在线模型验收 O01—O08 未执行。** 本机没有为本次准备隔离的在线连接；按验收文档要求，端点不可用时记为未执行，不切换本地模型，也不用固定回复替代。因此「讨论调整」只验证到请求构造与服务端指纹校验（离线用例覆盖过期指纹被拒），未验证模型实际读取材料并给出建议。
- 未做 200 节点 / 300 条关系的规模测量（S5）；时间轴一次返回全部节点，关系图有预算限制。
- 未做 200% 缩放与 320px 宽度的专项检查；窄屏在 390 与 640 宽度各有一条用例。
- 未做移动端触屏滑动；时间轴本来没有拖动交互。

## 5. 实际结果

（见下一条记录补充。）
