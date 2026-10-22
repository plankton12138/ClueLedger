# 0.6 运行与验证

当前包版本为 `0.6.0`（前端与桌面同版本）。在 0.5 文件工作区之上，本版提供：可选文件
参考与受理快照、有界的读取与搜索、本轮修改范围、章节索引、有原文依据的只读检查。界面
新增参考选择（输入区选择器与目录每行的「＋」）、材料与范围详情、索引面板与检查报告
（过期时标注并说明旧结论不再对应当前版本）。目录与接口以
[当前模块说明](modules/README.md)为准。

## 运行

与 0.5 相同，在仓库根目录执行：

```powershell
uv run python -m hastings.server
# 或启动时预先打开作品
uv run python -m hastings.server --workspace <作品目录>
```

前端开发在 `frontend/` 执行 `npm run dev`，生产构建执行 `npm run build` 后由后端托管。
接口前缀仍为 `/api/file-workspaces`；`conversation-ui.json` 升到版本 2（新增草稿参考），
旧文件按无参考读取，不需要迁移。

## 行为变化（相对 0.5）

- **写文件前必须登记范围**：模型先 `set_task_scope`，否则 `write_file` / `edit_file` /
  `move_file` / `delete_file` 被拒绝。纯讨论不受影响。
- **登记过的范围不会因重新登记而放宽**：再次登记只能收窄；扩大范围只有 `extend_task_scope`
  一条路，且要求可发现范围、操作类别白名单与关联证据——依据要给出「哪份文件 + 哪句原文」，
  这句原文必须在本轮读过的区间里。
- **改写已有文件前必须读到当前版本全文**；局部替换只要求被替换区间。外部改动后的写入按
  版本失效失败，重读后恢复。
- **作者选中的参考默认为必读**，模型再次登记同一份文件不能取消必读标记。
- **参考不产生修改授权**：作者选了参考、没说改，写入仍被拒绝。
- **检查由作者触发**：普通修改不会自动跑检查；点结果卡的「检查本次改动」或明说「检查」
  才会进入只读检查。检查与写作共用同一轮回合上限，写作阶段用完后不再进入检查。

## 本次验证

2026-10-22，本次修正在线验收器的空改动与后续轮误判，补齐浏览器页面断言和报告响应的
目标版本字段。以下只列本次重新执行的验证，详见[V06-D2 在线模型验收](notes/V06-D2-acceptance.md)。
此前的约束加固与阶段证据见[V06-D1](notes/V06-D1-constraints.md)。

| 检查 | 结果与证据范围 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/d2-full-regression -o tmp_path_retention_count=100` | 460 通过，含十四项验收器回归；报告接口补字段前执行 |
| `uv run pytest tests/workspace/test_review.py -q --basetemp data/test-tmp/d2-review-interface -o tmp_path_retention_count=100` | 报告接口补字段后 17 项通过，含目标快照与失效后的旧版本保留 |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 42 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、104 项通过 |
| `npm run build`（frontend） | 通过 |
| 在线浏览器（frontend，端口 8946） | 首轮参考/范围/草稿、索引/过期/重读两条通过；报告流程因接口缺字段失败，修复后仅定向复测该条，通过。不是一次三条全绿；[命令与证据](artifacts/d2-browser-validation-20261022.md) |
| `uv run python scripts/model_acceptance.py`（在线模型） | 七个类别、十二个独立样例的必需检查通过；范围追加观察项未覆盖，明细与原件见 [V06-D2](notes/V06-D2-acceptance.md) |

分阶段证据（含每条用例清单）见各[阶段记录](README.md)；以下事项**未验证或未实施**，
不作为当前能力：

- **不调模型的完整浏览器项目本次未重跑**：此前的通过结果保留在阶段历史，不计入本次验证。
- **真实模型验收只覆盖一个模型**：结论只对 `deepseek-flash` 成立，不外推到其他模型；
  七类任务由[驱动器](../../scripts/model_acceptance.py)自动核对可判定事实，
  文笔、伏笔与人物动机等判断需要作者看样例。
- **范围追加的证据要求未在在线样例中出现**：模型在同步任务里选择一次登记完整范围；
  该约束由工具层用例覆盖。
- 检查质量（误报、漏报、把回忆当事实）未验收；报告只能说明证据存在，不能证明语义正确。
