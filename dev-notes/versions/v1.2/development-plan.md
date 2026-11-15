# v1.2 详细开发计划：单入口写作与上下文指引

日期：2026-11-03。状态：**待实施**。目标版本：1.2.0。

## 1. 目标与约束

本版不是在既有“作品意图”流程上补确认卡，而是删除平行全局状态。完成后：

- `STRUCTURE.md` 是唯一全局语义提示；
- `StoryVariant` 只保存章节登记、信息索引和正文绑定等事实层数据；
- 空作品在明确创作任务中先写结构、再继续同轮工作；
- 模型可以建议装入现有模块，也可以在库为空或无匹配时建议创建模块；
- 主写作区只有对话输入能够改变创作内容；
- 旧格式不迁移，旧作品不作为本版验收对象。

实现优先复用现有能力：`STRUCTURE.md` 读写、模块库不可变版本、作品装配、`QuestionStore`、
`ask_author`、任务暂停恢复、正文候选、Git 记录和活动日志。不要新建第二套候选或问题系统。

## 2. 当前基线与删除清单

| 当前事实 | 本版处理 |
|---|---|
| `FORMAT_VERSION = 4`，`StoryVariant.book_intent` | 升为格式 5并删除字段；不迁移格式 4 |
| `BookIntent`、`SetBookIntent` | 删除模型、变更分支和序列化引用 |
| `save_book_intent`、起步确认函数 | 删除 |
| `PUT /story/book-intent`、`POST /story/startup/confirm-intent` | 删除 |
| `BOOK_INTENT_REQUIRED` | 删除错误码、策略和页面分支 |
| `ChapterWritePolicy.require_book_intent` | 删除；仅保留正文路径归属等事实规则 |
| `_startup_phase`: intent/draft/done | 改为结构是否存在与本轮是否已建立结构 |
| `BookPlanView`、`StartupGateView` | 退出写作主流程并删除 |
| `ChapterEditor` 直接正文输入 | 退出主流程；章节使用只读 `Paper` |
| `WritingNavigator` 新建章节、导入、模块装配 | 移出目录；目录只定位 |
| `ReferencePicker` 常驻“添加文件” | 从默认输入区移除；显式选段与讨论焦点保留 |
| 模块库搜索需作者原话点名 | 拆分元数据发现与正文读取；目录元数据可自动查询 |
| `ask_author` 已能暂停、回答、恢复 | 扩展为结构化上下文建议，不新建确认系统 |

P0 首先用 `rg` 生成完整引用清单，冻结删除顺序。删除后再新增模块建议，避免新功能继续依赖
`book_intent`。

## 3. M1：删除作品意图与写作门槛

### 3.1 数据格式

`StoryVariant` 格式 5 只保留事实层数据。旧格式直接报不支持，不做转换。

```python
FORMAT_VERSION = 5

class StoryVariant(StrictRecord):
    id: StoryId
    name: ShortText
    format_version: Literal[2, 4, 5] = FORMAT_VERSION
    revision: str

    # 当前格式 5：不含任何全局创作摘要。
    chapter_plans: dict[str, ChapterPlan] = Field(default_factory=dict)
    information_threads: dict[str, InformationThread] = Field(default_factory=dict)
    information_moves: dict[str, InformationMove] = Field(default_factory=dict)
    bindings: dict[str, ProseBinding] = Field(default_factory=dict)
```

实施时删除 `BookIntent`、`SetBookIntent`、`set_book_intent` 解析和差异生成；格式 4 文件只返回
`STORY_FORMAT_UNSUPPORTED`。不要为了读取开发机旧作品增加迁移分支。

### 3.2 章节与正文

章节登记不再依赖全书构思。没有方案状态时，服务端可以在建章联合提交里初始化空的格式 5 事实层。

```python
def current_or_new_variant(store: StoryStore) -> StoryVariant:
    current = store.read().variant(MAIN_VARIANT_ID)
    if current is None:
        return StoryVariant(
            id=MAIN_VARIANT_ID,
            name="主方案",
            format_version=FORMAT_VERSION,
            revision=EMPTY_REVISION,
        )
    if current.format_version != FORMAT_VERSION:
        raise unsupported_format()
    return current
```

`ChapterWritePolicy` 只保留确定性文件规则，例如已登记章节的路径不能直接改名。普通章节写入、导入和
候选采用不检查 `STRUCTURE.md`；结构先行由 Agent 的本轮能力状态保证，不能重新变成 HTTP 门槛。

### 3.3 投影和接口

