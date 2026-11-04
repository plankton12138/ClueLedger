# v1.2 数据与接口：结构唯一来源与模块建议

日期：2026-11-03。状态：**契约草案，实施前于 P0 冻结**。

## 1. 数据归属

| 数据 | 归属 | 是否进入每轮全局提示 |
|---|---|---|
| 全书方向、铁律、路线、章节骨架、模块索引 | 作品根目录 `STRUCTURE.md` | 是，全文 |
| 人物、世界、线索等详细资料 | 全局模块库不可变版本 + 作品精确装配 | 否，按任务选择 |
| 章节顺序、标题、正文路径 | 格式 5 `StoryVariant.chapter_plans` | 只作为事实摘要 |
| 信息线、信息变化、正文绑定 | 格式 5 事实层 | 只按目标章与索引需要 |
| 正文实际入档状态 | 正文文件 + 正文台账 | 是，确定性事实摘要 |
| 上下文建议与作者回答 | 应用状态 `questions.json` + 会话事件 | 否，只供恢复与授权 |
| 作品意图 | 不存在 | 否 |

`STRUCTURE.md` 是唯一全局**语义**提示，不代表每轮只能注入一个文本。正文台账、当前文件和作者请求仍作为
事实与任务输入，但不能再出现另一份概括全书方向的字段或文件。

## 2. 格式 5

```python
FORMAT_VERSION = 5

class StoryVariant(StrictRecord):
    id: StoryId
    name: ShortText
    format_version: Literal[2, 4, 5] = FORMAT_VERSION
    base_variant_id: str | None = None
    base_revision: str | None = None
    revision: str
    chapter_plans: dict[str, ChapterPlan] = Field(default_factory=dict)
    information_threads: dict[str, InformationThread] = Field(default_factory=dict)
    information_moves: dict[str, InformationMove] = Field(default_factory=dict)
    bindings: dict[str, ProseBinding] = Field(default_factory=dict)
```

删除：

- `BookIntent`；
- `StoryVariant.book_intent`；
- `SetBookIntent`；
- `StoryOperation` 中的 `set_book_intent`；
- 差异生成、反向操作和修订摘要中的作品意图分支。

格式 4 不转换为格式 5。读取旧作品时返回：

`[STORY_FORMAT_UNSUPPORTED] 写作工作台：这份作品使用已退出的旧格式。建议：新建作品后使用当前写作流程。`

## 3. 写作投影

```python
class WritingBookView(BaseModel):
    structure_exists: bool
    chapters: list[ChapterSummary]
    information_threads: list[InformationThreadSummary]
    unplanned_prose: list[UnplannedProse]

class WritingProseView(BaseModel):
    path: str
    digest: str
    text: str
    editable: Literal[False] = False
```

- `WritingBookView.intent` 删除。
- `WritingProseView.blocked_reason` 删除。
- `editable` 可以在接口兼容期固定为 `false`，P4 清理前端后再评估是否删除字段。
- 空作品允许 `variant=None`；投影返回空章节和 `structure_exists=false`。

## 4. 删除的 HTTP 接口

| 方法与路径 | 处理 |
|---|---|
| `PUT /api/file-workspaces/{id}/story/book-intent` | 删除 |
| `POST /api/file-workspaces/{id}/story/startup/confirm-intent` | 删除 |
| 以 `kind=book_intent` 发起的创意辅助 | 删除 |

相关请求体 `BookIntentRequest`、`StartupConfirmRequest` 和前端 `saveBookIntent` 一并删除。没有兼容代理。

## 5. 保留的 HTTP 接口

- 写作工作台只读投影；
- 结构读取；
- 对话发送、暂停、恢复和回答作者问题；
- 正文候选提出与采用；
- 模块库浏览、版本读取、创建、发布；
- 作品装配读取与精确更新；
- 活动记录、Git 历史与撤销。

章节创建不再作为写作页面按钮使用，但 Agent 内部仍可复用服务函数；若保留 HTTP 端点，只允许内部对话链
调用，不在主界面暴露。

## 6. 模块目录元数据

