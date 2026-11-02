# v1.0.9 数据、接口与同步契约

日期：2026-11-02。状态：**P1—P5 已按章节门槛设计实施；同日修订为“正文为主”，P6 拆门槛改造待实施**。本文描述修订后的目标契约；已实施部分的记录见 [P0 契约冻结](notes/p0-contract-freeze.md)与 `notes/`，历史记录不回改。当前格式 3 为已实施的中间格式，改造升为当前格式 4。

## 1. 设计原则

1. 正式构思、正文事实、自动分析候选和聊天建议分开保存。
2. 页面读取一个工作台投影，但不能把投影当作新的可编辑账本。
3. 全书构思前置由服务端执行：任何正文写入路径都要求作品意图非空，不能只依赖前端按钮状态；章节没有构思前置。
4. 确定性锚点与模型语义分析分开：前者随保存完成，后者异步且只产生候选。
5. 模型辅助不要求作者先写提示词；它依据目标类型和当前上下文生成候选。
6. 模型候选不产生写权限，不自动覆盖作者草稿，也不自动改变正式内容。
7. 每个响应明确绑定作品、计划修订、正文摘要和索引修订，旧响应不得覆盖新页面。

## 2. 当前格式 4

为复用现有修订、联合提交和撤销能力，内部继续使用 `StoryVariant` 作为存储根；它不出现在作者界面。v1.0.9 改造直接采用当前格式 4，不迁移格式 3 及更早数据。

以下是字段边界示意，不是当前可运行代码：

```python
class BookIntent(BaseModel):
    text: str                       # 一段自然语言，可同时包含处境和起点
    source: Literal["author", "model_adopted"]


class ChapterPlan(BaseModel):
    id: str
    order: int
    title: str | None
    prose_path: str | None          # 章节创建事务分配；创建即建文件


class InformationThread(BaseModel):
    id: str
    summary: str                   # 例如“湿手套为何仍在滴水”
    author_truth: str | None       # 作者隐藏事实，不进入读者视角


class InformationMove(BaseModel):
    id: str
    thread_id: str
    chapter_id: str
    statement: str                 # 本章中这条信息发生的变化
    effect: Literal[
        "introduce", "reinforce", "reframe", "refute", "resolve", "knowledge"
    ] | None                       # 内部推断，可未知，不是作者必填项
    knowledge_changes: tuple[KnowledgeChange, ...] = ()


class ProseBinding(BaseModel):
    id: str
    move_id: str
    path: str
    digest: str
    start: int                     # Unicode 码点，左闭右开
    end: int
    quote: str
    source: Literal["author", "model_adopted", "index_confirmed"]


class StoryVariant(BaseModel):
    format_version: Literal[4] = 4
    id: Literal["main"] = "main"
    revision: str
    book_intent: BookIntent | None
    chapter_plans: tuple[ChapterPlan, ...]
    information_threads: tuple[InformationThread, ...]
    information_moves: tuple[InformationMove, ...]
    bindings: tuple[ProseBinding, ...]
    constraints: tuple[Constraint, ...]
```

与格式 3 的差异：`ChapterPlan` 只剩顺序、可选标题和正文路径；章节构思、信息变化 ID、章节内容修订和确认修订不再存在。`InformationMove` 只能由确认候选入账（或作者明确建立信息线）创建。

### 2.1 服务端判定

全书构思有效条件：`book_intent.text` 在去除空白后非空，并且服务端能把它解释为核心处境 / 冲突或起点。系统可以返回需要补充的一处关键缺口，但不能要求固定字段齐全。这是唯一的写作门槛；章节创建与正文写入只要求作品已有有效全书构思。

确认入账有效条件：

- 候选依据的正文摘要与当前章节摘要一致；
- 候选引用的原句仍在当前正文中，且位置可核对；
- 相关对象都属于同一作品和章节。

通过后，同一联合提交创建信息线（若候选未指向已有信息线）、信息变化与绑定；任何一步失败整体不提交。入账不修改正文原句。

