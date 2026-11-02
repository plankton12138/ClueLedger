# P0 基线：入口、消费者与写入路径

日期：2026-11-02。状态：**已实施（P0）**。基线提交 `23f02ff`，运行版本 1.0.8；工作树有 92 项与本版无关的既有改动，实施期间不清理、不覆盖。

本记录描述 1.0.8 的真实行为，作为 P5 删除旧链路的消费者依据，并约束 P1—P4 的实现。计划中的类名与实际代码不一致时，以本记录为准并在契约文档中修正。

## 1. 核对结论

1. **应用内没有正文编辑器。** `Paper` 是只读 Markdown 阅读器，文件开头写明「修改走对话里的 Agent 或应用之外的编辑器」。
2. **没有正文保存 HTTP 接口。** `workspace/api.py` 的写端点只有导入、Git 记录 / 回退、活动设置和会话；没有「保存正文」。
3. **正文写入有四条实际路径**：Agent `write_file`、Agent `edit_file`、导入（`import_text` → `create_chapter`）、结构提交里的 `prose_patches`（`update_story` 联合提交）。四者都不校验章节构思。
4. **构思页与线索页只读。** 节点、阶段、线索和关系的修改全部经对话工具 `update_story` 提交；前端没有节点或线索表单。
5. **现有候选只服务故事正文场景。** `StoryCandidate` 有 draft / reviewing 等状态和采用路径，但没有「目标类型 + 基础修订」的通用创作文本候选。
6. **没有专用编辑器依赖。** `frontend/package.json` 不含 CodeMirror、TipTap 或 ProseMirror；编辑内核的选择必须在真实长章验证后冻结。
7. **存在无前端消费者的服务端能力。** 例如 `/story/variants/{id}/mystery` 只有路由与投影，没有前端调用。
8. **计划里的 `WorkspaceFiles` 实际名为 `Workspace`**（`files.py` 第 220 行）；写入策略应注入该类，而不是新建一个同名文件层。

## 2. 页面与导航

| 位置 | 现状 |
|---|---|
| `frontend/src/state/navigation.ts` 第 14—27 行 | `WorkspacePage` 用 `mode?: "story" \| "clues" \| "chapter"` 表达三种写作模式；正文不写 `mode`。 |
| 同文件第 56—77 行 | `parseHash` 从 query 读取 `mode / source / clue / node / as_of / task_chapter`，并校验取值。 |
| 同文件第 92—109 行 | `pageHash` 把上述字段写回 hash；旧 `overview`、`materials` 链接已回落写作页。 |
| `components/workspace/AppHeader.tsx` 第 15—37 行 | 作品内导航固定三项：正文、构思、线索，另有模块库。 |
| `components/workspace/WritingLayout.tsx` 第 37—43 行 | 根据 `mode` 计算 `storyView / cluesView / chapterView`。 |
| 同文件第 266—281 行 | 主区域按模式渲染 `StorySurface`、`ClueSurface`、模块阅读器或 `Paper`。 |
| 同文件第 284—311 行 | 辅助区按模式渲染 `ChapterContext` 或 `WorkspaceDetail`；对话打开时替换辅助区。 |
| `components/workspace/Paper.tsx` 第 1—6 行 | 只读是有意设计；第 129 行读取正文标记用于高亮。 |
| `components/story/StorySurface.tsx` 第 3—5 行 | 页面只读；创建和调整直接进入对话。 |
| `components/clues/ClueSurface.tsx` 第 3—5 行 | 线索图只是投影，打开页面不调用模型。 |
| `components/workspace/WorkspaceDetail.tsx` 第 1—6 行 | 共享只读详情区；「讨论调整」只准备焦点并打开对话，不调用模型。 |

## 3. 前端消费者矩阵

