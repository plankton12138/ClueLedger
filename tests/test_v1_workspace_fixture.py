"""V1 旧工作区基准 fixture 校验（Phase 11 T11.3）。

fixture（tests/fixtures/v1_workspace/winter-manor-case）是纯测试数据，
作为 Phase 12–17 迁移测试与迁移报告的对照基线。本测试保证三件事：

1. manifest 覆盖 fixture 目录下除自身外的全部文件（无遗漏、无多余）；
2. 每个文件的 SHA-256 与记录数与 manifest 一致——哈希漂移必须显式重录，
   迁移报告才可信；
3. fixture 能被 V1 存储层原样读取（schema 兼容的活证据，不是死快照）。

records 口径：character/chapter/revision/story 每文件 1 条；
clue/foreshadow/method/timeline/outline 按 YAML 列表长度；
session_index 按 sessions 数组长度；session_log/events 按非空行数。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import yaml

from hastings.bible.models import (
    Clue,
    Foreshadow,
    Method,
    OutlineEntry,
    TimelineEntry,
)
from hastings.bible.store import StoryWorkspace, list_characters, load_ledger, load_story_doc
from hastings.manuscript.store import current_revision

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "v1_workspace" / "winter-manor-case"
SLUG = "winter-manor-case"

RECORD_COUNTERS: dict[str, Callable[[Path], int]] = {
    "story": lambda _: 1,
    "character": lambda _: 1,
    "chapter": lambda _: 1,
    "revision_md": lambda _: 1,
    "revision_meta": lambda _: 1,
    "clue": lambda p: len(yaml.safe_load(p.read_text(encoding="utf-8"))),
    "foreshadow": lambda p: len(yaml.safe_load(p.read_text(encoding="utf-8"))),
    "method": lambda p: len(yaml.safe_load(p.read_text(encoding="utf-8"))),
    "timeline": lambda p: len(yaml.safe_load(p.read_text(encoding="utf-8"))),
    "outline": lambda p: len(yaml.safe_load(p.read_text(encoding="utf-8"))),
    "session_index": lambda p: len(json.loads(p.read_text(encoding="utf-8"))["sessions"]),
    "session_log": lambda p: sum(
        1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
    ),
    "events": lambda p: sum(
        1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
    ),
}


def _load_manifest() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    data = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    by_path = {entry["path"]: entry for entry in data["files"]}  # type: ignore[index]
    return data, by_path


def test_manifest_covers_every_fixture_file() -> None:
    _, by_path = _load_manifest()
    actual = {
        p.relative_to(FIXTURE_ROOT).as_posix()
        for p in FIXTURE_ROOT.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    assert actual == set(by_path), "manifest 与 fixture 目录内容不一致"


def test_manifest_hashes_and_record_counts_match() -> None:
    _, by_path = _load_manifest()
    for rel, entry in sorted(by_path.items()):
        path = FIXTURE_ROOT / rel
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == entry["sha256"], f"哈希漂移：{rel}（fixture 被改动须重录 manifest）"
        counter = RECORD_COUNTERS[str(entry["type"])]
        assert counter(path) == entry["records"], f"记录数不符：{rel}"


def test_fixture_loads_through_v1_stores() -> None:
    """fixture 必须能被 V1 存储层原样读取——迁移源兼容性的活证据。"""
    workspace = StoryWorkspace(root=FIXTURE_ROOT.parent, slug=SLUG)

    doc = load_story_doc(workspace)
    assert doc.slug == SLUG
    assert doc.stage == "writing"

    characters = list_characters(workspace)
    assert {c.name for c in characters} == {"桐生绫", "早坂慎一", "雾岛千鹤"}

    clues = load_ledger(workspace.clues_path, Clue)
    assert len(clues) == 3
    foreshadows = load_ledger(workspace.foreshadows_path, Foreshadow)
    assert len(foreshadows) == 2
    methods = load_ledger(workspace.method_path, Method)
    assert methods[0].status == "locked"
    timeline = load_ledger(workspace.timeline_path, TimelineEntry)
    assert len(timeline) == 3
    outlines = load_ledger(workspace.outline_path, OutlineEntry)
    assert [o.chapter for o in outlines] == [1, 2, 3]

    assert current_revision(workspace, "ch01") == 2  # 1 次归档 + 主文件
    assert current_revision(workspace, "ch02") == 1
