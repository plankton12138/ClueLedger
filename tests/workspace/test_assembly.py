"""上下文装配（0.6 V06-E1）。

契约：装配按用途分区，每个分区带预算与稳定 key；必装分区超预算直接失败，可裁分区裁掉
后留下原因。装配结果要能回答「这一轮给了什么、没给什么」，不只是拼出一段字符串。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.assembly import Exclusion, Partition, assemble
from hastings.workspace.context import build_assembled_context
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.state import conversation_root, workspace_state_root


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    workspace.write("正文/0001 雨夜.md", "第一章 雨夜\n\n雪压住了山路。", expected_digest=None)
    for path, text in (
        ("设定集/线索.md", "门锁没有被撬。"),
        ("设定集/真相.md", "凶手从内侧打开了门。"),
    ):
        try:
            expected = workspace.read(path).digest
        except WorkspaceError:
            expected = None
        workspace.write(path, text, expected_digest=expected)
    return workspace


def _part(key: str, size: int, budget: int, *, required: bool = False) -> Partition:
    return Partition(key=key, title=key, text="字" * size, budget=budget, required=required)


def test_partition_over_budget_fails_when_required() -> None:
    with pytest.raises(WorkspaceError) as excinfo:
        assemble("writer", [_part("instructions", 50, 10, required=True)])
    assert excinfo.value.code == "CONTEXT_ASSEMBLY_FAILED"


def test_partition_over_budget_is_skipped_when_optional() -> None:
    result = assemble(
        "writer",
        [_part("instructions", 5, 100, required=True), _part("index", 50, 10)],
    )
    assert [part.key for part in result.parts] == ["instructions"]
    assert [(skip.key, skip.reason) for skip in result.skipped] == [("index", "budget")]
    assert "字" * 50 not in result.text


def test_empty_partition_is_reported_not_rendered() -> None:
    result = assemble(
        "writer",
        [_part("instructions", 5, 100, required=True), Partition("settings", "设定", "  ", 100)],
    )
    assert [(skip.key, skip.reason) for skip in result.skipped] == [("settings", "empty")]
    assert "## 设定" not in result.text


def test_summary_reports_candidates_left_out_by_budget() -> None:
    excluded = Exclusion(
        id="clue:门锁",
        reason="budget",
        chars=42,
        location="read_state domain=clues",
    )
    result = assemble(
        "writer",
        [
            _part("instructions", 5, 100, required=True),
            Partition("clues", "未回收线索", "正文", 100, excluded=(excluded,)),
        ],
    )

    summary = result.summary()
    parts = summary["parts"]
    assert isinstance(parts, list)
    rows = {row["key"]: row for row in parts}
    assert rows["clues"]["excluded"] == [
        {
            "id": "clue:门锁",
            "reason": "budget",
            "chars": 42,
            "location": "read_state domain=clues",
        }
    ]
    assert "excluded" not in rows["instructions"]


def test_writer_prompt_keeps_partition_headers_and_order(tmp_path: Path) -> None:
    assembled = build_assembled_context(
        _workspace(tmp_path),
        profile="writer",
        materials="- 本轮材料：`正文/0001 雨夜.md`｜已提供全文（12 字符）",
    )
    assert [part.key for part in assembled.parts] == [
        "instructions",
        "catalog",
        "materials",
        "settings",
    ]
    assert assembled.text.startswith("## ")
    assert "## 设定集内容" in assembled.text


def test_summary_reports_profile_and_usage(tmp_path: Path) -> None:
    assembled = build_assembled_context(_workspace(tmp_path), profile="review")
    summary = assembled.summary()
    assert summary["profile"] == "review"
    assert {item["key"] for item in summary["parts"]} >= {"instructions", "catalog", "settings"}
    assert summary["budget"]["used"] > 0  # type: ignore[index]
    assert summary["skipped"] == []


def test_glossary_partition_lists_declared_names(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write(
        "设定集/角色小传/林静.md",
        "---\nroles: [suspect]\naliases: [林医生]\n---\n\n# 林静\n",
        expected_digest=None,
    )
    assembled = build_assembled_context(workspace, profile="writer")
    keys = [part.key for part in assembled.parts]
    assert "glossary" in keys
    assert "林静（林医生）" in assembled.text


def test_workspace_state_root_keeps_previous_path(tmp_path: Path) -> None:
    assert workspace_state_root(tmp_path, "w1") == tmp_path / "workspaces" / "w1"
    assert conversation_root(tmp_path, "w1") == workspace_state_root(tmp_path, "w1")


def test_roster_and_timeline_partitions_reach_writer_and_review(tmp_path: Path) -> None:
    """状态分区进写作与检查两种用途。"""
    from hastings.workspace.character_states import CharacterStateStore, StateSnapshot
    from hastings.workspace.disclosure import ObservedEvidence
    from hastings.workspace.timeline import TimelineEntry, TimelineStore

    workspace = _workspace(tmp_path)
    workspace.write(
        "设定集/角色小传/林静.md",
        "---\nroles: [suspect]\n---\n\n# 林静\n",
        expected_digest=None,
    )
    chapter = workspace.read("正文/0001 雨夜.md")
    quote = "雪压住了山路。"
    start = chapter.text.index(quote)
    evidence = ObservedEvidence(
        path=chapter.path,
        digest=chapter.digest,
        start=start,
        end=start + len(quote),
        quote=quote,
    )
    person = workspace.read("设定集/角色小传/林静.md")
    CharacterStateStore(tmp_path / "state").replace(
        (
            StateSnapshot(
                entity_path=person.path,
                entity_digest=person.digest,
                chapter=1,
                location="大堂",
                alive=False,
                stance="",
                evidence=evidence,
            ),
        )
    )
    TimelineStore(tmp_path / "state").replace(
        (
            TimelineEntry(
                chapter=1, when="十月二十日深夜", span="整夜", location="旅馆", evidence=evidence
            ),
        )
    )
    stores = {
        "roster_store": CharacterStateStore(tmp_path / "state"),
        "timeline_store": TimelineStore(tmp_path / "state"),
    }

    writer = build_assembled_context(workspace, profile="writer", as_of_chapter=1, **stores)
    review = build_assembled_context(workspace, profile="review", as_of_chapter=1, **stores)

    for assembled in (writer, review):
        keys = [part.key for part in assembled.parts]
        assert "roster" in keys
        assert "timeline" in keys
        assert "已死亡" in assembled.text
        assert "十月二十日深夜" in assembled.text


def test_overdue_clue_reaches_the_writer_context(tmp_path: Path) -> None:
    """超期未回收的线索必须出现在写作上下文里，已回收的不占预算。"""
    from hastings.workspace.clues import ClueEntry, ClueStore
    from hastings.workspace.disclosure import ObservedEvidence

    workspace = _workspace(tmp_path)
    workspace.write("正文/0002 来信.md", "第二章 来信\n\n雪压住了山路。", expected_digest=None)
    chapter = workspace.read("正文/0001 雨夜.md")
    quote = "雪压住了山路。"
    start = chapter.text.index(quote)
    evidence = ObservedEvidence(
        path=chapter.path,
        digest=chapter.digest,
        start=start,
        end=start + len(quote),
        quote=quote,
    )
    ClueStore(tmp_path / "state").replace(
        (
            ClueEntry(
                clue="门锁内侧有擦痕",
                planted_chapter=1,
                due_chapter=2,
                status="open",
                supports="凶手从内侧开门",
                evidence=evidence,
            ),
            ClueEntry(
                clue="已回收的伞",
                planted_chapter=1,
                due_chapter=None,
                status="resolved",
                supports="",
                evidence=evidence,
                resolution_chapter=2,
                resolution_evidence=evidence,
            ),
        )
    )

    assembled = build_assembled_context(
        workspace, profile="writer", as_of_chapter=2, clue_store=ClueStore(tmp_path / "state")
    )

    keys = [part.key for part in assembled.parts]
    assert "clues" in keys
    assert "门锁内侧有擦痕" in assembled.text
    assert "已超期" in assembled.text
    assert "已回收的伞" not in assembled.text


def test_no_clue_partition_when_nothing_is_pending(tmp_path: Path) -> None:
    assembled = build_assembled_context(_workspace(tmp_path), profile="writer", clue_store=None)
    assert "clues" not in [part.key for part in assembled.parts]