| 消费者 | 读取 | 服务端接口 | 性质 |
|---|---|---|---|
| `StorySurface` | 方案图、节点、阶段、时间轴、核对提醒 | `GET /story/variants`、`/variants/{id}`、`/timeline`、`/sync` | 只读；修改经对话 |
| `WorkspaceDetail` | 节点详情、线索详情、正文标记 | `GET /story/variants/{id}/nodes/{node_id}`、`/prose-marks`、`/clues/{clue_id}` | 只读 |
| `ClueSurface` | 全书线索、一跳关系 | `GET /clues?scope=story`、`/clue-relations` | 只读 |
| `ChapterContext` | 本章出现 / 计划 / 失效的构思与线索 | `GET /story/chapter-context`（分页） | 只读 |
| `Paper` | 正文、线索标记、正文标记 | `GET /file`、`/clue-marks`、`/story/variants/{id}/prose-marks` | 只读；来源阅读器另用 `/source-locations/{id}` |
| `StoryDraft` | 任务草稿与约定 | `GET /story/drafts/{operation_id}`；`POST .../confirm-constraints` | 读 + 确认 |
| `StoryCommitCard` | 故事回执、撤销 | `GET /story/changes/{change_id}`；`POST .../undo` | 读 + 撤销 |
| `ConversationPanel` / 对话 | 会话、轮次、事件、操作状态 | `GET/POST /conversations`、`/turns`、`/events`、`/operations` | 读 + 发起轮次；写入由 Agent 执行 |
| `WorkspaceToc` | 目录、搜索、导入 | `GET /files`、`/search`；`POST /imports` | 读 + 导入 |
| `ActivityPanel` | 活动记录、设置、导出、重建 | `GET/PUT/DELETE /activity...` | 读 + 管理 |
| `IdeasSurface` | 灵感列表、正文、历史、活动 | `/ideas...` | 读 + 编辑 |
| 模块库 / 装配 | 模块、修订、装配、变更集 | `/library/...`、`/assembly`、`/module-updates` | 读 + 编辑 |

`GET /story/variants/{id}/mystery` 无前端调用；`POST /story/changes` 也无前端调用，结构修改通过 Agent 工具完成。

## 4. 正文写入路径

| 路径 | 入口 | 现有校验 | 副作用 |
|---|---|---|---|
| `Workspace.write`（`files.py` 第 389 行） | 文件层唯一写入口 | 受管目录、摘要前提、元信息；`expected_digest=None` 只允许新建 | 临时文件原子替换，返回新摘要 |
| `Workspace.create_chapter`（第 406 行） | 仅被导入调用 | 标题清洗、序号取最大值加一 | 生成「正文/NNNN 标题.md」并写入标题行 |
| `Workspace.import_text`（第 464 行） | `POST /imports` | 只校验文本、分章与文件名 | `.md` 一章一文件，`.txt` 分章后逐章创建 |
| Agent `write_file`（`agent.py` 第 851 行） | 模型工具 | `before_mutation`：任务范围、读取前提、候选限制、Git 基线 | 调 `workspace.write`，记录 `file_changed` |
| Agent `edit_file`（第 888 行） | 模型工具 | 原文唯一性、区间读取前提、同 `write_file` | 局部替换后整文件写回 |
| `StoryChangeSet.prose_patches` | `update_story` 工具 / 联合提交 | 基于修订、正文摘要、来源定位 | 结构变更与正文补丁联合提交 |
| 外部编辑器 | 文件系统 | 无 | 不受应用控制，只能事后发现摘要变化 |

写入前的统一钩子是 `RunContext.before_mutation`（`agent.py` 第 262—315 行）：依次做任务范围、指纹预检、读取前提、参考完整性、版本核对和 Git 基线准备。它已有一个故事限制：处于「构思页设计」或存在场景候选时，正文必须走 `propose_story_scene`，否则报 `STORY_CANDIDATE_REQUIRED`。该限制与章节构思门槛不同，P1 需要并存而不是互斥。

## 5. 修订、幂等、联合提交与撤销

