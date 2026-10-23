"""人物状态（0.6 V06-E4）。

契约：状态只能从原文核对出来，带证据；源文件一改就失效，失效即不注入并说明退回到哪一章。
写第 N 章时只该看到「截至第 N 章」的状态，不能让第 9 章的死亡出现在第 7 章的上下文里。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.character_states import (
    CharacterStateStore,
    StateSnapshot,
    build_roster,
    publish_states,
    roster_section,
    roster_view,
)
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace

_LIN = """---
roles: [suspect]
surface: 被害人的主治医师
---

# 林静

手术灯下站了整夜。
"""

_ZHOU = """---
roles: [assistant]
---

# 周岚

她把伞收在门口。
"""


_CHAPTERS = ("雨夜", "来信", "旧怨")


def _workspace(tmp_path: Path, *, chapters: int = 3) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    for index, name in enumerate(_CHAPTERS[:chapters], start=1):
        workspace.write(
            f"正文/{index:04d} {name}.md",
            f"第{index}章 {name}\n\n雪还在下。",
            expected_digest=None,
        )
    workspace.write("设定集/角色小传/林静.md", _LIN, expected_digest=None)
    workspace.write("设定集/角色小传/周岚.md", _ZHOU, expected_digest=None)
    return workspace


def _snapshot(
    workspace: Workspace,
    *,
    person: str = "林静",
    chapter: int = 3,
    location: str = "书房",
    alive: bool = True,
    stance: str = "",
    quote: str = "雪还在下。",
) -> StateSnapshot:
    entity = f"设定集/角色小传/{person}.md"
    source = workspace.read(f"正文/{chapter:04d} {_CHAPTERS[chapter - 1]}.md")
    start = source.text.index(quote)
    return StateSnapshot(
        entity_path=entity,
        entity_digest=workspace.read(entity).digest,
        chapter=chapter,
        location=location,
        alive=alive,
        stance=stance,
        evidence=ObservedEvidence(
            path=source.path,
            digest=source.digest,
            start=start,
            end=start + len(quote),
            quote=quote,
        ),
    )


def test_latest_state_before_current_chapter_wins(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = CharacterStateStore(tmp_path / "state")
    publish_states(
        workspace,
        store,
        (
            _snapshot(workspace, chapter=1, location="书房"),
            _snapshot(workspace, chapter=3, location="车库", stance="对佐藤隐瞒"),
        ),
    )

    roster = build_roster(workspace, store)
    lin = next(item for item in roster.characters if item.name == "林静")

    assert lin.current is not None
    assert (lin.current.chapter, lin.current.location, lin.current.stance) == (
        3,
        "车库",
        "对佐藤隐瞒",
    )
    assert lin.lag == 0
    assert "林静" in roster_section(roster)
    assert "车库" in roster_section(roster)


def test_state_of_deleted_chapter_is_not_visible(tmp_path: Path) -> None:
    """第 3 章被删掉后，那一章的状态不再可见：宁可不注入，也不拿它当当前状态。"""
    seed = _workspace(tmp_path / "seed")
    store = CharacterStateStore(tmp_path / "state")
    store.replace((_snapshot(seed, chapter=3, alive=False, location="车库"),))
    workspace = _workspace(tmp_path, chapters=2)

    roster = build_roster(workspace, store)
    lin = next(item for item in roster.characters if item.name == "林静")

    assert lin.current is None
    assert lin.dead is False
    assert lin.stale is True
    assert roster_section(roster) == ""


def test_changed_chapter_invalidates_its_state(tmp_path: Path) -> None:
    """第 3 章被改过：该章状态失效，退回第 1 章并说明落后。"""
    workspace = _workspace(tmp_path)
    store = CharacterStateStore(tmp_path / "state")
    publish_states(
        workspace,
        store,
        (
            _snapshot(workspace, chapter=1, location="书房"),
            _snapshot(workspace, chapter=3, location="车库"),
        ),
    )
    chapter = workspace.read("正文/0003 旧怨.md")
    workspace.write(
        "正文/0003 旧怨.md", chapter.text + "\n门外有人。", expected_digest=chapter.digest
    )

    roster = build_roster(workspace, store)
    lin = next(item for item in roster.characters if item.name == "林静")

    assert lin.current is not None
    assert lin.current.chapter == 1
    assert lin.stale is True
    assert lin.lag == 2
    assert any("失效" in warning for warning in lin.warnings)
    assert any("落后" in warning or "没有核对记录" in warning for warning in lin.warnings)


def test_evidence_must_match_source_range(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = CharacterStateStore(tmp_path / "state")
    good = _snapshot(workspace)
    wrong = StateSnapshot(
        entity_path=good.entity_path,
        entity_digest=good.entity_digest,
        chapter=good.chapter,
        location="车库",
        alive=True,
        stance="",
        evidence=ObservedEvidence(
            path=good.evidence.path,
            digest=good.evidence.digest,
            start=good.evidence.start,
            end=good.evidence.end,
            quote="雪停了。",
        ),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        publish_states(workspace, store, (wrong,))
    assert excinfo.value.code == "STATE_EVIDENCE_INVALID"
    assert store.entries() == ()


def test_state_must_be_on_a_character_sheet(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = CharacterStateStore(tmp_path / "state")
    good = _snapshot(workspace)
    misplaced = StateSnapshot(
        entity_path="设定集/线索.md",
        entity_digest=good.entity_digest,
        chapter=1,
        location="车库",
        alive=True,
        stance="",
        evidence=good.evidence,
    )
    with pytest.raises(WorkspaceError) as excinfo:
        publish_states(workspace, store, (misplaced,))
    assert excinfo.value.code == "STATE_ENTITY_INVALID"


def test_empty_location_and_stance_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = CharacterStateStore(tmp_path / "state")
    with pytest.raises(WorkspaceError) as excinfo:
        publish_states(workspace, store, (_snapshot(workspace, location="", stance=""),))
    assert excinfo.value.code == "STATE_EVIDENCE_INVALID"


def test_dead_characters_come_first_and_are_dropped_last(tmp_path: Path) -> None:
    """预算不够时先丢活着的人：已死角色出现在场景里是最硬的错误。"""
    workspace = _workspace(tmp_path)
    store = CharacterStateStore(tmp_path / "state")
    publish_states(
        workspace,
        store,
        (
            _snapshot(workspace, person="林静", chapter=1, location="书房"),
            _snapshot(workspace, person="周岚", chapter=1, alive=False, location="山下"),
        ),
    )
    roster = build_roster(workspace, store)
    section = roster_section(roster)
    assert section.index("周岚") < section.index("林静")

    tight = roster_section(roster, budget=120)
    assert "已死亡" in tight
    assert "林静" not in tight
    assert "还有 1 人未列入" in tight


def test_view_separates_unknown_from_known(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = CharacterStateStore(tmp_path / "state")
    publish_states(workspace, store, (_snapshot(workspace, chapter=2),))

    view = roster_view(build_roster(workspace, store))
    rows = {row["name"]: row for row in view["characters"]}  # type: ignore[index]

    assert rows["林静"]["chapter"] == 2
    assert rows["林静"]["lag"] == 1
    assert rows["周岚"]["chapter"] is None
