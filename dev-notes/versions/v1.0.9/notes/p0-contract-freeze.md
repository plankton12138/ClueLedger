# P0 契约冻结：当前格式 3、写作接口与退休清单

日期：2026-11-02。状态：**已冻结（P0）**。本文件取代 [data-and-api.md](../data-and-api.md) 中的「拟议」部分；语义仍以那份文档为准，这里只固定名称、形状与边界。任何偏离都要先改本文件，再改代码。

## 1. 冻结结论

1. 章节身份使用稳定的 `chapter_id`，正文路径是运行时附加物，不再充当身份。
2. 正文摘要继续使用 `sha256` 十六进制字符串，不带 `sha256:` 前缀，与现有 `digest_bytes` 一致。
3. 文件层类名是 `Workspace`（`files.py` 第 220 行）；计划中的 `WorkspaceFiles` 统一改称 `Workspace`。
4. 新当前格式写入 `StoryDocument.schema_version = 3` 与 `StoryVariant.format_version = 3`；旧 schema 明确拒绝，不迁移。
5. 章节构思确认与首个正文文件创建在 `start` 事务中原子完成；`plan` 保存本身不确认。
6. 正文保存只要求当前章修订与正文摘要，不要求全书修订；无关章节变化不阻断保存。
7. 模型辅助只新增两条薄路由，复用现有操作、材料、模型选择、恢复与候选存储。

## 2. 当前格式 3

### 2.1 存储与身份

```python
class StoryDocument(StrictRecord):
    schema_version: Literal[3] = 3
    workspace_lineage: str
    revision: str                  # 文档修订，任何提交后更新
    primary_variant_id: str
    variants: dict[str, StoryVariant]
    changes: dict[str, StoryChangeRecord]
    requests: dict[str, StoryReceipt]


class StoryVariant(StrictRecord):
    format_version: Literal[3] = 3
    id: Literal["main"] = "main"
    revision: str                  # 即 plan_revision
    book_intent: BookIntent | None
    chapter_plans: tuple[ChapterPlan, ...]
    information_threads: tuple[InformationThread, ...]
    information_moves: tuple[InformationMove, ...]
    bindings: tuple[ProseBinding, ...]
    constraints: tuple[Constraint, ...]
```

- `stages / nodes / clue_plans / threads / edges / event_times / clue_relations / attention_receipts / assessments` 不进入新格式。
- 核对、提醒与旧索引若仍需保留，改存独立只读记录，不挂回变体。
- `Constraint.scope.kind` 改为 `book | chapter | information_thread | prose`。

### 2.2 章节与正文

```python
class ChapterPlan(StrictRecord):
    id: str                        # ch_ 前缀，稳定身份
    order: int
    title: str | None
    intent: str                    # 本章要改变什么
    information_move_ids: tuple[str, ...]
    revision: str                  # 本章构思内容修订
    confirmed_revision: str | None # 等于 revision 才表示已确认
    prose_path: str | None         # 开始前为 None

class InformationThread(StrictRecord):
    id: str
    summary: str                   # 作者可读的一句话
    author_truth: str | None       # 隐藏事实，不进入读者视角

class InformationMove(StrictRecord):
    id: str
    thread_id: str
    chapter_id: str
    statement: str
    effect: Literal["introduce", "reinforce", "reframe", "refute", "resolve", "knowledge"] | None
    knowledge_changes: tuple[KnowledgeChange, ...] = ()

class ProseBinding(StrictRecord):
    id: str
    move_id: str
    path: str
    digest: str                    # 绑定时的正文摘要
    start: int                     # Unicode 码点，左闭右开
    end: int
    quote: str
    source: Literal["author", "model_adopted", "index_confirmed"]
```

**章节身份规则**

- `chapter_id` 在创建计划壳时生成，之后不随标题、顺序或正文路径变化。
- 正文文件名继续为 `正文/NNNN 标题.md`，序号只用于阅读顺序；`order` 是工作台排序依据。
- 一个正文路径最多属于一个计划；`prose_path` 为空表示尚未开始。
- 导入或外部写入产生的正文可能没有计划。工作台在全书投影中把它们列为 `unplanned_prose`，按 `prose_path` 只读打开，不自动创建计划。

**最小构思的确定性回退**

作者只写一句 `intent` 时，服务端必须能在无模型条件下形成至少一条信息变化：

