# 1.0.3 分模块实现示例

日期：2026-10-30。状态：**设计示例，未接入生产代码**。字段、错误码与响应以[数据契约](data-and-runtime.md)和[接口契约](api-and-frontend.md)为准；代码中的类型名可以按既有模块调整，行为不能改变。

这些示例按同一条数据流排列：先定义能存什么，再说明几份状态怎样一起提交、怎样读成一份视图、怎样进入模型输入，最后落到画布和定位。这样安排是因为本版的主要风险不是单个函数写不出来，而是同一件事在结构、账本、接口和页面各有一套形状，最后出现「保存成功但读不到」或「界面显示了并不存在的线索」。

## 1. 阶段、故事线与节点归属

本版的结构总览先回答两个问题：故事在哪几个阶段推进，每条故事线要解决什么。节点只记录关键行动带来的局面或理解变化，不按章节逐一生成。阶段表示作者安排揭示的推进顺序，不是正文目录，也不是实际事件时间；故事线目标可以未定，空白不等于否定事实。未分阶段的片段允许暂存，避免作者只有零散想法时被迫补全整本书。

以下只列新增或改变的字段，其余沿用当前模型。

```python
from typing import Annotated, Literal
from pydantic import Field, model_validator

StoryId = Annotated[str, Field(min_length=1, max_length=64)]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[str, Field(min_length=1, max_length=4000)]


class StoryStage(StrictRecord):
    id: StoryId
    title: ShortText
    order: int = Field(ge=0)
    summary: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def check_summary(self) -> "StoryStage":
        if self.summary is not None and not self.summary.strip():
            raise ValueError("阶段摘要要么留空表示未写，要么写明内容")
        return self


class StoryThread(StrictRecord):
    id: StoryId
    title: ShortText
    order: int = Field(ge=0)
    goal: str | None = Field(default=None, max_length=1000)
    obstacle: str | None = Field(default=None, max_length=1000)
    intended_resolution: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def check_plan(self) -> "StoryThread":
        for label, value in (
            ("目标", self.goal),
            ("主要阻力", self.obstacle),
            ("计划收束", self.intended_resolution),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{label}要么留空表示未定，要么写明内容")
        return self


class StoryNode(StrictRecord):
    # 既有字段：id、title、action、thread_ids、clue_uses、time_note、
    # knowledge_changes。本版新增 stage_id 与 change，移除 group_id。
    stage_id: str | None = None
    change: str | None = Field(default=None, max_length=1000)


class StoryVariant(StrictRecord):
    # 既有字段：id、name、revision、edges、event_times、constraints、
    # bindings、assessments。
    stages: dict[str, StoryStage] = Field(default_factory=dict)
    threads: dict[str, StoryThread] = Field(default_factory=dict)
    nodes: dict[str, StoryNode] = Field(default_factory=dict)
    clue_plans: dict[str, "StoryCluePlan"] = Field(default_factory=dict)
```

索引、顺序和引用在方案自检里一次查完。顺序不能重复，但允许有空位；数组顺序不参与判断。节点可以暂时没有阶段，不能引用不存在的阶段。

```python
def _require_unique_order(items: dict[str, object], label: str) -> None:
    orders = [item.order for item in items.values()]
    if len(set(orders)) != len(orders):
        raise ValueError(f"{label}顺序不能重复")


class StoryVariant(StrictRecord):
    @model_validator(mode="after")
    def check_structure(self) -> "StoryVariant":
        _require_unique_order(self.stages, "阶段")
        _require_unique_order(self.threads, "故事线")
        for node in self.nodes.values():
            if node.stage_id is not None and node.stage_id not in self.stages:
                raise ValueError(f"节点引用了不存在的阶段：{node.stage_id}")
            if len(set(node.thread_ids)) != len(node.thread_ids):
                raise ValueError("同一节点不能重复加入同一条故事线")
        return self
```

阶段顺序还要约束推进边。`next` 仍由现有 `validate_story_variant` 检查无环；这里只补「不能从较后阶段回到较前阶段」。`reframe / answer` 不参与这项检查，因为它们本来就允许从后文回指前文。

```python
def validate_stage_flow(variant: StoryVariant) -> None:
    order = {stage.id: stage.order for stage in variant.stages.values()}
    for edge in variant.edges.values():
        if edge.kind != "next":
            continue
        source = variant.nodes[edge.from_id]
        target = variant.nodes[edge.to_id]
        if (
            source.stage_id is not None
            and target.stage_id is not None
            and order[target.stage_id] < order[source.stage_id]
        ):
            raise StoryInvalid("推进关系不能从较后阶段回到较前阶段")
```