Agent 只读工具统一为 `inspect_module_catalog`。它复用 `ModuleLibrary.modules`，返回名称、版本摘要与条目标题：

```json
{
  "status": "library_empty | no_match | matches",
  "library_total": 7,
  "matched_total": 1,
  "items": [
    {
      "library_id": "lib_x",
      "module_id": "mod_x",
      "revision_id": "rev_x",
      "name": "人物小传：金·舒尔茨与姜戈",
      "summary": "记录两人的身份、关系、性格边界与故事时间点。",
      "entry_titles": ["金·舒尔茨", "姜戈"]
    }
  ],
  "truncated": false
}
```

目录不返回 `body` 或长预览。模型确认名称、摘要或条目标题与任务相关后，可以用
`read_library_record` 读取精确版本的条目目录与必要正文；读取不构成装入、修改或发布授权。
检索不接受模块类型过滤，`no_match` 不能推断成 `library_empty`。

## 7. 上下文模块建议

### 7.1 Proposal

```python
ModuleProposalAction = Literal["attach", "create"]

class ModuleProposalItem(TypedDict, total=False):
    kind: str
    name: str
    purpose: str
    library_id: str
    module_id: str
    revision_id: str

class ContextModuleProposal(TypedDict):
    action: ModuleProposalAction
    reason: str
    modules: list[ModuleProposalItem]
    carried_from_seq: int
```

确定性校验：

- `modules` 为 1—3 项；
- `reason/name/purpose` 非空；
- `attach` 每项必须包含精确三段引用并验证存在；
- `create` 每项只能使用允许的模块 kind，不带正文；
- `carried_from_seq` 必须定位当前会话可见的作者请求；
- 同一轮只能创建一个待答建议。

### 7.2 QuestionStore

不增加第二份 proposal 文件。扩展 `AuthorQuestion` 的公开类型和私有上下文：

```python
class AuthorQuestion:
    # 现有字段……
    kind: Literal["decision", "context_modules"] = "decision"

context = {
    "question_kind": "context_modules",
    "module_proposal": proposal,
    "request_context": original_context,
}
```

`view()` 公开 `kind`，但不公开精确授权配方；前端根据 `kind` 使用相同卡片的紧凑文案。旧问题缺少 `kind`
时按 `decision` 读取，不涉及作品数据迁移。

### 7.3 事件

沿用：

- `author_input_requested`；
- `author_replied`；
- `run_finished(status=needs_input)`。

事件增加可选字段：

```json
{
  "type": "author_input_requested",
  "question_kind": "context_modules",
  "question_id": "q_x",
  "question": "……",
  "options": ["按建议创建并继续", "不创建，直接继续"],
  "related": ["人物小传", "故事背景"]
}
```

活动记录另记 `module_guidance_finished`：`accepted/declined`、动作、显示名、装配前后 digest 和是否继续原任务。

### 7.4 接受与拒绝

回答通过现有 `reply_to_question` 提交。服务端按 question ID 读取 proposal：

- 点击推荐选项：`accepted=True`，生成精确模块授权；
- 点击“不使用”：`declined=True`，禁止同任务再次建议；
- 自由回答“你决定/自由发挥”：交给任务意图判断为接受最小推荐；
- 其他自由回答：作为作者对建议的修订，模型只能按修订后再次提出或继续，不能猜成接受。

授权只存在于回答开启的新轮次，不跨会话、不跨任务继承。

## 8. 建议履行事务

创建建议继续使用 `prepare_module_changes` 生成正文和条目，但不能在 `apply_module_changes` 后让模型继续调用
`write_structure`：现有应用成功后会把本轮置为 `finalizing`，后续工具仍持有旧装配。v1.2 在应用层增加
`ContextModuleProposalService`，把模块变化、作品装配和结构索引协调为一个可恢复提交：

```python
@dataclass(frozen=True)
class ContextModuleFulfillment:
    question_id: str
    change_set_id: str | None
    exact_bindings: tuple[ModuleBinding, ...]
    structure_after: str
    expected_assembly_digest: str
    expected_structure_digest: str
    carried_from_seq: int
```

