# P2 实施记录：保存、确定性绑定与工作台投影

日期：2026-11-02。状态：**已实施（P2）**。未改前端，未改版本号；运行版本仍为 1.0.8。

## 1. 实际改动

| 文件 | 改动 |
|---|---|
| `src/hastings/workspace/writing_view.py` | 新增工作台只读投影：全书、章节、未挂计划正文、确定性章节索引；索引只区分 `prepared / written`，锚点状态来自既有算法 |
| `src/hastings/workspace/writing_actions.py` | 新增作品意图、章节壳与章节构思保存；无模型时把一句话构思整理为一条信息线 + 一条信息变化；显式空变化与跨章复用标识被拒绝 |
| `src/hastings/workspace/writing_api_models.py` | 新增五类请求体：作品意图、创建章节、保存构思、开始写作、保存正文 |
| `src/hastings/workspace/chapter_writing.py` | 正文保存在同一提交内刷新已有绑定：唯一引文重定位并更新摘要；缺失或歧义保留原引用，由索引报告；保存响应携带 `context_revision`、`index_revision` 与完整索引 |
| `src/hastings/workspace/story_api.py` | 接线 `GET /story/workbench`、`PUT /story/book-intent`、`POST /story/chapters`、`PUT .../plan`、`POST .../start`、`PUT .../prose`；新写作入口使用格式 3 存储 |
| `src/hastings/workspace/agent.py` | 写文件与局部替换从服务端读取当前章节修订并传入文件层；计划变化后 Agent 写入按 `CHAPTER_PLAN_STALE` 拒绝 |
| `src/hastings/workspace/errors.py` | 章节门槛、正文摘要冲突与格式不支持映射为 HTTP 409 |
| `tests/workspace/test_writing_api.py` | 新增：HTTP 全流程、工作台章节状态、保存冲突、修订过期、未挂计划正文只读 |
| `tests/workspace/test_agent_chapter_gate.py` | 新增：Agent 使用当前章修订、改写后拒绝、不能创建未构思章节 |
| `tests/workspace/test_chapter_writing.py` | 新增：绑定重定位与失效；既有用例随响应字段扩展 |

## 2. 关键语义

- **索引只做能证明的事。** 计划项没有出处是 `prepared`，有绑定进入 `written`；原句状态由当前正文计算。P2 不产生语义候选，也不显示虚构的“计划外信息”。
- **语义分析状态是 `unavailable`。** 有正文时索引明确说明语义分析尚未接入，不把确定性结果冒充完整分析；P3 接任务后改成 `queued / running / ready / failed`。
- **绑定更新与正文保存同生共死。** 唯一引文重定位后写入新摘要与区间；缺失或歧义的绑定保留原引用，撤销与恢复仍可依据原引文。绑定更新失败会连带整次保存失败。
- **未挂计划正文只读。** 工作台列出 `unplanned_prose`；按路径打开时 `plan=null`、`editable=false`、`blocked_reason=chapter_plan_required`，不自动补写构思。
- **计划保存不确认。** 内容变化后 `confirmed_revision` 清空；开始写作事务才确认。标题变化不改变章修订，跨章调整顺序也不改变各章修订。
- **Agent 不再自填修订。** 写入前从故事状态读取当前章修订，文件层再核对；Agent 无法跳过门槛或用旧修订写入。
- **HTTP 冲突状态。** `CHAPTER_PLAN_REQUIRED / STALE`、`PROSE_DIGEST_MISMATCH`、`STORY_FORMAT_UNSUPPORTED` 返回 409；输入不完整仍为 400。

## 3. 验证

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/workspace/test_writing_api.py tests/workspace/test_chapter_writing.py tests/workspace/test_agent_chapter_gate.py -q` | 16 passed |
| `uv run pytest tests/workspace/test_writing_api.py ... test_agent.py test_files.py -q` | 167 passed |
| `uv run pytest tests/workspace/test_story_attention.py ... test_chapter_context.py -q` | 204 passed |
| `uv run ruff check <本次改动文件>` | All checks passed |

未运行：后端全量、前端测试、浏览器测试与在线模型。P2 没有前端改动；等 P3 候选与索引接线后再做一次必要的广泛回归。

## 4. 未完成项（进入 P3）

1. 语义索引任务、候选存储与事件流尚未接入；索引固定 `analysis.state=unavailable`。
2. 模型辅助入口、候选采用与过期处理尚未实现；`candidate_id` 传入正文保存会明确拒绝。
3. 工作台的 `model_support` 固定为不可用，P3 接真实模型状态。
4. 「完成本章」、差异处理与信息线展开视图尚未实现。
5. 前端仍使用旧构思/线索页；新工作台没有页面入口，P4 替换。
6. 未执行真实长章的编辑内核验证；编辑内核仍是 P2/P4 的前置项。