| 能力 | 现状 |
|---|---|
| 修订 | 每次结构提交后 `StoryVariant.revision = new_id("rev")`（`story_store.py` 第 455 行）；文档修订同时更新。修订是随机标识，不是内容哈希。 |
| 幂等 | `commit_change` 以 `request_key` 查历史收据；同键同负载返回同一收据，同键异负载报冲突（第 166—204 行）。 |
| 并发 | `base_revision` 必须等于当前方案修订，否则 `STORY_CHANGE_CONFLICT`（第 238—239 行）。 |
| 联合提交 | `prepare_joint` 可在锁内改写变更集并给出 `JointTarget`；正文补丁与读取依赖要求 `workspace` 已接通（第 206—215 行）。 |
| 撤销 | `undo` 只反向恢复当次触碰的对象；先核对 `record.touched` 的当前摘要，后续无关编辑会阻止撤销并列出目标（第 333—419 行）。 |
| 正文锚点 | 唯一引文定位、区间与摘要校验、摘要变化后按引文重定位、五种锚点状态（`story_bindings.py` 第 83—152 行）。 |
| 绑定构造 | 服务端按当前正文重算区间与摘要；当前要求绑定目标至少有一个 `node_id` 或 `clue_id`（第 155—200 行）。 |

`chapter_index.py` 是「这一章发生了什么」的可重建派生缓存；`story_index.py` 是跨章统一索引。两者都不能当作正式设定，P3 的章节信息索引应复用它们的证据与失效思路，而不是再建第三份人工账本。

## 6. 候选与模型辅助

`story_candidate.py` 的 `StoryCandidate` 保存正文场景候选，状态为 `draft` / `reviewing` 等，提供 `propose`、`propose_structure`、核对与修复工具，并最终产出 `StoryChangeSet`。它绑定具体操作轮次，不是按目标类型发起的通用辅助请求。P3 需要新增通用候选类型，但不能另建一套模型调用器。

## 7. 测试与样本基础

| 项目 | 现状 |
|---|---|
| 后端测试 | `pyproject.toml` 第 65—68 行：`testpaths = ["tests"]`、`pythonpath = ["."]`、`-ra`。故事相关测试在 `tests/workspace/`，覆盖 store、bindings、candidate、joint commit、chapter context 等。 |
| 后端夹具 | `tests/fixtures/story/` 已有 `alternative / blank-mystery / change-set / emoji-positions / error / flashback / long-distance / testimony` 等 JSON。 |
| 前端单测 | Vitest，`npm test`；状态与组件测试直接构造数据，没有统一 MSW 层。 |
| 浏览器测试 | Playwright，`frontend/playwright.config.ts` 第 48—52 行启动真服务；`workspace` 项目跑确定性用例，`online` 项目真调模型。 |
| 样本脚本 | `frontend/e2e/fixtures/clue_seed.py`、`story_variant_seed.py` 通过真实接口准备隔离作品。 |
| 大作品样本 | `frontend/e2e/large-workspace.spec.ts` 已创建 200 节点、300 关系并写入「林砚 / 湿手套」正文；可作为 P6 大样本的起点，但结构是旧格式，P3 后需改为新格式夹具。 |

运行命令：后端定向测试 `uv run pytest tests/workspace/<file> -q`；前端单测 `npm test`；构建 `npm run build`；本地浏览器 `npm run test:e2e:local`。

## 8. P0 对后续阶段的约束

1. P1 不得在 `Workspace` 之外新建第二套文件写入器；策略注入点放在 `check_write_intent`、`write`、`create_chapter` 的前置链。
2. P1 必须保留 `before_mutation` 的任务范围与读取前提，章节构思门槛是新增条件，不是替代条件。
3. P2 的正文保存必须复用摘要前提与 `StoryStore` 的联合提交、幂等和撤销，不新建状态文件。
4. P5 删除旧入口前，按第 3 节矩阵确认每个前端组件与其接口都已由工作台投影覆盖；`StoryCandidate`、绑定、提交与撤销属于保留领域服务。