```text
accepted proposal
  → 回答开启建议履行轮并生成精确授权
  → create: prepare_module_changes / attach: 使用冻结的精确版本
  → 生成完整 structure_after
  → ContextModuleProposalService.commit
       ├─ 应用模块创建或精确装入
       ├─ 更新作品装配
       └─ 写入 STRUCTURE.md 模块索引
  → 提交履行收据并结束当前轮
  → 调度器按新 assembly digest 自动开启原任务续接轮
```

这不是一个新的公开 HTTP 接口，也不是模型可任意调用的通用工具；它由回答恢复链在已接受 proposal 的精确
范围内调用。`commit` 使用恢复日志保存每个子步骤的 before/after digest 和撤销信息。只有全部完成才标记
`committed` 并排入续接任务。

如果任一步失败：

- 不声称模块已创建或已用于作品；
- 按恢复日志撤销已经完成的模块、装配或结构变化；
- 恢复成功后记录失败事实，并在不使用新模块的情况下自动续接原任务；
- 恢复失败时停止续接，以统一错误格式报告不确定状态；
- 不能形成库已创建但作品未装入，或装配已更新但结构索引仍声称旧状态的半完成结果。

## 9. 装入事务

装入 proposal 已冻结精确版本。接受后比较当前装配 digest：

- 未变化：写入绑定；
- 已存在同一精确版本：幂等成功；
- digest 变化：旧问题按现有 stale conflict 失效；
- 模块版本不存在或已删除：失败并建议重新判断，不自动换 head。

模型读取正文必须等新轮次装配冻结后再进行，不能在装入提交前用库 head 冒充已装版本。

## 10. 前端类型

删除：

- `WritingBookView.intent`；
- `blocked_reason: book_intent_required`；
- `AssistKind.book_intent`；
- `BookIntentRequest` / `StartupConfirmRequest` 客户端类型。

扩展：

```ts
export interface AuthorQuestionView {
  questionId: string;
  kind: "decision" | "context_modules";
  question: string;
  options: string[];
  related: string[];
  answer: string;
}
```

前端不接收 proposal 的模块 ID 与授权字段；按钮只回传原有 option 文本和 question ID。

## 11. 错误码

删除：

- `BOOK_INTENT_REQUIRED`；
- `BOOK_INTENT_INVALID`；
- `BOOK_INTENT_CONFIRMED_CONFLICT`。

新增或明确：

| 错误码 | 使用位置 | 建议 |
|---|---|---|
| `STRUCTURE_NOT_READY` | Agent 同轮正文顺序 | 先写结构再继续；内部可恢复 |
| `MODULE_PROPOSAL_INVALID` | 建议结构校验 | 缩小到 1—3 个确切建议 |
| `MODULE_PROPOSAL_SCOPE_CONFLICT` | 接受后超出 proposal | 只处理已接受的模块 |
| `MODULE_CATALOG_UNAVAILABLE` | 元数据目录不可用 | 使用当前上下文继续，不阻断写作 |

`STRUCTURE_NOT_READY` 默认不进入用户错误区；只有模型无法恢复并结束任务时才展示。

## 12. 原子性与幂等

- suggestion question ID 唯一，重复回答沿用 `AUTHOR_QUESTION_ANSWERED_CONFLICT`；
- 一个 question ID 只对应一个履行事务 ID；模块、装配和结构子步骤使用从事务 ID 派生的 request key；
- 重放时先读取履行收据：`committed` 直接返回，`rolled_back` 不重建，`in_progress` 按恢复日志完成或撤销；
- 原任务续接使用独立的 `context-proposal:{question_id}:resume` 幂等键，不会因进程重启生成两个续接轮；
- 续接轮必须冻结收据记录的新 assembly digest；不允许在模块应用的 `finalizing` 轮继续写作；
- `carried_from_seq` 固定原请求，回答不能承接另一条旧任务；
- 正文候选仍以 candidate ID 与正文 digest 防止过期采用；
- 整体撤销至少恢复模块装配、结构和本轮直接写入；正文采用按既有变更收据撤销。
