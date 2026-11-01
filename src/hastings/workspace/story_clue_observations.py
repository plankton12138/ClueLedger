"""把已提交的候选正文登记为线索观察，让构思计划与正文证据使用同一身份。"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from hastings.workspace.clue_models import ClueEntry
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.files import Workspace
from hastings.workspace.story_store import StoryStore

__all__ = ["record_scene_observations"]


def _evidence(path: str, digest: str, text: str, quote: str) -> ObservedEvidence | None:
    start = text.find(quote)
    if start < 0:
        return None
    return ObservedEvidence(
        path=path,
        digest=digest,
        start=start,
        end=start + len(quote),
        quote=quote,
    )


def record_scene_observations(
    workspace: Workspace,
    state_root: Path,
    *,
    variant_id: str,
    node_ids: tuple[str, ...],
    documents: dict[str, str],
    task_id: str,
) -> dict[str, Any]:
    """只登记候选正文里确实出现的线索呈现；找不到原句就跳过。

    一章只保留一份贡献。第一次出现登记为埋设，同一线索在后续章出现时登记为本轮
    提及，不把后续章误当成新的起点。写入失败不撤销已提交正文，由调用方记录。
    """
    variant = StoryStore(state_root, workspace).require_variant(variant_id)
    chapters = {
        row.path: row.chapter_number
        for row in workspace.catalog().chapters
        if row.exists and row.chapter_number is not None
    }
    ledger = ClueLedgerStore(state_root)
    snapshot = ledger.read()
    identities = snapshot.ledger.clues if snapshot.ledger else {}

    by_chapter: dict[int, list[ClueEntry]] = defaultdict(list)
    seen: set[tuple[int, str]] = set()
    unresolved: list[str] = []
    for path, text in documents.items():
        chapter = chapters.get(path)
        if chapter is None:
            unresolved.append(path)
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        for node_id in node_ids:
            node = variant.nodes.get(node_id)
            if node is None:
                continue
            for use in node.clue_uses:
                key = (chapter, use.clue_id)
                if key in seen:
                    continue
                quote = use.presentation.strip()
                current = _evidence(path, digest, text, quote)
                if current is None:
                    continue
                seen.add(key)
                identity = identities.get(use.clue_id)
                previous = sorted(
                    (
                        row
                        for row in (
                            snapshot.ledger.contributions.values() if snapshot.ledger else ()
                        )
                        if row.clue_id == use.clue_id
                    ),
                    key=lambda row: row.content.source_chapter,
                )
                first = previous[0] if previous else None
                by_chapter[chapter].append(
                    ClueEntry(
                        clue=identity.title if identity is not None else use.clue_id,
                        planted_chapter=first.content.planted_chapter if first else chapter,
                        due_chapter=None,
                        status="open",
                        supports="",
                        evidence=first.content.evidence.observed() if first else current,
                        source_chapter=chapter,
                        clue_id=use.clue_id,
                        mention_evidence=current if first else None,
                        mode=use.mode,
                    )
                )

    published = 0
    # 即使这一章没有匹配到线索，也要发布空批：候选整章替换后，旧观察不能继续冒充当前。
    for chapter in sorted({chapters[path] for path in documents if path in chapters}):
        entries = by_chapter.get(chapter, [])
        ledger.publish(
            entries,
            source_chapter=chapter,
            expected_revision=ledger.read().revision,
            request_key=f"candidate_observations:{task_id}:{chapter}",
            task_id=task_id,
        )
        published += len(entries)
    return {"published": published, "chapters": sorted(by_chapter), "unresolved": unresolved}