删除阶段时不要自动把节点丢进「未分阶段」。自动清空看起来省事，实际会把作者明确安排的节点变成无归属，而且提交后很难看出发生了什么。示例选择要求同一变更里先显式调整或解除节点归属，再删除阶段：

```python
class RemoveStage(StrictRecord):
    op: Literal["remove_stage"] = "remove_stage"
    stage_id: StoryId
```

```python
elif isinstance(operation, RemoveStage):
    _require_existing(stages, operation.stage_id, "阶段")
    affected = [
        node["id"] for node in nodes.values() if node["stage_id"] == operation.stage_id
    ]
    if affected:
        raise StoryInvalid(
            "删除阶段前，先在同一变更中调整或解除这些节点的阶段：" + "、".join(affected)
        )
    del stages[operation.stage_id]
```

共同节点只保存一次，用多个 `thread_ids` 表达参与关系；故事线成员从节点导出，不在故事线上另存一份 `node_ids`。这和现有 `StoryVariant.members()` 的取舍一致：两处保存成员，编辑后必然分叉。

## 2. 线索身份、计划与使用

当前一条线索的标题、作者真相、回收意图与正文贡献分散在构思和账本里，容易出现同名不同物、计划改了但详情还是旧标题。本版把唯一身份留在账本，构思只保存作者计划，节点只保存使用安排，正文实际发生的内容仍由账本贡献记录。四者分开后，一条线索可以只有计划、也可以只有正文观察，两种状态都合法。

```python
ClueRole = Literal["plant", "develop", "misdirect", "reinterpret", "resolve"]


class StoryCluePlan(StrictRecord):
    clue_id: StoryId
    author_truth: str | None = None
    intention: str = Field(default="", max_length=2000)
    resolution_intent: ResolutionIntent = "undecided"

    @model_validator(mode="after")
    def check_truth(self) -> "StoryCluePlan":
        if self.author_truth is not None and not self.author_truth.strip():
            raise ValueError("作者真相要么留空表示未定，要么写明内容")
        return self


class ClueUse(StrictRecord):
    # 既有字段：clue_id、presentation、mode、speaker_id、expected_interpretation。
    roles: list[ClueRole] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_roles(self) -> "ClueUse":
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("同一次使用不能重复登记相同作用")
        return self
```

新身份由服务端分配。模型可以在同一请求里用 `temp_id` 引用刚创建的线索，但保存前必须解析成稳定 ID；回执返回 `id_map`，重试复用同一结果。同名不等于同一线索，身份检查不按标题合并。

```python
class CluePlanInput(StrictRecord):
    author_truth: str | None = None
    intention: str = Field(default="", max_length=2000)
    resolution_intent: ResolutionIntent = "undecided"


class CreateClueIdentity(StrictRecord):
    op: Literal["create_clue_identity"] = "create_clue_identity"
    temp_id: StoryId
    title: ShortText


class UpsertCluePlan(StrictRecord):
    op: Literal["upsert_clue_plan"] = "upsert_clue_plan"
    # 稳定 ID，或本请求内刚创建的临时引用。
    clue_id: StoryId
    plan: CluePlanInput
```

解析时只替换引用，不改变操作顺序。解析完成后还要再查一次：任何临时引用残留都说明请求不完整，不能落盘。

```python
def resolve_change(change: "StoryChangeSet", mapping: dict[str, str]) -> "StoryChangeSet":
    operations: list[StoryOperation] = []
    for operation in change.operations:
        if isinstance(operation, UpsertCluePlan):
            operations.append(
                operation.model_copy(
                    update={"clue_id": mapping.get(operation.clue_id, operation.clue_id)}
                )
            )
        elif isinstance(operation, (AddNode, UpdateNode)):
            uses = [
                use.model_copy(update={"clue_id": mapping.get(use.clue_id, use.clue_id)})
                for use in operation.node.clue_uses
            ]
            node = operation.node.model_copy(update={"clue_uses": uses})
            operations.append(operation.model_copy(update={"node": node}))
        else:
            operations.append(operation)
    resolved = change.model_copy(update={"operations": operations})
    _reject_unresolved_temp_ids(resolved, set(mapping))
    return resolved
```

`create_clue_identity` 的分配要写在提交准备里，而不是先写一份账本再回来改结构。否则第一份请求失败时，作者会看到一条没有计划、没有使用的空身份。建议的分配入口：

