"""构思范围的线索读取：身份、计划、使用位置与正文观察使用同一快照。"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from hastings.library.modules import ModuleLibrary
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clue_views import (
    ClueCoverageView,
    ClueDetail,
    ClueLifecycle,
    ClueList,
    CluePlanView,
    ClueReader,
    ClueSummary,
    ClueUsePosition,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_clue_catalog import StoryClueCatalog
from hastings.workspace.story_models import StoryVariant
from hastings.workspace.story_store import StoryStore

__all__ = ["story_clue_detail", "story_clue_list"]


def _uses(variant: StoryVariant, clue_id: str, node_id: str | None) -> list[ClueUsePosition]:
    result: list[ClueUsePosition] = []
    for node in variant.nodes.values():
        if node_id is not None and node.id != node_id:
            continue
        for use in node.clue_uses:
            if use.clue_id != clue_id:
                continue
            result.append(
                ClueUsePosition(
                    node_id=node.id,
                    stage_id=node.stage_id,
                    thread_ids=list(node.thread_ids),
                    roles=list(use.roles),
                    presentation=use.presentation,
                    mode=use.mode,
                    speaker_id=use.speaker_id,
                    expected_interpretation=use.expected_interpretation,
                )
            )
    result.sort(key=lambda row: (row.node_id, row.presentation))
    return result


def _view_revision(
    variant: StoryVariant,
    ledger_revision: str,
    as_of_chapter: int | None,
    node_id: str | None,
    rows: list[ClueDetail],
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "story": variant.revision,
                "ledger": ledger_revision,
                "as_of": as_of_chapter,
                "node_id": node_id,
                "clues": [
                    {
                        "id": row.clue_id,
                        "identity": row.clue_revision,
                        "plan": row.plan.model_dump(mode="json") if row.plan else None,
                        "uses": [use.model_dump(mode="json") for use in row.uses],
                        "observations": len(row.observations),
                        "evidence": [[item.evidence_id, item.freshness] for item in row.evidence],
                    }
                    for row in rows
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _reader(
    workspace: Workspace,
    root: Path,
    variant: StoryVariant,
    as_of_chapter: int | None,
    *,
    library: ModuleLibrary | None = None,
) -> tuple[StoryClueCatalog, ClueReader]:
    ledger = ClueLedgerStore(root).read()
    return StoryClueCatalog.from_variant(variant, ledger), ClueReader(
        workspace, root, as_of_chapter, library=library, snapshot=ledger
    )


def _detail(
    reader: ClueReader,
    catalog: StoryClueCatalog,
    variant: StoryVariant,
    clue_id: str,
    uses: list[ClueUsePosition],
    view_revision: str,
) -> ClueDetail | None:
    fact = catalog.get(clue_id)
    if fact is None:
        return None
    base = reader.detail(clue_id)
    if base is None:
        base = ClueDetail(
            clue_id=clue_id,
            clue_revision=fact.identity_revision or "empty",
            title=fact.title,
            as_of_chapter=reader.as_of,
            lifecycle=ClueLifecycle(),
            freshness="none",
            coverage=ClueCoverageView(),
            mentioned_chapters=[],
            observations=[],
            reader_seen=[],
            evidence=[],
            workspace_lineage="",
            ledger_revision=reader.snapshot.revision,
        )
    return base.model_copy(
        update={
            "scope": "story",
            "plan": CluePlanView.model_validate(fact.plan.model_dump())
            if fact.plan is not None
            else None,
            "uses": uses,
            "story_revision": variant.revision,
            "view_revision": view_revision,
        }
    )


def story_clue_list(
    workspace: Workspace,
    root: Path,
    *,
    variant_id: str,
    node_id: str | None = None,
    as_of_chapter: int | None = None,
    query: str = "",
    limit: int = 20,
    cursor: str = "",
    library: ModuleLibrary | None = None,
) -> ClueList:
    snapshot = StoryStore(root, workspace).read()
    variant = snapshot.variant(variant_id)
    if variant is None:
        raise WorkspaceError(
            code="STORY_VARIANT_NOT_FOUND",
            where="clues.story",
            why="当前作品没有这个构思",
            fix="刷新构思后重新选择",
        )
    if node_id is not None and node_id not in variant.nodes:
        raise WorkspaceError(
            code="STORY_CONTEXT_INCOMPLETE",
            where="clues.story",
            why="筛选的节点不在当前构思里",
            fix="重新选择节点",
        )
    catalog, reader = _reader(workspace, root, variant, as_of_chapter, library=library)
    rows: list[ClueDetail] = []
    for fact in catalog:
        uses = _uses(variant, fact.clue_id, node_id)
        if node_id is not None and not uses:
            continue
        row = _detail(reader, catalog, variant, fact.clue_id, uses, "")
        if row is not None:
            rows.append(row)
    needle = query.strip().casefold()
    if needle:
        rows = [
            row
            for row in rows
            if needle
            in (row.title + " " + " ".join(item.text for item in row.observations)).casefold()
        ]
    rows.sort(
        key=lambda row: (
            {"open": 0, "resolved": 1, "dropped": 2, "unknown": 3}[row.lifecycle.status],
            row.lifecycle.due_chapter or 10**9,
            row.lifecycle.planted_chapter or 10**9,
            row.clue_id,
        )
    )
    view_revision = _view_revision(
        variant, reader.snapshot.revision, as_of_chapter, node_id, rows
    )
    rows = [row.model_copy(update={"view_revision": view_revision}) for row in rows]
    size = min(50, max(1, limit))
    offset = 0
    if cursor:
        try:
            raw = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if (
                raw["stamp"] != view_revision
                or type(raw["offset"]) is not int
                or not 0 <= raw["offset"] <= len(rows)
            ):
                raise ValueError("游标范围已变化")
            offset = raw["offset"]
        except (ValueError, KeyError, TypeError) as exc:
            raise WorkspaceError(
                code="CLUE_CURSOR_CONFLICT",
                where="clues.story",
                why="分页游标与当前资料或范围不一致",
                fix="重新读取第一页",
            ) from exc
    page = rows[offset : offset + size]
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps({"stamp": view_revision, "offset": offset + len(page)}).encode()
        ).decode()
        if offset + len(page) < len(rows)
        else ""
    )
    return ClueList(
        items=[ClueSummary.model_validate(row.model_dump()) for row in page],
        total=len(rows),
        next_cursor=next_cursor,
        as_of_chapter=reader.as_of,
        current_chapter=reader.latest,
        ledger_revision=reader.snapshot.revision,
        view_revision=view_revision,
        chapters=reader.chapters(),
        chapter=None,
    )


def story_clue_detail(
    workspace: Workspace,
    root: Path,
    *,
    variant_id: str,
    clue_id: str,
    as_of_chapter: int | None = None,
    library: ModuleLibrary | None = None,
) -> ClueDetail:
    snapshot = StoryStore(root, workspace).read()
    variant = snapshot.variant(variant_id)
    if variant is None:
        raise WorkspaceError(
            code="STORY_VARIANT_NOT_FOUND",
            where="clues.story",
            why="当前作品没有这个构思",
            fix="刷新构思后重新选择",
        )
    catalog, reader = _reader(workspace, root, variant, as_of_chapter, library=library)
    if catalog.get(clue_id) is None:
        raise WorkspaceError(
            code="CLUE_NOT_FOUND",
            where="clues.story",
            why="当前构思里没有这条线索",
            fix="重新读取线索列表后选择",
        )
    uses = _uses(variant, clue_id, None)
    row = _detail(reader, catalog, variant, clue_id, uses, "")
    assert row is not None
    revision = _view_revision(
        variant, reader.snapshot.revision, as_of_chapter, None, [row]
    )
    return row.model_copy(update={"view_revision": revision})
