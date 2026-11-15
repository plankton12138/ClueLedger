# V06-A3 章节索引

> 实施日期：2026-10-22。计划见[上下文与文件操作开发计划](../context-and-file-operations-plan.md)。
> 本阶段做「索引生成与发布、状态与失效、注入与面板」，不含写后检查（C1）。

## 目标

后续写作能按索引定位伏笔、人物首次出现与时间地点，而不用每次读完整本书。索引只是
按当前原文生成的派生缓存：源文件一变就失效，不再注入；生成过程不能改作品。

## 实际改动

| 位置 | 改动 |
|---|---|
| `workspace/chapter_index.py`（新增） | `ChapterIndexDraft` / `ChapterIndexRecord` / `ChapterIndexStore`、`validate_draft`、`publish_index`、`index_injection`；状态按当前磁盘算出 |
| `workspace/agent.py` | `build_tools(mode="index")` 只返回目录、搜索、读取与 `save_chapter_index`；新增该工具与参数解析；`build_harness` 支持索引模式与索引指令 |
| `workspace/context.py` | `INDEX_INSTRUCTIONS`（只读、只从当前原文生成、证据写法）；系统提示新增「章节索引」段，只有有效记录会注入 |
| `workspace/service.py` | `accept(..., purpose=..., index_paths=...)`；`_index_targets` 校验 1–10 章；索引轮次用索引工具集，普通写作注入有效索引 |
| `workspace/state.py` | `AcceptedTurn.purpose` / `index_paths`，写入 `turn_started`；旧日志按 chat 处理 |
| `workspace/api.py` | 请求模型加 `purpose` / `index_paths` 与互斥校验；新增 `GET /{wid}/chapter-index` |
| `frontend/src/api/workspace.ts` | 索引类型与 `fetchChapterIndex`；`TurnInput` 增加可选用途字段 |
| `frontend/src/state/conversations.ts` | `send(text, context?, options?)`：索引用途由界面明确发起 |
| `frontend/src/components/workspace/ChapterIndexPanel.tsx`（新增） | 每章状态、摘要与证据详情；选择章节后发起索引任务 |
| `frontend/src/components/workspace/WorkspaceShell.tsx` | 顶栏「索引」入口 |

## 接口与数据影响

- `POST /{wid}/conversations`、`POST /{wid}/conversations/{cid}/turns` 新增
  `purpose`（`chat` / `update_chapter_index`）与 `index_paths`（最多 10 章）。
  索引用途必须有目标，普通对话不能带目标，否则 422。
- `GET /{wid}/chapter-index?path=`：返回已发布记录与状态；没有记录的章节由界面标为「未生成」。
- 索引缓存在作品绑定状态目录的 `chapter-index.json`，不参与作品 Git。
- 新事件：`chapter_index_updated`（路径、源指纹、规则版本、条目数、状态）。
- 新错误码：`CHAPTER_INDEX_SCOPE_INVALID`、`CHAPTER_INDEX_EVIDENCE_INVALID`、
  `CHAPTER_INDEX_WRITE_FAILED`。
- 规则版本 `RECIPE_VERSION = 1`：规则变化时旧记录按过期处理，不猜测兼容。

## 取舍

- **只从当前原文生成**：索引轮次不注入任何已有摘要，避免误差逐版放大；普通写作才注入
  有效索引。
- **发布四道核对**：目标在允许范围、读完当前版本、引文与区间一致、发布前复核源指纹。
  任何一条不成立都不写缓存。
- **状态与内容分开**：缓存只存「上次生成的结果」；`valid` / `stale` / `source_missing` /
  `unavailable` 每次按磁盘算出，不写回，也不会因为一次读取失败就把记录改坏。
- **引文只证明原文存在**：人物说法与推测要写成「某人声称」，索引不作为事实账本。
- **界面明确发起**：索引用途由面板按钮提交，不让模型从「更新索引」这句话里自己推断。

## 验证证据

- `uv run pytest tests/workspace/test_chapter_index.py -q`：15 项通过。覆盖引文与区间不一致、
  区间越界、编号不连续、条目缺证据、摘要超长、范围外发布、未读完、源指纹变化、发布后
  读回状态、源文件改动与删除、缓存损坏、注入预算、索引工具集不含写工具、接口状态与
  用途校验。
- `tests/workspace/test_chat.py::test_index_turn_publishes_and_hides_write_tools`：走完整
  HTTP + 脚本化模型，确认索引轮次只调用 `read_file` 与 `save_chapter_index`、发布后接口
  返回有效索引、正文指纹不变。
- 前端：`npm test` 96 项通过，`npm run build` 通过；新增用例覆盖索引用途与目标一起提交。

## 偏差与未完成项

- 索引面板只读展示与发起更新，没有逐条编辑；修改索引等于重新生成。
- 「停止保留已发布项」由发布粒度保证（每章一次整份替换），未单独做中断用例。
- 写后检查（C1）与范围事件展示（B2）仍缺；包版本仍为 `0.5.0`。
