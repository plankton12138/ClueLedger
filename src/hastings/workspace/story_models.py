"""构思结构的持久化契约（1.0.1 P1）。

这一层只回答两个问题：一份构思里有什么，以及哪些写法不能存进去。它不读正文、
不调模型、不做提交协调——那些分别属于 ``story_changes``、``story_context`` 和
``operations``。

几处刻意的取舍：

* 推进顺序与实际时间分开。``StoryEdge.kind == "next"`` 才参与无环校验；
  ``reframe`` 和 ``answer`` 允许从后文指向前文，因为重新解释和回收本来就发生在后面。
* 真相允许未定。``StoryCluePlan.author_truth`` 可以是 ``None``，
  ``resolution_intent`` 可以是 ``undecided``——未决定不等于否定事实，模型也不能把它
  填成空字符串来「表示没有」。
* 分支成员只由节点持有（``StoryNode.thread_ids``）。线程上的 ``node_ids`` 由节点导出，
  不再存一份，避免两处真相在编辑后分叉。
* 知情按事件位置取值。``KnowledgeChange.effective_event_node_id`` 决定「读到哪一章时
  这个角色知道什么」，所以回写前章不会误用后章状态。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

__all__ = [
    "Assessment",
    "AssessmentFinding",
    "AssessmentRequirement",
    "CluePlanInput",
    "ClueUse",
    "Constraint",
    "ConstraintScope",
    "CreateClueIdentity",
    "EventTimeRelation",
    "KnowledgeChange",
    "ProseBinding",
    "ProsePatch",
    "ReadDependency",
    "RemoveClueIdentity",
    "RemoveCluePlan",
    "RenameClueIdentity",
    "StoryChangeRecord",
    "StoryChangeSet",
    "StoryCluePlan",
    "StoryDocument",
    "StoryEdge",
    "StoryNode",
    "StoryOperation",
    "StoryReceipt",
    "StoryStage",
    "StoryThread",
    "StoryVariant",
    "UpsertCluePlan",
    "validate_story_variant",
]

StoryId = Annotated[str, Field(min_length=1, max_length=64)]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[str, Field(min_length=1, max_length=4000)]

#: 线索在正文里的呈现方式；未登记性质的记录只能是 ``unknown``，不能从标题猜成叙述。
PresentationMode = Literal[
    "narration",
    "action",
    "testimony",
    "recollection",
    "hypothesis",
    "dream",
    "unknown",
]
EdgeKind = Literal["next", "reframe", "answer"]
ResolutionIntent = Literal["explain", "leave_open", "undecided"]
ClueRole = Literal["plant", "develop", "misdirect", "reinterpret", "resolve"]
Provenance = Literal["author_request", "authorized_creation", "model_suggestion"]
ConstraintStrength = Literal["required", "preference"]
ConstraintScopeKind = Literal["book", "character", "node", "prose"]
Stance = Literal["knows", "believes"]


class StrictRecord(BaseModel):
    """所有结构对象的共同约束：拒绝多余字段，不做隐式类型转换。"""

    model_config = ConfigDict(strict=True, extra="forbid")


class ClueUse(StrictRecord):
    """一条线索在某个节点里怎么出现；预期理解是创作目标，不是读者的实际认知。

    ``roles`` 只描述作者计划，不推导正文里的实际埋设或回收。
    """

    clue_id: StoryId
    roles: list[ClueRole] = Field(default_factory=list)
    presentation: LongText
    mode: PresentationMode = "unknown"
    speaker_id: str = ""
    expected_interpretation: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def check_presentation(self) -> ClueUse:
        if not self.presentation.strip():
            raise ValueError("线索呈现不能为空")
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("同一次使用不能重复登记相同作用")
        if self.mode != "testimony" and self.speaker_id:
            raise ValueError("只有证词需要指明说话人")
        return self


class KnowledgeChange(StrictRecord):
    """某角色在某事件位置之后知道或相信什么；来源可以是多个先前节点。"""

    subject_id: StoryId
    claim: LongText
    stance: Stance = "knows"
    source_node_ids: list[StoryId] = Field(default_factory=list)
    effective_event_node_id: StoryId

    @model_validator(mode="after")
    def check_sources(self) -> KnowledgeChange:
        if len(set(self.source_node_ids)) != len(self.source_node_ids):
            raise ValueError("同一知情变化的来源节点不能重复")
        if self.effective_event_node_id in self.source_node_ids:
            raise ValueError("知情生效节点不能同时是自己的来源")
        return self


class StoryNode(StrictRecord):
    """一个创作节点：发生了什么、属于哪些分支、用了哪些线索。"""

    id: StoryId
    title: ShortText
    action: LongText
    thread_ids: list[StoryId] = Field(default_factory=list)
    clue_uses: list[ClueUse] = Field(default_factory=list)
    time_note: str = Field(default="", max_length=500)
    knowledge_changes: list[KnowledgeChange] = Field(default_factory=list)
    #: 关键行动带来的局面或理解变化；未定不等于没有变化。
    change: str | None = Field(default=None, max_length=1000)
    #: 阶段由作者安排，允许暂不归属。
    stage_id: str | None = None

    @model_validator(mode="after")
    def check_membership(self) -> StoryNode:
        if len(set(self.thread_ids)) != len(self.thread_ids):
            raise ValueError("同一节点不能重复加入同一条分支")
        used = [use.clue_id for use in self.clue_uses]
        if len(set(used)) != len(used):
            raise ValueError("同一节点不能重复使用同一条线索")
        if self.change is not None and not self.change.strip():
            raise ValueError("关键变化要么留空表示未定，要么写明内容")
        if self.stage_id is not None and not self.stage_id.strip():
            raise ValueError("节点阶段标识不能是空字符串")
        return self


class StoryCluePlan(StrictRecord):
    """构思里的线索计划；标题与身份在账本，这里只保存作者安排。"""

    clue_id: StoryId
    author_truth: str | None = None
    intention: str = Field(default="", max_length=2000)
    resolution_intent: ResolutionIntent = "undecided"

    @model_validator(mode="after")
    def check_truth(self) -> StoryCluePlan:
        if self.author_truth is not None and not self.author_truth.strip():
            raise ValueError("作者真相要么留空表示未定，要么写明内容")
        return self


class CluePlanInput(StrictRecord):
    """写入计划的公开字段；``clue_id`` 由操作目标给出，不在计划里重复。"""

    author_truth: str | None = None
    intention: str = Field(default="", max_length=2000)
    resolution_intent: ResolutionIntent = "undecided"

    @model_validator(mode="after")
    def check_truth(self) -> CluePlanInput:
        if self.author_truth is not None and not self.author_truth.strip():
            raise ValueError("作者真相要么留空表示未定，要么写明内容")
        return self


class StoryEdge(StrictRecord):
    """节点之间的关系；只有 ``next`` 表示揭示推进。"""

    id: StoryId
    from_id: StoryId
    to_id: StoryId
    kind: EdgeKind
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def check_ends(self) -> StoryEdge:
        if self.from_id == self.to_id:
            raise ValueError("节点不能指向自己")
        return self


class EventTimeRelation(StrictRecord):
    """实际事件时间：``before_node_id`` 发生在 ``after_node_id`` 之前，与揭示顺序无关。"""

    before_node_id: StoryId
    after_node_id: StoryId

    @model_validator(mode="after")
    def check_ends(self) -> EventTimeRelation:
        if self.before_node_id == self.after_node_id:
            raise ValueError("实际时间关系不能指向同一个节点")
        return self


class StoryStage(StrictRecord):
    """一段揭示推进；摘要用于折叠，不写入节点或关系。"""

    id: StoryId
    title: ShortText
    order: int = Field(ge=0)
    summary: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def check_summary(self) -> StoryStage:
        if self.summary is not None and not self.summary.strip():
            raise ValueError("阶段摘要要么留空表示未写，要么写明内容")
        return self


class StoryThread(StrictRecord):
    """一条故事线；成员由节点的 ``thread_ids`` 决定，这里不另存一份。

    目标、阻力与计划收束可以暂时未定。``order`` 决定画布列顺序，不是剧情顺序。
    """

    id: StoryId
    title: ShortText
    order: int = Field(ge=0)
    goal: str | None = Field(default=None, max_length=1000)
    obstacle: str | None = Field(default=None, max_length=1000)
    intended_resolution: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def check_plan(self) -> StoryThread:
        for label, value in (
            ("目标", self.goal),
            ("主要阻力", self.obstacle),
            ("计划收束", self.intended_resolution),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{label}要么留空表示未定，要么写明内容")
        return self


class ConstraintScope(StrictRecord):
    """约定作用在哪儿：全书、某个人物、某个节点或某段正文。"""

    kind: ConstraintScopeKind = "book"
    target_id: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def check_target(self) -> ConstraintScope:
        if self.kind == "book" and self.target_id:
            raise ValueError("全书约定不应指定目标")
        if self.kind != "book" and not self.target_id.strip():
            raise ValueError("人物、节点或正文约定需要明确目标")
        return self


class Constraint(StrictRecord):
    """作者约定的原文与强度；修订号由应用生成，用于判断依赖是否过期。"""

    id: StoryId
    text: LongText
    strength: ConstraintStrength = "required"
    scope: ConstraintScope = Field(default_factory=ConstraintScope)
    source_message_id: str = Field(default="", max_length=64)
    origin: Provenance = "author_request"
    revision: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def check_text(self) -> Constraint:
        if not self.text.strip():
            raise ValueError("约定不能为空")
        return self


class ProseBinding(StrictRecord):
    """结构节点与正文区间的关联；区间是服务端规范化后的 Unicode 码点。"""

    id: StoryId
    node_id: StoryId
    clue_id: str | None = None
    document_id: StoryId
    path: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    observation: str = Field(default="", max_length=2000)
    assessment_id: str | None = None

    @model_validator(mode="after")
    def check_range(self) -> ProseBinding:
        if self.end <= self.start or self.end - self.start != len(self.quote):
            raise ValueError("引文长度与字符区间不一致")
        if self.clue_id is not None and not self.clue_id.strip():
            raise ValueError("线索关联要么留空，要么给出线索标识")
        return self


class AssessmentRequirement(StrictRecord):
    """核对时被逐条对照的要求；来自任务受理时的固定要求，模型不能改写。"""

    requirement_id: StoryId
    text: LongText


class AssessmentFinding(StrictRecord):
    """一处具体发现：对应要求、严重性、原文位置与说明。"""

    requirement_id: str = Field(default="", max_length=64)
    severity: Literal["blocking", "advisory", "uncertain"] = "advisory"
    note: LongText
    path: str = ""
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    quote: str = ""

    @model_validator(mode="after")
    def check_position(self) -> AssessmentFinding:
        if (self.start is None) != (self.end is None):
            raise ValueError("发现的位置区间必须同时给出起点和终点")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("发现的位置区间必须非空")
        if self.path and self.start is None:
            raise ValueError("给出原文路径时需要同时给出位置")
        return self


class Assessment(StrictRecord):
    """一次语义核对的结果；不覆盖原记录，作者裁决另记。"""

    id: StoryId
    kind: Literal["write_check", "link_check", "manual"] = "write_check"
    requirements: list[AssessmentRequirement] = Field(default_factory=list)
    findings: list[AssessmentFinding] = Field(default_factory=list)
    checked_scope: str = Field(default="", max_length=2000)
    structure_revision: str = Field(min_length=1, max_length=64)
    prose_digests: dict[str, str] = Field(default_factory=dict)
    source_operation_id: str = Field(default="", max_length=64)
    decision_source: Literal["model", "author", "app"] = "model"

    @model_validator(mode="after")
    def check_coverage(self) -> Assessment:
        ids = [item.requirement_id for item in self.requirements]
        if len(set(ids)) != len(ids):
            raise ValueError("核对要求不能重复")
        known = set(ids)
        for finding in self.findings:
            if finding.requirement_id and finding.requirement_id not in known:
                raise ValueError("发现引用了本次核对之外的要求")
        return self


class StoryVariant(StrictRecord):
    """一个完整方案：主方案或备选，各自持有可解析的结构。"""

    id: StoryId
    name: ShortText
    base_variant_id: str | None = None
    base_revision: str | None = None
    revision: str = Field(min_length=1, max_length=64)
    stages: dict[str, StoryStage] = Field(default_factory=dict)
    nodes: dict[str, StoryNode] = Field(default_factory=dict)
    clue_plans: dict[str, StoryCluePlan] = Field(default_factory=dict)
    threads: dict[str, StoryThread] = Field(default_factory=dict)
    constraints: dict[str, Constraint] = Field(default_factory=dict)
    edges: dict[str, StoryEdge] = Field(default_factory=dict)
    event_times: list[EventTimeRelation] = Field(default_factory=list)
    bindings: dict[str, ProseBinding] = Field(default_factory=dict)
    assessments: dict[str, Assessment] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_indexes(self) -> StoryVariant:
        for name, items in (
            ("阶段", self.stages),
            ("节点", self.nodes),
            ("分支", self.threads),
            ("约定", self.constraints),
            ("关系", self.edges),
            ("正文关联", self.bindings),
            ("核对", self.assessments),
        ):
            if any(key != value.id for key, value in items.items()):
                raise ValueError(f"{name}的索引与自身标识不一致")
        if any(key != value.clue_id for key, value in self.clue_plans.items()):
            raise ValueError("线索计划的索引与自身标识不一致")
        for label, items in (("阶段", self.stages), ("故事线", self.threads)):
            orders = [item.order for item in items.values()]
            if len(set(orders)) != len(orders):
                raise ValueError(f"{label}顺序不能重复")
        if (self.base_variant_id is None) != (self.base_revision is None):
            raise ValueError("备选的来源方案与来源修订必须同时给出")
        if self.base_variant_id == self.id:
            raise ValueError("备选不能以自己为来源")
        for node in self.nodes.values():
            if node.stage_id is not None and node.stage_id not in self.stages:
                raise ValueError(f"节点引用了不存在的阶段：{node.stage_id}")
            missing = [value for value in node.thread_ids if value not in self.threads]
            if missing:
                raise ValueError(f"节点引用了不存在的分支：{missing[0]}")
            unknown_nodes = [
                change.effective_event_node_id
                for change in node.knowledge_changes
                if change.effective_event_node_id not in self.nodes
            ]
            if unknown_nodes:
                raise ValueError(f"知情变化引用了不存在的节点：{unknown_nodes[0]}")
        for edge in self.edges.values():
            if edge.from_id not in self.nodes or edge.to_id not in self.nodes:
                raise ValueError(f"关系指向不存在的节点：{edge.id}")
        for plan in self.clue_plans.values():
            resolve_nodes = [
                node
                for node in self.nodes.values()
                if any(
                    use.clue_id == plan.clue_id and "resolve" in use.roles
                    for use in node.clue_uses
                )
            ]
            if plan.resolution_intent == "explain" and not resolve_nodes:
                raise ValueError("明确解释的线索需要至少一个安排为回收的节点")
            if plan.resolution_intent != "explain" and resolve_nodes:
                raise ValueError("未计划解释或仍待定的线索不能安排回收作用")
        for binding in self.bindings.values():
            if binding.node_id not in self.nodes:
                raise ValueError(f"正文关联指向不存在的节点：{binding.id}")
            if binding.clue_id is not None and not binding.clue_id.strip():
                raise ValueError("正文关联的线索要么留空，要么给出标识")
        return self

    def node_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.nodes))

    def thread_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.threads))

    def members(self, thread_id: str) -> tuple[str, ...]:
        """某条分支的成员；按节点声明导出，不依赖另一份成员表。"""
        return tuple(
            sorted(
                node.id for node in self.nodes.values() if thread_id in node.thread_ids
            )
        )


def validate_story_variant(variant: StoryVariant) -> None:
    """推进关系与实际时间分别无环；失败抛 ``ValueError``，由调用层映射错误码。

    只检查结构本身能判断的事：悬空引用、重复标识、循环。语义是否成立留给核对阶段，
    这里不能假装检查了「这条线索是否真的成立」。
    """
    predecessors: dict[str, set[str]] = {node: set() for node in variant.nodes}
    seen: set[str] = set()
    stage_order = {stage.id: stage.order for stage in variant.stages.values()}
    for edge in variant.edges.values():
        if edge.id in seen:
            raise ValueError("关系标识重复")
        seen.add(edge.id)
        if edge.kind == "next":
            predecessors[edge.to_id].add(edge.from_id)
            source = variant.nodes[edge.from_id]
            target = variant.nodes[edge.to_id]
            if (
                source.stage_id is not None
                and target.stage_id is not None
                and stage_order[target.stage_id] < stage_order[source.stage_id]
            ):
                raise ValueError("推进关系不能从较后阶段回到较前阶段")
    _require_acyclic(predecessors, "推进顺序存在循环")

    time_predecessors: dict[str, set[str]] = {node: set() for node in variant.nodes}
    pairs: set[tuple[str, str]] = set()
    for relation in variant.event_times:
        pair = (relation.before_node_id, relation.after_node_id)
        if pair in pairs:
            raise ValueError("实际时间关系重复")
        pairs.add(pair)
        time_predecessors[relation.after_node_id].add(relation.before_node_id)
    _require_acyclic(time_predecessors, "实际事件时间存在循环")


def _require_acyclic(predecessors: dict[str, set[str]], message: str) -> None:
    from graphlib import CycleError, TopologicalSorter

    try:
        tuple(TopologicalSorter(predecessors).static_order())
    except CycleError as exc:
        raise ValueError(message) from exc


class ProsePatch(StrictRecord):
    """一处正文替换；按规范化字符区间校验原文，由构思存储协调提交。"""

    path: str = Field(min_length=1)
    base_digest: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    replacement: str
    delete: bool = False
    expected: str = ""

    @model_validator(mode="after")
    def check_range(self) -> ProsePatch:
        if self.end < self.start:
            raise ValueError("正文区间终点早于起点")
        if self.end - self.start != len(self.expected):
            raise ValueError("被替换原文长度与区间不一致")
        return self


class ReadDependency(StrictRecord):
    """这次变更读过的对象与修订；提交前重查，变了就冲突。"""

    kind: Literal["variant", "prose", "constraint", "module", "conversation"]
    id: str = Field(min_length=1)
    revision: str = Field(min_length=1)


class AddNode(StrictRecord):
    op: Literal["add_node"] = "add_node"
    node: StoryNode


class UpdateNode(StrictRecord):
    op: Literal["update_node"] = "update_node"
    node_id: StoryId
    node: StoryNode

    @model_validator(mode="after")
    def check_identity(self) -> UpdateNode:
        if self.node.id != self.node_id:
            raise ValueError("更新节点的目标与节点自身标识不一致")
        return self


class RemoveNode(StrictRecord):
    op: Literal["remove_node"] = "remove_node"
    node_id: StoryId


class CreateClueIdentity(StrictRecord):
    op: Literal["create_clue_identity"] = "create_clue_identity"
    temp_id: StoryId
    title: ShortText


class RenameClueIdentity(StrictRecord):
    op: Literal["rename_clue_identity"] = "rename_clue_identity"
    clue_id: StoryId
    title: ShortText


class RemoveClueIdentity(StrictRecord):
    op: Literal["remove_clue_identity"] = "remove_clue_identity"
    clue_id: StoryId


class UpsertCluePlan(StrictRecord):
    op: Literal["upsert_clue_plan"] = "upsert_clue_plan"
    clue_id: StoryId
    plan: CluePlanInput


class RemoveCluePlan(StrictRecord):
    op: Literal["remove_clue_plan"] = "remove_clue_plan"
    clue_id: StoryId


class AddStage(StrictRecord):
    op: Literal["add_stage"] = "add_stage"
    stage: StoryStage


class UpdateStage(StrictRecord):
    op: Literal["update_stage"] = "update_stage"
    stage_id: StoryId
    stage: StoryStage

    @model_validator(mode="after")
    def check_identity(self) -> UpdateStage:
        if self.stage.id != self.stage_id:
            raise ValueError("更新阶段的目标与阶段自身标识不一致")
        return self


class RemoveStage(StrictRecord):
    op: Literal["remove_stage"] = "remove_stage"
    stage_id: StoryId


class AddThread(StrictRecord):
    op: Literal["add_thread"] = "add_thread"
    thread: StoryThread


class RemoveThread(StrictRecord):
    op: Literal["remove_thread"] = "remove_thread"
    thread_id: StoryId


class Connect(StrictRecord):
    op: Literal["connect"] = "connect"
    edge: StoryEdge


class Disconnect(StrictRecord):
    op: Literal["disconnect"] = "disconnect"
    edge_id: StoryId


class AddConstraint(StrictRecord):
    op: Literal["add_constraint"] = "add_constraint"
    constraint: Constraint


class UpdateConstraint(StrictRecord):
    op: Literal["update_constraint"] = "update_constraint"
    constraint_id: StoryId
    constraint: Constraint

    @model_validator(mode="after")
    def check_identity(self) -> UpdateConstraint:
        if self.constraint.id != self.constraint_id:
            raise ValueError("更新约定的目标与约定自身标识不一致")
        return self


class RemoveConstraint(StrictRecord):
    op: Literal["remove_constraint"] = "remove_constraint"
    constraint_id: StoryId


class SetEventTime(StrictRecord):
    op: Literal["set_event_time"] = "set_event_time"
    relation: EventTimeRelation


class UpdateThread(StrictRecord):
    op: Literal["update_thread"] = "update_thread"
    thread_id: StoryId
    thread: StoryThread

    @model_validator(mode="after")
    def check_identity(self) -> UpdateThread:
        if self.thread.id != self.thread_id:
            raise ValueError("更新分支的目标与分支自身标识不一致")
        return self


class UnsetEventTime(StrictRecord):
    op: Literal["unset_event_time"] = "unset_event_time"
    before_node_id: StoryId
    after_node_id: StoryId


class RemoveAssessment(StrictRecord):
    op: Literal["remove_assessment"] = "remove_assessment"
    assessment_id: StoryId


class BindProse(StrictRecord):
    op: Literal["bind_prose"] = "bind_prose"
    binding: ProseBinding


class UnbindProse(StrictRecord):
    op: Literal["unbind_prose"] = "unbind_prose"
    binding_id: StoryId


class RecordAssessment(StrictRecord):
    op: Literal["record_assessment"] = "record_assessment"
    assessment: Assessment


StoryOperation = Annotated[
    AddNode
    | UpdateNode
    | RemoveNode
    | AddStage
    | UpdateStage
    | RemoveStage
    | CreateClueIdentity
    | RenameClueIdentity
    | RemoveClueIdentity
    | UpsertCluePlan
    | RemoveCluePlan
    | AddThread
    | UpdateThread
    | RemoveThread
    | Connect
    | Disconnect
    | AddConstraint
    | UpdateConstraint
    | RemoveConstraint
    | SetEventTime
    | UnsetEventTime
    | BindProse
    | UnbindProse
    | RecordAssessment
    | RemoveAssessment,
    Field(discriminator="op"),
]


class StoryChangeSet(StrictRecord):
    """一次完整变更单元：模型或界面只描述改什么，元数据由应用补齐。"""

    id: StoryId
    base_revision: str = Field(min_length=1, max_length=64)
    #: 允许为空：只改正文、不碰结构的一次提交是合法变更。
    operations: list[StoryOperation] = Field(default_factory=list)
    prose_patches: list[ProsePatch] = Field(default_factory=list)
    read_dependencies: list[ReadDependency] = Field(default_factory=list)
    requested_scope: str = Field(default="", max_length=500)
    operation_id: str = Field(default="", max_length=64)
    generation: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_unique(self) -> StoryChangeSet:
        if not self.operations and not self.prose_patches:
            raise ValueError("变更没有任何操作")
        deps = [(item.kind, item.id) for item in self.read_dependencies]
        if len(set(deps)) != len(deps):
            raise ValueError("同一读取依赖不能重复登记")
        return self


class StoryReceipt(StrictRecord):
    """一次已受理变更的收据；同键同负载返回它，同键异负载报冲突。"""

    fingerprint: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    ledger_revision: str = ""
    request_key: str = Field(min_length=1)
    #: 建方案这类没有结构变更的受理为空字符串，不用假标识占位。
    change_id: str = ""
    id_map: dict[str, str] = Field(default_factory=dict)
    result: dict[str, JsonValue] = Field(default_factory=dict)


class StoryChangeRecord(StrictRecord):
    """已提交变更的原文，用于查看改动与撤销；不随之后的编辑改写。"""

    change_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    request_key: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    operations: list[StoryOperation]
    #: 撤销用的反向操作；提交时按前后状态差生成，不靠事后重算。
    inverse_operations: list[StoryOperation] = Field(default_factory=list)
    inverse_ledger: list[dict[str, JsonValue]] = Field(default_factory=list)
    inverse_prose_patches: list[ProsePatch] = Field(default_factory=list)
    #: 本次改动的对象 → 提交后的内容摘要；撤销前重查，变了就冲突。
    touched: dict[str, str] = Field(default_factory=dict)
    undone_by: str | None = None


class StoryDocument(StrictRecord):
    """状态目录里 ``story/story.json`` 的全部内容。"""

    schema_version: Literal[1] = 1
    workspace_lineage: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    primary_variant_id: str = Field(min_length=1)
    variants: dict[str, StoryVariant] = Field(default_factory=dict)
    changes: dict[str, StoryChangeRecord] = Field(default_factory=dict)
    requests: dict[str, StoryReceipt] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_primary(self) -> StoryDocument:
        if any(key != value.id for key, value in self.variants.items()):
            raise ValueError("方案的索引与自身标识不一致")
        if self.primary_variant_id not in self.variants:
            raise ValueError("主方案不在方案集合里")
        if any(key != value.change_id for key, value in self.changes.items()):
            raise ValueError("变更记录的索引与自身标识不一致")
        if any(key != value.request_key for key, value in self.requests.items()):
            raise ValueError("请求收据的索引与请求键不一致")
        return self