### 2.2 正式内容与候选

下列内容不得混入 `StoryVariant`：

- 尚未采用的模型起草文本；
- 自动索引发现的“正文新增”；
- 章节方向建议（`chapter_direction`）——它只是参考文字，永远不进入正式数据；
- 聊天中的建议和假设；
- 过期修订生成的候选；
- 模型对读者反应的推测。

候选保存在现有任务 / 候选存储的扩展结构中：

```typescript
interface CreativeTextCandidate {
  candidate_id: string;
  workspace_id: string | null;       // 新建作品或全局模块候选可以尚未绑定作品
  target: CreativeTarget;
  kind: AssistKind;
  text: string;
  alternatives?: string[];
  base: {
    plan_revision?: string;
    context_revision?: string;
    prose_path?: string;
    prose_digest?: string;
    draft_revision?: string;
  };
  proposed_bindings?: ProposedBinding[];
  status: "streaming" | "ready" | "stale" | "failed" | "discarded";
  created_at: string;
}
```

采用候选的本质是把候选文字放入当前人工草稿，再通过目标领域原有保存接口提交。正文保存可以附带 `candidate_id`，服务端据此重新验证绑定建议；候选本身不能直接写正式数据。方向建议候选没有采用动作，只能阅读与复制。

## 3. 统一写入策略

### 3.1 策略位置

统一写入策略（已实施的 `ChapterWritePolicy`，改造后校验全书构思）是正文受管路径的强制前置条件。拟议调用顺序：

```text
HTTP 正文保存 / 章节创建 / Agent write_file / Agent edit_file
    → WorkspaceFiles.check_write_intent
    → 写入策略.require_book_intent()
    → 摘要与授权预检
    → Git / 联合提交准备
    → WorkspaceFiles.write
    → 锚点更新与索引投影
```

策略必须发生在准备 Git 基线或写入临时状态之前。角色小传、固定设置和模块文件不套用写作门槛。策略对象是作品：作品已有有效全书构思即放行，与具体章节无关。

正文写入不需要章节修订前提；正文并发由 `expected_digest` 控制。Agent 写入与人工编辑经过同一策略，模型不能凭生成文本绕过。

### 3.2 章节创建事务

`POST /story/chapters`

请求：

```json
{
  "expected_story_revision": "plan_r12",
  "request_key": "create:chapter-2:..."
}
```

服务端在一个领域事务中：

1. 校验作品已有有效全书构思。
2. 分配章节顺序与唯一正文路径，创建只含章节标题的初始文件。
3. 提交章节登记与文件创建，记录可撤销回执。

重复 `request_key` 返回同一结果。任何步骤失败都整体回滚，不留下章节登记或正文文件其中之一。已实施的 `POST /story/chapters/{chapter_id}/start`（确认构思并开始写作）在改造中移除：不存在章节确认环节。

### 3.3 正文保存

`PUT /story/chapters/{chapter_id}/prose`

```json
{
  "text": "# 第一章\n……",
  "expected_digest": "sha256:...",
  "draft_revision": "local_27",
  "candidate_id": null,
  "request_key": "save:chapter-1:local-27"
}
```

保存响应：

```typescript
interface ProseSaveResult {
  path: string;
  prose_digest: string;
  plan_revision: string;
  context_revision: string;
  index_revision: string;
  index: ChapterInformationIndex;       // 已完成的确定性结果
  analysis_job_id: string | null;       // 需要语义增量分析时返回
  committed_change_id: string;
}
```

同一 `draft_revision` 和请求键重试不得重复提交。正文摘要冲突时返回当前摘要和可安全比较的服务端版本，保留客户端文字；不自动选择任一版本。其他章节的正式变化不影响当前章保存。

### 3.4 外部正文

文件系统外部写入不受 HTTP 策略控制。重新扫描时：

