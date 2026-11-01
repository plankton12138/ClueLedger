"""构思与正文的同步读模型：契约摘要、绑定状态和机械冲突。

这一层只回答应用能确定的事：节点契约是否变化、正文文件是否变化、绑定引文是否仍在。
语义是否真的冲突交给模型核对，不在这里伪装成已经判断。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import FileEntry, FileView, Workspace
from hastings.workspace.questions import QuestionStore
from hastings.workspace.story_changes import apply_change_set, object_digest
from hastings.workspace.story_knowledge import effective_knowledge
from hastings.workspace.story_models import (
    BindProse,
    ProseBinding,
    StoryChangeSet,
    StoryNode,
    StoryVariant,
    SyncDecision,
    UnbindProse,
)

__all__ = [
    "ChapterSyncState",
    "NodeSyncState",
    "StorySyncView",
    "SyncConflictView",
    "SyncCounts",
    "binding_state",
    "build_sync_view",
    "contract_digest",
    "contract_projection",
    "current_span",
    "detect_sync_conflicts",
    "require_sync_decision",
    "sync_conflict_digest",
    "sync_fingerprint",
]

NodeSyncState = Literal[
    "aligned",
    "outline_ahead",
    "prose_ahead",
    "diverged",
    "unchecked",
    "waived",
    "outline_only",
    #: 有正文章节尚未归属到任何节点；节点是否有正文还无法判定。
    "unlinked",
]
ChapterSyncState = Literal[
    "aligned",
    "outline_ahead",
    "prose_ahead",
    "diverged",
    "unchecked",
    "waived",
    "unbound",
    #: 章节已有锚点，但仍有正文没有归属到任何节点。
    "partial",
]
#: 绑定正文的状态；「只有构思、没有正文」属于节点/章节层面，不由绑定产生。
BindingSyncState = Literal[
    "aligned",
    "outline_ahead",
    "prose_ahead",
    "diverged",
    "unchecked",
    "waived",
]


class SyncCounts(BaseModel):
    aligned: int = 0
    outline_ahead: int = 0
    prose_ahead: int = 0
    diverged: int = 0
    unchecked: int = 0
    waived: int = 0
    outline_only: int = 0
    unlinked: int = 0
    unbound_prose: int = 0
    partial_prose: int = 0


class SyncNodeView(BaseModel):
    node_id: str
    title: str
    state: NodeSyncState
    chapter_paths: list[str] = Field(default_factory=list)
    binding_ids: list[str] = Field(default_factory=list)
    contract_digest: str = ""
    bound_quote: str = ""
    bound_chapter: str = ""


class SyncChapterView(BaseModel):
    path: str
    chapter: int
    title: str
    state: ChapterSyncState
    node_ids: list[str] = Field(default_factory=list)
    prose_digest: str = ""
    checked_digest: str = ""


class SyncConflictView(BaseModel):
    conflict_id: str
    kind: Literal[
        "outline_ahead",
        "prose_ahead",
        "diverged",
        "unchecked",
        "missing_presentation",
        "unbound_prose",
    ]
    node_id: str = ""
    binding_id: str = ""
    path: str = ""
    message: str
    options: list[str] = Field(default_factory=list)


class StorySyncView(BaseModel):
    variant_id: str
    revision: str
    counts: SyncCounts
    nodes: list[SyncNodeView]
    chapters: list[SyncChapterView]
    conflicts: list[SyncConflictView]


@dataclass(frozen=True, slots=True)
class _ChapterFile:
    digest: str
    text: str


def _applicable_constraints(
    variant: StoryVariant, node: StoryNode, subjects: set[str]
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for constraint in variant.constraints.values():
        scope = constraint.scope
        if (
            scope.kind == "book"
            or (scope.kind == "node" and scope.target_id == node.id)
            or (scope.kind == "character" and scope.target_id in subjects)
        ):
            result.append(constraint.model_dump(mode="json"))
    return sorted(result, key=lambda item: str(item["id"]))


def contract_projection(variant: StoryVariant, node: StoryNode) -> dict[str, object]:
    """正文必须兑现的节点内容；标题、阶段顺序等展示字段不进入契约。"""
    knowledge = effective_knowledge(variant, [node.id])
    subjects = {change.subject_id for change in knowledge}
    event_times = [
        {
            "before": relation.before_node_id,
            "after": relation.after_node_id,
        }
        for relation in variant.event_times
        if node.id in (relation.before_node_id, relation.after_node_id)
    ]
    return {
        "action": node.action,
        "change": node.change,
        "time_note": node.time_note,
        "clue_uses": [
            {
                "clue_id": use.clue_id,
                "roles": list(use.roles),
                "presentation": use.presentation,
                "mode": use.mode,
                "speaker_id": use.speaker_id,
                "expected_interpretation": use.expected_interpretation,
            }
            for use in node.clue_uses
        ],
        "knowledge": [change.model_dump(mode="json") for change in knowledge],
        "event_times": event_times,
        "constraints": _applicable_constraints(variant, node, subjects),
    }


def contract_digest(variant: StoryVariant, node: StoryNode) -> str:
    return object_digest(contract_projection(variant, node))


def sync_fingerprint(binding: ProseBinding, contract: str, prose: str) -> str:
    """作者忽略的差异摘要；任一侧再变化，摘要就不再匹配。"""
    return object_digest(
        {
            "node_id": binding.node_id,
            "binding_id": binding.id,
            "path": binding.path,
            "start": binding.start,
            "end": binding.end,
            "quote": binding.quote,
            "contract_digest": contract,
            "prose_digest": prose,
        }
    )


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _binding_span(binding: ProseBinding, text: str) -> tuple[int, int] | None:
    """区间仍可用时返回当前位置；位置失效后只在引文唯一出现时重新定位。"""
    if 0 <= binding.start < binding.end <= len(text):
        return binding.start, binding.end
    if binding.quote and text.count(binding.quote) == 1:
        start = text.find(binding.quote)
        if start >= 0:
            return start, start + len(binding.quote)
    return None


def _current_span(binding: ProseBinding, text: str) -> tuple[int, int, str] | None:
    if (
        0 <= binding.start < binding.end <= len(text)
        and text[binding.start : binding.end] == binding.quote
    ):
        return binding.start, binding.end, _digest_text(binding.quote)
    if binding.quote and text.count(binding.quote) == 1:
        start = text.find(binding.quote)
        if start >= 0:
            return start, start + len(binding.quote), _digest_text(binding.quote)
    return None


def current_span(binding: ProseBinding, text: str) -> tuple[int, int, str] | None:
    """锚点在当前正文里的位置与区间摘要；位置失效时返回 None。"""
    return _current_span(binding, text)


def _bound_digest(binding: ProseBinding) -> str:
    # 旧记录没有区间摘要时，用引文本身作为区间摘要，避免整章改动误伤锚点。
    return binding.span_digest or _digest_text(binding.quote)


def _binding_prose_digest(binding: ProseBinding, file: _ChapterFile | None) -> str:
    if file is None:
        return ""
    span = _current_span(binding, file.text)
    if span is not None:
        return span[2]
    # 新记录有区间摘要时，引文缺失就是正文已改；旧记录才退回整份文件摘要。
    return "" if binding.span_digest else file.digest


_HEADING_RE = re.compile(r"(?m)^#{1,6}\s.*$")
_WORD_RE = re.compile(r"\w")


def _has_uncovered_prose(text: str, bindings: list[ProseBinding]) -> bool:
    """锚点区间之外是否还有实质正文；标题、空白和纯标点不算未归属内容。"""
    content = _HEADING_RE.sub(lambda match: " " * len(match.group(0)), text)
    intervals = sorted(
        span for binding in bindings if (span := _binding_span(binding, text)) is not None
    )
    cursor = 0
    for start, end in intervals:
        if start < cursor:
            start = cursor
        if _WORD_RE.search(content[cursor:start]):
            return True
        cursor = max(cursor, end)
    return bool(_WORD_RE.search(content[cursor:]))


def binding_state(binding: ProseBinding, contract: str, prose: str) -> BindingSyncState:
    fingerprint = sync_fingerprint(binding, contract, prose)
    if binding.waived_digest and binding.waived_digest == fingerprint:
        return "waived"
    if not binding.contract_digest:
        return "unchecked"
    outline_changed = binding.contract_digest != contract
    prose_changed = _bound_digest(binding) != prose
    if outline_changed and prose_changed:
        return "diverged"
    if outline_changed:
        return "outline_ahead"
    if prose_changed:
        return "prose_ahead"
    return "aligned"


_NODE_SEVERITY = {
    "diverged": 6,
    "outline_ahead": 5,
    "prose_ahead": 4,
    "unchecked": 3,
    "aligned": 2,
    "waived": 1,
}


def _aggregate(states: Iterable[BindingSyncState]) -> BindingSyncState:
    values = list(states)
    if not values:
        return "unchecked"
    active = [state for state in values if state != "waived"]
    if not active:
        return "waived"
    return max(active, key=lambda state: _NODE_SEVERITY[state])


def _chapter_state(states: list[BindingSyncState]) -> ChapterSyncState:
    if not states:
        return "unchecked"
    return _aggregate(states)


def _count(nodes: list[SyncNodeView], chapters: list[SyncChapterView]) -> SyncCounts:
    counts = SyncCounts()
    for node in nodes:
        if node.state == "outline_only":
            counts.outline_only += 1
        else:
            setattr(counts, node.state, getattr(counts, node.state) + 1)
    counts.unbound_prose = sum(1 for chapter in chapters if chapter.state == "unbound")
    counts.partial_prose = sum(1 for chapter in chapters if chapter.state == "partial")
    return counts


def _conflict_id(
    kind: str,
    *,
    node_id: str = "",
    path: str = "",
    contract: str = "",
    prose: str = "",
    extra: str = "",
) -> str:
    return object_digest([kind, node_id, path, contract, prose, extra])


def _read_chapter(
    workspace: Workspace, path: str, cache: dict[str, _ChapterFile | None]
) -> _ChapterFile | None:
    if path not in cache:
        try:
            view: FileView = workspace.read(path)
            cache[path] = _ChapterFile(view.digest, view.text)
        except (OSError, WorkspaceError):
            # 读不到正文仍要让同步视图可用；状态按正文已变化处理。
            cache[path] = None
    return cache[path]


def build_sync_view(
    workspace: Workspace,
    variant: StoryVariant,
    *,
    chapter_path: str | None = None,
) -> StorySyncView:
    entries: list[FileEntry] = [
        entry
        for entry in workspace.catalog().chapters
        if entry.exists and entry.chapter_number is not None
    ]
    entry_by_path = {entry.path: entry for entry in entries}
    bindings_by_node: dict[str, list[ProseBinding]] = defaultdict(list)
    bindings_by_path: dict[str, list[ProseBinding]] = defaultdict(list)
    for binding in variant.bindings.values():
        bindings_by_node[binding.node_id].append(binding)
        if binding.path in entry_by_path:
            bindings_by_path[binding.path].append(binding)

    files: dict[str, _ChapterFile | None] = {}
    #: 有正文尚未归属（整章未绑定，或只绑定了部分区间）；此时没有锚点的节点不能被判定为「未写」。
    uncovered_paths: set[str] = set()
    for entry in entries:
        if entry.size <= 0:
            continue
        bindings = bindings_by_path.get(entry.path, [])
        if not bindings:
            uncovered_paths.add(entry.path)
            continue
        file = _read_chapter(workspace, entry.path, files)
        if file is not None and _has_uncovered_prose(file.text, bindings):
            uncovered_paths.add(entry.path)

    contracts: dict[str, str] = {}
    nodes: list[SyncNodeView] = []
    conflicts: dict[str, SyncConflictView] = {}

    def add_conflict(row: SyncConflictView) -> None:
        conflicts.setdefault(row.conflict_id, row)

    for node in sorted(variant.nodes.values(), key=lambda item: item.id):
        contract = contract_digest(variant, node)
        contracts[node.id] = contract
        bindings = bindings_by_node.get(node.id, [])
        states: list[BindingSyncState] = []
        for binding in bindings:
            file = _read_chapter(workspace, binding.path, files)
            prose = _binding_prose_digest(binding, file)
            state = binding_state(binding, contract, prose)
            states.append(state)
            if state in ("outline_ahead", "prose_ahead", "diverged"):
                message = {
                    "outline_ahead": "节点契约已修改；正文还是上一次核对时的内容。",
                    "prose_ahead": "正文已变化；需要核对构思是否仍然成立。",
                    "diverged": "构思和正文都改过了；需要重新对齐。",
                }[state]
                add_conflict(
                    SyncConflictView(
                        conflict_id=_conflict_id(
                            state,
                            node_id=node.id,
                            path=binding.path,
                            contract=contract,
                            prose=prose,
                            extra=binding.id,
                        ),
                        kind=state,
                        node_id=node.id,
                        binding_id=binding.id,
                        path=binding.path,
                        message=message,
                        options=["follow_outline", "follow_prose", "defer"],
                    )
                )
            if file is not None:
                for use in node.clue_uses:
                    quote = use.presentation.strip()
                    if quote and quote not in file.text:
                        add_conflict(
                            SyncConflictView(
                                conflict_id=_conflict_id(
                                    "missing_presentation",
                                    node_id=node.id,
                                    path=binding.path,
                                    contract=contract,
                                    prose=prose,
                                    extra=f"{binding.id}:{quote}",
                                ),
                                kind="missing_presentation",
                                node_id=node.id,
                                binding_id=binding.id,
                                path=binding.path,
                                message=f"线索呈现句不在正文里：{quote}",
                                options=["follow_outline", "follow_prose", "defer"],
                            )
                        )
        node_state: NodeSyncState = (
            _aggregate(states)
            if bindings
            else ("unlinked" if uncovered_paths else "outline_only")
        )
        first = bindings[0] if bindings else None
        chapter_entry = entry_by_path.get(first.path) if first is not None else None
        nodes.append(
            SyncNodeView(
                node_id=node.id,
                title=node.title,
                state=node_state,
                chapter_paths=sorted({binding.path for binding in bindings}),
                binding_ids=[binding.id for binding in bindings],
                contract_digest=contract,
                bound_quote=first.quote if first is not None else "",
                bound_chapter=chapter_entry.display_title if chapter_entry is not None else "",
            )
        )

    chapters: list[SyncChapterView] = []
    for entry in entries:
        bindings = bindings_by_path.get(entry.path, [])
        if not bindings:
            if entry.size <= 0:
                continue
            chapters.append(
                SyncChapterView(
                    path=entry.path,
                    chapter=entry.chapter_number or 0,
                    title=entry.display_title,
                    state="unbound",
                )
            )
            add_conflict(
                SyncConflictView(
                    conflict_id=_conflict_id("unbound_prose", path=entry.path),
                    kind="unbound_prose",
                    path=entry.path,
                    message=f"{entry.display_title}有正文，但没有绑定到构思节点。",
                )
            )
            continue
        file = _read_chapter(workspace, entry.path, files)
        prose_digest = file.digest if file is not None else ""
        states = [
            binding_state(binding, contracts[binding.node_id], _binding_prose_digest(binding, file))
            for binding in bindings
            if binding.node_id in contracts
        ]
        chapter_state = _chapter_state(states)
        if (
            chapter_state == "aligned"
            and file is not None
            and _has_uncovered_prose(file.text, bindings)
        ):
            chapter_state = "partial"
        chapters.append(
            SyncChapterView(
                path=entry.path,
                chapter=entry.chapter_number or 0,
                title=entry.display_title,
                state=chapter_state,
                node_ids=sorted({binding.node_id for binding in bindings}),
                prose_digest=prose_digest,
                checked_digest=prose_digest,
            )
        )

    if chapter_path is not None:
        chapters = [chapter for chapter in chapters if chapter.path == chapter_path]
        wanted_nodes = {node_id for chapter in chapters for node_id in chapter.node_ids}
        nodes = [node for node in nodes if node.node_id in wanted_nodes]
        conflicts = {
            key: row
            for key, row in conflicts.items()
            if row.path == chapter_path or row.node_id in wanted_nodes
        }

    return StorySyncView(
        variant_id=variant.id,
        revision=variant.revision,
        counts=_count(nodes, chapters),
        nodes=nodes,
        chapters=chapters,
        conflicts=sorted(conflicts.values(), key=lambda row: (row.path, row.node_id, row.kind)),
    )


def detect_sync_conflicts(
    workspace: Workspace,
    variant: StoryVariant,
    change: StoryChangeSet,
) -> list[SyncConflictView]:
    """只检查本次结构变更改到的节点，避免无关正文反复提示。"""
    try:
        after = apply_change_set(variant, change)
    except ValueError:
        # 结构本身无效时由提交层报错，不能把校验失败伪装成同步冲突。
        return []
    before_binding_ids = set(variant.bindings)
    files: dict[str, _ChapterFile | None] = {}
    conflicts: list[SyncConflictView] = []
    seen: set[str] = set()
    for node_id, node in after.nodes.items():
        before = variant.nodes.get(node_id)
        after_contract = contract_digest(after, node)
        if before is not None and contract_digest(variant, before) == after_contract:
            continue
        for binding in after.bindings.values():
            if binding.node_id != node_id or binding.id not in before_binding_ids:
                continue
            file = _read_chapter(workspace, binding.path, files)
            prose = _binding_prose_digest(binding, file)
            state = binding_state(binding, after_contract, prose)
            if state == "aligned" or state == "waived":
                continue
            conflict_id = _conflict_id(
                state,
                node_id=node_id,
                path=binding.path,
                contract=after_contract,
                prose=prose,
                extra=binding.id,
            )
            if conflict_id in seen:
                continue
            seen.add(conflict_id)
            conflicts.append(
                SyncConflictView(
                    conflict_id=conflict_id,
                    kind=state,
                    node_id=node_id,
                    binding_id=binding.id,
                    path=binding.path,
                    message={
                        "outline_ahead": "节点契约已修改；已有正文还是上一次核对时的内容。",
                        "prose_ahead": "正文已变化；需要核对构思是否仍然成立。",
                        "diverged": "构思和正文都改过了；需要重新对齐。",
                        "unchecked": "这段正文还没有做过同步核对。",
                    }.get(state, "构思与正文需要重新对齐。"),
                    options=["follow_outline", "follow_prose", "defer"],
                )
            )
    return sorted(conflicts, key=lambda row: (row.path, row.node_id, row.binding_id))


def sync_conflict_digest(conflicts: list[SyncConflictView]) -> str:
    return object_digest(
        [row.conflict_id for row in sorted(conflicts, key=lambda item: item.conflict_id)]
    )


def _sync_decision_gate(
    root: Path,
    decision: SyncDecision | None,
    digest: str,
    conflicts: list[SyncConflictView],
) -> None:
    if decision is None:
        raise WorkspaceError(
            code="STORY_SYNC_CONFIRM_REQUIRED",
            where="story.sync",
            why=f"这次修改影响 {len(conflicts)} 处已有正文，需要作者选择处理方式",
            fix="先说明差异与选项，记录作者回答后带 sync 决定重新提交",
            details={
                "conflict_digest": digest,
                "conflicts": [row.model_dump(mode="json") for row in conflicts],
            },
        )
    if decision.conflict_digest != digest:
        raise WorkspaceError(
            code="STORY_SYNC_CONFLICT_STALE",
            where="story.sync",
            why="冲突内容在作者确认后已经变化",
            fix="重新核对当前构思与正文，再请作者确认",
            details={"expected": decision.conflict_digest, "actual": digest},
        )
    if decision.question_id:
        question = QuestionStore(root).get(decision.question_id)
        if question is None or not question.answered:
            raise WorkspaceError(
                code="STORY_SYNC_CONFIRM_REQUIRED",
                where="story.sync",
                why="没有找到作者对这次冲突的回答",
                fix="使用 ask_author 记录选项，等作者回答后再提交",
                details={"question_id": decision.question_id},
            )
    elif not decision.note.strip():
        raise WorkspaceError(
            code="STORY_SYNC_CONFIRM_REQUIRED",
            where="story.sync",
            why="同步决定没有记录作者确认",
            fix="通过 ask_author 询问，或写明作者本轮的原话依据",
            details={"conflict_digest": digest},
        )


def _resolve_sync_bindings(
    workspace: Workspace,
    variant: StoryVariant,
    change: StoryChangeSet,
    conflicts: list[SyncConflictView],
    decision: SyncDecision,
) -> StoryChangeSet:
    """把作者选择的确定性结果写回绑定；正文改写仍由模型提交补丁。"""
    if decision.choice == "follow_outline":
        return change
    after = apply_change_set(variant, change)
    files: dict[str, _ChapterFile | None] = {}
    extra: list[UnbindProse | BindProse] = []
    seen: set[str] = set()
    for conflict in conflicts:
        if not conflict.binding_id or conflict.binding_id in seen:
            continue
        binding = after.bindings.get(conflict.binding_id)
        if binding is None:
            continue
        node = after.nodes.get(binding.node_id)
        if node is None:
            continue
        contract = contract_digest(after, node)
        file = _read_chapter(workspace, binding.path, files)
        prose = _binding_prose_digest(binding, file)
        update: dict[str, object] = {}
        if decision.choice == "follow_prose":
            update = {"contract_digest": contract, "waived_digest": "", "waived_note": ""}
        else:
            update = {
                "waived_digest": sync_fingerprint(binding, contract, prose),
                "waived_note": decision.note or "作者确认暂不处理",
            }
        if all(getattr(binding, key) == value for key, value in update.items()):
            continue
        updated = binding.model_copy(update=update)
        extra.extend(
            [
                UnbindProse(op="unbind_prose", binding_id=binding.id),
                BindProse(op="bind_prose", binding=updated),
            ]
        )
        seen.add(binding.id)
    if not extra:
        return change
    return change.model_copy(update={"operations": [*change.operations, *extra]})


def require_sync_decision(
    workspace: Workspace,
    root: Path,
    variant: StoryVariant,
    change: StoryChangeSet,
) -> StoryChangeSet:
    """结构变更影响已绑定正文时，必须带作者确认；defer/follow_prose 自动更新绑定。"""
    conflicts = detect_sync_conflicts(workspace, variant, change)
    if not conflicts:
        return change
    digest = sync_conflict_digest(conflicts)
    _sync_decision_gate(root, change.sync, digest, conflicts)
    assert change.sync is not None
    return _resolve_sync_bindings(workspace, variant, change, conflicts, change.sync)
