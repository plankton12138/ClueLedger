"""对象级授权：把作者本轮的话变成可核对的许可集合（1.0.1 P4）。

模型能做什么由应用决定，不由模型自述。这里不猜测任务意图，只把两件确定的事合起来：

* 界面冻结的节点范围（``StoryContextSnapshot.node_ids``）；
* 作者本轮原话里出现的对象名称。

从这些对象出发，再把它们直接依存的对象（所属分支、使用的线索、正文关联、两端都在
范围内的关系、指向这些节点的约定）纳入范围。范围外的已有对象只能读，不能改、不能删；
新建对象本身是作者要求的创作，允许提交。引用文字里写着「把所有线索都改掉」也不会扩大
范围——授权只来自作者消息中的对象名称与界面选择。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_clue_catalog import StoryClueCatalog
from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_models import (
    AddConstraint,
    AddNode,
    AddStage,
    AddThread,
    BindProse,
    Connect,
    CreateClueIdentity,
    Disconnect,
    RecordAssessment,
    RemoveAssessment,
    RemoveClueIdentity,
    RemoveCluePlan,
    RemoveConstraint,
    RemoveNode,
    RemoveStage,
    RemoveThread,
    RenameClueIdentity,
    SetEventTime,
    StoryChangeSet,
    StoryOperation,
    StoryVariant,
    UnbindProse,
    UnsetEventTime,
    UpdateConstraint,
    UpdateNode,
    UpdateStage,
    UpdateThread,
    UpsertCluePlan,
)
from hastings.workspace.story_store import new_id

__all__ = ["StoryAuthority", "blocks_writes", "build_authority", "is_discussion"]

_LABEL = {
    "stage": "阶段",
    "node": "节点",
    "clue": "线索",
    "thread": "故事线",
    "constraint": "约定",
    "edge": "关系",
    "binding": "正文关联",
    "assessment": "核对记录",
}

_WHITESPACE = re.compile(r"\s+")

#: 讨论与追问的标记。它们出现在命令动词之前时，这一轮按只读处理。
_DISCUSSION = ("解释", "什么意思", "含义", "指的是", "怎么理解", "为什么", "为何")

#: 命令动词。只用来判断作者先说讨论还是先下命令，不单独授予任何权限。
_COMMAND = (
    "采用",
    "改为",
    "改成",
    "设为",
    "设成",
    "修改为",
    "新增",
    "添加",
    "增加",
    "删除",
    "移除",
    "取消",
    "创建",
    "建立",
    "保存",
    "写下",
    "写入",
    "改",
)

_NEGATION = (
    "不要",
    "不改",
    "不能",
    "禁止",
    "不许",
    "只讨论",
    "不保存",
    "只分析",
    "不要修改",
    "不执行修改",
)
_HYPOTHETICAL = ("假设", "如果", "假如", "要是")
_QUOTED = ("引用", "原文", "转述")

#: 明确只读的说法；与讨论标记一起决定这一轮是否允许写入。
_READ_ONLY = (
    "只讨论",
    "不保存",
    "只分析",
    "不要修改",
    "不执行修改",
    "不要改",
    "别改",
    "不用改",
)

#: 成对引号。引号里本身是一句命令时，它是被引用的内容，不是作者的命令。
_QUOTE_PAIRS = {"“": "”", "「": "」", "『": "』", '"': '"', "‘": "’"}


def _flat(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _named(title: str, text: str) -> bool:
    """名称至少两个字，按原样出现在作者话里才算点到；单字名称容易误配。"""
    name = _flat(title)
    return len(name) >= 2 and name in text


def _quoted_spans(text: str) -> list[tuple[int, int, str]]:
    """按出现顺序给出成对引号覆盖的区间与内容；落单的引号按普通字符处理。"""
    spans: list[tuple[int, int, str]] = []
    index = 0
    while index < len(text):
        close = _QUOTE_PAIRS.get(text[index])
        if close is None:
            index += 1
            continue
        end = text.find(close, index + 1)
        if end < 0:
            index += 1
            continue
        spans.append((index, end + 1, text[index + 1 : end]))
        index = end + 1
    return spans


def _without_quoted(text: str) -> str:
    """去掉引号内容：判断讨论还是命令时，引文里的词不算作者自己的话。"""
    parts: list[str] = []
    last = 0
    for start, end, _ in _quoted_spans(text):
        parts.append(text[last:start])
        last = end
    parts.append(text[last:])
    return "".join(parts)


def _plain(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    return _without_quoted(text)


def _first(text: str, words: tuple[str, ...]) -> int:
    found = [text.find(word) for word in words if word in text]
    return min(found) if found else -1


def is_discussion(author_text: str) -> bool:
    """作者是在解释、追问或讨论，而不是下命令。

    只看引号外的文字，并比较讨论标记与命令动词的先后：``解释“把真相改为…”`` 是讨论，
    ``把真相改为…，再解释一下`` 仍然是命令。关键词只用于定位，最终判断依赖这个顺序。
    """
    text = _plain(author_text)
    discussion = _first(text, _DISCUSSION)
    if discussion < 0:
        return False
    command = _first(text, _COMMAND)
    return command < 0 or discussion < command


def blocks_writes(author_text: str) -> bool:
    """这一轮只允许讨论与读取：讨论、解释或作者明确说不要改。"""
    text = _plain(author_text)
    return is_discussion(author_text) or any(word in text for word in _READ_ONLY)


def _strip_quoted_commands(text: str, subject: str, category: str, actions: tuple[str, ...]) -> str:
    """把「引号里的完整命令」从授权判断中拿掉；引号里只有新值时保留。"""
    parts: list[str] = []
    last = 0
    for start, end, inner in _quoted_spans(text):
        if (
            category in inner
            and any(action in inner for action in actions)
            and (not subject or subject in inner)
        ):
            parts.append(text[last:start])
            parts.append(" ")
            last = end
    parts.append(text[last:])
    return "".join(parts)


@dataclass(frozen=True)
class StoryAuthority:
    """本轮允许模型触及的既有对象；``permitted`` 为空表示只允许新建。"""

    variant_id: str
    revision: str
    permitted: frozenset[tuple[str, str]]

    def covers(self, kind: str, object_id: str) -> bool:
        return (kind, object_id) in self.permitted

    def require(self, operation: StoryOperation) -> None:
        """范围外的既有对象一律拒绝；新建对象与范围内的修改照常提交。"""
        for kind, object_id in self._targets(operation):
            if self.covers(kind, object_id):
                continue
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.update",
                why=f"作者本轮没有要求修改{_LABEL[kind]}「{object_id}」，这次变更没有保存",
                fix=(
                    "只改作者明确点到或界面选中的对象；需要改其他对象时先让作者说明要改哪个，"
                    "或用 read_story 确认名称后再提交"
                ),
                details={"object_kind": kind, "object_id": object_id},
            )

    def describe(self) -> list[dict[str, str]]:
        return [{"kind": kind, "id": object_id} for kind, object_id in sorted(self.permitted)]

    def _targets(self, operation: StoryOperation) -> list[tuple[str, str]]:
        if isinstance(
            operation,
            (AddNode, AddStage, AddThread, AddConstraint, RecordAssessment, CreateClueIdentity),
        ):
            return []
        if isinstance(operation, (BindProse, Connect)):
            return self._creation_targets(operation)
        if isinstance(operation, (UpdateStage, RemoveStage)):
            return [("stage", operation.stage_id)]
        if isinstance(operation, (UpdateNode | RemoveNode)):
            return [("node", operation.node_id)]
        if isinstance(
            operation,
            (UpsertCluePlan, RemoveCluePlan, RenameClueIdentity, RemoveClueIdentity),
        ):
            return [("clue", operation.clue_id)]
        if isinstance(operation, (UpdateThread | RemoveThread)):
            return [("thread", operation.thread_id)]
        if isinstance(operation, UpdateConstraint | RemoveConstraint):
            return [("constraint", operation.constraint_id)]
        if isinstance(operation, Disconnect):
            return [("edge", operation.edge_id)]
        if isinstance(operation, UnbindProse):
            return [("binding", operation.binding_id)]
        if isinstance(operation, RemoveAssessment):
            return [("assessment", operation.assessment_id)]
        if isinstance(operation, (SetEventTime, UnsetEventTime)):
            # 实际时间表述的是两个节点之间的先后，至少要有一个节点在本轮范围内。
            if isinstance(operation, SetEventTime):
                ends = (operation.relation.before_node_id, operation.relation.after_node_id)
            else:
                ends = (operation.before_node_id, operation.after_node_id)
            if any(self.covers("node", node_id) for node_id in ends):
                return []
            return [("node", node_id) for node_id in ends]
        return []  # pragma: no cover - 联合类型穷尽，新增操作必须先补这里

    def _creation_targets(self, operation: BindProse | Connect) -> list[tuple[str, str]]:
        """新建关系或正文关联：锚点必须是范围内的既有对象。"""
        if isinstance(operation, BindProse):
            ends: tuple[str, ...] = (operation.binding.node_id,)
            kind = "node"
        else:
            ends = (operation.edge.from_id, operation.edge.to_id)
            kind = "node"
        if any(self.covers(kind, object_id) for object_id in ends):
            return []
        return [(kind, object_id) for object_id in ends]


def build_authority(
    variant: StoryVariant,
    frozen: StoryContextSnapshot,
    author_text: str,
    *,
    catalog: StoryClueCatalog | None = None,
    design_wide: bool = True,
    candidate_text: str = "",
) -> StoryAuthority:
    """按冻结范围与作者原话生成许可集合；不读正文、不调模型。

    ``design_wide`` 只对「设计用途且没有选中节点」生效：普通修改按整份结构工作，
    采用备选时必须先由作者明确要求整案，不能把「没选节点」当成整案授权。

    ``candidate_text`` 是作者确认的上一轮候选原文。它只把其中点到的既有对象纳入
    可修改范围，不参与作者真相与约定授权的判断：候选里的命令不是作者的命令。
    """
    catalog = catalog or StoryClueCatalog.from_variant(variant)
    text = _flat(author_text)
    naming_text = f"{text}\n{_flat(candidate_text)}" if candidate_text.strip() else text
    permitted: set[tuple[str, str]] = {
        ("node", node_id) for node_id in frozen.node_ids if node_id in variant.nodes
    }
    for stage in variant.stages.values():
        if _named(stage.title, naming_text):
            permitted.add(("stage", stage.id))
    for node in variant.nodes.values():
        if _named(node.title, naming_text):
            permitted.add(("node", node.id))
    for fact in catalog:
        if _named(fact.title, naming_text):
            permitted.add(("clue", fact.clue_id))
    for thread in variant.threads.values():
        if _named(thread.title, naming_text):
            permitted.add(("thread", thread.id))
    for constraint in variant.constraints.values():
        if _named(constraint.text, naming_text):
            permitted.add(("constraint", constraint.id))

    # 设计用途且没有选中对象时，作者在整份结构上工作；采用备选要单独确认整案。
    if design_wide and frozen.purpose == "design" and not frozen.node_ids:
        for collection, kind in (
            (variant.stages, "stage"),
            (variant.nodes, "node"),
            (variant.clue_plans, "clue"),
            (variant.threads, "thread"),
            (variant.constraints, "constraint"),
            (variant.bindings, "binding"),
            (variant.edges, "edge"),
        ):
            permitted.update((kind, object_id) for object_id in collection)

    prior = -1
    while prior != len(permitted):
        prior = len(permitted)
        for kind, object_id in list(permitted):
            if kind == "stage":
                permitted.update(
                    ("node", node.id)
                    for node in variant.nodes.values()
                    if node.stage_id == object_id
                )
            if kind != "node":
                continue
            node = variant.nodes[object_id]
            if node.stage_id is not None:
                permitted.add(("stage", node.stage_id))
            permitted.update(("thread", thread_id) for thread_id in node.thread_ids)
            permitted.update(("clue", use.clue_id) for use in node.clue_uses)
        for binding in variant.bindings.values():
            if ("node", binding.node_id) in permitted:
                permitted.add(("binding", binding.id))
        for edge in variant.edges.values():
            if ("node", edge.from_id) in permitted and ("node", edge.to_id) in permitted:
                permitted.add(("edge", edge.id))
        for constraint in variant.constraints.values():
            if (
                constraint.scope.kind == "node"
                and ("node", constraint.scope.target_id) in permitted
            ):
                permitted.add(("constraint", constraint.id))

    return StoryAuthority(
        variant_id=variant.id,
        revision=variant.revision,
        permitted=frozenset(permitted),
    )


def authorize_change(
    variant: StoryVariant,
    frozen: StoryContextSnapshot,
    author_text: str,
    change: StoryChangeSet,
    *,
    catalog: StoryClueCatalog | None = None,
    design_wide: bool = True,
    candidate_text: str = "",
) -> None:
    if frozen.structure_readonly and any(
        not isinstance(operation, (BindProse, UnbindProse, RecordAssessment))
        for operation in change.operations
    ):
        raise WorkspaceError(
            code="STORY_AUTHORIZATION_CONFLICT",
            where="story.update",
            why="本轮结构只读，候选不能再次改动已经采用的结构",
            fix="只调整正文及其呈现；结构修改另行提出",
        )
    if blocks_writes(author_text):
        raise WorkspaceError(
            code="STORY_AUTHORIZATION_CONFLICT",
            where="story.update",
            why="作者本轮在解释或讨论，没有保存结构变更的授权",
            fix="返回讨论结果；作者明确要求修改时再提交变更",
        )
    authority = build_authority(
        variant,
        frozen,
        author_text,
        catalog=catalog,
        design_wide=design_wide,
        candidate_text=candidate_text,
    )
    created = set(authority.permitted)
    for operation in change.operations:
        for cls, kind, field in (
            (AddNode, "node", "node"),
            (AddStage, "stage", "stage"),
            (AddThread, "thread", "thread"),
            (AddConstraint, "constraint", "constraint"),
        ):
            if isinstance(operation, cls):
                created.add((kind, getattr(operation, field).id))
        if isinstance(operation, CreateClueIdentity):
            created.add(("clue", operation.temp_id))
    authority = StoryAuthority(authority.variant_id, authority.revision, frozenset(created))
    for operation in change.operations:
        authority.require(operation)


def _explicit(
    author_text: str, subject: str, category: str, value: str = "", *, remove: bool = False
) -> bool:
    """只接受作者直接给出的对象、动作和新值；引用、假设、讨论与否定不授予权限。"""
    text = re.sub(r"```[\s\S]*?```", "", author_text)
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    actions = (
        ("删除", "移除", "取消")
        if remove
        else (
            "改为",
            "改成",
            "设为",
            "设成",
            "修改为",
            "新增",
            "添加",
            "增加",
            "约定：",
            "约定:",
        )
    )
    for clause in re.split(r"[。；;\n]", text):
        markers = _plain(clause)
        if is_discussion(clause) or any(
            word in markers for word in _NEGATION + _HYPOTHETICAL + _QUOTED
        ):
            continue
        clause = _strip_quoted_commands(clause, subject, category, actions)
        if subject and subject not in clause:
            continue
        if category not in clause or not any(word in clause for word in actions):
            continue
        if remove or (value and value in clause):
            return True
    return False


def validate_model_change(
    variant: StoryVariant,
    change: StoryChangeSet,
    author_text: str = "",
    *,
    catalog: StoryClueCatalog | None = None,
    confirmed_sources: Mapping[str, Any] | None = None,
    confirmed_constraints: frozenset[str] = frozenset(),
) -> None:
    """统一保护作者真相、必需约定与裁决来源，并由应用固定来源。"""
    from hastings.workspace.story_constraint_sources import source_key

    catalog = catalog or StoryClueCatalog.from_variant(variant)
    confirmed_sources = confirmed_sources or {}
    for operation in change.operations:
        if isinstance(operation, AddConstraint) and operation.constraint.source_ref is not None:
            inherited = operation.constraint
            source_ref = operation.constraint.source_ref
            for row in confirmed_sources.values():
                if (
                    row["source_ref"] == source_ref.model_dump()
                    and row["text"] == inherited.text
                    and row["scope"] == inherited.scope.model_dump()
                    and row["strength"] == "required"
                    and inherited.strength != "required"
                ):
                    raise WorkspaceError(
                        code="STORY_AUTHORIZATION_CONFLICT",
                        where="story.update",
                        why="继承的模块必需约定被改成了偏好",
                        fix="保持来源约定的 required 强度",
                    )
        if isinstance(operation, UpsertCluePlan) and operation.clue_id in variant.clue_plans:
            existing = variant.clue_plans[operation.clue_id]
            if operation.plan.author_truth != existing.author_truth and not _explicit(
                author_text,
                catalog.title(operation.clue_id),
                "真相",
                operation.plan.author_truth or "",
            ):
                raise WorkspaceError(
                    code="STORY_AUTHORIZATION_CONFLICT",
                    where="story.update",
                    why="本轮无法验证对已有线索真相的对象级修改授权，没有保存变更",
                    fix="保留原真相；由作者明确说明对象和新的真相内容",
                )
        if isinstance(operation, RemoveCluePlan) and operation.clue_id in variant.clue_plans:
            existing = variant.clue_plans[operation.clue_id]
            if existing.author_truth is not None and not _explicit(
                author_text, catalog.title(operation.clue_id), "线索", remove=True
            ):
                raise WorkspaceError(
                    code="STORY_AUTHORIZATION_CONFLICT",
                    where="story.update",
                    why="本轮无法验证删除已有线索真相的对象级授权，没有保存变更",
                    fix="保留线索；由作者明确说明对象和新的真相内容",
                )
        if (
            isinstance(operation, RecordAssessment)
            and operation.assessment.decision_source != "model"
        ):
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.update",
                why="模型不能把核对结论标记为作者或应用裁决",
                fix="保留模型来源；作者裁决应由作者操作记录",
            )
        if (
            isinstance(operation, RemoveAssessment)
            and operation.assessment_id in variant.assessments
            and variant.assessments[operation.assessment_id].decision_source != "model"
        ):
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.update",
                why="模型不能删除作者或应用的核对裁决",
                fix="保留原裁决，并在新的核对记录中说明变化",
            )
        protected = (
            (isinstance(operation, AddConstraint) and operation.constraint.strength == "required")
            or (
                isinstance(operation, UpdateConstraint)
                and (
                    operation.constraint.strength == "required"
                    or (
                        operation.constraint_id in variant.constraints
                        and variant.constraints[operation.constraint_id].strength == "required"
                    )
                )
            )
            or (
                isinstance(operation, RemoveConstraint)
                and operation.constraint_id in variant.constraints
                and variant.constraints[operation.constraint_id].strength == "required"
            )
        )
        allowed_constraint = False
        if isinstance(operation, AddConstraint):
            allowed_constraint = (
                _explicit(author_text, "", "约定", operation.constraint.text)
                or (
                    operation.constraint.source_ref is not None
                    and source_key(operation.constraint) in confirmed_sources
                )
                or source_key(operation.constraint) in confirmed_constraints
            )
        elif (
            isinstance(operation, UpdateConstraint)
            and operation.constraint_id in variant.constraints
        ):
            allowed_constraint = _explicit(
                author_text,
                variant.constraints[operation.constraint_id].text,
                "约定",
                operation.constraint.text,
            )
        elif (
            isinstance(operation, RemoveConstraint)
            and operation.constraint_id in variant.constraints
        ):
            allowed_constraint = _explicit(
                author_text, variant.constraints[operation.constraint_id].text, "约定", remove=True
            )
        if protected and not allowed_constraint:
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.update",
                why="本轮无法验证模型对必需约定的修改授权，没有保存变更",
                fix=(
                    "保留必需约定和草稿；在界面逐项核对并确认当前草稿，"
                    "或由作者明确说明原文，不要降为 preference"
                ),
            )

    # 约定修订由应用填入，不能由模型自报为作者原话。
    for index, operation in enumerate(change.operations):
        if isinstance(operation, AddConstraint):
            change.operations[index] = operation.model_copy(
                update={
                    "constraint": operation.constraint.model_copy(
                        update={
                            "origin": (
                                "author_module"
                                if operation.constraint.source_ref is not None
                                and source_key(operation.constraint) in confirmed_sources
                                else "author_request"
                                if operation.constraint.strength == "required"
                                else "model_suggestion"
                            ),
                            "source_message_id": "",
                            "revision": new_id("rev"),
                        }
                    ),
                }
            )
        elif (
            isinstance(operation, UpdateConstraint)
            and operation.constraint_id in variant.constraints
        ):
            original = variant.constraints[operation.constraint_id]
            change.operations[index] = operation.model_copy(
                update={
                    "constraint": operation.constraint.model_copy(
                        update={
                            "origin": original.origin,
                            "source_message_id": original.source_message_id,
                            "revision": new_id("rev"),
                        }
                    ),
                }
            )
