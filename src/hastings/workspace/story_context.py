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

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_models import (
    Clue,
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
    "READER_QUESTION",
    "StoryContextSnapshot",
    "StoryInput",
    "StoryRequest",
    "StorySection",
    "assemble_reader_input",
    "assemble_story_input",
    "assert_reader_isolated",
    "freeze_story_context",
    "verify_story_context",
]

StoryPurpose = Literal["discuss", "design", "write", "revise", "review", "reader_preview"]

READER_PURPOSE: StoryPurpose = "reader_preview"

#: 试读的问题由应用固定，不接收作者的自由提问：问题本身会泄露隐藏信息。
READER_QUESTION = (
    "根据已经读到的内容，你认为接下来最可能发生什么？只依据读到的部分，并引用一两处原文。"
)

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

    def identity(self) -> dict[str, object]:
        return {
            "variant_id": self.variant_id,
            "revision": self.revision,
            "node_ids": list(self.node_ids),
            "purpose": self.purpose,
            "reader_cutoff": self.reader_cutoff,
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
        )


@dataclass(frozen=True, slots=True)
class StoryContextSnapshot:
    """受理时冻结的结构身份：方案、修订与位置。执行时修订已变则这一轮失败。"""

    variant_id: str
    revision: str
    node_ids: tuple[str, ...] = ()
    purpose: StoryPurpose = "write"
    reader_cutoff: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "variant_id": self.variant_id,
            "revision": self.revision,
            "node_ids": list(self.node_ids),
            "purpose": self.purpose,
            "reader_cutoff": self.reader_cutoff,
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


@dataclass(frozen=True, slots=True)
class StoryInput:
    """一次装配的结果：实际进入请求的块、被省略的内容与覆盖范围。"""

    variant_id: str
    revision: str
    sections: tuple[StorySection, ...]
    omitted: tuple[dict[str, str], ...] = ()
    coverage: dict[str, object] = field(default_factory=dict)
    reader: bool = False

    @property
    def empty(self) -> bool:
        return not any(section.text.strip() for section in self.sections)

    def render(self) -> str:
        head = (
            "## 构思结构\n"
            f"方案 `{self.variant_id}` · 修订 `{self.revision}`。"
            "以下分栏说明每种内容是什么；不要把一个角色说的话当成作者真相，"
            "也不要把创作目标当成读者已经产生的理解。"
        )
        body = "\n\n".join(
            section.render() for section in self.sections if section.text.strip()
        )
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


def freeze_story_context(
    store: StoryStore, request: StoryRequest
) -> StoryContextSnapshot:
    """受理时固定方案与位置；方案或节点不存在当场失败。"""
    snapshot = store.read()
    if snapshot.document is None:
        # 空白构思也允许进对话：没有点名别的方案、也没带修订时，
        # 按空的「主方案（empty 修订）」冻结，模型用结构工具建立第一份结构。
        if request.variant_id not in ("", MAIN_VARIANT_ID) or request.revision:
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


def _precedes(ancestors: Mapping[str, frozenset[str]], before: str, after: str) -> bool:
    return before in ancestors.get(after, frozenset())


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
    variant: StoryVariant, nodes: Iterable[StoryNode]
) -> list[Clue]:
    """必需线索：被选中节点用到的，以及明确回收在这些节点上的。"""
    selected_ids = {node.id for node in nodes}
    wanted: list[str] = []
    for node in nodes:
        for use in node.clue_uses:
            if use.clue_id not in wanted:
                wanted.append(use.clue_id)
    for clue_id, clue in variant.clues.items():
        if clue_id in wanted:
            continue
        if selected_ids & set(clue.resolution.node_ids):
            wanted.append(clue_id)
    return [variant.clues[clue_id] for clue_id in wanted if clue_id in variant.clues]


def _applicable_constraints(
    variant: StoryVariant, nodes: Iterable[StoryNode]
) -> list[Constraint]:
    node_ids = {node.id for node in nodes}
    subjects = {
        change.subject_id
        for node in nodes
        for change in node.knowledge_changes
    }
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

    return [
        constraint
        for constraint in variant.constraints.values()
        if applies(constraint.scope)
    ]


def _knowledge_lines(
    variant: StoryVariant,
    nodes: Iterable[StoryNode],
    ancestors: Mapping[str, frozenset[str]],
) -> list[str]:
    """角色在当前位置知道什么：只看能到达选中节点的变化，不拿后章状态写前章。"""
    subjects = {
        change.subject_id for node in nodes for change in node.knowledge_changes
    }
    if not subjects:
        return []
    visible: set[str] = set()
    for node in nodes:
        visible.add(node.id)
        visible |= set(ancestors.get(node.id, frozenset()))
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for node in variant.nodes.values():
        if node.id not in visible:
            continue
        for change in node.knowledge_changes:
            if change.subject_id not in subjects:
                continue
            key = (change.subject_id, change.claim)
            if key in seen:
                continue
            seen.add(key)
            stance = "知道" if change.stance == "knows" else "相信"
            lines.append(f"- {change.subject_id} {stance}：{change.claim}")
    return lines