- 作品已有全书构思：按外部新摘要更新确定性锚点并排队增量分析，应用内可继续写入。
- 作品尚无全书构思：保留并显示正文，但 `editable=false`、`reason=book_intent_required`；作者补齐意图后才恢复应用内写入。
- 不自动根据正文补写全书构思；可以提供“根据现有正文提出意图候选”。

### 3.5 确认入账

`POST /story/index-candidates/{candidate_id}/confirm`

请求：

```json
{
  "expected_prose_digest": "sha256:...",
  "request_key": "confirm:candidate-31:..."
}
```

服务端按 2.1 的确认入账条件核对后，在同一联合提交中创建信息线（若需要）、信息变化与绑定，返回 `committed_change_id` 与最新 `plan_revision`。重复 `request_key` 返回同一结果。摘要或原句不匹配时拒绝确认，候选保留并标记依据修订；不产生部分提交。入账结果可整体撤销。

## 4. 工作台读投影

本节故事工作台接口的完整前缀仍为 `/api/file-workspaces/{workspace_id}`。

`GET /story/workbench` 返回全书构思；`GET /story/workbench?chapter_id=...` 返回指定章节。两者共享修订和信息线身份，不要求浏览器拼接多个旧页面接口。

```typescript
interface WritingWorkbenchView {
  workspace_id: string;
  selection: { kind: "book" } | { kind: "chapter"; chapter_id: string };
  plan_revision: string | null;
  book: {
    intent: string | null;
    chapters: ChapterSummary[];
    information_threads: InformationThreadSummary[];
  };
  chapter: null | {
    chapter_id: string;
    order: number;
    title: string | null;
    prose: null | {
      path: string;
      digest: string;
      text: string;
      editable: boolean;
      blocked_reason: "book_intent_required" | null;
    };
    index: ChapterInformationIndex;
  };
  model_support: {
    available: boolean;
    disabled_reason: string | null;
    supported_assists: AssistKind[];
  };
  view_revision: string;
}

interface ChapterInformationIndex {
  index_revision: string;
  plan_revision: string;
  context_revision: string;          // 当前章及直接相关信息线的组合修订
  prose_digest: string | null;
  analysis: {
    state: "not_needed" | "queued" | "running" | "ready" | "unavailable" | "failed";
    based_on_digest: string | null;
    operation_id: string | null;
  };
  items: InformationIndexItem[];
}

interface InformationIndexItem {
  item_id: string;
  thread_id: string | null;
  move_id: string | null;
  label: string;
  placement: "written" | "observed_candidate" | "alignment";
  occurrences: Array<{
    path: string;
    digest: string;
    quote: string;
    start: number | null;
    end: number | null;
    state: "exact" | "relocated" | "missing" | "ambiguous";
  }>;
  candidate_id: string | null;
  explanation: string | null;
}
```

投影规则：

- `written` 至少有一个当前摘要下可验证出处；同章多次出现保留多个 occurrence。
- `observed_candidate` 来自未采用自动分析，不进入正式信息线；作者确认入账（3.5）后才成为 `written`。
- `alignment` 必须指出具体信息线与具体原句的差异；有意谎言、角色不知情和作者隐藏真相不能误判为普通冲突。
- 空列表是合法结果：本章没有信息变化时由前端给出空章反馈，服务端不制造占位项。
- 读取、模型或来源失败必须由独立状态表达，不能伪装为空。
- 这是作者视图，禁止直接作为读者试读上下文。

## 5. 实时更新与修订

### 5.1 修订

| 修订 | 代表什么 | 何时变化 |
|---|---|---|
| `plan_revision` | 整份正式作品意图、章节、信息线与正式绑定 | 任一正式计划或绑定变化 |
| `context_revision` | 当前章和直接相关跨章信息的组合输入 | 当前章或相关信息线变化；无关章节变化不影响它 |
| `prose_digest` | 当前章节正文原始文本 | 人工保存、已授权模型采用、Agent 修改或外部文件变化 |
| `index_revision` | 对上述两者的确定性投影与当前分析候选 | 锚点更新、分析状态变化、语义候选返回或失效 |

