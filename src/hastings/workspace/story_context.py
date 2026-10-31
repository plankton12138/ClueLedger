"""构思结构进入模型输入：必需依赖、认知边界与试读隔离（1.0.1 P3）。

结构不是「一份设定文档」。同一条线索至少有五种身份，混在一起模型就会拿作者的真相
去写角色的猜测：

- **作者真相**：作者决定的事实，可以未定；
- **创作计划**：作者打算怎么安排（节点动作、回收意图、约定）；
- **正文观察**：读者已经读到的原句与当时的观察；
- **角色证词**：角色说了什么，不等于事实；
- **创作目标**：作者希望读者产生的理解，不等于读者已经产生的理解。

这里按任务、方案、位置与依赖装配这些内容，并把它们分栏写明。装配是纯函数：
读结构、不调模型、不写状态。容量不足时先保证必需项，装不下就报缺口，不截断必需要求。

试读是另一条路径：只给截止位置之前读者实际能看到的内容，过滤作者真相、未出现的
节点与线索、约定与核对记录。它保证的是「作品隐藏内容没有被提供」，不代表模型没有
常识，也不代表所有读者的理解。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from hastings.prompts.story import (
    AUTHOR_WRITING_CHECKS,
    CLUE_INTENT_UNWRITTEN,
    CLUE_RESOLUTION_OPEN,
    CLUE_RESOLUTION_UNDECIDED,
    DEFAULT_SPEAKER,
    EMPTY_PLAN_TEXT,
    SECTION_LABELS,
    clue_plan_line,
    clue_resolution_explained,
    clue_truth_line,
    constraint_line,
    empty_variant_text,
    judgement_line,
    knowledge_line,
    prerequisite_line,
    prose_binding_line,
    reader_speaker_prefix,
    split_note,
    story_head,
    story_overview_line,
    story_plan_line,
    testimony_narration_line,
    testimony_speech_line,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_clue_catalog import StoryClueCatalog, StoryClueFact
from hastings.workspace.story_models import (
    Constraint,
    StoryNode,
    StoryVariant,
)
from hastings.workspace.story_store import (
    EMPTY_REVISION,
    MAIN_VARIANT_ID,
    StorySnapshot,
    StoryStore,
)

__all__ = [
    "READER_PURPOSE",
    "SPLIT_MARGIN",
    "StoryBatch",
    "StoryContextSnapshot",
    "StoryInput",
    "StoryRequest",
    "StorySection",
    "StorySplit",
    "assemble_reader_input",
    "assemble_story_input",
    "assert_reader_isolated",
    "freeze_story_context",
    "scoped_request",
    "split_story_request",
    "story_required_chars",
    "verify_story_context",
]

StoryPurpose = Literal["discuss", "design", "write", "revise", "review", "reader_preview"]

READER_PURPOSE: StoryPurpose = "reader_preview"


#: 标题短于这个长度不参与泄露检查：单字标题在正常行文里出现不算泄露。
_MIN_FORBIDDEN = 2


@dataclass(frozen=True, slots=True)
class StoryRequest:
    """客户端这一次要看的方案、位置与用途；客户端只表达意图，不给权限。"""

    variant_id: str = ""
    revision: str = ""
    node_ids: tuple[str, ...] = ()
    purpose: StoryPurpose = "write"
    reader_cutoff: str | None = None
    structure_readonly: bool = False

    def identity(self) -> dict[str, object]:
        return {
            "variant_id": self.variant_id,
            "revision": self.revision,
            "node_ids": list(self.node_ids),
            "purpose": self.purpose,
            "reader_cutoff": self.reader_cutoff,
            "structure_readonly": self.structure_readonly,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> StoryRequest:
        purpose = str(raw.get("purpose") or "write")
        if purpose not in ("discuss", "design", "write", "revise", "review", "reader_preview"):
            raise WorkspaceError(
                code="STORY_SCOPE_INVALID",
                where="story.request",
                why=f"不支持的用途：{purpose}",
                fix="使用 discuss、design、write、revise、review 或 reader_preview",
                details={"purpose": purpose},
            )
        nodes = raw.get("node_ids") or []
        if not isinstance(nodes, list):
            raise WorkspaceError(
                code="STORY_SCOPE_INVALID",
                where="story.request",
                why="节点列表不是数组",
                fix="把要处理的节点标识放进数组",
            )
        cutoff = raw.get("reader_cutoff")
        return cls(
            variant_id=str(raw.get("variant_id") or ""),
            revision=str(raw.get("revision") or ""),
            node_ids=tuple(str(item) for item in nodes),
            purpose=purpose,  # type: ignore[arg-type]
            reader_cutoff=str(cutoff) if cutoff else None,
            structure_readonly=bool(raw.get("structure_readonly", False)),
        )


@dataclass(frozen=True, slots=True)
class StoryContextSnapshot:
    """受理时冻结的结构身份：方案、修订与位置。执行时修订已变则这一轮失败。"""

    variant_id: str
    revision: str
    node_ids: tuple[str, ...] = ()
    purpose: StoryPurpose = "write"
    reader_cutoff: str | None = None
    structure_readonly: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "variant_id": self.variant_id,
            "revision": self.revision,
            "node_ids": list(self.node_ids),
            "purpose": self.purpose,
            "reader_cutoff": self.reader_cutoff,
            "structure_readonly": self.structure_readonly,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> StoryContextSnapshot | None:
        variant_id = str(raw.get("variant_id") or "")
        if not variant_id:
            return None
        purpose = str(raw.get("purpose") or "write")
        nodes = raw.get("node_ids") or []
        cutoff = raw.get("reader_cutoff")
        return cls(
            variant_id=variant_id,
            revision=str(raw.get("revision") or ""),
            node_ids=tuple(str(item) for item in nodes) if isinstance(nodes, list) else (),
            purpose=purpose,  # type: ignore[arg-type]
            reader_cutoff=str(cutoff) if cutoff else None,
            structure_readonly=bool(raw.get("structure_readonly", False)),
        )


@dataclass(frozen=True, slots=True)
class StorySection:
    """一块按认知身份分开的内容；``required`` 的项目不因预算被丢弃。"""

    key: str
    title: str
    note: str
    text: str
    required: bool = True

    def render(self) -> str:
        return f"### {self.title}\n{self.note}\n{self.text}"

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "chars": len(self.text),
            "required": self.required,
        }


def _section(key: str, text: str, *, required: bool = True) -> StorySection:
    """按分区键取标题与说明，避免标题和说明散落在装配代码里。"""
    title, note = SECTION_LABELS[key]
    return StorySection(key=key, title=title, note=note, text=text, required=required)


@dataclass(frozen=True, slots=True)
class StoryInput:
    """一次装配的结果：实际进入请求的块、被省略的内容与覆盖范围。"""

    variant_id: str
    revision: str
    sections: tuple[StorySection, ...]
    omitted: tuple[dict[str, str], ...] = ()
    coverage: dict[str, object] = field(default_factory=dict)
    reader: bool = False
    #: 试读时截止前经核验的正文：已经公开的内容不再算隐藏，供发送前自检使用。
    published: str = ""

    @property
    def empty(self) -> bool:
        return not any(section.text.strip() for section in self.sections)

    def render(self) -> str:
        head = story_head(self.variant_id, self.revision)
        if not self.reader:
            head += AUTHOR_WRITING_CHECKS
        body = "\n\n".join(section.render() for section in self.sections if section.text.strip())
        return f"{head}\n\n{body}" if body else ""

    def summary(self) -> dict[str, object]:
        return {
            "variant_id": self.variant_id,
            "revision": self.revision,
            "reader": self.reader,
            "sections": [section.to_dict() for section in self.sections],
            "omitted": list(self.omitted),
            "coverage": dict(self.coverage),
        }


# -- 冻结与复核 -------------------------------------------------------------


def freeze_story_context(store: StoryStore, request: StoryRequest) -> StoryContextSnapshot:
    """受理时固定方案与位置；方案或节点不存在当场失败。"""
    snapshot = store.read()
    if snapshot.document is None:
        # 空白构思也允许进对话：没有点名别的方案、也没带修订时，
        # 按空的「主方案（empty 修订）」冻结，模型用结构工具建立第一份结构。
        if (
            request.variant_id not in ("", MAIN_VARIANT_ID)
            or request.revision not in ("", EMPTY_REVISION)
            or request.node_ids
        ):
            raise WorkspaceError(
                code="STORY_VARIANT_NOT_FOUND",
                where="story.accept",
                why="这本作品还没有构思结构",
                fix="先在构思里建立节点与线索，或把这一轮改成普通讨论",
            )
        if request.purpose == READER_PURPOSE:
            # 空结构上没有可读内容，试读不伪造。
            raise WorkspaceError(
                code="STORY_SCOPE_INVALID",
                where="story.accept",
                why="这本作品还没有构思结构，没有可试读的方案",
                fix="先在构思里建立节点与线索，再发起试读",
            )
        return StoryContextSnapshot(
            variant_id=MAIN_VARIANT_ID,
            revision=EMPTY_REVISION,
            node_ids=(),
            purpose=request.purpose,
            reader_cutoff=None,
            structure_readonly=request.structure_readonly,
        )
    variant_id = request.variant_id or snapshot.document.primary_variant_id
    variant = snapshot.variant(variant_id)
    if variant is None:
        raise WorkspaceError(
            code="STORY_VARIANT_NOT_FOUND",
            where="story.accept",
            why=f"没有这个方案：{variant_id}",
            fix="刷新构思后按当前方案发起",
            details={"variant_id": variant_id},
        )
    if request.revision and request.revision not in (variant.revision, snapshot.revision):
        raise WorkspaceError(
            code="STORY_REVISION_CONFLICT",
            where="story.accept",
            why=f"方案已经更新到修订 {variant.revision}，请求里写的是 {request.revision}",
            fix="刷新构思后按当前修订重新发起",
            details={"variant_id": variant_id, "revision": variant.revision},
        )
    missing = [node_id for node_id in request.node_ids if node_id not in variant.nodes]
    if missing:
        raise WorkspaceError(
            code="STORY_CONTEXT_INCOMPLETE",
            where="story.accept",
            why=f"选中的节点不在当前方案里：{missing[0]}",
            fix="刷新构思后重新选择节点；已删除的节点不能作为这一轮的目标",
            details={"node_id": missing[0]},
        )
    if request.purpose == READER_PURPOSE:
        if not request.reader_cutoff:
            raise WorkspaceError(
                code="STORY_SCOPE_INVALID",
                where="story.accept",
                why="试读没有给出读到哪里的截止位置",
                fix="在构思里选一个截止节点再发起试读",
            )
        if request.reader_cutoff not in variant.nodes:
            raise WorkspaceError(
                code="STORY_CONTEXT_INCOMPLETE",
                where="story.accept",
                why=f"试读截止节点不在当前方案里：{request.reader_cutoff}",
                fix="重新选择截止位置",
                details={"node_id": request.reader_cutoff},
            )
    return StoryContextSnapshot(
        variant_id=variant.id,
        revision=variant.revision,
        node_ids=request.node_ids,
        purpose=request.purpose,
        reader_cutoff=request.reader_cutoff,
        structure_readonly=request.structure_readonly,
    )


def verify_story_context(store: StoryStore, frozen: StoryContextSnapshot) -> StoryVariant:
    """执行前复核：方案还在且修订未变，否则这一轮不按旧结构继续。"""
    snapshot: StorySnapshot = store.read()
    if snapshot.document is None:
        # 空白构思的第一轮：受理时冻结的就是 empty 主方案，执行时还没有文件是正常的。
        if frozen.revision == EMPTY_REVISION and frozen.variant_id == MAIN_VARIANT_ID:
            return StoryVariant(id=MAIN_VARIANT_ID, name="主方案", revision=EMPTY_REVISION)
        raise WorkspaceError(
            code="STORY_VARIANT_NOT_FOUND",
            where="story.verify",
            why="这本作品还没有构思结构",
            fix="先在构思里建立节点与线索，或把这一轮改成普通讨论",
            details={"variant_id": frozen.variant_id},
        )
    variant = snapshot.variant(frozen.variant_id)
    if variant is None:
        raise WorkspaceError(
            code="STORY_VARIANT_NOT_FOUND",
            where="story.verify",
            why=f"方案已经不存在：{frozen.variant_id}",
            fix="刷新构思后重新发起这一轮",
            details={"variant_id": frozen.variant_id},
        )
    if variant.revision != frozen.revision:
        raise WorkspaceError(
            code="STORY_REVISION_CONFLICT",
            where="story.verify",
            why=f"受理之后方案已经更新：{frozen.revision} → {variant.revision}",
            fix="按当前结构重新发起；不按旧修订继续写",
            details={"variant_id": variant.id, "revision": variant.revision},
        )
    return variant


# -- 关系与位置 -------------------------------------------------------------


def _next_graph(variant: StoryVariant) -> dict[str, set[str]]:
    successors: dict[str, set[str]] = {node_id: set() for node_id in variant.nodes}
    for edge in variant.edges.values():
        if edge.kind == "next":
            successors[edge.from_id].add(edge.to_id)
    return successors


def _ancestors(variant: StoryVariant) -> dict[str, frozenset[str]]:
    """揭示顺序里的前驱闭包：``a`` 在 ``b`` 之前 ⇒ ``a ∈ ancestors[b]``。

    分叉结构没有全序；这里只回答「能不能到达」，不把偶然的拓扑序当成作者决定的顺序。
    """
    successors = _next_graph(variant)
    predecessors: dict[str, set[str]] = {node_id: set() for node_id in variant.nodes}
    for source, targets in successors.items():
        for target in targets:
            predecessors[target].add(source)
    memo: dict[str, frozenset[str]] = {}

    def walk(node_id: str, trail: frozenset[str]) -> frozenset[str]:
        if node_id in memo:
            return memo[node_id]
        result: set[str] = set()
        for parent in predecessors[node_id]:
            if parent in trail:
                continue
            result.add(parent)
            result |= walk(parent, trail | {parent})
        memo[node_id] = frozenset(result)
        return memo[node_id]

    for node_id in variant.nodes:
        walk(node_id, frozenset({node_id}))
    return memo


# -- 装配 -------------------------------------------------------------------


def _selected_nodes(variant: StoryVariant, node_ids: Iterable[str]) -> list[StoryNode]:
    ordered: list[StoryNode] = []
    for node_id in node_ids:
        node = variant.nodes.get(node_id)
        if node is not None:
            ordered.append(node)
    return ordered


def _thread_titles(variant: StoryVariant, node: StoryNode) -> str:
    names = [variant.threads[item].title for item in node.thread_ids if item in variant.threads]
    return "、".join(names)


def _involved_clues(
    nodes: Iterable[StoryNode],
    catalog: StoryClueCatalog,
) -> list[StoryClueFact]:
    """必需线索：被选中节点用到的；回收位置由节点的 ``resolve`` 作用导出。"""
    wanted: list[str] = []
    for node in nodes:
        for use in node.clue_uses:
            if use.clue_id not in wanted:
                wanted.append(use.clue_id)
    return [fact for clue_id in wanted if (fact := catalog.get(clue_id)) is not None]


def _applicable_constraints(variant: StoryVariant, nodes: Iterable[StoryNode]) -> list[Constraint]:
    node_ids = {node.id for node in nodes}
    subjects = {change.subject_id for node in nodes for change in node.knowledge_changes}

    def applies(scope: object) -> bool:
        kind = getattr(scope, "kind", "")
        target = getattr(scope, "target_id", "")
        if kind == "book":
            return True
        if kind == "node":
            return target in node_ids
        if kind == "character":
            return target in subjects
        return False

    return [constraint for constraint in variant.constraints.values() if applies(constraint.scope)]


def _knowledge_lines(
    variant: StoryVariant,
    nodes: Iterable[StoryNode],
    ancestors: Mapping[str, frozenset[str]],
) -> list[str]:
    """角色在当前位置知道什么：只看能到达选中节点的变化，不拿后章状态写前章。"""
    from hastings.workspace.story_knowledge import effective_knowledge

    del ancestors  # 推进先后不等于角色经历先后。
    selected = list(nodes)
    lines: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for change in effective_knowledge(variant, (node.id for node in selected)):
        key = (change.subject_id, change.stance, change.claim)
        if key not in seen:
            seen.add(key)
            lines.append(knowledge_line(change.subject_id, change.stance == "knows", change.claim))
    return lines


def _macro_lines(variant: StoryVariant) -> list[str]:
    """全书层面的阶段、故事线目标与关系；不重复每个节点的完整安排。"""
    lines: list[str] = []
    for stage in sorted(variant.stages.values(), key=lambda item: (item.order, item.id)):
        lines.append(
            f"阶段「{stage.title}」（第 {stage.order + 1} 段）：{stage.summary or '摘要未定'}"
        )
    for thread in sorted(variant.threads.values(), key=lambda item: (item.order, item.id)):
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


def assemble_story_input(
    variant: StoryVariant,
    frozen: StoryContextSnapshot,
    *,
    capacity: int | None = None,
    catalog: StoryClueCatalog | None = None,
) -> StoryInput:
    """按任务装配结构内容；必需项装不下时报缺口，不截断。"""
    catalog = catalog or StoryClueCatalog.from_variant(variant)
    nodes = _selected_nodes(variant, frozen.node_ids)
    if not nodes:
        if not variant.nodes:
            # 空白方案也要实际告诉模型「现在是空的、可以建立什么」，不能靠界面提示代替。
            section = _section(
                "empty",
                empty_variant_text(len(variant.clue_plans), len(variant.constraints)),
                required=False,
            )
            return StoryInput(
                variant_id=variant.id,
                revision=variant.revision,
                sections=(section,),
                coverage={
                    "nodes": [],
                    "clues": sorted(fact.clue_id for fact in catalog),
                    "constraints": sorted(variant.constraints),
                    "empty_plan": True,
                },
            )
        # 没有指定节点：只给方案概况，够讨论用，不假装知道作者在写哪一段。
        lines = [
            story_overview_line(node.title, len(node.clue_uses))
            for node in list(variant.nodes.values())[:40]
        ]
        macro = _macro_lines(variant)
        overview_sections = ([_section("macro", "\n".join(macro))] if macro else []) + [
            _section("overview", "\n".join(lines), required=False)
        ]
        return StoryInput(
            variant_id=variant.id,
            revision=variant.revision,
            sections=tuple(overview_sections),
            coverage={
                "nodes": [],
                "clues": [],
                "constraints": [],
                "stages": sorted(variant.stages),
                "threads": sorted(variant.threads),
            },
        )

    ancestors = _ancestors(variant)
    clues = _involved_clues(nodes, catalog)
    constraints = _applicable_constraints(variant, nodes)

    plan_lines: list[str] = []
    for node in nodes:
        threads = _thread_titles(variant, node)
        stage = variant.stages[node.stage_id].title if node.stage_id in variant.stages else ""
        plan_lines.append(
            story_plan_line(
                node.title,
                threads,
                node.action,
                node.time_note,
                stage=stage,
                change=node.change or "",
            )
        )
    for fact in clues:
        plan = fact.plan
        intent = (plan.intention if plan else "").strip() or CLUE_INTENT_UNWRITTEN
        intent_value = plan.resolution_intent if plan else "undecided"
        if intent_value == "explain":
            targets = "、".join(
                node.title
                for node in variant.nodes.values()
                for use in node.clue_uses
                if use.clue_id == fact.clue_id and "resolve" in use.roles
            )
            resolution = clue_resolution_explained(targets)
        elif intent_value == "leave_open":
            resolution = CLUE_RESOLUTION_OPEN
        else:
            resolution = CLUE_RESOLUTION_UNDECIDED
        plan_lines.append(clue_plan_line(fact.title, intent, resolution))

    truth_lines = [
        clue_truth_line(fact.title, fact.plan.author_truth if fact.plan else None)
        for fact in clues
        if fact.plan is not None
    ]

    testimony_lines: list[str] = []
    judgement_lines: list[str] = []
    for node in nodes:
        for use in node.clue_uses:
            title = catalog.title(use.clue_id)
            if use.mode == "testimony":
                speaker = use.speaker_id or DEFAULT_SPEAKER
                testimony_lines.append(testimony_speech_line(node.title, speaker, use.presentation))
            elif use.mode in ("hypothesis", "recollection", "dream"):
                testimony_lines.append(
                    testimony_narration_line(node.title, use.mode, title, use.presentation)
                )
            if use.expected_interpretation.strip():
                judgement_lines.append(judgement_line(node.title, use.expected_interpretation))

    plan_ids = {node.id for node in nodes}
    # 读者/作者走到这一位置时已经经过的节点：只用它们做前提，不含后面的安排。
    visible: set[str] = set(plan_ids)
    for node in nodes:
        visible |= set(ancestors.get(node.id, frozenset()))
    prerequisite_lines: list[str] = []
    for fact in clues:
        for node in variant.nodes.values():
            if node.id in plan_ids or node.id not in visible:
                continue
            for use in node.clue_uses:
                if use.clue_id != fact.clue_id:
                    continue
                prerequisite_lines.append(
                    prerequisite_line(fact.title, node.title, use.presentation, use.mode)
                )

    knowledge_lines = _knowledge_lines(variant, nodes, ancestors)
    prose_lines: list[str] = []
    for node in nodes:
        for binding in variant.bindings.values():
            if binding.node_id != node.id:
                continue
            prose_lines.append(
                prose_binding_line(node.title, binding.path, binding.quote, binding.observation)
            )

    constraint_lines = [
        constraint_line(constraint.text, constraint.strength == "required")
        for constraint in constraints
    ]

    sections: list[StorySection] = []
    macro = _macro_lines(variant)
    if macro:
        sections.append(_section("macro", "\n".join(macro)))
    if truth_lines:
        sections.append(_section("truth", "\n".join(truth_lines)))
    sections.append(_section("plan", "\n".join(plan_lines) or EMPTY_PLAN_TEXT))
    if prerequisite_lines:
        sections.append(_section("prerequisites", "\n".join(prerequisite_lines)))
    if constraint_lines:
        sections.append(_section("constraints", "\n".join(constraint_lines)))
    if knowledge_lines:
        sections.append(_section("knowledge", "\n".join(knowledge_lines)))
    if prose_lines:
        sections.append(_section("prose", "\n".join(prose_lines)))
    if testimony_lines:
        sections.append(_section("testimony", "\n".join(testimony_lines)))
    if judgement_lines:
        sections.append(_section("judgement", "\n".join(judgement_lines), required=False))

    omitted: list[dict[str, str]] = []
    rendered = sum(len(section.text) for section in sections)
    if capacity is not None:
        required_size = sum(len(section.text) for section in sections if section.required)
        if required_size > capacity:
            raise WorkspaceError(
                code="STORY_CONTEXT_INCOMPLETE",
                where="story.context",
                why=f"必需结构内容需要 {required_size} 字符，超出本次可用容量 {capacity}",
                fix="减少这一轮选中的节点，或先把它拆成几个更小的任务",
                details={"required": required_size, "capacity": capacity},
            )
        used = 0
        kept: list[StorySection] = []
        for section in sections:
            size = len(section.text)
            if section.required or used + size <= capacity:
                kept.append(section)
                used += size
            else:
                omitted.append({"key": section.key, "reason": "budget", "chars": str(size)})
        sections = kept
        rendered = used

    return StoryInput(
        variant_id=variant.id,
        revision=variant.revision,
        sections=tuple(sections),
        omitted=tuple(omitted),
        coverage={
            "nodes": [node.id for node in nodes],
            "clues": [fact.clue_id for fact in clues],
            "constraints": [constraint.id for constraint in constraints],
            "stages": sorted(variant.stages),
            "threads": sorted(variant.threads),
            "chars": rendered,
        },
    )


# -- 任务拆分 ---------------------------------------------------------------

#: 拆分时给「本轮只处理第几组」的说明预留的字符数：说明也进入结构分区。
SPLIT_MARGIN = 600

#: 拆分说明里最多直接列出的节点标识；其余用「等」收口，避免说明本身过长。
_SPLIT_ID_LIMIT = 12


def story_required_chars(
    variant: StoryVariant,
    frozen: StoryContextSnapshot,
    *,
    catalog: StoryClueCatalog | None = None,
) -> int:
    """必需分区在装配里占用的字符数；不应用容量，因此不会抛出缺口错误。"""
    assembled = assemble_story_input(variant, frozen, catalog=catalog)
    return sum(len(section.text) for section in assembled.sections if section.required)


@dataclass(frozen=True, slots=True)
class StoryBatch:
    """拆分后的一组节点：整组一起发起时必需内容放得下。"""

    node_ids: tuple[str, ...]
    required: int

    def to_dict(self) -> dict[str, object]:
        return {"node_ids": list(self.node_ids), "required": self.required}


@dataclass(frozen=True, slots=True)
class StorySplit:
    """一次超过容量的任务被拆成的若干组，以及因容量停下的节点。"""

    batches: tuple[StoryBatch, ...]
    stopped: tuple[str, ...]
    capacity: int

    def summary(self) -> dict[str, object]:
        return {
            "capacity": self.capacity,
            "batches": [batch.to_dict() for batch in self.batches],
            "stopped": list(self.stopped),
        }

    def note(self) -> str:
        """这一轮只说第 1 组：模型不能把没装进来的节点当成同一轮的目标。"""
        first = _ids_text(self.batches[0].node_ids)
        stopped = _ids_text(self.stopped) if self.stopped else ""
        return split_note(self.capacity, len(self.batches), first, stopped)


def _ids_text(node_ids: Iterable[str]) -> str:
    ids = list(node_ids)
    head = "、".join(ids[:_SPLIT_ID_LIMIT])
    return f"{head} 等 {len(ids)} 个" if len(ids) > _SPLIT_ID_LIMIT else head


def split_story_request(
    variant: StoryVariant,
    frozen: StoryContextSnapshot,
    *,
    capacity: int,
    catalog: StoryClueCatalog | None = None,
) -> StorySplit:
    """按必需内容把一次多节点任务拆成能单独发起的组。

    单个节点装不下时连同依赖它的后继一起停下：那些节点的前提不全，不能继续写。
    所有节点都装不下时不再拆分，直接报缺口，避免给出一个实际无法执行的计划。
    """
    selected = list(dict.fromkeys(frozen.node_ids))
    if len(selected) < 2:
        required = story_required_chars(variant, frozen, catalog=catalog)
        raise WorkspaceError(
            code="STORY_CONTEXT_INCOMPLETE",
            where="story.split",
            why=f"单个节点的必需内容需要 {required} 字符，超出本次容量 {capacity}，无法再拆分",
            fix="缩小这个节点的结构内容，或把它拆成几个更小的任务",
            details={"required": required, "capacity": capacity},
        )

    ancestors = _ancestors(variant)
    stopped: set[str] = {
        node_id
        for node_id in selected
        if story_required_chars(variant, scoped_request(frozen, (node_id,)), catalog=catalog)
        > capacity
    }
    # 依赖被停下节点的后继也必须停下：后面的内容缺了前提。
    changed = True
    while changed:
        changed = False
        for node_id in selected:
            if node_id in stopped:
                continue
            if ancestors.get(node_id, frozenset()) & stopped:
                stopped.add(node_id)
                changed = True

    batches: list[StoryBatch] = []
    current: tuple[str, ...] = ()
    for node_id in selected:
        if node_id in stopped:
            continue
        candidate = (*current, node_id)
        if (
            story_required_chars(variant, scoped_request(frozen, candidate), catalog=catalog)
            <= capacity
        ):
            current = candidate
            continue
        if current:
            batches.append(
                StoryBatch(
                    current,
                    story_required_chars(variant, scoped_request(frozen, current), catalog=catalog),
                )
            )
        current = (node_id,)
    if current:
        batches.append(
            StoryBatch(
                current,
                story_required_chars(variant, scoped_request(frozen, current), catalog=catalog),
            )
        )
    if not batches:
        required = story_required_chars(variant, frozen, catalog=catalog)
        raise WorkspaceError(
            code="STORY_CONTEXT_INCOMPLETE",
            where="story.split",
            why=f"每个节点的必需内容都超出本次容量 {capacity} 字符",
            fix="缩小节点范围，或把结构内容拆成更小的任务",
            details={"required": required, "capacity": capacity, "stopped": sorted(stopped)},
        )
    return StorySplit(
        batches=tuple(batches),
        stopped=tuple(node_id for node_id in selected if node_id in stopped),
        capacity=capacity,
    )


def scoped_request(frozen: StoryContextSnapshot, node_ids: tuple[str, ...]) -> StoryContextSnapshot:
    """同一方案与修订下的另一个节点范围；拆分与装配共用同一份冻结身份。"""
    return StoryContextSnapshot(
        variant_id=frozen.variant_id,
        revision=frozen.revision,
        node_ids=node_ids,
        purpose=frozen.purpose,
        reader_cutoff=frozen.reader_cutoff,
        structure_readonly=frozen.structure_readonly,
    )


# -- 试读 -------------------------------------------------------------------


def assemble_reader_input(
    variant: StoryVariant, cutoff: str, *, prose: Mapping[str, tuple[str, str]] | None = None
) -> StoryInput:
    """读者阶段能看到的内容：截止节点及其前驱的呈现稿，不含真相、计划与约定。"""
    ancestors = _ancestors(variant)
    if cutoff not in variant.nodes:
        raise WorkspaceError(
            code="STORY_CONTEXT_INCOMPLETE",
            where="story.reader",
            why=f"试读截止节点不在当前方案里：{cutoff}",
            fix="重新选择截止位置",
            details={"node_id": cutoff},
        )
    allowed = {cutoff, *ancestors.get(cutoff, frozenset())}
    ordered = [variant.nodes[node_id] for node_id in variant.nodes if node_id in allowed]

    lines: list[str] = []
    published_parts: list[str] = []
    for node in ordered:
        bindings = [binding for binding in variant.bindings.values() if binding.node_id == node.id]
        if bindings and prose is not None:
            for binding in bindings:
                digest, text = prose.get(binding.path, ("", ""))
                if digest != binding.digest or text[binding.start : binding.end] != binding.quote:
                    raise WorkspaceError(
                        code="STORY_READER_SOURCE_CONFLICT",
                        where="story.reader",
                        why="试读关联的正文已经变化",
                        fix="重新核对正文关联后再试读",
                    )
                lines.append(binding.quote)
                published_parts.append(binding.quote)
            continue
        for use in node.clue_uses:
            speaker = reader_speaker_prefix(use.speaker_id) if use.speaker_id else ""
            # 不打印线索标题：标题常常直接写着答案，读者阶段看不到它。
            lines.append(f"  - {speaker}{use.presentation}")
    if not any(line.strip() for line in lines):
        raise WorkspaceError(
            code="STORY_CONTEXT_INCOMPLETE",
            where="story.reader",
            why="截止位置之前没有可试读的正文关联或呈现稿",
            fix="先写场景或补充读者实际可见的呈现",
        )
    published = "\n".join(published_parts)
    section = _section("reader_prefix", "\n".join(lines))
    hidden = _hidden_strings(variant, allowed, published)
    return StoryInput(
        variant_id=variant.id,
        revision=variant.revision,
        sections=(section,),
        coverage={
            "nodes": sorted(allowed),
            "hidden": len(hidden),
            "published_chars": len(published),
        },
        reader=True,
        published=published,
    )


def _hidden_strings(
    variant: StoryVariant,
    allowed: set[str],
    published: str = "",
    catalog: StoryClueCatalog | None = None,
) -> list[str]:
    """读者还不该看到的内容：作者真相、未出现的节点与线索标题、约定原文。

    ``published`` 是截止前经核验的正文。已经出现在这段正文里的内容读者早已读到，
    不再算隐藏；未公开的真相、作者侧资料与未来内容仍然拦截。
    """

    def public(value: str) -> bool:
        return bool(published) and value in published

    catalog = catalog or StoryClueCatalog.from_variant(variant)
    hidden: list[str] = []
    for fact in catalog:
        truth = fact.plan.author_truth.strip() if fact.plan and fact.plan.author_truth else ""
        if len(truth) >= _MIN_FORBIDDEN and not public(truth):
            hidden.append(truth)
    for node in variant.nodes.values():
        title = node.title.strip()
        if node.id not in allowed and len(title) >= _MIN_FORBIDDEN and not public(title):
            hidden.append(title)
    for fact in catalog:
        used = any(
            use.clue_id == fact.clue_id and node.id in allowed
            for node in variant.nodes.values()
            for use in node.clue_uses
        )
        title = fact.title.strip()
        if not used and len(title) >= _MIN_FORBIDDEN and not public(title):
            hidden.append(title)
    for constraint in variant.constraints.values():
        text = constraint.text.strip()
        if len(text) >= _MIN_FORBIDDEN and not public(text):
            hidden.append(text)
    return hidden


def assert_reader_isolated(
    text: str, variant: StoryVariant, cutoff: str, *, published: str = ""
) -> None:
    """试读请求的自检：隐藏内容不能出现在实际发送的文本里。

    ``published`` 由装配给出，只包含截止前经核验的正文；不要用未核验的呈现稿或
    作者侧资料扩大可见范围。
    """
    ancestors = _ancestors(variant)
    allowed = {cutoff, *ancestors.get(cutoff, frozenset())}
    for value in _hidden_strings(variant, allowed, published):
        if value in text:
            raise WorkspaceError(
                code="STORY_READER_LEAK",
                where="story.reader",
                why="试读请求里出现了读者还不该看到的内容",
                fix="检查试读是否混入了结构资料或作者对话；不要安抚性地继续发送",
                details={"chars": len(value)},
            )