```python
def allocate_identities(
    change: "StoryChangeSet",
) -> tuple["StoryChangeSet", dict[str, str]]:
    mapping: dict[str, str] = {}
    for operation in change.operations:
        if not isinstance(operation, CreateClueIdentity):
            continue
        if operation.temp_id in mapping:
            raise StoryInvalid(f"同一请求里的临时引用重复：{operation.temp_id}")
        mapping[operation.temp_id] = new_id("clue")
    return resolve_change(change, mapping), mapping
```

回执中的映射必须保存到请求收据里。同一 `request_key` 与同一负载再次到达时，返回原来的 `id_map`；负载不同则冲突。准备阶段被放弃、还没有提交决定的临时 ID 不能当成已发布身份。

计划的回收意图与节点作用要一致，不能一边写「不解释」一边安排回收：

```python
def check_resolution_consistency(variant: "StoryVariant") -> None:
    for plan in variant.clue_plans.values():
        resolve_nodes = [
            node
            for node in variant.nodes.values()
            if any(
                use.clue_id == plan.clue_id and "resolve" in use.roles
                for use in node.clue_uses
            )
        ]
        if plan.resolution_intent == "explain" and not resolve_nodes:
            raise StoryInvalid("明确解释的线索需要有安排为回收的节点")
        if plan.resolution_intent != "explain" and resolve_nodes:
            raise StoryInvalid("未计划解释或仍待定的线索不能安排回收作用")
```

删除身份与删除计划的边界不同。删除计划只去掉作者安排，身份和正文贡献继续存在；删除身份要求最终状态里没有计划、没有节点使用、没有正文贡献，撤销创建身份也执行同一检查。冲突要指出具体仍被谁引用，不能只回一句「删除失败」。

```python
def assert_identity_removable(
    clue_id: str, variant: "StoryVariant", ledger: "ClueLedger"
) -> None:
    plan = clue_id in variant.clue_plans
    uses = any(
        use.clue_id == clue_id for node in variant.nodes.values() for use in node.clue_uses
    )
    contributions = any(
        row.clue_id == clue_id for row in ledger.contributions.values()
    )
    if plan or uses or contributions:
        raise WorkspaceError(
            code="STORY_CLUE_REFERENCE_INVALID",
            where="story.clue",
            why="这条线索仍被计划、节点使用或正文贡献引用",
            fix="先处理这些引用，再删除身份",
            details={"clue_id": clue_id, "plan": plan, "uses": uses,
                     "contributions": contributions},
        )
```

所有新操作都要同时进入 `StoryOperation` 联合类型、`apply_change_set`、`diff_variant` 和工具 schema。只加模型、不加反向操作，撤销会在运行到一半时找不到对象。

## 3. 联合提交准备

结构和账本是两个文件，物理上无法一起替换。现有构思提交已经用「待提交内容 → 提交决定 → 发布」保证应用可见性；本版把账本目标和必要的正文补丁纳入同一份决定。关键取舍是：宁可多一次恢复，也不让界面或模型看到结构用了新线索、账本却还没有这条身份的半更新状态。

锁顺序固定为「构思 → 账本」。所有写入口，包括只改线索贡献的独立入口，都走同一个协调器；否则两个入口一个先拿构思锁、一个先拿账本锁，迟早会互相等待。

```python
@dataclass(frozen=True, slots=True)
class CommitTarget:
    kind: Literal["story", "ledger", "prose"]
    key: str
    before: str
    after: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class JointPlan:
    base_story_revision: str
    base_ledger_revision: str
    targets: tuple[CommitTarget, ...]
    inverse_operations: tuple[StoryOperation, ...]
    inverse_prose: tuple[ProsePatch, ...]
    id_map: dict[str, str]
```

```python
def commit_joint(
    store: StoryStore,
    ledger_store: ClueLedgerStore,
    *,
    variant_id: str,
    change: StoryChangeSet,
    expected_ledger_revision: str,
    request_key: str,
    guard: Callable[[], None] | None = None,
) -> StoryReceipt:
    with locked(store.lock_file), locked(ledger_store.lock_file):
        store.recover_locked()
        ledger_store.recover_locked()

        prior = store.receipt_locked(request_key, _fingerprint(change))
        if prior is not None:
            return prior

        story_doc = store.read_head_locked()
        ledger = ledger_store.read_locked()
        _require_base(story_doc, ledger, change.base_revision, expected_ledger_revision)

        resolved = _allocate_and_resolve(change, ledger)
        updated_story, updated_ledger = _apply_and_validate(story_doc, ledger, resolved)
        plan = _build_plan(story_doc, ledger, updated_story, updated_ledger, resolved)

        store.write_joint_decision(plan)
        _publish(plan)
        return _receipt(plan, request_key)
```