- `WritingBookView` 删除 `intent`；新增 `structure_exists` 只用于只读展示，不作为写入门槛。
- `WritingProseView.blocked_reason` 删除 `book_intent_required`。
- 创意辅助种类删除 `book_intent`。
- 删除两个作品意图接口与请求体。
- 错误注册表删除 `BOOK_INTENT_REQUIRED`、`BOOK_INTENT_INVALID`、
  `BOOK_INTENT_CONFIRMED_CONFLICT`。

### 3.4 测试

删除依赖作品意图的测试，重写为：

- 无 StoryVariant 时可创建第一章，并原子初始化格式 5；
- 格式 4 明确拒绝且不迁移；
- 章节正文写入不依赖结构文件或额外摘要；
- 已登记正文路径仍不能绕过章节事务改名。

## 4. M2：`STRUCTURE.md` 唯一全局提示与同轮起步

### 4.1 装配

调整 `structure_assembly.py` 和 `build_turn_assembly`：

```python
def global_prompt(structure: StructureState) -> str:
    if not structure.exists:
        return ""
    return f"## 全书工作记忆（唯一全局语义提示）\n{structure.text.strip()}"

parts = [
    global_prompt(structure),
    relevant_modules,
    prose_ledger,
    reconcile_note,
    selected_materials,
    author_request,
]
```

删除 `book_intent` 和 `STYLE.md` 的注入位。模块概览只列当前装配；模块正文必须由相关选择或工具读取。
结构预算从 1600 调整为 6000 字符，仍在 `write_structure` 中先检查、超限时要求模型同轮压缩。

### 4.2 本轮结构状态

空作品不能再依赖提示词自觉遵守顺序。`RunContext` 增加本轮结构状态：

```python
@dataclass
class RunContext:
    structure_ready: bool = False

    def require_structure_for_prose(self) -> None:
        if not self.structure_ready:
            raise WorkspaceError(
                code="STRUCTURE_NOT_READY",
                where="tools.prose",
                why="本轮尚未建立全书结构",
                fix="先用 write_structure 建立 STRUCTURE.md，再继续当前正文任务",
            )
```

装配时按磁盘现状初始化；`write_structure` 成功后立即把 `structure_ready` 设为 `True`。正文候选和 Agent
章节写工具调用 `require_structure_for_prose()`。该错误属于模型可恢复的内部顺序提示，正常界面不显示给作者。

导入已有正文是项目管理例外，不经过 Agent 顺序检查；导入后第一次创作任务由模型从现有正文建立结构。

### 4.3 空作品任务判定

- “我要写”“开始写”“按这个做”“你自由发挥”等明确创作请求：允许创建 `STRUCTURE.md`。
- 比较、讨论、询问建议、明确“暂不保存”：只回复。
- 确实无法判断是否要建立作品时，一次只问“现在直接建立作品，还是继续聊？”；不追问内部字段。

第一轮登记范围时一次写清 `STRUCTURE.md`、计划建立的章节和模块内容授权，避免写完结构后因固定范围无法
继续。结构建立不要求设定卡确认。

### 4.4 结构写入后的承接

原始请求要求正文时，同轮继续提出正文候选；只要求人物小传等资料时，转入 M3 的模块判断；只要求规划时，
写完结构并回显即可。

## 5. M3：模块上下文指引

### 5.1 元数据发现与正文权限分离

统一只读工具 `inspect_module_catalog`，任何正常创作轮都可调用，返回：

```python
class ModuleCatalogItem(TypedDict):
    library_id: str
    module_id: str
    revision_id: str
    name: str
    summary: str
    entry_titles: list[str]
```

每个精确版本保存 240 字符以内的摘要；未显式提供时从实际条目稳定生成。目录检索覆盖名称、摘要、
条目标题和别名，不用模块类型做排他过滤。`search_module_library` 不再作为模型入口；
`read_library_record` 在目录候选与任务相关时可以直接只读，不再要求 `library_search` 作者原话。
读取不改变装配，也不产生修改权限。

返回结果必须区分：

- `library_unavailable`：模块库服务不可用；
- `library_empty`：库存在但无模块；
- `no_match`：有模块但无相关项；
- `matches`：有精确版本候选。

### 5.2 建议工具

扩展 `ask_author`，增加可选的结构化建议，而不是新建另一种卡片系统：

```python
class ContextModuleProposal(TypedDict):
    action: Literal["attach", "create"]
    reason: str
    modules: list[ModuleProposalItem]  # 最多 3 个
    carried_from_seq: int

record = QuestionStore(root).create(
    question=question,
    options=(accept_label, "不使用，直接继续"),
    context={
        **request_context,
        "question_kind": "context_modules",
        "module_proposal": proposal,
    },
)
```

`ModuleProposalItem`：

- 装入：精确 `library_id/module_id/revision_id`、显示名和用途；
- 创建：`kind`、建议名称和用途，不含模型尚未生成的正文。