客户端只接受同时匹配当前作品、相关上下文修订和正文摘要的结果。全局 `plan_revision` 用于刷新全书投影；无关章节变化不应让当前正文无法保存。`index_revision` 新但基础摘要旧的响应仍然过期。已实施的 `chapter_revision` 随格式 4 移除。

### 5.2 保存后的顺序

1. 保存正文并计算新摘要。
2. 在同一请求内重定位现有绑定并返回确定性索引。
3. 若变化范围可能包含新信息或差异，返回 `analysis_job_id`。
4. 前端通过现有操作事件流或有界轮询读取任务；切章、换作品或新保存时取消订阅。
5. 任务只在基础修订仍然匹配时发布候选；否则标记 stale。

自动分析失败时 `analysis.state=failed`，错误可重试但不阻止正文保存。没有可用模型时为 `unavailable`；页面仍显示确定性绑定。

### 5.3 合并与限流

- 一个作品的一章同一时间只有一个活动索引任务。
- 连续保存合并为最新摘要；未开始任务直接替换，运行中任务请求取消，无法取消时结果丢弃。
- 输入范围为变化段落、相邻段落、全书构思和直接相关信息线；跨章读取只按相关信息线发生。
- 自动分析不使用本地模型。在线模型未配置时返回合法不可用状态，不静默换供应商。

## 6. 模型辅助协议

### 6.1 创作输入范围

P0 已生成 [创作性文本输入清单](notes/creative-input-inventory.md)，每一项登记组件、保存目标、模型动作和无模型行为。至少覆盖：

- 作品创建与重命名；
- 灵感记录与编辑；
- 模块名称及创作内容；
- 全书意图与章节标题；
- 章节方向建议（空正文章节的起步入口之一，参考文字不进入正式数据）；
- 正文起草、续写与改稿；
- 章末差异处理说明；
- 对话输入的下一步建议。

搜索、筛选、路径、URL、连接名称、模型 ID、密钥和数值设置是控制信息，不进入模型辅助协议。

### 6.2 发起辅助

拟议使用同一领域服务的两条薄路由：

- `POST /api/file-workspaces/{workspace_id}/creative-assists`：需要当前作品上下文的构思、正文、灵感、作品重命名和工作区模块内容。
- `POST /api/creative-assists`：新建作品尚无作品 ID，或编辑全局模块时使用；只能读取请求明确提供的草稿与允许的全局来源。

两条路由返回同一候选类型、复用同一模型运行与恢复服务，不能形成两套提示词或候选存储。

```typescript
type AssistKind =
  | "book_intent"
  | "chapter_direction"
  | "title"
  | "prose_draft"
  | "prose_continue"
  | "alignment_resolution"
  | "idea"
  | "module_content"
  | "next_action";

interface CreativeAssistInput {
  kind: AssistKind;
  target: CreativeTarget;
  base: {
    plan_revision?: string;
    context_revision?: string;
    prose_path?: string;
    prose_digest?: string;
    draft_revision?: string;
  };
  selection?: { start: number; end: number; quote: string };
  author_note?: string;                 // 可选，不能要求非空
  request_key: string;
}
```

服务端按 `kind` 形成可审计的任务意图，例如“根据当前全书构思与已完成章节提出方向建议候选”；客户端不发送隐藏的长提示词。任务复用现有模型选择、材料预算、停止、恢复、事件和候选存储，不另建一套模型调用器。

`CreativeTarget` 必须是按类型区分的稳定身份，例如当前章节、指定灵感草稿或指定模块草稿；服务端验证作品归属和可读范围。客户端不能把任意文件路径、任意系统提示或另一个作品的对象塞进辅助请求。全局路由只接受尚未绑定作品的命名草稿和有权限的全局模块目标。

`author_note` 允许作者补充方向，但为空时仍可执行。对于完全空白作品，模型只能给出多个悬疑起点候选，不能声称其中任何一个已经由作者决定。