`_build_plan` 为每个目标记录前摘要、后摘要和实际内容。正文补丁沿用现有构思存储的检查：区间、原文和摘要都必须与工作副本一致；账本目标同样不能在发布时被外部修改。

```python
def _publish(plan: JointPlan) -> None:
    for target in plan.targets:
        current = _read_digest(target)
        if current == target.after:
            continue
        if current != target.before:
            raise WorkspaceError(
                code="STORY_CLUE_REVISION_CONFLICT",
                where="story.commit",
                why="提交期间有一个目标被外部修改",
                fix="保留外部内容，重新读取后核对再提交",
                details={"target": target.key},
            )
        _write_target(target)
```

恢复规则只有两条：没有提交决定时丢弃待提交内容；有提交决定时把每个目标补到目标状态。某个目标既不是原状态也不是目标状态时报告冲突，不覆盖外部修改。

```python
def recover_joint(store: StoryStore, ledger_store: ClueLedgerStore) -> None:
    decision = store.read_decision()
    if decision is None:
        store.discard_pending()
        return
    for target in decision.targets:
        current = _read_digest(target)
        if current == target.before:
            _write_target(target)
        elif current != target.after:
            raise WorkspaceError(
                code="STORY_CLUE_REVISION_CONFLICT",
                where="story.recover",
                why="恢复提交时目标既不是提交前状态，也不是提交后状态",
                fix="检查外部修改，保留现场后再处理",
                details={"target": target.key},
            )
    store.clear_decision()
```

`expected_ledger_revision` 在操作引用或更新身份、计划、节点使用时必需；只改阶段标题这类不涉及线索的内容可以不提供。独立线索写入口不能绕过检查：它同样需要读取当前结构，确认计划或使用是否引用了该身份，再走同一协调器。

撤销也覆盖联合计划。记录里保存结构反向操作、账本反向操作和正文反向补丁；执行前逐项检查这些对象在提交后有没有再变化，并检查身份有没有新增引用。无关修改保留，相关修改或新增引用报冲突。

## 4. 统一读取视图

线索详情如果先读账本、再单独读结构，作者可能看到一条刚被删除的身份仍带着计划，或者计划已经改名而同一条线索的标题还是旧值。本版要求一次响应使用同一份结构快照和同一份账本快照；跨文件阅读要么共用协调锁，要么读后复核修订并重读。

`story` 范围与现有 `chapter` 范围是两种读取方式，不是两套资料：

| 范围 | 能看到 | 看不到 |
|---|---|---|
| `chapter` | 截止位置内已出现的身份、贡献与依据 | 未来作者计划 |
| `story` | 全书身份、计划、使用位置与正文观察 | 截止位置外的正文事实 |

`story` 范围的正文观察默认截至最新实际章节；显式传 `as_of_chapter` 时只限制观察，不改变作者计划的性质。

```python
class StoryCluePosition(BaseModel):
    node_id: str
    stage_id: str | None
    thread_ids: list[str]
    roles: list[str]
    presentation: str
    mode: str
    speaker_id: str = ""
    expected_interpretation: str = ""


class StoryClueView(BaseModel):
    clue_id: str
    title: str
    clue_revision: str
    story_revision: str
    ledger_revision: str
    view_revision: str
    plan: StoryCluePlan | None
    uses: list[StoryCluePosition]
    observations: list[ClueObservation]
    evidence: list[ClueBasis]
```

