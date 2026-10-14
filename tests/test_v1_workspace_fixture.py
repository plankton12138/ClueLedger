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


def test_fixture_satisfies_v1_domain_invariants() -> None:
    """评审收口：fixture 不只是字节快照，其内容必须满足 V1 领域不变量。

    迁移测试会把 fixture 喂给 V2 导入器；若源数据本身违反主键/引用约束，
    导入失败将无法区分是导入器的错还是数据的错。
    """
    workspace = StoryWorkspace(root=FIXTURE_ROOT.parent, slug=SLUG)

    characters = list_characters(workspace)
    character_names = {c.name for c in characters}
    assert len(character_names) == len(characters), "角色主键不唯一"

    clues = load_ledger(workspace.clues_path, Clue)
    assert len({c.name for c in clues}) == len(clues), "线索 name 主键不唯一"

    outlines = load_ledger(workspace.outline_path, OutlineEntry)
    chapters = [o.chapter for o in outlines]
    assert len(set(chapters)) == len(chapters), "章纲 chapter 编号不唯一"

    timeline = load_ledger(workspace.timeline_path, TimelineEntry)
    for entry in timeline:
        unknown = set(entry.participants) - character_names
        assert not unknown, f"时间线引用了未建档角色：{unknown}"

    methods = load_ledger(workspace.method_path, Method)
    for method in methods:
        alibi_persons = {claim.person for claim in method.alibi_claims}
        unknown = alibi_persons - character_names
        assert not unknown, f"手法不在场证明引用了未建档角色：{unknown}"

    doc = load_story_doc(workspace)
    assert doc.stage_history[0].from_stage == "intake", "阶段史必须从 intake 开始"


def test_event_and_session_logs_are_wellformed() -> None:
    """评审收口：JSONL 逐行可解析且事件结构满足 V1 事件契约。

    Phase 12 导入器要按 seq 重放事件；seq 断裂或乱序会让「导入失败原因」
    无法归因。message_update 在 V1 中被刻意裁剪（不落盘），出现即为契约漂移。
    """
    import json

    events = [
        json.loads(line)
        for line in (FIXTURE_ROOT / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events, "事件日志为空"
    for i, event in enumerate(events, start=1):
        assert set(event) == {"ts", "seq", "type", "data"}, f"第 {i} 行字段不符：{set(event)}"
        assert event["seq"] == i, f"事件 seq 断裂：第 {i} 行 seq={event['seq']}"
        assert event["type"] != "message_update", "message_update 不应落盘（V1 刻意裁剪）"

    session_log = FIXTURE_ROOT / "sessions" / "session-20261001-120000-ab12.jsonl"
    for line in session_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        message = json.loads(line)
        assert set(message) == {"role", "content"}

    revision_meta = json.loads(
        (FIXTURE_ROOT / "chapters" / ".revisions" / "ch01" / "001.json").read_text(encoding="utf-8")
    )
    assert set(revision_meta) == {"ts", "hunks", "stats"}
