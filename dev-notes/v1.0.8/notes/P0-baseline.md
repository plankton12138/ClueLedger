# P0 基线、调用清单与操作覆盖

日期：2026-11-02。基线提交 `4755814 feat(v1.0.7): 构思与线索三态简化`，主包与前端均为 1.0.7。本阶段只核对静态基线、冻结接口与样本，不运行后端全量，不调用模型。

## 1. 工作树

开工前跟踪文件有三处修改：`dev-notes/README.md`、`product-design/README.md` 与若干 `dev-notes/v0.8/artifacts/frontend-experience/screenshots/*.png`。未跟踪文件包括 `dev-notes/v1.0.6/anchor-model.html`、`dev-notes/v1.0.8/`、`product-design/v1.0.8/`、根目录 `package.json`、`package-lock.json` 与 `release/`。截图、根目录包文件与 `release/` 不属于本版修改；本版只写 `dev-notes/v1.0.8/`、`product-design/v1.0.8/` 与实现代码。

## 2. 现有读接口与消费者

路径省略公共前缀 `/api/file-workspaces/{workspace_id}`。

| 接口 | 读取内容 | 当前消费者 | 本版处理 |
|---|---|---|---|
| `GET /story/variants` | 当前主方案摘要 | `state/story.ts → load/refresh` | 复用 |
| `GET /story/variants/{v}` | 节点、连接、阶段、故事线、约定 | `state/story.ts`、`WorkspaceDetail` | 复用；详情改为只读投影的输入 |
| `GET /story/variants/{v}/nodes/{id}` | 单个节点完整字段 | `WorkspaceDetail.NodeDetail` | 复用；不再形成可编辑表单 |
| `GET /story/variants/{v}/sync` | 三状态、原因、绑定与章节 | `state/story.ts` | 复用；详情显示提醒依据 |
| `GET /story/variants/{v}/timeline` | 按披露章分组的节点与时间 | `StorySurface → StoryTimeline` | 保留为按需进入的辅助视图 |
| `GET /story/variants/{v}/prose-marks` | 当前章节点出处标记 | `Paper` | 复用；点击标记进入同一详情 |
| `GET /story/variants/{v}/mystery` | 悬疑投影与诊断 | 无前端消费者 | 保留读接口，本版不新增界面 |
| `GET /clues?scope=story` | 全书线索分页 | `ClueSurface`、`WorkspaceDetail` | 复用；保留分页与搜索 |
| `GET /clues/{id}?scope=story` | 线索身份、计划、出现与关系 | `WorkspaceDetail.ClueDetailPanel` | 复用；详情改只读，删除用途与关系表单 |
| `GET /clue-marks` | 当前章线索标记与失效条目 | `Paper` | 复用；作为随章投影的交叉核对 |
| `GET /clue-relations` | 选中线索一跳关系 | `state/story.ts → ClueSurface` | 复用；保留关系图按需进入 |
| `GET /source-locations/{evidence_id}` | 账本依据的真实来源 | `state/sourceLocation.ts` | 复用；定位不把 binding ID 当 evidence ID |
| `GET /story/changes/{id}` | 一次结构改动的操作与影响 | `state/story.ts`（最近一次） | 复用；对话回执展开时读取 |