```python
def build_story_clue_views(
    workspace: Workspace,
    root: Path,
    *,
    variant_id: str,
    node_id: str | None = None,
    as_of_chapter: int | None = None,
) -> list[StoryClueView]:
    store = StoryStore(root, workspace)
    story = store.read()
    variant = story.variant(variant_id)
    if variant is None:
        raise WorkspaceError(
            code="STORY_VARIANT_NOT_FOUND", where="clues.story",
            why="当前作品没有这个构思", fix="刷新构思后重新选择",
        )
    if node_id is not None and node_id not in variant.nodes:
        raise WorkspaceError(
            code="STORY_CONTEXT_INCOMPLETE", where="clues.story",
            why="筛选的节点不在当前构思里", fix="重新选择节点",
        )

    ledger = ClueLedgerStore(root).read()
    # P3 给 ClueReader 增加 snapshot 参数，避免详情再次读取账本。
    reader = ClueReader(workspace, root, as_of_chapter, snapshot=ledger)
    views: list[StoryClueView] = []
    for clue_id, identity in sorted((ledger.ledger.clues if ledger.ledger else {}).items()):
        uses = _uses(variant, clue_id, node_id)
        if node_id is not None and not uses:
            continue
        detail = reader.detail(clue_id)
        views.append(
            StoryClueView(
                clue_id=clue_id,
                title=identity.title,  # 标题只从稳定身份取
                clue_revision=identity.clue_revision,
                story_revision=variant.revision,
                ledger_revision=ledger.revision,
                view_revision="",  # 下方统一计算
                plan=variant.clue_plans.get(clue_id),
                uses=uses,
                observations=detail.observations if detail else [],
                evidence=detail.evidence if detail else [],
            )
        )
    revision = _view_revision(variant, ledger, as_of_chapter, node_id, views)
    return [row.model_copy(update={"view_revision": revision}) for row in views]
```

组合视图修订不能只看账本版本。身份标题、计划内容、节点使用位置和正文来源状态任何一项变化，都要让旧分页和旧引用失效：

```python
def _view_revision(
    variant: StoryVariant,
    ledger: ClueSnapshot,
    as_of_chapter: int | None,
    node_id: str | None,
    views: list[StoryClueView],
) -> str:
    return object_digest(
        {
            "story": variant.revision,
            "ledger": ledger.revision,
            "as_of": as_of_chapter,
            "node_id": node_id,
            "clues": [
                {
                    "id": row.clue_id,
                    "identity": row.clue_revision,
                    "plan": row.plan.model_dump(mode="json") if row.plan else None,
                    "uses": [use.model_dump(mode="json") for use in row.uses],
                    "evidence": [
                        [item.evidence_id, item.freshness] for item in row.evidence
                    ],
                }
                for row in views
            ],
        }
    )
```

分页游标绑定 `view_revision`、筛选条件和偏移；期间内容变化时按项目现有规则报 `CLUE_CURSOR_CONFLICT`，不能把两个修订的页拼在一起。`node_id` 只允许在 `story` 范围使用，且必须属于当前作品与当前构思；非法组合要明确失败，不能默默返回全书。

前端的类型与视图状态保持一对一，不在客户端重新拼标题或推断角色：

```typescript
interface StoryClue {
  clue_id: string;
  title: string;
  clue_revision: string;
  view_revision: string;
  plan: {
    author_truth: string | null;
    intention: string;
    resolution_intent: "explain" | "leave_open" | "undecided";
  } | null;
  uses: {
    node_id: string;
    stage_id: string | null;
    thread_ids: string[];
    roles: string[];
    presentation: string;
    expected_interpretation: string;
  }[];
  observations: { kind: "plant" | "mention" | "resolve"; text: string; chapter: number }[];
  evidence: { path: string; start: number; end: number; quote: string; freshness: string }[];
}
```

选择节点只更新浏览范围；点击「查看相关线索」才主动切换右侧标签。切换作品或快速重复选择时用请求序号丢弃迟到响应；旧作品的线索不能落进新作品的状态。

## 5. 模型输入与提示

现有全局读取在没有选中节点时主要列节点标题，模型看不到各线目标、阶段和关系理由；提示示例也还是一条两节点的单链。这样即使页面有总览，模型仍会按章节往一条线上加节点。本版把「整体安排」和「局部细化」分开装配：全局输入保留阶段、线目标、重要变化和关系，局部输入只补当前任务需要的前文与前提。

`read_story` 的工具返回要能让模型直接建立多线结构，而不是先猜标题：

```json
{
  "variant_id": "main",
  "revision": "rev_01",
  "stages": [
    {"id": "stage-night", "title": "失踪当晚", "order": 0, "summary": "三条线分别建立时间、物证与目击说法"}
  ],
  "threads": [
    {"id": "thread-alibi", "title": "不在场证明", "order": 0,
     "goal": "确认每个人当晚的位置", "obstacle": "证词互相矛盾",
     "intended_resolution": "在汇合节点用时间差收束"}
  ],
  "nodes": [
    {"id": "node-observe", "title": "发现异样", "change": "调查者注意到木板没有响",
     "stage_id": "stage-night", "threads": ["thread-physical"], "clues": ["clue-board"]}
  ],
  "clue_plans": [
    {"clue_id": "clue-board", "title": "没有响的木板", "author_truth": null,
     "intention": "让调查者怀疑有人熟悉走廊结构", "resolution_intent": "undecided"}
  ],
  "edges": [
    {"id": "edge-observe-alibi", "from_id": "node-observe", "to_id": "node-alibi",
     "kind": "next", "reason": "物证促使调查者核对证词"}
  ]
}
```

