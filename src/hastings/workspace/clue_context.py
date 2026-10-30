"""对话里的线索引用：受理时冻结作者看到的版本，正文依据单独成区。

引用本身不是正文依据：它只告诉服务端「作者指的是哪一条线索、哪个版本、看到哪一章」。
服务端按当前账本与截止章重建读者已见的部分。版本或章界在受理后变化时，这一轮按冲突
失败，不静默换成新版本。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from hastings.library.modules import ModuleLibrary
from hastings.prompts.sections import (
    CLUE_READER_SEEN_EMPTY,
    CLUE_READER_SEEN_HEAD,
    CLUE_REFERENCES_HEAD,
    CLUE_REFERENCES_TAIL,
    CLUE_STALE_HEAD,
    clue_reader_line,
    clue_reference_line,
    clue_reference_warning,
)
from hastings.workspace.clue_models import ClueReference
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clue_views import clue_detail
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace

__all__ = [
    "MAX_CLUE_REFERENCES",
    "ClueReferenceInput",
    "ClueReferenceSnapshot",
    "clue_references_section",
    "freeze_clue_references",
]

MAX_CLUE_REFERENCES = 10


@dataclass(frozen=True, slots=True)
class ClueReferenceInput:
    """客户端提交的引用：只给标识与看到的版本，不给正文。"""

    workspace_lineage: str
    clue_id: str
    clue_revision: str
    browse_as_of_chapter: int

    def identity(self) -> dict[str, object]:
        return {
            "workspace_lineage": self.workspace_lineage,
            "clue_id": self.clue_id,
            "clue_revision": self.clue_revision,
            "browse_as_of_chapter": self.browse_as_of_chapter,
        }


@dataclass(frozen=True, slots=True)
class ClueReferenceSnapshot:
    """一份引用的冻结内容；``reader`` 才是正文依据。"""

    workspace_lineage: str
    clue_id: str
    clue_title: str
    clue_revision: str
    browse_as_of_chapter: int
    reader_cutoff: int
    reader: tuple[dict[str, object], ...]
    warnings: tuple[str, ...]

    def identity(self) -> dict[str, object]:
        return {
            "workspace_lineage": self.workspace_lineage,
            "clue_id": self.clue_id,
            "clue_revision": self.clue_revision,
            "browse_as_of_chapter": self.browse_as_of_chapter,
            "reader_cutoff": self.reader_cutoff,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.identity(),
            "clue_title": self.clue_title,
            "reader": [dict(item) for item in self.reader],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> ClueReferenceSnapshot:
        def rows(value: object) -> tuple[dict[str, object], ...]:
            if not isinstance(value, list):
                return ()
            return tuple(cast(dict[str, object], item) for item in value if isinstance(item, dict))

        warnings = raw.get("warnings")
        return cls(
            workspace_lineage=str(raw.get("workspace_lineage") or ""),
            clue_id=str(raw.get("clue_id") or ""),
            clue_title=str(raw.get("clue_title") or ""),
            clue_revision=str(raw.get("clue_revision") or ""),
            browse_as_of_chapter=int(str(raw.get("browse_as_of_chapter") or 0)),
            reader_cutoff=int(str(raw.get("reader_cutoff") or 0)),
            reader=rows(raw.get("reader")),
            warnings=tuple(str(item) for item in warnings) if isinstance(warnings, list) else (),
        )


def freeze_clue_references(
    workspace: Workspace,
    root: Path | None,
    references: tuple[ClueReference, ...] | tuple[ClueReferenceInput, ...],
    *,
    writer_cutoff: int,
    library: ModuleLibrary | None = None,
) -> tuple[ClueReferenceSnapshot, ...]:
    """按当前账本核对每一份引用；任何一项不成立都不进入受理。"""
    if not references:
        return ()
    if root is None:
        raise _input_error("这一轮没有可核对线索引用的作品状态目录", "在作品会话里引用线索")
    if len(references) > MAX_CLUE_REFERENCES:
        raise _input_error(
            f"一次最多引用 {MAX_CLUE_REFERENCES} 条线索，这次有 {len(references)} 条",
            "减少引用，或在对话里说明要看哪几条",
        )
    store = ClueLedgerStore(root)
    snapshot = store.read()
    ledger = snapshot.ledger
    frozen: list[ClueReferenceSnapshot] = []
    seen: set[tuple[str, str]] = set()
    for reference in references:
        clue_id = reference.clue_id
        revision = reference.clue_revision
        if (clue_id, revision) in seen:
            continue
        seen.add((clue_id, revision))
        if ledger is None or clue_id not in ledger.clues:
            raise WorkspaceError(
                code="CLUE_NOT_FOUND",
                where="clues.reference",
                why=f"引用的线索当前不存在：{clue_id}",
                fix="刷新线索详情后重新引用",
                details={"clue_id": clue_id},
            )
        if reference.workspace_lineage != ledger.workspace_lineage:
            raise WorkspaceError(
                code="CLUE_ACCESS_DENIED",
                where="clues.reference",
                why="引用的线索不属于当前作品",
                fix="切回原作品，或从当前作品重新选择线索",
                details={"clue_id": clue_id},
            )
        identity = ledger.clues[clue_id]
        if identity.clue_revision != revision:
            raise WorkspaceError(
                code="CLUE_REFERENCE_STALE_CONFLICT",
                where="clues.reference",
                why=f"线索在读取后已经变化：{identity.title}",
                fix="保留草稿并重新读取线索，确认后继续发送",
                details={
                    "clue_id": clue_id,
                    "expected": revision,
                    "current": identity.clue_revision,
                },
            )
        cutoff = max(0, min(reference.browse_as_of_chapter, writer_cutoff))
        detail = clue_detail(workspace, root, clue_id, as_of_chapter=cutoff, library=library)
        reader: list[dict[str, object]] = []
        for observation in detail.observations:
            for basis_id in observation.evidence_ids:
                basis = next(
                    (item for item in detail.evidence if item.evidence_id == basis_id), None
                )
                if basis is None:
                    continue
                reader.append(
                    {
                        "observation_id": observation.observation_id,
                        "chapter": observation.chapter,
                        "kind": observation.kind,
                        "mode": observation.mode,
                        "text": observation.text,
                        "quote": basis.quote,
                        "path": basis.path,
                        "freshness": basis.freshness,
                        "usable": basis.freshness == "valid",
                    }
                )
        warnings = list(detail.warnings)
        stale = [row for row in reader if not row["usable"]]
        if stale:
            warnings.append("部分正文依据的来源已变化，只能作为旧引文，不能证明当前内容。")
        frozen.append(
            ClueReferenceSnapshot(
                workspace_lineage=str(reference.workspace_lineage),
                clue_id=clue_id,
                clue_title=identity.title,
                clue_revision=revision,
                browse_as_of_chapter=max(0, reference.browse_as_of_chapter),
                reader_cutoff=cutoff,
                reader=tuple(reader),
                warnings=tuple(dict.fromkeys(warnings)),
            )
        )
    return tuple(frozen)


def clue_references_section(clues: tuple[ClueReferenceSnapshot, ...]) -> str:
    """系统提示里的线索引用区：只列出读者已见的正文依据。"""
    if not clues:
        return ""
    lines = [CLUE_REFERENCES_HEAD]
    for item in clues:
        lines.append(
            clue_reference_line(
                item.clue_title,
                item.clue_id,
                item.reader_cutoff,
                item.clue_revision,
            )
        )
        usable = [row for row in item.reader if row.get("usable")]
        stale = [row for row in item.reader if not row.get("usable")]
        if usable:
            lines.append(CLUE_READER_SEEN_HEAD)
            for row in usable:
                lines.append(
                    clue_reader_line(
                        str(row.get("chapter")),
                        str(row.get("kind")),
                        str(row.get("quote")),
                        str(row.get("path")),
                    )
                )
        else:
            lines.append(CLUE_READER_SEEN_EMPTY)
        if stale:
            lines.append(CLUE_STALE_HEAD)
            for row in stale:
                lines.append(f"      - 「{row.get('quote')}」（`{row.get('path')}`）")
        for warning in item.warnings:
            lines.append(clue_reference_warning(warning))
    lines.append(CLUE_REFERENCES_TAIL)
    return "\n".join(lines)


def _input_error(why: str, fix: str) -> WorkspaceError:
    return WorkspaceError(
        code="CLUE_REFERENCE_INVALID",
        where="clues.reference",
        why=why,
        fix=fix,
    )