def assemble_story_input(
    variant: StoryVariant,
    frozen: StoryContextSnapshot,
    *,
    capacity: int | None = None,
) -> StoryInput:
    """按任务装配结构内容；必需项装不下时报缺口，不截断。"""
    nodes = _selected_nodes(variant, frozen.node_ids)
    if not nodes:
        # 没有指定节点：只给方案概况，够讨论用，不假装知道作者在写哪一段。
        lines = [
            f"- 「{node.title}」（{len(node.clue_uses)} 条线索）"
            for node in list(variant.nodes.values())[:40]
        ]
        section = StorySection(
            key="overview",
            title="方案概况",
            note="这一轮没有指定节点，只列已有节点标题；需要细节时先选定节点。",
            text="\n".join(lines),
            required=False,
        )
        return StoryInput(
            variant_id=variant.id,
            revision=variant.revision,
            sections=(section,),
            coverage={"nodes": [], "clues": [], "constraints": []},
        )

    ancestors = _ancestors(variant)
    clues = _involved_clues(variant, nodes)
    constraints = _applicable_constraints(variant, nodes)

    plan_lines: list[str] = []
    for node in nodes:
        threads = _thread_titles(variant, node)
        suffix = f"（分支：{threads}）" if threads else ""
        time_note = f"；时间：{node.time_note}" if node.time_note else ""
        plan_lines.append(f"- 「{node.title}」{suffix}：{node.action}{time_note}")
    for clue in clues:
        intent = clue.intention.strip() or "未写回收意图"
        if clue.resolution.intent == "explain":
            targets = "、".join(
                variant.nodes[item].title
                for item in clue.resolution.node_ids
                if item in variant.nodes
            )
            resolution = f"计划在「{targets}」解释"
        elif clue.resolution.intent == "leave_open":
            resolution = "计划保持不解释"
        else:
            resolution = "回收方式未定"
        plan_lines.append(f"- 线索「{clue.title}」：{intent}；{resolution}")

    truth_lines = [
        f"- 「{clue.title}」：{'未定' if clue.author_truth is None else clue.author_truth}"
        for clue in clues
    ]

    testimony_lines: list[str] = []
    judgement_lines: list[str] = []
    for node in nodes:
        for use in node.clue_uses:
            used_clue = variant.clues.get(use.clue_id)
            title = used_clue.title if used_clue is not None else use.clue_id
            if use.mode == "testimony":
                speaker = use.speaker_id or "某个角色"
                testimony_lines.append(
                    f"- 在「{node.title}」里 {speaker} 说：{use.presentation}"
                    "（这是角色的话，不等于作者真相）"
                )
            elif use.mode in ("hypothesis", "recollection", "dream"):
                testimony_lines.append(
                    f"- 在「{node.title}」里以 {use.mode} 呈现「{title}」：{use.presentation}"
                    "（这是叙述中的推测或回忆，不等于事实）"
                )
            if use.expected_interpretation.strip():
                judgement_lines.append(
                    f"- 「{node.title}」想引导读者理解为：{use.expected_interpretation}"
                    "（这是创作目标，不表示读者已经这样理解）"
                )

    plan_ids = {node.id for node in nodes}
    # 读者/作者走到这一位置时已经经过的节点：只用它们做前提，不含后面的安排。
    visible: set[str] = set(plan_ids)
    for node in nodes:
        visible |= set(ancestors.get(node.id, frozenset()))
    prerequisite_lines: list[str] = []
    for clue in clues:
        for node in variant.nodes.values():
            if node.id in plan_ids or node.id not in visible:
                continue
            for use in node.clue_uses:
                if use.clue_id != clue.id:
                    continue
                prerequisite_lines.append(
                    f"- 线索「{clue.title}」在「{node.title}」里已经这样出现：{use.presentation}"
                    f"（{use.mode}）"
                )

    knowledge_lines = _knowledge_lines(variant, nodes, ancestors)
    prose_lines: list[str] = []
    for node in nodes:
        for binding in variant.bindings.values():
            if binding.node_id != node.id:
                continue
            observation = binding.observation.strip() or "尚未记录观察"
            prose_lines.append(
                f"- 「{node.title}」在 `{binding.path}` 的原文：{binding.quote}"
                f"（观察：{observation}）"
            )

    constraint_lines = [
        f"- {constraint.text}"
        + ("" if constraint.strength == "required" else "（偏好，可权衡）")
        for constraint in constraints
    ]

    sections: list[StorySection] = []
    if truth_lines:
        sections.append(
            StorySection(
                key="truth",
                title="作者真相",
                note="只有作者知道的事实；角色与读者未必知道，写场景时不要直接说出来。",
                text="\n".join(truth_lines),
            )
        )
    sections.append(
        StorySection(
            key="plan",
            title="创作计划",
            note="作者打算怎么安排；这是计划，不是已经写出来的正文。",
            text="\n".join(plan_lines) or "（没有可用的计划内容）",
        )
    )
    if prerequisite_lines:
        sections.append(
            StorySection(
                key="prerequisites",
                title="必需前提",
                note="这些内容在前面已经出现，回收或回应时要与之相符；不含后面的安排。",
                text="\n".join(prerequisite_lines),
            )
        )
    if constraint_lines:
        sections.append(
            StorySection(
                key="constraints",
                title="本作品约定",
                note="作者定下的约束；与计划冲突时以约定为准。",
                text="\n".join(constraint_lines),
            )
        )
    if knowledge_lines:
        sections.append(
            StorySection(
                key="knowledge",
                title="角色知情（按位置）",
                note="只列与当前节点相关的知情变化；后面才发生的变化不能用在这一段。",
                text="\n".join(knowledge_lines),
            )
        )
    if prose_lines:
        sections.append(
            StorySection(
                key="prose",
                title="正文观察",
                note="读者已经读到的原句与当时的观察；与计划冲突时以原文为准。",
                text="\n".join(prose_lines),
            )
        )
    if testimony_lines:
        sections.append(
            StorySection(
                key="testimony",
                title="角色证词与推测",
                note="角色说出来的话；可以是假话或误信，不能当成作者真相。",
                text="\n".join(testimony_lines),
            )
        )
    if judgement_lines:
        sections.append(
            StorySection(
                key="judgement",
                title="创作目标",
                note="作者希望读者产生的理解；可以实现得更好或更含蓄，但不是既成事实。",
                text="\n".join(judgement_lines),
                required=False,
            )
        )

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
            "clues": [clue.id for clue in clues],
            "constraints": [constraint.id for constraint in constraints],
            "chars": rendered,
        },
    )