1. 若请求已带 `information_moves`，逐条校验归属后采用。
2. 若未带，创建一条 `InformationMove`，`thread_id` 指向自动创建的信息线，`statement` 取 `intent` 的规范化首句，`effect=None`。
3. 信息线摘要取同一句的截断文本，作者之后可在工作台修改；模型候选只能替换文字，不能替作者确认。

这条回退保证「一句话即可满足门槛」，不把模型设为前置条件。

### 2.3 修订定义

| 修订 | 计算范围 | 变化时机 |
|---|---|---|
| `plan_revision` | `StoryVariant.revision`（沿用现有 `rev_` 随机标识） | 任一正式计划、信息线、信息变化或正式绑定变化 |
| `chapter_revision` | 本章 `intent` + 排序后的信息变化内容（`id / thread_id / statement / effect / knowledge_changes`）的规范化摘要，前缀 `ch_` | 本章意图或信息变化变化；标题、顺序、其他章节变化不影响 |
| `context_revision` | 本章 `chapter_revision` + 直接相关信息线的 `id / summary` 摘要，前缀 `ctx_` | 本章或相关信息线变化 |
| `prose_digest` | 当前正文原始字节的 SHA-256 十六进制 | 人工保存、模型采用、Agent 写入、外部文件变化 |
| `index_revision` | 确定性索引内容 + `prose_digest` + 分析状态摘要，前缀 `idx_` | 锚点、分析状态或语义候选变化 |

`index_revision` 是内容摘要，不是随机标识；同一输入必须得到同一值。客户端只有在作品、章节、`chapter_revision`、`context_revision`、`prose_digest` 全部匹配时才接受结果。

## 3. 冻结接口

前缀统一为 `/api/file-workspaces/{workspace_id}`。

### 3.1 工作台读取

`GET /story/workbench`

- 无查询参数：返回全书。
- `?chapter_id=...`：返回指定计划章节。
- `?prose_path=...`：返回未挂计划的正文；与 `chapter_id` 互斥。

响应在 [data-and-api.md](../data-and-api.md) 第 4 节的 `WritingWorkbenchView` 基础上增加：

```typescript
interface BookView {
  intent: string | null;
  chapters: ChapterPlanSummary[];
  information_threads: InformationThreadSummary[];
  unplanned_prose: Array<{
    path: string;
    title: string;
    digest: string;
    chapter_number: number | null;
  }>;
}

interface ChapterView {
  plan: ChapterPlanView | null;   // prose_path 选择时为 null
  can_start: boolean;
  prose: null | {
    path: string;
    digest: string;
    text: string;
    editable: boolean;
    blocked_reason: "chapter_plan_required" | "chapter_plan_stale" | null;
  };
  index: ChapterInformationIndex | null;   // 无计划时为 null
}
```

- 未开始且构思有效的章节：`can_start=true`，`prose=null`。
- 未挂计划的正文：`plan=null`、`editable=false`、`blocked_reason="chapter_plan_required"`。
- 计划改动后未重新确认：`editable=false`、`blocked_reason="chapter_plan_stale"`。

### 3.2 全书构思

`PUT /story/book-intent`

```json
{
  "text": "暴雪封山的旅馆发生命案，林砚从仍在滴水的手套开始怀疑管家。",
  "expected_plan_revision": "rev_...",
  "request_key": "book-intent:rev_...:..."
}
```

响应：`{plan_revision, receipt}`。空文本拒绝，错误码 `CHAPTER_PLAN_REQUIRED` 不适用于全书；使用 `BOOK_INTENT_REQUIRED`。

### 3.3 章节计划壳

`POST /story/chapters`

```json
{
  "title": "第一章",
  "after_chapter_id": null,
  "expected_plan_revision": "rev_...",
  "request_key": "chapter-create:..."
}
```

响应：`{chapter_id, order, plan_revision, receipt}`。只创建计划壳，不分配正文路径，不写文件。

### 3.4 保存章节构思

`PUT /story/chapters/{chapter_id}/plan`

```json
{
  "title": "第一章",
  "intent": "封山、入住、管家说谎，湿手套最后出现。",
  "information_moves": null,
  "expected_chapter_revision": "ch_...",
  "expected_plan_revision": "rev_...",
  "request_key": "chapter-plan:..."
}
```

