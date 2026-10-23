"""身份披露状态（0.6 V06-E2）。

契约：身份按章节生效——只有正文已经写到的披露才进写作上下文；计划披露只给作者看。
坏条目单独作废并给诊断，同一字段的其他条目照常生效。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.disclosure import (
    DisclosureStore,
    ObservedEvidence,
    ObservedFact,
    build_disclosure_state,
    publish_observed,
    state_section,
    state_view,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.metadata import parse_character

_LIN = """---
roles: [suspect]
surface: 被害人的主治医师
aliases: [林医生]
revealed: [3:主治医师, 6:与死者有旧怨]
planned: [9:凶手]
---

# 林静

手术灯下站了整夜。
"""


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    for index, name in enumerate(("雨夜", "来信", "旧怨"), start=1):
        workspace.write(
            f"正文/{index:04d} {name}.md", f"第{index}章 {name}\n\n雪还在下。", expected_digest=None
        )
    workspace.write("设定集/角色小传/林静.md", _LIN, expected_digest=None)
    return workspace


def test_character_metadata_parses_identity_fields() -> None:
    meta = parse_character("设定集/角色小传/林静.md", _LIN)
    assert meta.surface == "被害人的主治医师"
    assert meta.aliases == ("林医生",)
    assert [(item.chapter, item.fact) for item in meta.revealed] == [
        (3, "主治医师"),
        (6, "与死者有旧怨"),
    ]
    assert [(item.chapter, item.fact) for item in meta.planned] == [(9, "凶手")]
    assert meta.warnings == ()


def test_broken_fact_entry_is_skipped_without_dropping_the_field() -> None:
    text = _LIN.replace("revealed: [3:主治医师, 6:与死者有旧怨]", "revealed: [3:主治医师, 缺章号]")
    meta = parse_character("设定集/角色小传/林静.md", text)
    assert [(item.chapter, item.fact) for item in meta.revealed] == [(3, "主治医师")]
    assert any("格式不对" in warning for warning in meta.warnings)


def test_only_revealed_facts_enter_the_writing_context(tmp_path: Path) -> None:
    state = build_disclosure_state(_workspace(tmp_path))
    assert state.current_chapter == 3
    entity = state.entities[0]
    assert entity.revealed == (entity.revealed[0],)
    assert entity.revealed[0].fact == "主治医师"
    # 第 6 章还没写出来：声明保留但暂不生效，也不注入。
    assert [item.fact for item in entity.upcoming] == ["与死者有旧怨"]
    assert any("只写到第 3 章" in warning for warning in entity.warnings)

    section = state_section(state)
    assert "主治医师" in section
    assert "与死者有旧怨" not in section
    assert "凶手" not in section


def test_planned_fact_never_appears_in_the_writing_context(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write("正文/0004 摊牌.md", "第四章 摊牌\n\n她说出了那个名字。", expected_digest=None)
    state = build_disclosure_state(workspace)
    # 第 4 章已写出：第 3、6 章的披露按当前章节算，第 6 章仍未到。
    assert state.current_chapter == 4
    section = state_section(state)
    assert "主治医师" in section
    assert "凶手" not in section


def test_empty_state_produces_no_section(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "空白")
    workspace.write("正文/0001 起.md", "第一章 起\n\n开始。", expected_digest=None)
    assert state_section(build_disclosure_state(workspace)) == ""


def test_alias_collision_is_reported(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    other = (
        _LIN.replace("林医生", "林医生")
        .replace("林静", "周岚")
        .replace("suspect", "witness_placeholder")
    )
    other = other.replace("witness_placeholder", "suspect")
    workspace.write("设定集/角色小传/周岚.md", other, expected_digest=None)
    state = build_disclosure_state(workspace)
    assert [conflict.kind for conflict in state.conflicts].count("alias_collision") == 2


def test_two_culprits_are_reported(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    culprit = _LIN.replace("roles: [suspect]", "roles: [culprit]")
    workspace.write(
        "设定集/角色小传/林静.md",
        culprit,
        expected_digest=workspace.read("设定集/角色小传/林静.md").digest,
    )
    second = culprit.replace("林静", "周岚").replace("[林医生]", "[周老板]")
    workspace.write("设定集/角色小传/周岚.md", second, expected_digest=None)
    state = build_disclosure_state(workspace)
    kinds = [conflict.kind for conflict in state.conflicts]
    assert kinds.count("duplicate_culprit") == 2


def test_same_fact_declared_twice_is_reported(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    text = _LIN.replace("planned: [9:凶手]", "planned: [9:主治医师]")
    workspace.write(
        "设定集/角色小传/林静.md",
        text,
        expected_digest=workspace.read("设定集/角色小传/林静.md").digest,
    )
    state = build_disclosure_state(workspace)
    assert [conflict.kind for conflict in state.conflicts] == ["fact_redeclared"]


def test_view_separates_revealed_upcoming_and_planned(tmp_path: Path) -> None:
    view = state_view(build_disclosure_state(_workspace(tmp_path)))
    entity = view["entities"][0]
    assert entity["name"] == "林静"
    assert [item["fact"] for item in entity["revealed"]] == ["主治医师"]
    assert [item["fact"] for item in entity["upcoming"]] == ["与死者有旧怨"]
    assert [item["fact"] for item in entity["planned"]] == ["凶手"]
    assert entity["overdue"] is False
    assert view["conflicts"] == []


def _observed(workspace: Workspace, *, quote: str, chapter: int = 3, fact: str = "主治医师"):
    source = workspace.read("正文/0003 旧怨.md")
    start = source.text.index(quote)
    return ObservedFact(
        entity_path="设定集/角色小传/林静.md",
        entity_digest=workspace.read("设定集/角色小传/林静.md").digest,
        fact=fact,
        chapter=chapter,
        evidence=ObservedEvidence(
            path=source.path,
            digest=source.digest,
            start=start,
            end=start + len(quote),
            quote=quote,
        ),
    )


def test_published_fact_is_marked_as_verified(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = DisclosureStore(tmp_path / "state")
    publish_observed(workspace, store, (_observed(workspace, quote="雪还在下。"),))

    state = build_disclosure_state(workspace, store)
    entity = state.entities[0]
    assert [(item.fact, item.source) for item in entity.revealed] == [("主治医师", "text")]
    assert entity.verified == 1
    assert entity.record_status == "valid"
    # 声明与核对不重复注入：这条事实只出现一次。
    assert state_section(state).count("主治医师（第3章起）") == 1


def test_stale_record_falls_back_to_declaration(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = DisclosureStore(tmp_path / "state")
    publish_observed(workspace, store, (_observed(workspace, quote="雪还在下。"),))

    chapter = workspace.read("正文/0003 旧怨.md")
    workspace.write(
        "正文/0003 旧怨.md",
        chapter.text + "\n门外有人。",
        expected_digest=chapter.digest,
    )

    state = build_disclosure_state(workspace, store)
    entity = state.entities[0]
    assert entity.record_status == "stale"
    assert [item.source for item in entity.revealed] == ["declared"]
    assert any("核对记录已失效" in warning for warning in entity.warnings)


def test_quote_must_match_the_source_range(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = DisclosureStore(tmp_path / "state")
    fact = _observed(workspace, quote="雪还在下。")
    wrong = ObservedFact(
        entity_path=fact.entity_path,
        entity_digest=fact.entity_digest,
        fact=fact.fact,
        chapter=fact.chapter,
        evidence=ObservedEvidence(
            path=fact.evidence.path,
            digest=fact.evidence.digest,
            start=fact.evidence.start,
            end=fact.evidence.end,
            quote="雪停了。",
        ),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        publish_observed(workspace, store, (wrong,))
    assert excinfo.value.code == "STATE_EVIDENCE_INVALID"
    assert store.entries() == ()


def test_source_change_blocks_publication(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = DisclosureStore(tmp_path / "state")
    fact = _observed(workspace, quote="雪还在下。")
    chapter = workspace.read("正文/0003 旧怨.md")
    workspace.write(
        "正文/0003 旧怨.md", chapter.text + "\n风停了。", expected_digest=chapter.digest
    )
    with pytest.raises(WorkspaceError) as excinfo:
        publish_observed(workspace, store, (fact,))
    assert excinfo.value.code == "STATE_SOURCE_STALE_CONFLICT"
