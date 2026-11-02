"""本轮材料的来源、版本与读取记录（0.6 V06-A1）。

一轮任务里，「作者选了哪些文件」「模型实际看到了哪些原文」「这些内容现在还是不是那一版」
是三件不同的事。这个模块只负责把它们分开记清楚：

- **依赖记录**：本轮用到的每一份材料及其字节指纹。参考文件后来被外部改动，写入前的
  检查据此失败，而不是拿旧内容继续改。
- **读取记录**：真正提供给模型的原文区间，按服务端字符索引、左闭右开。目录、摘要、
  搜索摘录和界面打开都不算原文；``edit_file`` 内部读文件也不算。
- **完成条件**：作者指定的参考要写作品前提供全文，局部替换要提供被替换区间，
  移动与删除要求读过那一版并拿到指纹。缺口按具体路径与剩余区间报告，不静默放过。

区间以 LF 规范化文本的字符下标表示；指纹仍按磁盘原始字节计算。同一 ``(path, digest)``
的重叠区间合并，不同版本不拼接——旧内容无法从模型上下文撤回，只能明确标为失效并让
模型重新读取。

本模块不写文件、不判断任务意图，也不记录界面状态。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import FileView, Workspace
from hastings.workspace.story_context import StoryContextSnapshot, StoryRequest

if TYPE_CHECKING:
    from hastings.library.modules import ModuleLibrary
    from hastings.workspace.clue_context import ClueReferenceInput, ClueReferenceSnapshot

__all__ = [
    "LIST_PREVIEW_CHARS",
    "LIST_RESULT_CHARS",
    "MAX_REFERENCES",
    "READ_MAX_CHARS",
    "READ_RESULT_CHARS",
    "REFERENCE_PREREAD_CHARS",
    "REFERENCE_PREREAD_TOTAL",
    "IDEA_PREREAD_CHARS",
    "SEARCH_MAX_HITS",
    "SEARCH_RESULT_CHARS",
    "SEARCH_SNIPPET_CHARS",
    "MaterialLedger",
    "MaterialSummary",
    "IdeaReference",
    "PreparedReference",
    "ReadCoverage",
    "ReadRequirement",
    "ReadSlice",
    "DiscussionFocus",
    "Reference",
    "SourceKind",
    "SourceRef",
    "bounded_search_hits",
    "covers",
    "freeze_context",
    "merge_spans",
    "prepare_references",
    "read_slice",
    "require_author_quote",
    "require_read_quote",
    "verify_context",
    "ContextRequest",
    "ContextSnapshot",
]

# -- 输出预算初始值 ---------------------------------------------------------
#
# 这些是实施初始值，集中定义便于测试与调整；不做成按页配置。
# 结构化字段同样受上限约束：超限时返回「截断 + 下一游标」，不能截断 JSON 后当成功。

#: 作者显式选择参考的上限与预读预算，按选择顺序分配。
MAX_REFERENCES = 20
REFERENCE_PREREAD_CHARS = 2_000
REFERENCE_PREREAD_TOTAL = 8_000
#: 单条选中灵感在上下文里的正文上限；更长时用专用工具继续读。
IDEA_PREREAD_CHARS = 2_000
#: 灵感引用在界面上的短预览长度；只用于显示，不保存也不参与请求身份。
PREVIEW_CHARS = 48

#: 单次 read_file 的原文上限与整个结果上限。
READ_MAX_CHARS = 16_000
READ_RESULT_CHARS = 20_000

#: 搜索命中条数、单条摘录与整体结果上限。
SEARCH_MAX_HITS = 50
SEARCH_SNIPPET_CHARS = 240
SEARCH_RESULT_CHARS = 8_000

#: 目录：给模型看的预览与工具结果上限。
LIST_PREVIEW_CHARS = 8_000
LIST_RESULT_CHARS = 12_000

SourceKind = Literal[
    "author_selected",
    "author_text",
    "discovered",
    "system_setting",
    "chapter_index",
]

Span = tuple[int, int]


@dataclass(frozen=True, slots=True)
class SourceRef:
    """一份材料的版本：路径 + 磁盘字节指纹。规范化文本不再另行散列。"""

    path: str
    digest: str


@dataclass(frozen=True, slots=True)
class Reference:
    """本轮登记的一份材料来源。

    ``quote`` 与 ``seq`` 只对作者文本来源有意义：前者是作者消息里的短引文，后者定位
    那条消息。``required`` 表示写作品前必须提供全文（作者明确说「参考」的文件）。
    """

    ref: SourceRef
    kind: SourceKind
    required: bool = False
    quote: str = ""
    seq: int | None = None
    note: str = ""

    @property
    def path(self) -> str:
        return self.ref.path


@dataclass(frozen=True, slots=True)
class ReadCoverage:
    """一个版本已提供的原文区间；``complete`` 由长度与区间共同决定。"""

    ref: SourceRef
    length: int
    spans: tuple[Span, ...]

    @property
    def complete(self) -> bool:
        return self.length == 0 or self.spans == ((0, self.length),)


@dataclass(frozen=True, slots=True)
class ReadRequirement:
    """写工具生成的前置读取要求，模型不能通过参数跳过。

    - ``full``：整体覆盖现有文件，要求当前版本全文已提供；
    - ``range``：局部替换，要求被替换区间已提供；
    - ``identity``：移动或删除，要求读过该版本并拿到指纹。
    """

    mode: Literal["full", "range", "identity"]
    path: str
    start: int = 0
    end: int = 0

    @classmethod
    def full(cls, path: str) -> ReadRequirement:
        return cls("full", path)

    @classmethod
    def span(cls, path: str, start: int, end: int) -> ReadRequirement:
        return cls("range", path, start, end)

    @classmethod
    def identity(cls, path: str) -> ReadRequirement:
        return cls("identity", path)


@dataclass(frozen=True, slots=True)
class ReadSlice:
    """一次有界读取的结果：实际区间、行号、下一游标。"""

    text: str
    start_char: int
    end_char: int
    start_line: int
    end_line: int
    next_cursor: int | None
    truncated: bool


@dataclass(frozen=True, slots=True)
class PreparedReference:
    """受理后对显式参考的预读结果；``truncated`` 表示只提供了开头一段。"""

    path: str
    digest: str
    length: int
    start: int
    end: int
    text: str = ""

    @property
    def truncated(self) -> bool:
        return self.end < self.length


@dataclass(frozen=True, slots=True)
class MaterialSummary:
    """给事件与界面看的一行材料状态；不包含原文。"""

    path: str
    digest: str
    status: Literal["已指定", "已定位", "已提供部分原文", "已提供全文"]
    read_chars: int
    length: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "digest": self.digest,
            "status": self.status,
            "read_chars": self.read_chars,
            "length": self.length,
        }


def merge_spans(spans: Iterable[Span]) -> tuple[Span, ...]:
    """合并重叠或相接的区间；空区间丢弃，非法区间直接拒绝。"""
    merged: list[Span] = []
    for start, end in sorted(spans):
        if start < 0 or end < start:
            raise ValueError("原文区间无效")
        if start == end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def missing_spans(spans: Sequence[Span], length: int) -> tuple[Span, ...]:
    """``[0, length)`` 里还没有提供的区间。空文件返回空元组（算作已读完）。"""
    if length == 0:
        return ()
    gaps: list[Span] = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < length:
        gaps.append((cursor, length))
    return tuple(gaps)


def covers(spans: Sequence[Span], start: int, end: int) -> bool:
    """区间 ``[start, end)`` 是否完全落在某个已提供的区间内。"""
    return any(span_start <= start and end <= span_end for span_start, span_end in spans)


def read_slice(
    text: str,
    *,
    cursor: int | None,
    offset: int,
    limit: int,
    budget: int = READ_MAX_CHARS,
) -> ReadSlice:
    """按行分页或字符游标取一段原文，始终不超过 ``budget`` 个字符。

    超长单行不能永久卡在同一行：按预算硬切，并给出下一游标继续读。
    """
    length = len(text)
    start = min(max(0, cursor), length) if cursor is not None else _line_start(text, offset)
    end = start
    lines = 0
    while end < length and lines < limit:
        newline = text.find("\n", end)
        line_end = length if newline < 0 else newline + 1
        if line_end - start > budget:
            end = start + budget
            break
        end = line_end
        lines += 1
    truncated = end < length
    return ReadSlice(
        text=text[start:end],
        start_char=start,
        end_char=end,
        start_line=_line_number(text, start),
        end_line=_line_number(text, end, at_end=True),
        next_cursor=end if truncated else None,
        truncated=truncated,
    )


def bounded_search_hits(
    hits: Sequence[tuple[str, int, str]],
    *,
    max_hits: int = SEARCH_MAX_HITS,
    max_chars: int = SEARCH_RESULT_CHARS,
) -> tuple[list[dict[str, object]], bool]:
    """把搜索命中压到条数与字符双上限内；返回命中与是否截断。"""
    kept: list[dict[str, object]] = []
    used = 0
    truncated = False
    for path, line, snippet in hits:
        text = snippet[:SEARCH_SNIPPET_CHARS]
        if len(kept) >= max_hits or used + len(text) > max_chars:
            truncated = True
            break
        kept.append({"path": path, "line": line, "text": text})
        used += len(text)
    return kept, truncated


def prepare_references(
    workspace: Workspace,
    ledger: MaterialLedger,
    paths: Sequence[str],
    *,
    per_file: int = REFERENCE_PREREAD_CHARS,
    total: int = REFERENCE_PREREAD_TOTAL,
) -> tuple[PreparedReference, ...]:
    """按选择顺序预读显式参考，并把实际提供的区间记入读取记录。

    预算是上限而不是承诺：读完一份再用剩下的额度读下一份，后面的文件可能只能给开头。
    文件缺失立即失败，不悄悄跳过——作者选中的参考不能凭空消失。
    """
    if len(paths) > MAX_REFERENCES:
        raise WorkspaceError(
            code="CONTEXT_REFERENCE_LIMIT_INVALID",
            where="materials.prepare",
            why=f"一次最多选择 {MAX_REFERENCES} 份参考，这次有 {len(paths)} 份",
            fix="减少参考文件，或在对话里说明要参考哪一段",
            details={"limit": MAX_REFERENCES, "count": len(paths)},
        )
    prepared: list[PreparedReference] = []
    remaining = total
    for path in paths:
        try:
            view = workspace.read(path)
        except WorkspaceError as exc:
            raise WorkspaceError(
                code="CONTEXT_SOURCE_NOT_FOUND",
                where="materials.prepare",
                why=f"参考文件当前不可用：{path}（{exc.why}）",
                fix="刷新目录后重新选择参考；文件可能已被移动或删除",
                details={"path": path},
            ) from exc
        quota = min(per_file, remaining)
        if quota <= 0:
            prepared.append(
                PreparedReference(
                    path=view.path, digest=view.digest, length=len(view.text), start=0, end=0
                )
            )
            continue
        end = min(len(view.text), quota)
        ledger.record_read(
            SourceRef(view.path, view.digest), length=len(view.text), start=0, end=end
        )
        remaining -= end
        prepared.append(
            PreparedReference(
                path=view.path,
                digest=view.digest,
                length=len(view.text),
                start=0,
                end=end,
                text=view.text[:end],
            )
        )
    return tuple(prepared)


@dataclass(frozen=True, slots=True)
class IdeaReference:
    """作者本轮明确选中的灵感修订；正文在受理时冻结。"""

    idea_id: str
    revision_id: str
    title: str
    body: str

    @property
    def preview(self) -> str:
        """显示用的短预览：合并空白后取前 48 个字符，不保存、不参与身份比较。"""
        collapsed = " ".join(self.body.split())
        return collapsed[:PREVIEW_CHARS] + ("…" if len(collapsed) > PREVIEW_CHARS else "")

    def view(self) -> dict[str, str]:
        return {
            "idea_id": self.idea_id,
            "revision_id": self.revision_id,
            "title": self.title,
            "preview": self.preview,
        }


@dataclass(frozen=True, slots=True)
class DiscussionFocus:
    """作者从详情或正文发起的讨论焦点；只用于定位材料，不构成写权限。

    ``object`` 指向一个构思节点或线索；``chapter`` 只指向当前章；``selection``
    额外带一段 Unicode 码点区间。发送时冻结，续聊与重启沿用同一份身份。
    """

    kind: Literal["object", "chapter", "selection"]
    object_kind: Literal["node", "clue"] | None = None
    object_id: str = ""
    variant_id: str | None = None
    story_revision: str | None = None
    clue_revision: str = ""
    view_revision: str = ""
    path: str = ""
    digest: str = ""
    start: int | None = None
    end: int | None = None
    quote: str = ""

    def identity(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "object_kind": self.object_kind,
            "object_id": self.object_id,
            "variant_id": self.variant_id,
            "story_revision": self.story_revision,
            "clue_revision": self.clue_revision,
            "view_revision": self.view_revision,
            "path": self.path,
            "digest": self.digest,
            "start": self.start,
            "end": self.end,
            "quote": self.quote,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> DiscussionFocus | None:
        kind = str(raw.get("kind") or "")
        if kind not in ("object", "chapter", "selection"):
            return None
        object_kind = raw.get("object_kind")
        start = raw.get("start")
        end = raw.get("end")
        return cls(
            kind=kind,  # type: ignore[arg-type]
            object_kind=(
                str(object_kind)  # type: ignore[arg-type]
                if object_kind in ("node", "clue")
                else None
            ),
            object_id=str(raw.get("object_id") or ""),
            variant_id=str(raw["variant_id"]) if raw.get("variant_id") else None,
            story_revision=(str(raw["story_revision"]) if raw.get("story_revision") else None),
            clue_revision=str(raw.get("clue_revision") or ""),
            view_revision=str(raw.get("view_revision") or ""),
            path=str(raw.get("path") or ""),
            digest=str(raw.get("digest") or ""),
            start=start if isinstance(start, int) else None,
            end=end if isinstance(end, int) else None,
            quote=str(raw.get("quote") or ""),
        )

    def to_dict(self) -> dict[str, object]:
        return self.identity()


@dataclass(frozen=True, slots=True)
class StoryReconcileRequest:
    """作者主动开启核对讨论时冻结的提醒目标；只表达对象，不构成写权限。"""

    variant_id: str
    variant_revision: str
    node_id: str
    binding_ids: tuple[str, ...]
    warning_fingerprint: str


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """客户端提交的当前阅读位置与显式参考；顺序即作者选择顺序。"""

    selected_path: str | None = None
    reference_paths: tuple[str, ...] = ()
    idea_references: tuple[tuple[str, str], ...] = ()
    observed_digests: Mapping[str, str] = field(default_factory=dict)
    state_chapter: int | None = None
    #: 作者在对话里明确引用的线索；只给标识与看到的版本，正文由服务端重建。
    clue_references: tuple[ClueReferenceInput, ...] = ()
    #: 这一轮要看的方案与位置；为空表示不使用构思结构。
    story: StoryRequest | None = None
    #: 作者点击「讨论调整」时带上的提醒目标；服务端核对后冻结。
    story_reconcile: StoryReconcileRequest | None = None
    #: 这一轮从详情或正文带入的对象、章节或选段焦点；只用于找材料。
    discussion_focus: DiscussionFocus | None = None


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """受理时冻结的材料身份：路径、服务端实际指纹与客户端看到的版本。

    它是版本记录，不是跨文件原子快照，也不保存全书内容副本。执行开始时版本已变则
    这一轮失败，不偷偷替换引用版本。
    """

    selected_path: str | None
    references: tuple[SourceRef, ...]
    observed_digests: Mapping[str, str]
    state_chapter: int | None = None
    ideas: tuple[IdeaReference, ...] = ()
    clues: tuple[ClueReferenceSnapshot, ...] = ()
    #: 受理时冻结的方案与修订；执行时修订已变则这一轮失败。
    story: StoryContextSnapshot | None = None
    #: 受理时冻结的讨论焦点；执行前复核对象版本与章节摘要。
    discussion_focus: DiscussionFocus | None = None

    @property
    def reference_paths(self) -> tuple[str, ...]:
        return tuple(ref.path for ref in self.references)

    def identity(self) -> dict[str, object]:
        """用于请求键查重的稳定表示：顺序有意义，指纹按路径排序。"""
        return {
            **({"state_chapter": self.state_chapter} if self.state_chapter is not None else {}),
            "selected_path": self.selected_path,
            "reference_paths": list(self.reference_paths),
            "idea_references": [[item.idea_id, item.revision_id] for item in self.ideas],
            "clue_references": [item.identity() for item in self.clues],
            "observed_digests": dict(sorted(self.observed_digests.items())),
            **({"story": self.story.to_dict()} if self.story is not None else {}),
            **(
                {"discussion_focus": self.discussion_focus.identity()}
                if self.discussion_focus is not None
                else {}
            ),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.identity(),
            "reference_digests": {ref.path: ref.digest for ref in self.references},
            "idea_revisions": [item.view() for item in self.ideas],
            "clues": [item.to_dict() for item in self.clues],
        }


def _focus_chapter(workspace: Workspace, path: str) -> FileView:
    entry = next(
        (row for row in workspace.catalog().chapters if row.path == path and row.exists),
        None,
    )
    if entry is None or entry.chapter_number is None:
        raise WorkspaceError(
            code="PROSE_CHAPTER_NOT_FOUND",
            where="materials.focus",
            why=f"焦点路径不是当前正文章节：{path}",
            fix="刷新正文目录后重新选择章节或选段",
            details={"path": path},
        )
    return workspace.read(path)


def freeze_discussion_focus(
    workspace: Workspace,
    state_root: Path,
    focus: DiscussionFocus,
) -> DiscussionFocus:
    """受理时核对焦点对象与版本；客户端给的摘要只用于发现过期，不覆盖服务端读数。"""
    if focus.kind == "object":
        from hastings.workspace.story_clue_catalog import StoryClueCatalog
        from hastings.workspace.story_store import StoryStore

        if focus.object_kind not in ("node", "clue") or not focus.object_id:
            raise WorkspaceError(
                code="CONTEXT_INPUT_INVALID",
                where="materials.focus",
                why="对象焦点缺少对象类型或标识",
                fix="重新从详情或正文选择讨论对象",
            )
        snapshot = StoryStore(state_root, workspace).read()
        document = snapshot.document
        if document is None and focus.object_kind == "clue":
            # 没有构思时，账本线索仍可作为讨论对象；这一轮不自动建立主方案。
            from hastings.workspace.clue_store import ClueLedgerStore

            ledger = ClueLedgerStore(state_root).read().ledger
            identity = ledger.clues.get(focus.object_id) if ledger is not None else None
            if identity is None:
                raise WorkspaceError(
                    code="CLUE_NOT_FOUND",
                    where="materials.focus",
                    why=f"找不到讨论焦点线索：{focus.object_id}",
                    fix="刷新线索详情后重新选择",
                    details={"clue_id": focus.object_id},
                )
            if focus.clue_revision and focus.clue_revision != identity.clue_revision:
                raise WorkspaceError(
                    code="CLUE_REFERENCE_STALE_CONFLICT",
                    where="materials.focus",
                    why="线索在选点后已经变化",
                    fix="刷新线索详情，确认后重新打开讨论",
                    details={
                        "clue_id": focus.object_id,
                        "expected": focus.clue_revision,
                        "current": identity.clue_revision,
                    },
                )
            return replace(
                focus,
                variant_id=None,
                story_revision=None,
                clue_revision=identity.clue_revision,
            )
        target = focus.variant_id or (document.primary_variant_id if document else "")
        variant = snapshot.variant(target) if document is not None else None
        if variant is None:
            raise WorkspaceError(
                code="STORY_VARIANT_NOT_FOUND",
                where="materials.focus",
                why=f"讨论焦点对应的方案不存在：{target or '当前主方案'}",
                fix="刷新构思后重新选择对象",
                details={"variant_id": target},
            )
        if focus.story_revision and focus.story_revision != variant.revision:
            raise WorkspaceError(
                code="STORY_REVISION_CONFLICT",
                where="materials.focus",
                why=f"方案在选对象后已经更新：{focus.story_revision} → {variant.revision}",
                fix="刷新详情后重新选择对象",
                details={"variant_id": variant.id, "revision": variant.revision},
            )
        if focus.object_kind == "node":
            if focus.object_id not in variant.nodes:
                raise WorkspaceError(
                    code="STORY_NODE_NOT_FOUND",
                    where="materials.focus",
                    why=f"找不到讨论焦点节点：{focus.object_id}",
                    fix="刷新详情后重新选择节点",
                    details={"node_id": focus.object_id},
                )
            clue_revision = focus.clue_revision
        else:
            catalog = StoryClueCatalog.from_root(variant, state_root)
            fact = catalog.get(focus.object_id)
            if fact is None:
                raise WorkspaceError(
                    code="CLUE_NOT_FOUND",
                    where="materials.focus",
                    why=f"找不到讨论焦点线索：{focus.object_id}",
                    fix="刷新线索详情后重新选择",
                    details={"clue_id": focus.object_id},
                )
            clue_revision = fact.identity_revision or ""
            if focus.clue_revision and focus.clue_revision != clue_revision:
                raise WorkspaceError(
                    code="CLUE_REFERENCE_STALE_CONFLICT",
                    where="materials.focus",
                    why="线索在选点后已经变化",
                    fix="刷新线索详情，确认后重新打开讨论",
                    details={
                        "clue_id": focus.object_id,
                        "expected": focus.clue_revision,
                        "current": clue_revision,
                    },
                )
        return replace(
            focus,
            variant_id=variant.id,
            story_revision=variant.revision,
            clue_revision=clue_revision,
        )
    if not focus.path:
        raise WorkspaceError(
            code="CONTEXT_INPUT_INVALID",
            where="materials.focus",
            why="章节或选段焦点缺少正文路径",
            fix="从当前正文重新选择",
        )
    file = _focus_chapter(workspace, focus.path)
    if focus.digest and focus.digest != file.digest:
        raise WorkspaceError(
            code="PROSE_SOURCE_STALE",
            where="materials.focus",
            why="正文在选段或打开本章后已经修改",
            fix="刷新这一章后重新选择",
            details={"path": focus.path},
        )
    if focus.kind == "selection":
        if focus.start is None or focus.end is None or not focus.quote:
            raise WorkspaceError(
                code="CONTEXT_INPUT_INVALID",
                where="materials.focus",
                why="选段焦点缺少区间或引文",
                fix="重新选择连续原文",
            )
        if (
            focus.start < 0
            or focus.end <= focus.start
            or focus.end > len(file.text)
            or file.text[focus.start : focus.end] != focus.quote
        ):
            raise WorkspaceError(
                code="PROSE_ANCHOR_MISMATCH",
                where="materials.focus",
                why="选段区间与当前正文不一致",
                fix="重新选择连续原文",
                details={"path": focus.path},
            )
    return replace(focus, digest=file.digest)


def verify_discussion_focus(
    workspace: Workspace,
    state_root: Path,
    focus: DiscussionFocus,
) -> None:
    """执行前复核：对象版本或正文摘要变化时整轮失败，不按旧焦点继续。"""
    if focus.kind == "object":
        from hastings.workspace.clue_store import ClueLedgerStore
        from hastings.workspace.story_store import StoryStore

        snapshot = StoryStore(state_root, workspace).read()
        if snapshot.document is None and focus.object_kind == "clue":
            ledger = ClueLedgerStore(state_root).read().ledger
            identity = ledger.clues.get(focus.object_id) if ledger is not None else None
            if identity is None:
                raise WorkspaceError(
                    code="CLUE_NOT_FOUND",
                    where="materials.verify_focus",
                    why="讨论焦点线索已经不存在",
                    fix="刷新线索详情后重新发起",
                )
            if focus.clue_revision and identity.clue_revision != focus.clue_revision:
                raise WorkspaceError(
                    code="CLUE_REFERENCE_STALE_CONFLICT",
                    where="materials.verify_focus",
                    why="受理之后线索版本已经变化",
                    fix="刷新线索详情后重新发送这一轮",
                    details={"clue_id": focus.object_id},
                )
            return
        variant = snapshot.variant(focus.variant_id or "") if snapshot.document else None
        if variant is None:
            raise WorkspaceError(
                code="STORY_VARIANT_NOT_FOUND",
                where="materials.verify_focus",
                why="讨论焦点对应的方案已经不存在",
                fix="刷新后重新发起这一轮",
            )
        if focus.story_revision and variant.revision != focus.story_revision:
            raise WorkspaceError(
                code="STORY_REVISION_CONFLICT",
                where="materials.verify_focus",
                why=f"受理之后方案已经更新：{focus.story_revision} → {variant.revision}",
                fix="按当前结构重新发起这一轮",
                details={"variant_id": variant.id},
            )
        if focus.object_kind == "node":
            if focus.object_id not in variant.nodes:
                raise WorkspaceError(
                    code="STORY_NODE_NOT_FOUND",
                    where="materials.verify_focus",
                    why="讨论焦点节点已经不存在",
                    fix="刷新后重新选择节点",
                )
            return
        ledger = ClueLedgerStore(state_root).read().ledger
        identity = ledger.clues.get(focus.object_id) if ledger is not None else None
        if identity is None:
            raise WorkspaceError(
                code="CLUE_NOT_FOUND",
                where="materials.verify_focus",
                why="讨论焦点线索已经不存在",
                fix="刷新线索详情后重新发起",
            )
        if focus.clue_revision and identity.clue_revision != focus.clue_revision:
            raise WorkspaceError(
                code="CLUE_REFERENCE_STALE_CONFLICT",
                where="materials.verify_focus",
                why="受理之后线索版本已经变化",
                fix="刷新线索详情后重新发送这一轮",
                details={"clue_id": focus.object_id},
            )
        return
    file = _focus_chapter(workspace, focus.path)
    if focus.digest and file.digest != focus.digest:
        raise WorkspaceError(
            code="PROSE_SOURCE_STALE",
            where="materials.verify_focus",
            why="受理之后这一章已经被修改",
            fix="刷新正文后重新发送这一轮",
            details={"path": focus.path},
        )
    if focus.kind == "selection" and (
        focus.start is None
        or focus.end is None
        or focus.end > len(file.text)
        or file.text[focus.start : focus.end] != focus.quote
    ):
        raise WorkspaceError(
            code="PROSE_ANCHOR_MISMATCH",
            where="materials.verify_focus",
            why="受理之后选段原文已经变化",
            fix="刷新正文后重新选择这一段",
            details={"path": focus.path},
        )


def _implicit_focus_clue(
    state_root: Path,
    focus: DiscussionFocus,
    writer_cutoff: int,
) -> tuple[ClueReferenceInput, ...]:
    """线索焦点自动补一份引用：作者没有手动引用时也要读到当前版本与正文观察。"""
    if focus.kind != "object" or focus.object_kind != "clue" or not focus.object_id:
        return ()
    from hastings.workspace.clue_context import ClueReferenceInput
    from hastings.workspace.clue_store import ClueLedgerStore

    ledger = ClueLedgerStore(state_root).read().ledger
    if ledger is None or focus.object_id not in ledger.clues:
        return ()
    identity = ledger.clues[focus.object_id]
    return (
        ClueReferenceInput(
            workspace_lineage=ledger.workspace_lineage,
            clue_id=focus.object_id,
            clue_revision=identity.clue_revision,
            browse_as_of_chapter=max(0, writer_cutoff),
        ),
    )


def freeze_context(
    workspace: Workspace,
    request: ContextRequest,
    *,
    state_root: Path | None = None,
    library: ModuleLibrary | None = None,
) -> ContextSnapshot:
    """受理时读取显式材料的实际版本；缺失、超限与越界都在这里失败。"""
    if request.state_chapter is not None and (
        isinstance(request.state_chapter, bool)
        or not isinstance(request.state_chapter, int)
        or request.state_chapter < 0
    ):
        raise WorkspaceError(
            code="CONTEXT_INPUT_INVALID",
            where="materials.accept",
            why="状态截止章必须是非负整数",
            fix="指定正确章号或省略此字段",
        )
    paths: list[str] = []
    for path in request.reference_paths:
        if path not in paths:
            paths.append(path)
    if len(paths) > MAX_REFERENCES:
        raise WorkspaceError(
            code="CONTEXT_REFERENCE_LIMIT_INVALID",
            where="materials.accept",
            why=f"一次最多选择 {MAX_REFERENCES} 份参考，这次有 {len(paths)} 份",
            fix="减少参考文件，或在对话里说明要参考哪一段",
            details={"limit": MAX_REFERENCES, "count": len(paths)},
        )
    known = {*paths, *([request.selected_path] if request.selected_path else [])}
    unknown = [path for path in request.observed_digests if path not in known]
    if unknown:
        raise WorkspaceError(
            code="CONTEXT_INPUT_INVALID",
            where="materials.accept",
            why=f"观察版本只能对应本次当前路径或显式参考：{unknown[0]}",
            fix="只提交这次真正带上来的路径；不要附带其他文件的指纹",
            details={"path": unknown[0]},
        )
    focus: DiscussionFocus | None = None
    story_request = request.story
    if request.discussion_focus is not None:
        if state_root is None:
            raise WorkspaceError(
                code="CONTEXT_INPUT_INVALID",
                where="materials.accept",
                why="没有应用状态目录，无法核对讨论焦点",
                fix="通过应用接口发起这一轮；直接调用需要给出 state_root",
            )
        focus = freeze_discussion_focus(workspace, state_root, request.discussion_focus)
        if focus.kind == "object" and focus.object_kind == "node":
            if story_request is None:
                story_request = StoryRequest(
                    variant_id=focus.variant_id or "",
                    revision=focus.story_revision or "",
                    node_ids=(focus.object_id,),
                    purpose="design",
                )
            else:
                story_request = replace(
                    story_request,
                    node_ids=tuple(dict.fromkeys((*story_request.node_ids, focus.object_id))),
                    purpose=(
                        "design" if story_request.purpose == "discuss" else story_request.purpose
                    ),
                )
        elif focus.kind == "object":
            # 线索焦点不追加节点；仍需要结构能力以便作者明确要求时修改用途或关系。
            if story_request is None:
                story_request = StoryRequest(
                    variant_id=focus.variant_id or "",
                    revision=focus.story_revision or "",
                    node_ids=(),
                    purpose="design",
                )
            elif story_request.purpose == "discuss":
                story_request = replace(story_request, purpose="design")
    references: list[SourceRef] = []
    for path in paths:
        try:
            view = workspace.read(path)
        except WorkspaceError as exc:
            raise WorkspaceError(
                code="CONTEXT_SOURCE_NOT_FOUND",
                where="materials.accept",
                why=f"参考文件当前不可用：{path}（{exc.why}）",
                fix="刷新目录后重新选择参考；文件可能已被移动或删除",
                details={"path": path},
            ) from exc
        references.append(SourceRef(view.path, view.digest))
    ideas: list[IdeaReference] = []
    if request.idea_references:
        from hastings.workspace.ideas import IdeaStore

        store = IdeaStore(workspace)
        for idea_id, revision_id in request.idea_references:
            try:
                # 同一条目锁内核对「条目未删除、所选修订可读」，避免分别读取造成竞争。
                revision = store.resolve_reference(idea_id, revision_id)
            except WorkspaceError as exc:
                raise WorkspaceError(
                    code="CONTEXT_SOURCE_NOT_FOUND",
                    where="materials.accept",
                    why=f"灵感修订当前不可用：{idea_id}@{revision_id}（{exc.why}）",
                    fix="刷新灵感本后重新选择；条目可能已被删除",
                    details={"idea_id": idea_id, "revision_id": revision_id},
                ) from exc
            ideas.append(
                IdeaReference(
                    idea_id=idea_id,
                    revision_id=revision_id,
                    title=str(revision.get("title") or ""),
                    body=str(revision.get("body") or ""),
                )
            )
    clues: tuple[ClueReferenceSnapshot, ...] = ()
    clue_inputs = tuple(request.clue_references)
    if focus is not None:
        from hastings.workspace.state_store import current_chapter

        writer_cutoff = (
            request.state_chapter
            if request.state_chapter is not None
            else current_chapter(workspace)
        )
        assert state_root is not None
        extra = _implicit_focus_clue(state_root, focus, writer_cutoff)
        seen: set[tuple[str, str]] = set()
        merged: list[ClueReferenceInput] = []
        for item in (*clue_inputs, *extra):
            key = (item.clue_id, item.clue_revision)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        clue_inputs = tuple(merged)
    if clue_inputs:
        from hastings.workspace.clue_context import freeze_clue_references
        from hastings.workspace.state_store import current_chapter

        writer_cutoff = (
            request.state_chapter
            if request.state_chapter is not None
            else current_chapter(workspace)
        )
        clues = freeze_clue_references(
            workspace,
            state_root,
            clue_inputs,
            writer_cutoff=writer_cutoff,
            library=library,
        )
    story: StoryContextSnapshot | None = None
    if story_request is not None:
        if state_root is None:
            raise WorkspaceError(
                code="STORY_SCOPE_INVALID",
                where="materials.accept",
                why="没有应用状态目录，无法读取构思结构",
                fix="通过应用接口发起这一轮；直接调用需要给出 state_root",
            )
        from hastings.workspace.story_context import freeze_story_context
        from hastings.workspace.story_store import StoryStore

        story = freeze_story_context(StoryStore(state_root, workspace), story_request)
    return ContextSnapshot(
        state_chapter=request.state_chapter,
        selected_path=request.selected_path,
        references=tuple(references),
        observed_digests=dict(request.observed_digests),
        ideas=tuple(ideas),
        clues=clues,
        story=story,
        discussion_focus=focus,
    )


def verify_context(
    workspace: Workspace,
    snapshot: ContextSnapshot,
    *,
    state_root: Path | None = None,
) -> None:
    """执行开始前复核受理快照；版本变化按整轮失败处理，不替换引用版本。"""
    if snapshot.discussion_focus is not None:
        if state_root is None:
            raise WorkspaceError(
                code="CONTEXT_INPUT_INVALID",
                where="materials.verify_context",
                why="讨论焦点没有可复核的应用状态目录",
                fix="通过应用接口发起这一轮",
            )
        verify_discussion_focus(workspace, state_root, snapshot.discussion_focus)
    stale: list[dict[str, str]] = []
    for ref in snapshot.references:
        try:
            view = workspace.read(ref.path)
        except WorkspaceError as exc:
            raise WorkspaceError(
                code="CONTEXT_SOURCE_NOT_FOUND",
                where="materials.verify_context",
                why=f"受理时的参考文件现在不可用：{ref.path}（{exc.why}）",
                fix="刷新目录后重新发送这一轮",
                details={"path": ref.path},
            ) from exc
        if view.digest != ref.digest:
            stale.append(
                {"path": ref.path, "expected_digest": ref.digest, "current_digest": view.digest}
            )
    if stale:
        raise WorkspaceError(
            code="CONTEXT_SOURCE_STALE_CONFLICT",
            where=f"materials.verify_context:{stale[0]['path']}",
            why="受理之后参考文件被改动，这一轮不能按旧版本继续",
            fix="刷新后重新发送；需要新内容时重新选择参考",
            details={"sources": stale},
        )


def require_read_quote(
    workspace: Workspace,
    ledger: MaterialLedger,
    *,
    path: str,
    quote: str,
    where: str,
    digest: str | None = None,
    text: str | None = None,
) -> None:
    """核验一段引文确实来自本轮提供给模型的原文。

    ``digest`` 与 ``text`` 供备选方案的正文使用：那一份内容不在作品目录里，
    由调用方按方案读取后传入；不给时照常读作品目录。
    检查顺序是「非空 → 文件存在 → 这个版本读过 → 引文落在已提供的区间里」。凭记忆写一句、
    读过文件开头却引用后面一段、引用一个本轮没打开过的文件，都过不了。这段原文是否真的
    「与任务相关」属于任务理解，不在程序判断范围内。
    """
    clean = " ".join(quote.split())
    if not clean:
        raise WorkspaceError(
            code="REFERENCE_EVIDENCE_INVALID",
            where=where,
            why="需要一段实际读到的原文作为依据",
            fix="先 read_file 读取该文件，再引用其中一句原文；不要用自己的概括",
            details={"path": path},
        )
    if digest is None or text is None:
        try:
            view = workspace.read(path)
        except WorkspaceError as exc:
            raise WorkspaceError(
                code="CONTEXT_SOURCE_NOT_FOUND",
                where=where,
                why=f"引文所在文件当前不可用：{path}（{exc.why}）",
                fix="刷新目录后重新读取；文件可能已被移动或删除",
                details={"path": path},
            ) from exc
        digest, text = view.digest, view.text
    coverage = ledger.coverage(path)
    if coverage is None or coverage.ref.digest != digest:
        raise WorkspaceError(
            code="REFERENCE_EVIDENCE_INVALID",
            where=where,
            why="这个文件本轮还没有读取过，不能作为依据",
            fix="先 read_file 读取当前内容，再引用其中的原文",
            details={"path": path},
        )
    if clean not in " ".join(text.split()):
        raise WorkspaceError(
            code="REFERENCE_EVIDENCE_INVALID",
            where=where,
            why="引文不在这个文件的当前内容里",
            fix="引用该文件里确实存在的一句原文；不要凭记忆写",
            details={"path": path},
        )
    if not any(clean in " ".join(text[start:end].split()) for start, end in coverage.spans):
        raise WorkspaceError(
            code="REFERENCE_EVIDENCE_INVALID",
            where=where,
            why="这段引文所在的区间本轮没有提供给模型",
            fix="先用 read_file 读到包含这句原文的区间，再引用",
            details={"path": path, "read": [list(span) for span in coverage.spans]},
        )


def require_author_quote(
    author_texts: Sequence[tuple[int, str]],
    quote: str,
    seq: int | None,
    *,
    where: str,
) -> None:
    """核验引文确实来自作者消息；作品内容与助手回复不能作为来源。"""
    clean = " ".join(quote.split())
    if not clean:
        raise WorkspaceError(
            code="REFERENCE_EVIDENCE_INVALID",
            where=where,
            why="作者文本来源需要一段作者原话里的短引文",
            fix="复制作者消息里明确提到这份材料的句子，再登记",
        )
    if not author_texts:
        raise WorkspaceError(
            code="REFERENCE_EVIDENCE_INVALID",
            where=where,
            why="这一轮没有可核对的作者消息",
            fix="只登记本轮作者消息里明确要求或承接的材料",
        )
    if seq is not None and seq not in {item for item, _ in author_texts}:
        raise WorkspaceError(
            code="REFERENCE_EVIDENCE_INVALID",
            where=where,
            why="引文定位到的不是本轮可见的作者消息",
            fix="用本轮作者消息的事件序号；不确定时不要填 seq",
            details={"seq": seq},
        )
    for _, text in author_texts:
        if clean in " ".join(text.split()):
            return
    raise WorkspaceError(
        code="REFERENCE_EVIDENCE_INVALID",
        where=where,
        why="引文没有出现在本轮可见的作者消息里",
        fix="引用作者原话，不要改写或引用助手自己的说法",
    )


class MaterialLedger:
    """本轮材料的来源、读取区间与版本核对。

    一个 ``RunContext`` 一份；不跨轮共享，也不持久化。事件里只保存路径、指纹与区间，
    原文留在磁盘上。
    """

    def __init__(self) -> None:
        self._coverage: dict[SourceRef, ReadCoverage] = {}
        self._references: list[Reference] = []
        self._deleted: set[str] = set()

    # -- 登记 ---------------------------------------------------------------

    def register(self, reference: Reference) -> Reference:
        """登记一份来源；同一路径只留一条记录，保留首次出现的顺序。

        重复登记只能补充：作者的指定与必读要求不会被后来的登记撤销，``required`` 只会
        保持或加严，``author_selected`` 类别也保持。否则模型用 ``required=false`` 再登记
        一次用户选中的参考，就能跳过读完检查。
        """
        for index, existing in enumerate(self._references):
            if existing.path == reference.path:
                merged = _merge_reference(existing, reference)
                self._references[index] = merged
                return merged
        self._references.append(reference)
        return reference

    def references(self) -> tuple[Reference, ...]:
        return tuple(self._references)

    def required_paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self._references if item.required)

    def forget(self, path: str) -> None:
        """作者移除参考后不再要求它；已读区间一并丢弃，避免旧内容继续算数。"""
        self._references = [item for item in self._references if item.path != path]
        for ref in [ref for ref in self._coverage if ref.path == path]:
            self._coverage.pop(ref, None)

    # -- 读取记录 -----------------------------------------------------------

    def record_read(self, ref: SourceRef, *, length: int, start: int, end: int) -> ReadCoverage:
        """记下一段真正提供给模型的原文；区间必须落在该版本长度内。"""
        if start < 0 or end < start or end > length:
            raise ValueError(f"原文区间无效：{ref.path} [{start}, {end}) 长度 {length}")
        previous = self._coverage.get(ref)
        spans = previous.spans if previous is not None else ()
        coverage = ReadCoverage(ref=ref, length=length, spans=merge_spans((*spans, (start, end))))
        self._coverage[ref] = coverage
        return coverage

    def coverage(self, path: str) -> ReadCoverage | None:
        """该路径当前记录的版本覆盖情况；多个版本并存时取最新登记的那一个。"""
        for ref in reversed(list(self._coverage)):
            if ref.path == path:
                return self._coverage[ref]
        return None

    def summaries(self) -> tuple[MaterialSummary, ...]:
        """界面与事件用的简短状态；按登记顺序，没有原文。"""
        rows: list[MaterialSummary] = []
        for reference in self._references:
            coverage = self.coverage(reference.path)
            if coverage is None:
                rows.append(
                    MaterialSummary(
                        path=reference.path,
                        digest=reference.ref.digest,
                        status="已指定" if reference.required else "已定位",
                        read_chars=0,
                        length=0,
                    )
                )
                continue
            read_chars = sum(end - start for start, end in coverage.spans)
            rows.append(
                MaterialSummary(
                    path=reference.path,
                    digest=coverage.ref.digest,
                    status="已提供全文" if coverage.complete else "已提供部分原文",
                    read_chars=read_chars,
                    length=coverage.length,
                )
            )
        return tuple(rows)

    # -- 写前检查 -----------------------------------------------------------

    def require_target_read(
        self, workspace: Workspace, requirement: ReadRequirement | None
    ) -> None:
        """目标文件的独立读取前提；新建操作没有要求（``None``）。"""
        if requirement is None:
            return
        ref, length = _current(workspace, requirement.path, where="materials.target")
        coverage = self._coverage.get(ref)
        if coverage is None:
            previous = self.coverage(requirement.path)
            if previous is not None:
                # 同一个文件读过，但磁盘上已经是另一版：这是版本失效，不是「没读过」。
                raise WorkspaceError(
                    code="CONTEXT_SOURCE_STALE_CONFLICT",
                    where=f"materials.target:{requirement.path}",
                    why="这个文件在本轮读取之后被改动，之前的读取不再对应当前版本",
                    fix="重新 read_file 读取当前内容，确认后再写入",
                    details={
                        "path": requirement.path,
                        "expected_digest": previous.ref.digest,
                        "current_digest": ref.digest,
                    },
                )
            raise WorkspaceError(
                code="REFERENCE_NOT_FULLY_READ_CONFLICT",
                where=f"materials.target:{requirement.path}",
                why="这个文件本轮还没有读取过，不能写入",
                fix="先用 read_file 读取当前内容，再用返回的 digest 写入",
                details={"path": requirement.path, "missing": [[0, length]]},
            )
        if requirement.mode == "identity":
            return
        if requirement.mode == "full":
            if not coverage.complete:
                raise WorkspaceError(
                    code="REFERENCE_NOT_FULLY_READ_CONFLICT",
                    where=f"materials.target:{requirement.path}",
                    why="整体覆盖现有文件前需要先读到当前版本全文",
                    fix="用 read_file 读完剩余部分（返回里带 next_cursor），再写入",
                    details={
                        "path": requirement.path,
                        "missing": [list(span) for span in missing_spans(coverage.spans, length)],
                    },
                )
            return
        start, end = requirement.start, requirement.end
        if not covers(coverage.spans, start, end):
            raise WorkspaceError(
                code="REFERENCE_NOT_FULLY_READ_CONFLICT",
                where=f"materials.target:{requirement.path}",
                why="要替换的原文区间本轮还没有提供给模型",
                fix="先 read_file 读到包含该原文的区间，再做局部修改",
                details={"path": requirement.path, "missing": [[start, end]]},
            )

    def require_references_complete(self, workspace: Workspace) -> None:
        """作者指定或明确要求的参考必须读完当前版本，否则不能写作品。"""
        for reference in self._references:
            if not reference.required:
                continue
            ref, length = _current(workspace, reference.path, where="materials.references")
            if ref.digest != reference.ref.digest:
                raise WorkspaceError(
                    code="CONTEXT_SOURCE_STALE_CONFLICT",
                    where=f"materials.references:{reference.path}",
                    why="参考文件在本轮开始后被改动，原内容不再对应当前版本",
                    fix="重新读取该文件；确认新内容后再继续写作",
                    details={
                        "path": reference.path,
                        "expected_digest": reference.ref.digest,
                        "current_digest": ref.digest,
                    },
                )
            coverage = self._coverage.get(ref)
            if coverage is None or not coverage.complete:
                gaps = (
                    missing_spans(coverage.spans, length)
                    if coverage is not None
                    else ((0, length),)
                )
                raise WorkspaceError(
                    code="REFERENCE_NOT_FULLY_READ_CONFLICT",
                    where=f"materials.references:{reference.path}",
                    why="指定的参考还没有读完，写作品前需要先读完",
                    fix="用 read_file 读完剩余部分（返回里带 next_cursor），再写入",
                    details={
                        "path": reference.path,
                        "missing": [list(span) for span in gaps],
                    },
                )

    def verify_current(self, workspace: Workspace) -> None:
        """写前复核所有依赖版本；外部改动必须重读，不接受只换指纹。"""
        stale: list[dict[str, object]] = []
        for ref in list(self._coverage):
            if ref.path in self._deleted:
                continue
            try:
                view = workspace.read(ref.path)
            except WorkspaceError:
                continue
            if view.digest != ref.digest:
                stale.append(
                    {
                        "path": ref.path,
                        "expected_digest": ref.digest,
                        "current_digest": view.digest,
                    }
                )
        if stale:
            first = stale[0]
            raise WorkspaceError(
                code="CONTEXT_SOURCE_STALE_CONFLICT",
                where=f"materials.verify:{first['path']}",
                why="本轮依据的材料已经被外部改动",
                fix="重新读取这些文件，确认当前内容后再写入",
                details={"sources": stale},
            )

    # -- 写后更新 -----------------------------------------------------------

    def after_full_write(self, view: FileView) -> None:
        """``write_file`` 的全文由模型提供：新版本可记为已完整提供。"""
        self._deleted.discard(view.path)
        self._drop_path(view.path)
        self.record_read(
            SourceRef(view.path, view.digest), length=len(view.text), start=0, end=len(view.text)
        )

    def after_edit(self, *, before: SourceRef, view: FileView, start: int, end: int) -> None:
        """局部修改后迁移覆盖区间：此前读完的可确定性映射，否则只保留未受影响的部分。"""
        previous = self._coverage.get(before)
        self._drop_path(view.path)
        if previous is None:
            return
        if previous.complete:
            self.record_read(
                SourceRef(view.path, view.digest),
                length=len(view.text),
                start=0,
                end=len(view.text),
            )
            return
        delta = len(view.text) - previous.length
        migrated: list[Span] = []
        for span_start, span_end in previous.spans:
            if span_end <= start:
                migrated.append((span_start, span_end))
            elif span_start >= end:
                migrated.append((span_start + delta, span_end + delta))
            else:
                # 与改动区间重叠：旧区间不再对应新内容，保守丢弃，等模型按需重读。
                if span_start < start:
                    migrated.append((span_start, start))
                if span_end > end:
                    migrated.append((end + delta, span_end + delta))
        if not migrated:
            return
        merged = merge_spans(migrated)
        length = len(view.text)
        bounded = [(min(a, length), min(b, length)) for a, b in merged if a < length]
        self._coverage[SourceRef(view.path, view.digest)] = ReadCoverage(
            ref=SourceRef(view.path, view.digest), length=length, spans=merge_spans(bounded)
        )

    def after_move(self, source: SourceRef, view: FileView) -> None:
        """移动保持内容指纹：已知区间迁到新路径，原路径标为消失。"""
        previous = self._coverage.get(source)
        self._deleted.add(source.path)
        self._drop_path(source.path)
        if previous is not None and previous.length == len(view.text):
            target = SourceRef(view.path, view.digest)
            self._coverage[target] = ReadCoverage(
                ref=target, length=previous.length, spans=previous.spans
            )

    def after_delete(self, ref: SourceRef) -> None:
        """本轮自己的删除是执行事实，不能被当成外部来源丢失永久阻断后续操作。"""
        self._deleted.add(ref.path)
        self._drop_path(ref.path)

    def _drop_path(self, path: str) -> None:
        for ref in [ref for ref in self._coverage if ref.path == path]:
            self._coverage.pop(ref, None)


def _merge_reference(existing: Reference, incoming: Reference) -> Reference:
    """合并同一路径的重复登记：作者指定的来源与必读要求只增不减。"""
    keeps_author_evidence = existing.kind == "author_selected"
    return Reference(
        ref=incoming.ref,
        kind=(
            "author_selected"
            if keeps_author_evidence or incoming.kind == "author_selected"
            else incoming.kind
        ),
        required=existing.required or incoming.required,
        quote=existing.quote if keeps_author_evidence else (incoming.quote or existing.quote),
        seq=(
            existing.seq
            if keeps_author_evidence
            else (incoming.seq if incoming.seq is not None else existing.seq)
        ),
        note=incoming.note or existing.note,
    )


def _current(workspace: Workspace, path: str, *, where: str) -> tuple[SourceRef, int]:
    """读取当前版本与长度；文件缺失时给出可定位的错误。"""
    try:
        view = workspace.read(path)
    except WorkspaceError as exc:
        raise WorkspaceError(
            code="CONTEXT_SOURCE_NOT_FOUND",
            where=f"{where}:{path}",
            why=f"文件当前不可用：{path}（{exc.why}）",
            fix="刷新目录后重新读取；文件可能已被移动或删除",
            details={"path": path},
        ) from exc
    return SourceRef(view.path, view.digest), len(view.text)


def _line_start(text: str, offset: int) -> int:
    """第 ``offset`` 行（从 1 起）的起始字符下标；越界时返回文本末尾。"""
    if offset <= 1:
        return 0
    position = 0
    for _ in range(offset - 1):
        newline = text.find("\n", position)
        if newline < 0:
            return len(text)
        position = newline + 1
    return position


def _line_number(text: str, index: int, *, at_end: bool = False) -> int:
    """字符下标所在行号（从 1 起）。``at_end`` 用于区间末尾：落在行首时算上一行。"""
    clamped = min(max(0, index), len(text))
    if at_end and clamped > 0 and text[clamped - 1] == "\n":
        clamped -= 1
    return text.count("\n", 0, clamped) + 1