- `information_moves=null` 触发 2.2 的确定性回退；显式数组则整体替换并校验归属。
- 响应：`{chapter_revision, plan_revision, normalized: {intent, moves, threads}, receipt}`。
- 保存不改变 `confirmed_revision`。已确认章节的内容一旦变化，正文转为只读，直到重新开始。

### 3.5 开始写作

`POST /story/chapters/{chapter_id}/start`

```json
{
  "expected_story_revision": "rev_...",
  "expected_chapter_revision": "ch_...",
  "attach_path": null,
  "attach_digest": null,
  "adopt_candidate_id": null,
  "request_key": "start:..."
}
```

- 校验最小构思与当前章节修订，在作品与章节锁内签发一次性 `ChapterStartGrant`；凭据不出现在 HTTP 响应、工具参数或模型上下文。
- `attach_path` 用于导入或外部已有正文：校验路径属于正文目录、摘要匹配、未被其他计划占用；成功时只附加，不重写文件。
- 未附加时按 `order` 与标题分配新路径，创建只含标题行的文件；若采用候选已确认，可同时写入候选文本。
- 响应：`{chapter_id, path, prose_digest, chapter_revision, plan_revision, receipt}`。
- 同一 `request_key` 返回同一结果；失败不得留下「已确认无正文」或「有正文无计划」。

### 3.6 保存正文

`PUT /story/chapters/{chapter_id}/prose`

```json
{
  "text": "# 第一章\n……",
  "expected_digest": "a1b2...",
  "expected_chapter_revision": "ch_...",
  "draft_revision": "local_27",
  "candidate_id": null,
  "request_key": "save:..."
}
```

响应：

```typescript
interface ProseSaveResult {
  path: string;
  prose_digest: string;
  plan_revision: string;
  chapter_revision: string;
  index_revision: string;
  index: ChapterInformationIndex;
  analysis_job_id: string | null;
  committed_change_id: string;
}
```

- 正文摘要冲突映射为 `PROSE_DIGEST_MISMATCH`，响应附带当前摘要与服务端文本，供客户端比较；文件层原始 `FILE_STALE_CONFLICT` 不直接透出。
- 同一 `draft_revision` 与请求键重试不得重复提交。
- 保存走 `Workspace.write` 与现有联合提交；不得新增第二套状态文件。

### 3.7 模型辅助

`POST /creative-assists`（全局）与 `POST /api/file-workspaces/{workspace_id}/creative-assists`（作品内）。

```typescript
type CreativeTarget =
  | { kind: "new_workspace"; draft_id: string }
  | { kind: "workspace_title"; workspace_id: string }
  | { kind: "book_intent"; workspace_id: string }
  | { kind: "chapter_plan"; workspace_id: string; chapter_id: string }
  | { kind: "prose"; workspace_id: string; chapter_id: string }
  | { kind: "idea_draft"; workspace_id: string | null; draft_id: string }
  | { kind: "module_draft"; module_id: string | null; draft_id: string }
  | { kind: "conversation"; workspace_id: string; conversation_id: string };

interface CreativeAssistInput {
  kind: AssistKind;
  target: CreativeTarget;
  base: {
    plan_revision?: string;
    chapter_revision?: string;
    context_revision?: string;
    prose_path?: string;
    prose_digest?: string;
    draft_revision?: string;
  };
  selection?: { start: number; end: number; quote: string };
  author_note?: string;
  draft_text?: string;      // 仅 new_workspace / idea_draft / module_draft
  request_key: string;
}
```

- `draft_id` 是客户端不透明令牌，服务端只回显，不用它读取任何数据。
- 服务端按 `kind` 与 `target` 校验归属；客户端不得传任意路径、系统提示或其他作品的对象。
- 作品内路由必须验证 `workspace_id` 与目标一致；全局路由只接受 `new_workspace`、`idea_draft`、`module_draft`，且只能读请求明确给出的文本。
- 响应沿用现有候选存储扩展：`candidate_id / target / kind / text / alternatives / base / proposed_bindings / status`。
- 无模型时返回 `MODEL_UNAVAILABLE`，保留人工路径；模型按钮仍可见但禁用。

## 4. 错误码

沿用 [data-and-api.md](../data-and-api.md) 第 10 节，并补充：

