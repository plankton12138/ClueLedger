"""S3 浏览器的真实跨章账本与覆盖；不调用模型。"""

import sys
from dataclasses import replace
from pathlib import Path

from hastings.workspace.clue_models import ClueCoverage, ClueEntry
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.files import Workspace

workspace = Workspace.open(Path(sys.argv[1]))
store = ClueLedgerStore(Path(sys.argv[2]))
paths = [
    "正文/0001 雨夜.md",
    "正文/0002 照片.md",
    "正文/0003 旁支.md",
    "正文/0004 空结果.md",
    "正文/0005 不足.md",
    "正文/0006 待写.md",
]
texts = [
    "# 雨夜\n\n信封背面有字。\n\n" + "港口仍在下雨。\n\n" * 70,
    "# 照片\n\n林岚说她没看过信封。\n\n照片证明她看过背面。\n",
    "# 旁支\n\n旅客尚未开口。\n",
    "# 空结果\n\n他们在茶馆闲谈天气。\n",
    "# 不足\n\n他似乎想说什么。\n",
    "# 待写\n",
]
for path, text in zip(paths, texts, strict=True):
    old = workspace.read(path) if (workspace.root / path).exists() else None
    workspace.write(path, text, expected_digest=old.digest if old else None)


def proof(index, quote):
    file = workspace.read(paths[index])
    start = file.text.index(quote)
    return ObservedEvidence(file.path, file.digest, start, start + len(quote), quote)


def publish(index, entries, outcome="found"):
    file = workspace.read(paths[index])
    return store.publish(
        tuple(entries),
        source_chapter=index + 1,
        expected_revision=store.read().revision,
        request_key=f"s3-{index}",
        coverage=ClueCoverage(
            path=file.path,
            digest=file.digest,
            chapter=index + 1,
            outcome=outcome,
            task_id=f"s3-{index}",
            at="2026-10-27T10:00:00+08:00",
            note="原文信息不足" if outcome == "insufficient" else "",
        ),
    )


publish(
    0,
    [
        ClueEntry(
            "信封背面的字",
            1,
            None,
            "open",
            "作者推测林岚隐瞒了经历",
            proof(0, "信封背面有字。"),
            source_chapter=1,
            mode="narration",
        )
    ],
)
entry = replace(
    store.read().entries()[0],
    source_chapter=2,
    status="resolved",
    resolution_chapter=2,
    mention_evidence=proof(1, "林岚说她没看过信封。"),
    resolution_evidence=proof(1, "照片证明她看过背面。"),
    mode="testimony",
)
publish(1, [entry])
publish(3, [], "empty")
publish(4, [], "insufficient")
