"""S7 规模测量：100 章、500 条线索、20 份报告下的列表与详情延时。

用法：uv run python scripts/measure_clue_scale.py [输出路径]
只读测量，不调用模型；数据全部建在临时目录，结束即删除。
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

from hastings.workspace.clue_designs import create_clue_identity, save_clue_inference
from hastings.workspace.clue_models import ClueCoverage, ClueEntry, FileSource
from hastings.workspace.clue_reviews import (
    ClueReviewDraft,
    ClueReviewFinding,
    clue_review_report,
    freeze_clue_review,
    publish_clue_review,
)
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clue_views import clue_detail, clue_list
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.files import Workspace
from hastings.workspace.materials import MaterialLedger, SourceRef
from hastings.workspace.review import ReviewEvidence

CHAPTERS = 100
CLUES_PER_CHAPTER = 5
REPORTS = 20


def build(root: Path) -> tuple[Workspace, ClueLedgerStore, list[str], list[str]]:
    workspace = Workspace.create(root / "规模作品")
    store = ClueLedgerStore(root / "state")
    quotes: dict[str, str] = {}
    for chapter in range(1, CHAPTERS + 1):
        quote = f"第 {chapter} 章的线索原文，编号 {chapter:03d}。"
        quotes[f"正文/{chapter:04d} 第{chapter}章.md"] = quote
    for path, quote in quotes.items():
        workspace.write(path, f"# 第 {len(quote)} 字\n\n{quote}\n", expected_digest=None)

    clue_ids: list[str] = []
    paths = list(quotes)
    for chapter in range(1, CHAPTERS + 1):
        path = paths[chapter - 1]
        quote = quotes[path]
        view = workspace.read(path)
        entries: list[ClueEntry] = []
        for offset in range(CLUES_PER_CHAPTER):
            title = f"线索 {chapter:03d}-{offset + 1}"
            clue_id = str(
                create_clue_identity(
                    store,
                    title=title,
                    request_key=f"clue-{chapter}-{offset}",
                    expected_revision=store.read().revision,
                )["clue_id"]
            )
            clue_ids.append(clue_id)
            entries.append(
                ClueEntry(
                    title,
                    chapter,
                    None,
                    "open",
                    f"第 {chapter} 章的观察 {offset + 1}",
                    ObservedEvidence(
                        path, view.digest, view.text.index(quote), view.text.index(quote) + len(quote), quote
                    ),
                    source_chapter=chapter,
                    clue_id=clue_id,
                    mode="narration",
                )
            )
        store.publish(
            tuple(entries),
            source_chapter=chapter,
            expected_revision=store.read().revision,
            request_key=f"publish-{chapter}",
            coverage=ClueCoverage(
                path=path,
                digest=view.digest,
                chapter=chapter,
                outcome="found",
                task_id=f"task-{chapter}",
                at="2026-10-27T09:00:00+08:00",
            ),
        )

    report_ids: list[str] = []
    for index in range(REPORTS):
        chapter = CHAPTERS - index
        path = paths[chapter - 1]
        view = workspace.read(path)
        quote = quotes[path]
        inference = save_clue_inference(
            store,
            clue_ids=clue_ids[(chapter - 1) * CLUES_PER_CHAPTER : chapter * CLUES_PER_CHAPTER],
            goal=f"第 {chapter} 章的推理目标",
            intent="deduce",
            premises=[],
            alternatives=[],
            design_link_ids=[],
            request_key=f"inference-{index}",
            expected_revision=store.read().revision,
            created_by="author",
        )
        scope = freeze_clue_review(
            workspace,
            store,
            inference_id=str(inference["inference_id"]),
            inference_revision=str(inference["inference_revision"]),
            clue_ids=[
                str(item) for item in inference["clue_ids"]
            ],
            as_of_chapter=chapter,
            chapter_paths=[path],
        )
        ledger = MaterialLedger()
        ledger.record_read(
            SourceRef(path, view.digest), length=len(view.text), start=0, end=len(view.text)
        )
        start = view.text.index(quote)
        report = publish_clue_review(
            scope,
            ClueReviewDraft(
                status="findings",
                findings=(
                    ClueReviewFinding(
                        kind="counter_evidence",
                        certainty="supported",
                        description=f"第 {chapter} 章的观察与结论不一致。",
                        suggestion="补一句当时的反应。",
                        evidence=(
                            ReviewEvidence(
                                path=path,
                                digest=view.digest,
                                start=start,
                                end=start + len(quote),
                                quote=quote,
                            ),
                        ),
                    ),
                ),
                uncovered=(),
            ),
            requirement="规模测量。",
            workspace=workspace,
            ledger=ledger,
            store=store,
            turn_id=f"scale-{index}",
        )
        report_ids.append(report.report_id)
    return workspace, store, clue_ids, report_ids


def timed(action, repeats: int = 5) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        action()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "median_ms": round(statistics.median(samples), 1),
        "min_ms": round(min(samples), 1),
        "max_ms": round(max(samples), 1),
    }


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        built = time.perf_counter()
        workspace, store, clue_ids, report_ids = build(root)
        build_seconds = round(time.perf_counter() - built, 1)
        target = clue_ids[len(clue_ids) // 2]
        chapter_path = "正文/0050 第50章.md"
        measurements = {
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "machine": platform.machine(),
            },
            "scale": {
                "chapters": CHAPTERS,
                "clues": len(clue_ids),
                "reports": len(report_ids),
                "build_seconds": build_seconds,
            },
            "catalog": timed(lambda: workspace.catalog()),
            "clue_list_chapter": timed(
                lambda: clue_list(workspace, store.root, chapter_path=chapter_path, as_of_chapter=50)
            ),
            "clue_list_search": timed(
                lambda: clue_list(workspace, store.root, as_of_chapter=100, query="线索 0", limit=20)
            ),
            "clue_detail": timed(lambda: clue_detail(workspace, store.root, target, as_of_chapter=100)),
            "review_report": timed(
                lambda: clue_review_report(workspace, store, report_ids[0])
            ),
        }
    text = json.dumps(measurements, ensure_ascii=False, indent=2) + "\n"
    if out is None:
        print(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