| 错误码 | 条件 | 建议 |
|---|---|---|
| `BOOK_INTENT_REQUIRED` | 全书意图为空 | 写一句作品起点，或选择「交给模型构思」 |
| `CHAPTER_PLAN_INCOMPLETE` | 章节缺少可执行变化或信息变化 | 补一句本章改变什么；不要求字段清单 |
| `CHAPTER_PLAN_STALE` | 已确认构思在写入前变化 | 重新读取当前摘要，保留作者草稿 |
| `PROSE_PATH_CONFLICT` | 目标正文已属于另一计划 | 选择空闲路径或先解除原计划 |
| `STORY_FORMAT_UNSUPPORTED` | 读到旧 schema 或旧变体格式 | 新建作品；本版不迁移旧数据 |
| `ASSIST_TARGET_INVALID` | 目标类型、归属或 `draft_text` 不合法 | 重新选择目标后重试 |

## 5. 退休清单（P5 执行）

| 旧能力 | 证据 | 处理 |
|---|---|---|
| `mode=story / clues / chapter` | `navigation.ts` 第 14—27、56—77 行 | 解析后重定向统一写作页，不保留隐藏页面 |
| 顶部「构思」「线索」入口 | `AppHeader.tsx` 第 15—37 行 | 删除，只保留写作与全局能力 |
| `StorySurface` / `ClueSurface` / `ChapterContext` | `WritingLayout.tsx` 第 266—311 行 | 由工作台主区与页边索引替代 |
| `WorkspaceDetail` 作为日常入口 | 同文件 | 由信息项详情替代 |
| `GET /story/chapter-context` | 仅 `ChapterContext` 调用 | 工作台投影上线后删除 |
| `GET /story/variants/{id}/nodes/{node_id}` | 仅 `WorkspaceDetail` 调用 | 节点详情退役后删除 |
| `GET /story/variants/{id}/mystery` | 无前端调用 | P5 直接删除 |
| `GET /story/variants/{id}/timeline`、`/sync` | 仅 `StorySurface` 通过 `state/story.ts` 调用 | 画面退役后删除；时间轴领域服务若仍被上下文使用则保留 |
| `GET /clues?scope=story`、`GET /clue-relations` | 仅 `ClueSurface` 调用 | 页面消费者删除；正文来源能力按信息线重新接入 |
| `GET /story/variants/{id}/prose-marks` | `Paper`、`WorkspaceDetail` 调用 | 改为信息变化绑定投影后删除 |
| `POST /story/changes` | 无前端调用，Agent 走领域服务 | 新适配器完成后删除 HTTP 壳，保留 `StoryChangeService` |
| `Paper` 作为正文阅读器 | `Paper.tsx` 第 1—6 行 | 保留通用阅读渲染，正文改用编辑器 |

**保留**：`StoryCandidate`、`story_store` 提交 / 撤销、`story_bindings` 定位、`story_changes` 联合提交、`materials` / `context` 装配、`source_locations`。

## 6. 未冻结项

| 项目 | 原因 | 冻结时点 |
|---|---|---|
| 「完成本章」接口 | 依赖 P3 语义索引与差异呈现 | P3 结束前补入本文件 |
| 章节信息索引的完整视图字段 | 需先实现确定性投影 | P2 结束时以真实响应冻结 |
| 编辑内核（原生 textarea 或专用编辑器） | 需真实长章、输入法与撤销验证 | P2 编辑器开工前，见 [p0-editor-verification.md](p0-editor-verification.md) |
| Aqua token 名称 | 需与现有 `index.css` 收敛 | P4 视觉收敛前 |

## 7. P1 实施修订

P1 按「新格式与旧格式并存、新作品只写格式 3」落地，避免在 P1—P4 中断整个应用。以下差异已经实际生效；P5 删除旧格式时一并移除。