建议工具校验：最多三份、名称非空、装入引用存在、同轮未建议过、原因不为空。建议成功后沿用现有
`needs_input` 状态结束本轮。

### 5.3 回答成为结构化授权

作者回答时读取 `QuestionStore` 中的私有 `context`。接受推荐选项后，下一轮 `RunContext` 得到
`AcceptedModuleProposal`；拒绝则得到 `declined=True`。授权不再依赖模型从“创建并继续”几个字猜文件路径。

```python
@dataclass(frozen=True)
class AcceptedModuleProposal:
    question_id: str
    action: Literal["attach", "create"]
    modules: tuple[ModuleProposalItem, ...]
    carried_from_seq: int

def module_grants_from_proposal(proposal: AcceptedModuleProposal) -> ScopeDraft:
    if proposal.action == "attach":
        return scope_for_exact_bindings(proposal.modules)
    return scope_for_new_module_slots(proposal.modules)
```

授权只覆盖建议列出的模块。模型不能在接受两份建议后创建第三份，也不能借装入建议修改库原稿。

### 5.4 自动继续

接受后分为两个内部轮次，对作者仍是一次确认：

1. **建议履行轮**：创建建议先生成模块内容；装入建议使用 proposal 中冻结的精确版本。应用服务把模块变化、
   作品装配和 `STRUCTURE.md` 模块索引作为一个带恢复日志的提交单元处理。
2. **原任务续接轮**：履行轮提交成功后结束，调度器以新的装配 digest 自动开启下一轮，用
   `carried_from_seq` 承接原请求并继续未完成工作。

不要在 `apply_module_changes` 后继续同一模型轮。现有实现会把成功应用模块的轮次置为 `finalizing`，这是为了
避免后续工具仍使用旧装配；v1.2 保留这一边界。新增的是应用层的履行收据与幂等续接，不是放宽工具限制。

```python
receipt = proposal_service.commit(
    question_id=proposal.question_id,
    module_change_set_id=prepared.change_set_id,
    exact_bindings=proposal.exact_bindings,
    structure_after=structure_after,
    expected_assembly_digest=proposal.assembly_digest,
    expected_structure_digest=structure_digest,
)
if receipt.committed:
    continuation_store.enqueue_once(
        key=f"context-proposal:{proposal.question_id}:resume",
        carried_from_seq=proposal.carried_from_seq,
        required_assembly_digest=receipt.assembly_digest,
    )
```

`ContextModuleProposalService.commit()` 是服务端应用服务，不作为新的模型自由调用工具。内部复用
`ModuleChangeService`、作品装配写入、结构写入及现有撤销信息；只有三者都完成才产生 `committed` 收据。
中途失败时先按恢复日志撤销已完成步骤，再以当前上下文自动续接原任务，并在活动记录中留下失败事实。若状态
无法恢复到确定结果，则停止续接并显示统一格式错误，不能让模型在不确定装配上继续写作。

拒绝后：标记本任务已经拒绝建议，禁止再次询问，直接以当前结构和模块继续。装配在提问后变化时沿用现有
`AUTHOR_QUESTION_STALE_CONFLICT`，让模型按当前状态重新判断。

### 5.5 提示规则

写作提示新增：

- 模块不是必需品；结构足够时不建议。
- 只有跨章复用、细节密集、遗忘影响明显、适合按需读取的资料才建议。
- 库为空时可以建议创建；不能说“没有模块所以无法继续”。
- 已有匹配优先建议装入，不重复创建近似模块。
- 建议被拒绝后继续，不在同一任务再次建议。
- 作者明确排除的内容不得进入模块建议。

## 6. M4：单入口写作界面

### 6.1 主区

`WritingLayout` 调整为：

- 空作品：`Paper` 空态显示“一句话起步”与示例说法，默认展开并聚焦 `ConversationPanel`；示例只填草稿，
  不自动发送、不创建会话，也不渲染 `StartupGateView`。
- 章节：用只读 `Paper` 展示正文，不渲染 `ChapterEditor`。
- 结构和模块：继续只读展示；修改动作只聚焦对话并携带讨论焦点。
- 宽屏对话常驻，不能完全关闭；窄屏在“稿纸/对话”之间切换，但创作输入仍只有对话。

```tsx
function WritingSurface({ page }: Props) {
  return (
    <Workbench>
      <WritingNavigator readOnly />
      <PaperSource page={page} />
      <ConversationPanel alwaysAvailable />
    </Workbench>
  );
}
```

### 6.2 目录

`WritingNavigator` 只显示章节和当前选择。删除：

- “全书构思/起步”行；
- “新建章节”；
- “导入正文”；
- “模块与导入”折叠区；
- 直接装卸模块按钮。

