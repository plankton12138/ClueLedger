"""S5 浏览器的真实核对报告；不调用模型。

用法：python e2e/fixtures/clue_review.py <作品路径> <状态目录>
"""

from __future__ import annotations

import sys
from pathlib import Path

from hastings.workspace.clue_designs import create_clue_identity, save_clue_inference
from hastings.workspace.clue_models import ClueCoverage, ClueEntry
from hastings.workspace.clue_reviews import (
    ClueReviewDraft,
    ClueReviewFinding,
    freeze_clue_review,
    publish_clue_review,
)
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.files import Workspace
from hastings.workspace.materials import MaterialLedger, SourceRef
from hastings.workspace.review import ReviewEvidence

FIRST = "正文/0001 雨夜.md"
SECOND = "正文/0002 照片.md"
QUOTE = "照片证明她看过背面。"

workspace = Workspace.open(Path(sys.argv[1]))
store = ClueLedgerStore(Path(sys.argv[2]))
for path, text in [
    (FIRST, "# 遗书\n\n信封背面有字。\n"),
    (SECOND, f"# 照片\n\n林岚说她没看过信封。\n\n{QUOTE}\n"),
]:
    old = workspace.read(path) if (workspace.root / path).exists() else None
    workspace.write(path, text, expected_digest=old.digest if old else None)

first = str(
    create_clue_identity(
        store, title="信封背面的字", request_key="s5-first", expected_revision=store.read().revision
    )["clue_id"]
)
second = str(
    create_clue_identity(
        store, title="公开的照片", request_key="s5-second", expected_revision=store.read().revision
    )["clue_id"]
)
file = workspace.read(SECOND)
proof = ObservedEvidence(
    file.path, file.digest, file.text.index(QUOTE), file.text.index(QUOTE) + len(QUOTE), QUOTE
)
store.publish(
    (
        ClueEntry(
            "公开的照片",
            2,
            None,
            "open",
            "作者推测林岚隐瞒了经历",
            proof,
            source_chapter=2,
            clue_id=second,
            mode="narration",
        ),
    ),
    source_chapter=2,
    expected_revision=store.read().revision,
    request_key="s5-publish",
    coverage=ClueCoverage(
        path=file.path,
        digest=file.digest,
        chapter=2,
        outcome="found",
        task_id="s5",
        at="2026-10-28T09:00:00+08:00",
    ),
)
inference = save_clue_inference(
    store,
    clue_ids=[first, second],
    goal="林岚提前接触过遗书原件",
    intent="deduce",
    premises=[],
    alternatives=[],
    design_link_ids=[],
    request_key="s5-inference",
    expected_revision=store.read().revision,
    created_by="author",
)
scope = freeze_clue_review(
    workspace,
    store,
    inference_id=str(inference["inference_id"]),
    inference_revision=str(inference["inference_revision"]),
    clue_ids=[first, second],
    as_of_chapter=2,
    chapter_paths=[SECOND],
)
ledger = MaterialLedger()
ledger.record_read(
    SourceRef(file.path, file.digest), length=len(file.text), start=0, end=len(file.text)
)
publish_clue_review(
    scope,
    ClueReviewDraft(
        status="findings",
        findings=(
            ClueReviewFinding(
                kind="counter_evidence",
                certainty="supported",
                description="林岚说没见过信封，但照片显示她看过背面。",
                suggestion="补一句她是否记得这封信。",
                evidence=(
                    ReviewEvidence(
                        path=SECOND,
                        digest=file.digest,
                        start=file.text.index(QUOTE),
                        end=file.text.index(QUOTE) + len(QUOTE),
                        quote=QUOTE,
                    ),
                ),
            ),
        ),
    ),
    requirement="核对这组线索是否足以认定林岚提前接触过原件。",
    workspace=workspace,
    ledger=ledger,
    store=store,
    turn_id="t-s5-fixture",
)