| 项目 | 冻结文本 | P1 实际实现 | 原因 |
|---|---|---|---|
| 存储形状 | `chapter_plans / information_threads / information_moves` 为元组 | 内部按 `id` 索引的映射，`information_move_ids` 为 JSON 列表 | 复用现有 `StoryStore` 的对象级 diff、撤销与摘要；接口层仍返回数组 |
| 文档版本 | `StoryDocument.schema_version = 3` | `schema_version` 仍为 2；格式标记放在 `StoryVariant.format_version`（新作品 3，旧作品默认 2） | 同一修订根、提交与撤销引擎在过渡期同时服务两种格式；P5 再提升文档 schema |
| 知识变化 | `InformationMove.knowledge_changes` | P1 不落该字段，留到 P3 以信息变化引用补齐 | 旧 `KnowledgeChange` 以节点为事件位置，当前格式没有节点；先保证最小构思可提交 |
| 绑定来源 | `author / model_adopted / index_confirmed` | 字面量同时保留旧值 `candidate / outline_from_prose / manual / inferred` | 旧模块仍读取旧绑定；新绑定只写新值 |
| 开始凭据 | 一次性 `ChapterStartGrant`，不暴露 | 在 `StoryStore.commit_change` 的锁内 `prepare_joint` 完成授权、确认、分配路径与创建文件；授权对象不持久化、不出锁 | 复用现有联合提交与回滚；锁内授权天然一次性，避免第二套凭据存储 |
| 章节修订 | 由内容摘要计算 | `chapter_content_revision()` 计算 `ch_` + 32 位摘要；计划校验强制比对 | 标题、顺序、确认状态与正文路径不参与；改标题不失效确认 |
| Agent 修订前提 | 每次正文写入都带章修订 | P1 强制「已确认构思」；`expected_chapter_revision` 参数已贯通，Agent 尚未取值，P2 接材料冻结修订 | 先建立不可绕过的门槛；修订绑定与材料装配在 P2 完成 |
| 新 HTTP 端点 | P1 冻结名称 | P1 只实现领域服务 `start_chapter / save_chapter_prose`，不挂路由 | P2 与工作台投影、正文保存响应一起接线，避免先出现无消费者的端点 |
| 导入 | 外部正文只读 | 导入允许创建「未挂计划」正文；之后应用内写入被拒绝，直到附加计划 | 导入不是绕过：内容保留，但不可继续编辑 |
| 移动 / 删除 | 未单独规定 | 已开始的章节禁止直接改文件名；删除前经过同一门槛检查 | 路径由开始事务维护，任意外部改名会让计划失配 |

### 7.1 P1 完成情况

- [x] 格式 3 模型、操作、diff、摘要与撤销。
- [x] 旧格式操作与当前格式操作互斥，不能写入同一文档。
- [x] `chapter_content_revision` 与「改标题不失效、改其他章不影响」测试。
- [x] `ChapterWritePolicy` 注入 `Workspace.write / check_write_intent / check_delete_intent`。
- [x] 开始事务：确认构思、分配路径、创建初始文件在一次联合提交中完成，可撤销。
- [x] 导入创建未挂计划正文，后续编辑被拒绝。
- [x] 工作台投影与 HTTP 路由（P2）。
- [x] Agent 写入携带章节修订（P2）。

实施记录与验证命令见 [P1 结构与门槛](p1-structure-and-gate.md)。

## 8. P2 实施修订

P2 把工作台与正文保存接到真实接口，同时保持「确定性先行、语义后置」。以下差异已实际生效。

| 项目 | 冻结文本 | P2 实际实现 | 原因 |
|---|---|---|---|
| 索引状态 | `analysis.state` 可为 `not_needed / queued / running / ready / unavailable / failed` | 无正文为 `not_needed`；有正文固定 `unavailable`，`based_on_digest` 与 `operation_id` 为空 | 语义任务在 P3 接入；不能把确定性结果标成完整分析 |
| 模型支持 | 返回真实可用性与支持类型 | 固定 `available=false`、`disabled_reason="模型辅助尚未接入"` | P3 之前没有辅助端点；不伪造可用 |
| 绑定刷新 | 保存响应携带重定位后的索引 | 唯一引文在同一次提交中更新区间与摘要；缺失或歧义保留原绑定，由索引报告 | 避免保存成功但绑定落后一版；保留原引用供恢复 |
| 未挂计划正文 | 只读打开并提示补构思 | 全书投影列 `unplanned_prose`；按路径打开 `plan=null`、`editable=false` | 与外部正文规则一致 |
| 计划保存 | 作者只写一句，服务端整理 | 无模型时创建一条信息线 + 一条信息变化；显式空数组拒绝；跨章复用变化标识拒绝 | 保证「一句话即可满足门槛」且不产生跨章错挂 |
| 章节顺序 | `order` 决定显示顺序 | 创建时可指定插入位置；被推后的章节只改 `order`，不改修订与确认 | 排版调整不等于内容变化 |
| 候选采用 | 正文保存可带 `candidate_id` | P2 明确拒绝，返回 `ASSIST_TARGET_INVALID` | 候选存储在 P3 才接入；静默忽略会让客户端以为已采用 |
| HTTP 冲突 | 错误码表 | `CHAPTER_PLAN_REQUIRED / STALE`、`PROSE_DIGEST_MISMATCH`、`STORY_FORMAT_UNSUPPORTED` 映射 409 | 这些都是状态前提冲突，不是输入格式错误 |
| Agent 修订 | 服务端从冻结材料取得章修订 | 写入前从故事状态读取当前章修订并传入文件层 | P2 先保证不能拿旧修订写入；材料快照随 P3 任务装配一起完善 |