### 6.3 候选采用

- 标题、意图、灵感和模块内容：选择候选后填入对应草稿；作者仍执行正常保存。
- 方向建议：只能阅读与复制；没有采用动作，不进入正式数据。
- 正文：支持替换当前空稿、插入光标或替换明确选段；编辑器先产生新的本地草稿，再走正文保存。
- 章末处理：选择候选后填入处理指令；明确发送后才执行领域变更。
- 下一步建议：选择后填入对话框；不自动发送。

候选依据修订已变化时，默认只能比较或复制；“强制采用”不作为 v1.0.9 功能。服务端保存时再次校验目标与基础修订，不能信任客户端标记。

### 6.4 无模型、失败和恢复

- 模型按钮始终占有稳定位置。无模型时禁用并显示“先完成模型设置”；不得隐藏造成作者以为此处不支持。
- 发起失败保留人工草稿。错误使用统一格式，不清空输入。
- 刷新后可以恢复 streaming / ready 候选；已被作者放弃的候选不重新弹出。
- 同一目标的新请求使旧请求 stale；重试复用同一请求键。

## 7. 写入与授权边界

- 浏览全书、选择章节、打开信息项或请求模型建议不产生写权限。
- 人工点击保存、创建章节、确认候选入账、采用并保存、发送明确修改指令属于具体写入动作。
- 模型给出的候选、索引建议和聊天回复不等于作者采用。
- 修改全书构思默认不修改正文；正文依据旧意图写成的内容由对齐候选提示。
- 修改正文默认不改变正式信息线；自动索引只给候选。作者确认入账后再提交计划修订。
- 删除信息线不能默认删除正文原句；删除正文也不能默认删除信息线。
- 撤销需要恢复正式计划、正文、绑定和索引失效结果；存在后续依赖时返回具体影响并拒绝不完整撤销。确认入账的撤销恢复信息线、信息变化与绑定，不修改正文。

## 8. 上下文装配

章节模型任务默认装入：

1. 当前正式的全书意图。
2. 当前章相关的信息线变化与原句。
3. 当前正文和准确绑定。
4. 直接相关信息线的前文原句。
5. 已完成章节的摘要（起草与方向建议使用）。
6. 与本章直接相关的人物知情边界和硬约定。
7. 作者本轮可选补充及明确选段。

不默认装入全部章节、全部关系、未采用候选或自动观察。材料缺失与截断必须记录。试读任务重新按截止章界装配，排除作者真相、未来信息、内部 `effect` 和带未来结论的摘要；不能复用作者工作台的上下文快照。

## 9. 前后端对应关系