结构、模块、导入和历史放入项目菜单或只读详情，不占章节目录。导入是明确的批量项目操作，保留独立入口，
但不能与日常写作按钮并列。

### 6.3 对话输入

删除默认 `ReferencePicker`。保留：

- 从只读正文选段“添加到对话”；
- 灵感“加入对话”；
- 当前阅读对象自动成为讨论焦点；
- 模型根据任务使用 `list_files`、模块索引和读取工具寻找材料。

作者问题卡复用现有 `AuthorQuestionCard`，上下文建议只显示一句原因、推荐组合和两个主要选项。技术详情放折叠
区，不增加常驻“上下文管理”面板。

### 6.4 删除直接创作入口

删除或退出路由：

- `BookPlanView`；
- `StartupGateView`；
- `ChapterEditor` 与 `ModelCandidate` 的正文/方向按钮；
- 写作页直接保存正文和全书构思的状态分支。

相关 API 可以在 P1/P4 一并删除；不要保留不可达的生产入口和长期测试。

## 7. M5：回执、撤销与错误收敛

上下文建议完成后的作者回执只包含：

- 创建或沿用了哪些资料；
- 这些资料将服务什么任务；
- 原任务是否已经继续；
- 撤销入口。

模块 ID、版本、字段、文件路径和任务范围只进入活动记录。结构/模块直接写入继续使用 Git 与装配版本；正文
候选采用仍沿用既有撤销语义。

可恢复的 `STRUCTURE_NOT_READY`、结构预算、范围收窄和模块准备失败由 Agent 在同轮处理；只有最终无法完成时
才以统一格式显示：

`[错误码] 位置：现象。建议：…`

删除所有提到“作品意图”“全书构思门槛”和“交给模型构思”的错误文案。

## 8. M6：验收与发布

详细用例见 [acceptance.md](acceptance.md)。核心真实用例：

1. 空库 + “西部吸血鬼”提示 → 建结构 → 建议人物小传/故事背景 → 接受 → 创建装入 → 继续；
2. 空库 + 同提示 → 拒绝建议 → 仍继续原任务；
3. 有高度相关模块 → 建议精确装入，不重复创建；
4. “雪山温泉杀人事件”只要求人物小传 → 不创建作案手法和调查模块；
5. 全流程界面只有一个创作输入框。

在线模型使用 `.env`；失败修复后只单例复测，不短时间重复。通过确定性、前端、浏览器和在线门槛后统一版本
为 1.2.0。

## 9. 阶段与退出条件

| 阶段 | 模块 | 退出条件 |
|---|---|---|
| P0 | 全部 | 引用清单、格式 5、删除 API、问题上下文和验收用例冻结 |
| P1 | M1 | 作品意图从数据、接口、策略、投影、前端类型和测试完全退出 |
| P2 | M2 | 空作品同轮结构先行；唯一全局提示装配可核对 |
| P3 | M3 | 创建/装入/拒绝三条建议链均能自动承接原请求 |
| P4 | M4、M5 | 主区只有对话可创作；回执和错误不泄漏内部对象 |
| P5 | M6 | 确定性、浏览器、在线用例通过，版本与文档同步 |

## 10. 风险与处理

| 风险 | 处理 |
|---|---|
| 删除作品意图后无处保存核心方向 | 核心方向进入 `STRUCTURE.md`；装配测试保证每轮唯一注入 |
| 空作品先写正文、忘建结构 | `RunContext.structure_ready` 在工具层限制同轮顺序，不做作者门槛 |
| 模型逢空库就建议模块 | 提示判据、每任务一次上限、确定性 proposal 状态和在线反例 |
| 建议创建过多模块 | 单次最多三份；优先人物/世界等最小组合，其他按需后建 |
| 接受建议后原任务丢失 | proposal 持久化 `carried_from_seq`，回答轮自动承接 |
| 元数据搜索泄露模块正文 | 新工具只返回名称、类型、条目标题和精确版本；正文读取仍受授权 |
| 单入口降低手工编辑自由 | 本版明确选择模型对话工作流；外部文件编辑按磁盘事实读取，不恢复应用内编辑器 |
| 旧格式测试与代码拖慢删除 | 不迁移；格式 4 明确拒绝，相关 UI 和兼容分支不延续 |

## 11. 文档与验证纪律

- 每阶段完成后写 `notes/Px-*.md`，只记录实际改动、命令、结果和未覆盖项。
- 决定变化同时更新产品方案、数据接口和验收，不留“作品意图仍是门槛”的现行文字。
- 仅文档修改不跑后端全量；P1/P2/P3 写入和上下文改动完成后按风险执行定向测试，再在 P5 跑一次广泛回归。
- 根目录 README 仅在作者要求时修改；`mynotes/` 始终只读。