### 8.1 P2 完成情况

- [x] 工作台只读投影：全书、章节、未挂计划正文与确定性索引。
- [x] `GET /story/workbench`、`PUT /story/book-intent`、`POST /story/chapters`、`PUT .../plan`、`POST .../start`、`PUT .../prose`。
- [x] 正文保存按摘要与章修订校验，冲突返回 409 并保留服务端版本。
- [x] 唯一引文重定位与摘要更新；缺失 / 歧义不猜测位置。
- [x] Agent 写入使用当前章修订，计划变化后拒绝。
- [x] 未挂计划正文只读，补构思前不能继续写。
- [x] 语义索引任务与候选（P3）。
- [ ] 工作台前端入口与编辑器（P4）。

实施记录与验证命令见 [P2 实施记录](p2-save-and-index.md)。

## 9. P3 实施修订

P3 落地了「候选按目标生成、索引只给候选、失败不阻断正文」。以下差异已经实际生效。

| 项目 | 冻结文本 | P3 实际实现 | 原因 |
|---|---|---|---|
| 辅助入口 | 作品内与全局两条薄路由 | 每条路由另有候选读取与停止；全局入口只接受 `new_workspace / idea / module` 且不允许作品或章节标识 | 页面需要轮询与停止；全局目标没有作品上下文 |
| 候选状态 | `streaming / ready / stale / failed / discarded` | 停止复用 `failed` 并保留部分文本；不新增状态 | 不扩大客户端状态机；停止是失败的一种事实 |
| 运行链路 | 复用现有操作、事件、停止与恢复 | 复用模型快照与流式协议，候选单独持久化；页面按候选读取，不占用会话事件 | 创作辅助没有会话轮次；候选文本不应混入聊天记录 |
| 过期 | 采用前核对基础修订 | 生成结束时复核作品、章节与摘要；变化则 `stale`；正文采用再核对一次 | 模型可能在作者继续编辑后才返回 |
| 索引任务 | 保存后排队增量分析 | 每章一条最新任务；新保存取消旧任务；任务基础摘要或作品修订变化时不发布 | 避免旧结果覆盖新正文 |
| 索引输出 | 可审计候选 | 只接受逐行 JSON 的 `statement`；引文唯一才给精确区间 | 模型自由文本不能当结构化事实 |
| 保存响应 | 返回确定性与分析状态 | 有任务时返回 `analysis_job_id`，索引状态改为 `queued`；语义候选在下一次工作台读取时合并 | 任务异步，保存响应不能等待模型 |
| 恢复 | 刷新与重启可恢复事实 | 候选或索引任务没有运行实例时改为失败并写明中断 | 不继续显示无法继续的生成 |
| 模型可用性 | 真实可用性 | 工作台临时构造并关闭模型快照；辅助请求无模型返回 `MODEL_UNAVAILABLE` | 不伪造“已配置” |

### 9.1 P3 完成情况

- [x] 通用候选存储、按目标发起、按请求键幂等。
- [x] 作品内与全局辅助路由、候选读取与停止。
- [x] 生成结束复核基础修订；过期候选不能直接采用。
- [x] 正文保存携带 `candidate_id` 时核对候选目标与基础修订。
- [x] 增量语义索引任务、每章单任务、取消与失效结果。
- [x] 索引失败或模型不可用时正文仍保存；工作台显示真实状态。
- [x] 候选与索引任务的进程重启恢复。
- [ ] 前端模型按钮、候选面板与采用交互（P4）。
- [ ] 统一操作事件流与在线模型验收（P4/P6）。

实施记录与验证命令见 [P3 实施记录](p3-assist-and-index.md)。