新增读接口 `GET /story/chapter-context` 见[契约](../data-and-api.md#2-随章聚合读取拟新增)。

## 3. 现有写接口与消费者

| 接口 | 当前前端消费者 | 对话工具路径 | 本版处理 |
|---|---|---|---|
| `POST /story/changes` | `WorkspaceDetail` 保存、`state/story.ts.markSelection`、关系表单 | `update_story` 直接调用 `StoryStore.commit_change` | P3 撤除表单后前端无消费者；保留为确定性 HTTP 写接口，供测试与外部调用，不新增界面入口 |
| `POST /story/changes/{id}/undo` | `state/story.ts.undo`（构思页「撤销上一次」） | 无 | 保留；对话回执新增同一能力入口 |
| `POST /clues` | 无 | `create_clue_identity` 操作 | P3 后无界面消费者；保留读多写一的通用入口，不在本版删除，避免破坏既有接口测试 |
| `POST /conversations`、`POST /conversations/{id}/turns` | `ConversationPanel` | — | 扩充 `context.discussion_focus` |

本版不新增独立章节存储、第四主入口或聊天专用写服务。所有对话写入仍进入 `story_store`、`story_clue_commit`、`story_changes` 的联合提交与逆操作。

## 4. 对话工具的操作覆盖

`update_story` 的 `StoryOperation` 联合已覆盖下列界面原有操作，无需新增聊天存储：

| 作者操作 | 工具操作 | 备注 |
|---|---|---|
| 创建、改名、修改、删除构思节点 | `add_node`、`update_node`、`remove_node` | 更新按 `node_id` 整份对象提交；服务端回读后合并 |
| 创建、改名、删除线索身份 | `create_clue_identity`、`rename_clue_identity`、`remove_clue_identity` | 删除有引用时由联合提交拒绝并说明依赖 |
| 设置、清除线索用途与作者解释 | `upsert_clue_plan`、`remove_clue_plan` | `kind` 可空，表示用途未定 |
| 关联、解除正文出处 | `bind_prose`、`unbind_prose` | 模型只给目标与引文，偏移与摘要由服务端重算 |
| 设置、删除线索关系 | `upsert_clue_relation`、`remove_clue_relation` | 关系说明必填 |
| 提醒暂缓、确认与清除暂缓 | `acknowledge_attention`、`confirm_alignment`、`clear_attention_receipt` | 确认需要作者原话作为依据 |
| 阶段、故事线、连接、时间关系、约定 | `add_stage`/`update_stage`/`remove_stage`、`add_thread`/`update_thread`/`remove_thread`、`connect`/`disconnect`、`set_event_time`/`unset_event_time`、`add_constraint`/`update_constraint`/`remove_constraint` | 原有表单未暴露的阶段与时间仍可由对话完成 |
| 撤销一次结构改动 | 无工具操作；`StoryStore.undo` + `POST /story/changes/{id}/undo` | 撤销不是语义变更，不进入 `update_story` |

结论：功能覆盖上没有必须新增的 `update_story` 操作。P2 需要验证的是「讨论轮能拿到写工具、明确指令能提交、回执可撤销」，而不是补齐操作类型。

## 5. 冻结的新接口

`GET /story/chapter-context?chapter_path=...&variant_id=...&cursor=...&limit=20`

- `chapter_path` 必填，必须是当前作品的正文；`variant_id` 省略时读当前主方案。
- `limit` 1—50，`cursor` 绑定作品、方案、文件摘要与读取修订。
- 响应字段与三类 `placement` 按[数据与接口契约](../data-and-api.md#2-随章聚合读取拟新增)执行；新增错误码只复用 `PROSE_CHAPTER_NOT_FOUND`、`STORY_VARIANT_NOT_FOUND`、`FILE_NOT_FOUND` 与新增 `CHAPTER_CONTEXT_CURSOR_CONFLICT`。
- 空列表是合法结果；文件不可读与服务失败分别返回错误，不返回空数组。

`ContextInput.discussion_focus`

- 三类焦点：`object`、`chapter`、`selection`，字段按契约执行。
- 点击「讨论调整」只写入本地草稿焦点；发送时才作为 `context.discussion_focus` 提交，受理后冻结进 `ContextSnapshot` 并随轮次恢复保存。
- 对象焦点在服务端合并进 `story.node_ids`（节点）或补一份线索引用（线索）；章节与选段焦点只用于材料定位，不构成写权限。
- 选段使用 Unicode 码点、左闭右开，服务端按规范化源文件复核；复核失败整轮不受理。

## 6. 验收样本落点

- 两章正文、计划节点、跨章节点、仅账本线索、失效引用：`tests/workspace/test_chapter_context.py` 与前端 `frontend/e2e/chapter-context.spec.ts` 共用同一组样本描述，不写入作者实际作品。
- 空作品与无主方案：后端 `test_chapter_context.py` 的空目录用例；前端不新增数据。
- 重复引文、非 BMP 字符：后端选段与聚合用例；沿用 `test_story_bindings.py` 的码点断言。
- 同名不同身份线索：后端以两个 `clue_id` 构造，前端按对象身份分组。

## 7. 未覆盖与下一步

- 未运行后端全量、前端构建或浏览器流程；本阶段只冻结契约与落点。
- 在线模型验收 O01—O06 尚未执行，不继承 v1.0.7 的任何对话结论。
- 下一步 P1：先补失败用例，再实现随章聚合读接口与发送焦点冻结。