下表省略作品前缀；改造涉及增删的端点以 [P6 改造顺序](development-plan.md#5-关键接口完成顺序) 为准。

| 用户行为 | 拟议 HTTP / 领域服务 | 前端消费者 |
|---|---|---|
| 打开全书构思 | `GET /story/workbench` | `BookPlanView`、workbench store |
| 打开章节 | `GET /story/workbench?chapter_id=...` | `ChapterEditor` / `ChapterMarginIndex` |
| 新建章节（创建即建正文文件） | `POST /story/chapters` | `WritingNavigator` |
| 保存全书构思 | 薄 REST 适配 → `StoryChangeService` | 创作输入与意图摘要 |
| 保存正文 | `PUT /story/chapters/{id}/prose` → 写入策略 | `ChapterEditor` |
| 确认索引候选入账 | `POST /story/index-candidates/{id}/confirm` → 联合提交 | `InformationItem`、页边索引 |
| 请求模型候选 | 作品内或全局 `/creative-assists` 薄路由 + 同一操作事件 | `ModelAssistAction`、`ModelCandidate` |
| 采用候选文字 | 先写本地草稿，再调用目标原保存接口 | 各创作输入 / 正文编辑器 |
| 查看语义分析进度 | 现有操作事件或有界状态读取 | workbench store |
| 处理需要对齐差异 | `StoryChangeService` / 联合提交 | `InformationItem`、对话回执 |
| 定位原句 | 既有精确来源与正文位置能力 | 正文编辑器 / 页边索引 |
| 撤销正式变更 | 既有 change undo 与依赖检查 | 对话或活动回执 |

已实施并将在 P6 移除的端点：`POST /story/chapters/{id}/start`（确认构思并开始写作）。

### 9.1 旧接口的 P5 处理结果

| 旧能力 | 实际处理 |
|---|---|
| `/story/chapter-context` | 已撤除；由工作台投影与页边索引覆盖 |
| 全书方案图、节点详情、时间轴、同步、悬疑投影路由 | 已撤除；`StoryStore`、绑定、`build_attention_view`、时间轴等仍被提交、撤销、Agent 或上下文装配使用的领域服务保留 |
| `/clues`、`/clues/{id}`、`/clue-marks`、`/clue-relations`、`POST /clues` | 已撤除；`/source-locations/{id}` 保留，账本、披露与线索视图服务继续服务 Agent 上下文 |
| `/story/drafts/*` | 已撤除；`StoryDraftStore` 仍被 Agent 的暂停确认与恢复使用 |
| `POST /story/changes` | 已撤除；改动读取与撤销保留 |
| prose marks | 已撤除；改为信息变化绑定投影 |
| `mode=story / clues / chapter` | 前端路由解析后重定向到统一写作页，不保存旧模式 |

P0 的消费者清单与 P5 实施记录见 [P0 基线](notes/p0-baseline.md)、[退休清单](notes/p0-contract-freeze.md#5-退休清单p5-执行) 与 [P5 记录](notes/p5-aqua-and-cleanup.md)。

## 10. 错误与状态

建议错误码（修订后）：

| 错误码 | 条件 | 建议 |
|---|---|---|
| `BOOK_INTENT_REQUIRED` | 正文写入或章节创建时作品尚无全书构思 | 返回全书构思入口及模型选项 |
| `PROSE_DIGEST_MISMATCH` | 正文已被其他写入修改 | 展示两版差异，不自动覆盖 |
| `ASSIST_TARGET_STALE` | 候选依据的目标已变化 | 比较或重新生成，禁止直接采用 |
| `MODEL_UNAVAILABLE` | 无可用在线模型 | 保留人工路径并指向模型设置 |
| `INDEX_ANALYSIS_FAILED` | 增量分析失败 | 正文已保存；允许单次重试索引 |
| `INDEX_RESULT_STALE` | 分析返回或确认入账时基础修订已更新 | 丢弃旧结果或拒绝确认，等待最新任务 |
| `CHAPTER_CREATE_PARTIAL` | 章节创建事务异常 | 自动回滚；若无法回滚则报告实际残留并禁止继续 |

示例：`[BOOK_INTENT_REQUIRED] 正文/作品：作品尚无全书构思。建议：先写一句全书意图，或选择“交给模型构思”。`

已实施并将在 P6 移除的错误码：`CHAPTER_PLAN_REQUIRED`、`CHAPTER_PLAN_STALE`、`CHAPTER_START_PARTIAL`。

界面文案统一为 `[错误码] 位置：现象。建议：…`。操作状态使用明确事实：正在保存、正文已保存 / 索引更新中、索引不可用、候选已过期。不要用“同步良好”之类无法核对的笼统标签。

## 11. 缓存与恢复

- 工作台缓存键至少包含作品、选择、计划修订、正文摘要和索引修订。
- 正式提交或撤销后失效同作品全书投影、受影响章节及相关信息线；第一版可以按作品整体失效，不能漏刷。
- 自动保存的本地草稿单独按作品 / 章节保存，不以服务端读取覆盖更晚的本地修订。
- 已发送对话引用和模型辅助基础修订持久化；页面选择变化只影响下一次请求。
- 快速切章时取消旧读取和分析订阅；即使取消失败，也要在写入状态前核对完整缓存键。
