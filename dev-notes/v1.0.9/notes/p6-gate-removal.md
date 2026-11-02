# 拆门槛改造：全书构思成为唯一写作门禁

日期：2026-11-02。状态：**已实施；后端全量、前端单测与构建、写作 / Aqua 浏览器用例均通过，在线模型与完整验收留到 P7**。运行版本仍为 1.0.8。

## 1. 为什么拆

章节构思门槛要求作者在写正文之前先说明「这一章要改变什么、包含哪些信息变化」。实际创作里，作者常常先写出正文，再从正文看出这一章做了什么；门槛把系统本该替作者完成的观察工作，变成了作者必须先交出的输入。

改造后只有一处门禁：作品有一段可继续发展的全书构思。章节退化为顺序、可选标题与正文文件；线索、伏笔和回收由正文保存后的语义索引给出候选，作者只做确认。

## 2. 领域与写入

- 当前格式升为 4。`ChapterPlan` 只保留 `id`、`order`、`title`、`prose_path`；`intent`、`information_move_ids`、`revision`、`confirmed_revision` 删除。
- 删除 `chapter_content_revision` 与 `ConfirmChapterPlan`；信息变化按自身 `chapter_id` 归属，章节不再维护 ID 列表。
- `ChapterWritePolicy.require_write` 只校验全书构思：没有任何故事状态时按新作品拒绝，旧格式 2 仍按原行为放行。
- 建章事务 `create_chapter` 在同一次联合提交里登记章节、分配正文路径、写入 `# 标题`；重复请求键返回同一结果，任一步失败一起回滚。
- 正文保存只校验全书构思与正文摘要，移除 `expected_chapter_revision`；请求键幂等与摘要冲突行为保留。
- Agent 的 `write_file`、`edit_file` 走同一策略，不再携带章节修订。

## 3. 索引与候选

- 工作台投影的 `placement` 只剩 `written`、`observed_candidate`、`alignment`；正文没有信息变化时返回空列表，前端负责提示。
- 正文或作品尚无全书构思时，投影给出 `editable=false`、`blocked_reason=book_intent_required`。
- 新增 `POST /story/index-candidates/{candidate_id}/confirm`：核对正文摘要、原句唯一性与章节归属后，联合提交信息线（需要时）、信息变化与绑定；结果可整体撤销，重复确认会被拒绝。
- 新增 `POST /story/chapters/{chapter_id}/index-review`：空章反馈里的「让模型看看本章」复用同一增量分析任务。
- 辅助类型 `chapter_plan` 改为 `chapter_direction`：只返回方向参考文字，可阅读与复制，不进入正式数据。候选基础修订去掉 `chapter_revision`。

## 4. 前端

- 删除 `ChapterPlanGate`：章节页直接进入稿纸，没有构思输入、整理、确认或「开始写作」步骤。
- 空正文只有三个入口：自己写第一段、由模型起草、看看方向建议。
- 页边索引显示已写入信息（准确原句）、正文新增候选（可确认入账）与需要对齐项；窄屏辅助区与宽屏内联使用同一组件。
- 保存后本章没有任何信息变化时，温和提示「本章还没有可核对的信息变化」，提供「让模型看看本章」与「暂时忽略」，不拦截继续输入。提示只看已入账信息，不等语义分析结算：分析排队时作者也需要立刻得到反馈，候选回来后再自然消失。
- 全书构思页改为意图段落、信息线总览与章节列表；左栏章节第二行显示第一条已入账信息，不再出现「先确定本章」。
- 视觉维持 P5 的 Aqua 方案，本次未调整材质与控件状态。

## 5. 与计划的差异

- **取消「完成本章」**：格式 4 没有章节修订，也没有可供「完成」提交的对象。验收计划 P06 已同步改写为保存过渡章后观察空章提示。
- **新增 `index-review` 端点**：空章反馈需要一条可执行的模型路径，否则「让模型看看本章」只是文案。
- **候选去重按 statement 判断**：确认入账后同一候选在投影中隐藏；撤销后信息变化消失，候选重新出现，不需要额外的确认状态文件。

## 6. 验证

| 命令 | 结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest tests/workspace -q` | 1453 passed（全量，8 分 59 秒） |
| `npm run build`（frontend） | tsc 与 Vite 构建通过；仅保留既有大包提示 |
| `npm test -- --no-file-parallelism`（frontend） | 29 个文件、324 个用例通过 |
| `npx playwright test e2e/writing-workbench.spec.ts e2e/aqua-layout.spec.ts --project=workspace` | 2 passed（32.8 秒；含新建章、空正文三入口、空章反馈、刷新保留与三档宽度） |
| `ruff check src/hastings/workspace` | All checks passed |
| `mypy src/hastings/workspace` | 仅剩改造前已存在的 4 条类型告警（`story_changes` 的变量复用等） |

## 7. 未完成（进入 P7）

1. 在线模型 O01—O10、确定性矩阵 A01—A40b 与性能观察。
2. 页边索引的段落级像素对齐、专用编辑内核比较。
3. 正文候选采用时的 `candidate_id` 全链路核对（页面目前仍走普通正文保存）。
4. 验收截图补齐：候选确认前后、需要对齐、模型不可用与索引失败状态。
5. 全部门槛通过后统一版本为 1.0.9。