超过读取上限时不能只截掉后半部节点。可以给出阶段／故事线概况并标明尚有多少未展开，或者要求模型按阶段继续读取；覆盖情况必须明确。

全局装配示例：

```python
def _global_lines(variant: StoryVariant) -> list[str]:
    lines: list[str] = []
    for stage in sorted(variant.stages.values(), key=lambda item: item.order):
        lines.append(f"阶段「{stage.title}」：{stage.summary or '摘要未定'}")
    for thread in sorted(variant.threads.values(), key=lambda item: item.order):
        lines.append(
            f"故事线「{thread.title}」：目标={thread.goal or '未定'}；"
            f"主要阻力={thread.obstacle or '未定'}；"
            f"计划收束={thread.intended_resolution or '未定'}"
        )
    for edge in variant.edges.values():
        source = variant.nodes[edge.from_id].title
        target = variant.nodes[edge.to_id].title
        reason = f"（{edge.reason}）" if edge.reason else ""
        if edge.kind == "next":
            lines.append(f"推进：{source} → {target}{reason}")
        else:
            lines.append(f"后文回应：{target} → {source}（{edge.kind}）{reason}")
    return lines
```

局部输入沿用现有分区：选中节点及其阶段／故事线、作者计划、必需前提、约束、角色知情、正文观察、证词和创作目标。新增的 `change` 进入计划分区；阶段和故事线目标进入全局分区，不重复塞进每个节点。容量不足时先保证必需项，装不下就报缺口或拆分，不能静默裁掉后半部结构。

```python
sections = [
    _section("macro", "\n".join(_global_lines(variant))),
    _section("plan", "\n".join(plan_lines) or EMPTY_PLAN_TEXT),
    *optional_sections,
]
required = sum(len(section.text) for section in sections if section.required)
if capacity is not None and required > capacity:
    raise WorkspaceError(
        code="STORY_CONTEXT_INCOMPLETE",
        where="story.context",
        why=f"必需结构内容需要 {required} 字符，超出本次可用容量 {capacity}",
        fix="按阶段或故事线拆分这一轮任务，或减少同时处理的节点",
        details={"required": required, "capacity": capacity},
    )
```

构建空白作品的多线结构时，提示应要求先形成阶段、各线目标和汇合点，再细化线索使用。下面是一份合法示例，示例中的 ID 由模型按本轮实际读取的方案生成：

