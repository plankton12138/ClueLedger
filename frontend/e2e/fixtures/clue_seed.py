"""浏览器的线索夹具：写入两章正文与一条可引用的线索；不调用模型。

用法：python e2e/fixtures/clue_seed.py <作品路径> <状态目录>
"""

from __future__ import annotations

import sys
from pathlib import Path

from hastings.workspace.clue_models import ClueCoverage, ClueEntry
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clues import create_clue_identity
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.files import Workspace

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

second = str(
    create_clue_identity(
        store, title="公开的照片", request_key="seed-second", expected_revision=store.read().revision
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
    request_key="seed-publish",
    coverage=ClueCoverage(
        path=file.path,
        digest=file.digest,
        chapter=2,
        outcome="found",
        task_id="seed",
        at="2026-10-29T09:00:00+08:00",
    ),
)