# -- 试读 -------------------------------------------------------------------


def assemble_reader_input(variant: StoryVariant, cutoff: str) -> StoryInput:
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
    for node in ordered:
        lines.append(f"- 「{node.title}」：{node.action}")
        for use in node.clue_uses:
            speaker = f"{use.speaker_id} 说：" if use.speaker_id else ""
            # 不打印线索标题：标题常常直接写着答案，读者阶段看不到它。
            lines.append(f"  - {speaker}{use.presentation}")
    section = StorySection(
        key="reader_prefix",
        title="已读到的内容",
        note="只包含截止位置之前读者能看到的呈现稿，不含作者真相、计划与约定。",
        text="\n".join(lines),
    )
    hidden = _hidden_strings(variant, allowed)
    return StoryInput(
        variant_id=variant.id,
        revision=variant.revision,
        sections=(section,),
        coverage={"nodes": sorted(allowed), "hidden": len(hidden)},
        reader=True,
    )


def _hidden_strings(variant: StoryVariant, allowed: set[str]) -> list[str]:
    """读者还不该看到的内容：作者真相、未出现的节点与线索标题、约定原文。"""
    hidden: list[str] = []
    for clue in variant.clues.values():
        if clue.author_truth and len(clue.author_truth.strip()) >= _MIN_FORBIDDEN:
            hidden.append(clue.author_truth.strip())
    for node in variant.nodes.values():
        if node.id not in allowed and len(node.title.strip()) >= _MIN_FORBIDDEN:
            hidden.append(node.title.strip())
    for clue in variant.clues.values():
        used = any(
            use.clue_id == clue.id and node.id in allowed
            for node in variant.nodes.values()
            for use in node.clue_uses
        )
        if not used and len(clue.title.strip()) >= _MIN_FORBIDDEN:
            hidden.append(clue.title.strip())
    for constraint in variant.constraints.values():
        if len(constraint.text.strip()) >= _MIN_FORBIDDEN:
            hidden.append(constraint.text.strip())
    return hidden


def assert_reader_isolated(text: str, variant: StoryVariant, cutoff: str) -> None:
    """试读请求的自检：隐藏内容不能出现在实际发送的文本里。"""
    ancestors = _ancestors(variant)
    allowed = {cutoff, *ancestors.get(cutoff, frozenset())}
    for value in _hidden_strings(variant, allowed):
        if value in text:
            raise WorkspaceError(
                code="STORY_READER_LEAK",
                where="story.reader",
                why="试读请求里出现了读者还不该看到的内容",
                fix="检查试读是否混入了结构资料或作者对话；不要安抚性地继续发送",
                details={"chars": len(value)},
            )