```json
{
  "operations": [
    {"op": "add_stage", "stage": {"id": "stage-night", "title": "失踪当晚", "order": 0,
      "summary": "三条线分别建立时间、物证与目击说法"}},
    {"op": "add_stage", "stage": {"id": "stage-search", "title": "第一轮调查", "order": 1,
      "summary": "各线结果汇合，时间差成为共同问题"}},
    {"op": "add_thread", "thread": {"id": "thread-alibi", "title": "不在场证明", "order": 0,
      "goal": "确认每个人当晚的位置", "obstacle": "证词互相矛盾",
      "intended_resolution": "在汇合节点用时间差收束"}},
    {"op": "add_thread", "thread": {"id": "thread-physical", "title": "物证", "order": 1,
      "goal": "找出与证词不符的物证", "obstacle": "物证只能证明发生过，不能证明是谁",
      "intended_resolution": "与不在场证明汇合"}},
    {"op": "add_thread", "thread": {"id": "thread-witness", "title": "目击说法", "order": 2,
      "goal": "核实目击者看到的时间", "obstacle": "目击者可能记错",
      "intended_resolution": "在第二轮排除错误时间"}},
    {"op": "create_clue_identity", "temp_id": "clue-board", "title": "没有响的木板"},
    {"op": "upsert_clue_plan", "clue_id": "clue-board", "plan": {
      "author_truth": null, "intention": "让调查者怀疑有人熟悉走廊结构",
      "resolution_intent": "undecided"}},
    {"op": "add_node", "node": {"id": "node-observe", "title": "发现异样",
      "action": "调查者注意到走廊木板没有响", "change": "调查者开始怀疑有人熟悉走廊结构",
      "stage_id": "stage-night", "thread_ids": ["thread-physical"],
      "clue_uses": [{"clue_id": "clue-board", "roles": ["plant"],
        "presentation": "有人经过时木板没有响", "mode": "action"}]}},
    {"op": "add_node", "node": {"id": "node-alibi", "title": "核对证词",
      "action": "调查者比较两名证人的时间", "change": "两名证人的时间对不上",
      "stage_id": "stage-night", "thread_ids": ["thread-alibi"]}},
    {"op": "add_node", "node": {"id": "node-witness", "title": "重问目击者",
      "action": "调查者追问目击者实际听到的内容", "change": "目击者承认只听到声音",
      "stage_id": "stage-night", "thread_ids": ["thread-witness"]}},
    {"op": "add_node", "node": {"id": "node-converge", "title": "汇合时间线",
      "action": "三条线的结果放到同一时间线上核对",
      "change": "三线结果指向同一段无人经过的时间",
      "stage_id": "stage-search",
      "thread_ids": ["thread-alibi", "thread-physical", "thread-witness"]}},
    {"op": "connect", "edge": {"id": "edge-observe-alibi", "from_id": "node-observe",
      "to_id": "node-alibi", "kind": "next", "reason": "物证促使调查者核对证词"}},
    {"op": "connect", "edge": {"id": "edge-physical-converge", "from_id": "node-observe",
      "to_id": "node-converge", "kind": "next", "reason": "物证结果进入汇合核对"}},
    {"op": "connect", "edge": {"id": "edge-alibi-converge", "from_id": "node-alibi",
      "to_id": "node-converge", "kind": "next", "reason": "时间矛盾需要共同核对"}},
    {"op": "connect", "edge": {"id": "edge-witness-converge", "from_id": "node-witness",
      "to_id": "node-converge", "kind": "next", "reason": "目击说法进入共同时间线"}}
  ]
}
```

提示里要同时写明几条边界：阶段与线的数量由作品决定，不要求固定模板；只有零散片段时允许先保存未分阶段节点；汇合节点只建一次并加入相关故事线；作者未定的目标、真相或结局保留 `null`；关系理由说明影响，不只写「发生在前」。一次有边界的修正后仍有缺口时，保留问题和未完成状态，不用一条更长的单链冒充多线。

## 6. 布局与定位

画布布局只依赖结构、故事线顺序和实测卡片尺寸。选中、拖动、折叠或缩放不触发重新排布，否则作者每次点节点，卡片都会跳一次。布局计算抽成纯函数，固定样例可以直接断言位置与折叠结果。

```typescript
const COLUMN_WIDTH = 320;
const CARD_GAP = 32;
const STAGE_GAP = 96;

interface StoryLayoutInput {
  stages: { id: string; order: number }[];
  threads: { id: string; order: number }[];
  nodes: {
    id: string;
    stageId: string | null;
    threadIds: string[];
    width: number;
    height: number;
  }[];
  edges: { id: string; fromId: string; toId: string; kind: "next" | "reframe" | "answer" }[];
}

interface PlacedNode {
  id: string;
  x: number;
  y: number;
  column: number;
  /** 共同节点横跨的故事线数量；仍只画一张卡。 */
  span: number;
}

interface StoryLayout {
  stages: { id: string; y: number; height: number }[];
  nodes: PlacedNode[];
  /** 折叠时保留的原有连接；不补造新的因果线。 */
  edges: StoryLayoutInput["edges"];
}
```

布局步骤：

1. 阶段按 `order` 排序，「未分阶段」作为独立区域放在最前；空阶段仍占位，不能因为暂时没有节点就消失。
2. 故事线按 `order` 固定列；共同节点放在参与列的最小列，`span` 取最大列与最小列之差加一。
3. 阶段内按 `next` 边计算层级；同层节点按最小列、再按 ID 排序。`reframe / answer` 不参与层级，只影响连线。
4. 汇总每个阶段的实际行高，按阶段顺序累加 `y`；卡片高度来自实测文字尺寸，不靠缩小字号维持固定高度。

