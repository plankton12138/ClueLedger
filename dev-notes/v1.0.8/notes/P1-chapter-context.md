# P1 随章聚合读取与讨论焦点

日期：2026-11-02。对应 M1 与 M2 的服务端部分。

## 1. 交付

- 新增 `src/hastings/workspace/story_chapter_view.py`：从当前方案的正文关联、账本证据与章节索引投影一页条目，不建副本、不调用模型。
- `GET /api/file-workspaces/{workspace_id}/story/chapter-context` 接入 `story_api.py`。参数为 `chapter_path`、可选 `variant_id`、`cursor` 与 1—50 的 `limit`。
- `materials.py` 新增 `DiscussionFocus`，并在 `ContextSnapshot` 中冻结。对象焦点合并进 `story.node_ids`（节点）或自动补一份线索引用（线索）；章节与选段焦点绑定正文摘要。
- `ContextInput.discussion_focus` 在 HTTP 边界校验形状；服务端复核对象版本、章节摘要与选段码点区间。
- `verify_context` 在每轮执行前复核焦点；版本或原文变化按整轮失败，不按旧焦点继续。
- `materials_section` 把焦点写成材料说明（对象、章节或选段引文），模型不必自己计算偏移。
- 续聊与恢复：`_context_from_question` 保留 `discussion_focus`，操作恢复时与原快照比对。
- 没有构思文件时，账本线索仍可成为讨论焦点并自动冻结引用；这一轮只给空的「主方案（empty）」工具范围，不写方案文件，只有明确指令才会建立结构。

## 2. 行为约定

- `observed` 只收当前章有效来源；同一对象同时有有效与失效引用时以 observed 为主项，并保留失效出处。
- `planned` 只来自节点明确的 `planned_chapter_number`，不制造引文与位置。
- `unresolved` 保留失效引用；文件不可读与服务失败返回错误，不伪装为空数组。
- 同一线索多次出现保留多次；同位置多来源合并 `binding_ids` 与 `evidence_ids`。
- 游标绑定作品、方案、章节摘要与读取修订；过期后返回 `CHAPTER_CONTEXT_CURSOR_CONFLICT`，客户端从首屏重读。

## 3. 验证

```
CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q \
  tests/workspace/test_chapter_context.py tests/workspace/test_discussion_focus.py \
  --basetemp data/test-tmp/v108-m1m2 -o tmp_path_retention_count=50
```

结果：12 passed。覆盖空作品、三类 placement、同线索多次出现、仅账本线索、游标过期、非章节路径、节点焦点合并、线索焦点隐式引用、无构思线索焦点、选段复核与 HTTP 形状校验。

定向回归（`test_story_api.py`、`test_chat.py`、`test_story_context.py`）97 passed。

## 4. 留白

- 游标分页的大作品规模测量见 [P5 发布记录](P5-release.md)：200 节点、300 关系的随章首页 50 条、15.8 ms、5.4 KB。
- 章节焦点不会预读整章原文；模型仍需用读取工具取正文，这是有意的按需读取。
- `view_revision` 仍使用客户端回传值作为展示字段，不作为写权限或材料依据。