```typescript
export function layoutStory(input: StoryLayoutInput): StoryLayout {
  const stages = [...input.stages].sort((a, b) => a.order - b.order);
  const threads = [...input.threads].sort((a, b) => a.order - b.order);
  const column = new Map(threads.map((thread, index) => [thread.id, index]));
  const placed: PlacedNode[] = [];
  const bands: StoryLayout["stages"] = [];
  let y = 0;

  for (const stage of [{ id: "__unassigned__", order: -1 }, ...stages]) {
    const stageNodes = input.nodes.filter((node) => (node.stageId ?? "__unassigned__") === stage.id);
    if (stage.id === "__unassigned__" && stageNodes.length === 0) continue;
    const rows = rowsByDepth(stageNodes, input.edges);
    const bandTop = y;
    y += 48; // 阶段标题
    for (const row of rows) {
      const height = Math.max(...row.map((node) => node.height));
      for (const node of row) {
        const columns = node.threadIds.map((id) => column.get(id) ?? 0);
        const start = columns.length ? Math.min(...columns) : 0;
        const span = columns.length ? Math.max(...columns) - start + 1 : 0;
        placed.push({
          id: node.id,
          x: start * COLUMN_WIDTH,
          y,
          column: start,
          span,
        });
      }
      y += height + CARD_GAP;
    }
    bands.push({ id: stage.id, y: bandTop, height: y - bandTop + STAGE_GAP });
    y += STAGE_GAP;
  }

  return { stages: bands, nodes: placed, edges: input.edges };
}
```

`rowsByDepth` 只使用 `next` 边，按最长路径分层；遇到环时由结构校验在保存前拒绝，布局不再自行修正。折叠阶段只移除该阶段的显示节点，保留跨阶段的原有连接作为摘要；展开后恢复真实节点和边。不能为了画面完整补一条「阶段概括」的假关系。

反向定位的顺序固定如下，任一步失败都停止并显示事实：

```typescript
async function locateStoryNode(
  workspaceId: string,
  variantId: string,
  nodeId: string,
  view: StoryViewState,
): Promise<void> {
  const epoch = ++view.locateEpoch;
  const graph = await readStoryVariant(workspaceId, variantId);
  if (epoch !== view.locateEpoch || view.workspaceId !== workspaceId) return;

  const node = graph.nodes.find((item) => item.id === nodeId);
  if (!node) {
    view.showMissingNode(nodeId); // 已删除或不属于当前作品
    return;
  }
  if (node.stage_id) view.expandStage(node.stage_id);
  await view.waitForLayout(); // 阶段展开后重新排布完成
  if (epoch !== view.locateEpoch) return;
  view.selectNode(nodeId);
  view.centerOn(nodeId, { minZoom: 0.8 }); // 保持可读缩放，不缩成整本书
}
```

从线索点回构思，或从正文返回构思时，恢复阶段展开状态、选中对象和视口；这些是浏览状态，不写入故事结构，也不发起模型调用。任务受理时冻结的对象和修订另行保存，浏览其他位置不会把正在运行的任务改到新位置。快速切换作品、重复点击和迟到响应用请求序号隔离，旧结果不能覆盖当前作品。

## 7. 前后端能力对齐

每个页面动作都要有对应的读取或写入能力；后端新增的能力也必须有实际使用方。P3 完成后按下表核对，删除已经不再被调用的 `group_id` 参数和客户端方法。

| 页面动作 | 接口 | 说明 |
|---|---|---|
| 打开构思 | `GET /story/variants`、`GET /story/variants/{variant_id}` | 只返回当前构思；图含阶段、故事线、节点和边 |
| 选择节点 | `GET /story/nodes/{node_id}?variant_id=...` | 阶段、关键变化、线索摘要、正文入口 |
| 查看相关线索 | `GET /clues?scope=story&node_id=...` | 节点为空时列全书，存在时按使用过滤 |
| 查看线索详情 | `GET /clues/{clue_id}?scope=story` | 计划、使用过程、正文观察分区 |
| 修改结构 | `POST /story/changes` | 带 `expected_revision`、`expected_ledger_revision`、`request_key` |
| 查看改动 | `GET /story/changes/{change_id}` | 涉及结构、账本与正文目标 |
| 撤销 | `POST /story/changes/{change_id}/undo` | 同一冲突检查边界 |
| 带入对话 | 现有 `POST /conversations`、`POST /conversations/{id}/turns` | 请求携带冻结的方案、修订与对象 |
| 定位正文 | 现有文件读取与原文定位 | 不新增资料系统，不调用模型 |

旧的分组读参数、备选／采用／比较／试读和候选正文接口不再出现在客户端调用中；对应的模型工具也不开放。发现「前端有按钮、后端无能力」或「后端有接口、前端无入口」时，在 P3 的对齐记录里列出处理结果，不带进 P4 之后。
