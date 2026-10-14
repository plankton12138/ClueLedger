"""T13.2 稳定 ID 与显示名索引测试。

关键断言：同名角色允许共存但按名定位必须消歧；改名不改变 ID；确定性 ID 可复现。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Character
from hastings.domain.story.catalog import StoryCatalog, StoryEntry, display_name
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.ids import deterministic_story_id

MakeCatalog = Callable[..., StoryCatalog]


def _char(artifact_id: str, name: str) -> tuple[str, str, dict[str, object]]:
    return (artifact_id, kinds.KIND_CHARACTER, Character(name=name).to_content())


def test_catalog_filters_non_story_kinds(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _char("art_c1", "绫辻行人"),
            ("art_m1", "manuscript.chapter", {"text": "正文"}),
        ]
    )
    assert [e.artifact_id for e in catalog.entries] == ["art_c1"]
    assert catalog.get("art_m1") is None


def test_resolve_name_unique_returns_id(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻行人")])
    assert catalog.resolve_name(kinds.KIND_CHARACTER, "绫辻行人", where="test") == "art_c1"


def test_same_name_two_ids_requires_choice(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog([_char("art_c1", "同名"), _char("art_c2", "同名")])
    with pytest.raises(StoryViolation) as exc:
        catalog.resolve_name(kinds.KIND_CHARACTER, "同名", where="cmd")
    assert exc.value.code == ec.STORY_AMBIGUOUS_NAME
    assert exc.value.details["candidates"] == ["art_c1", "art_c2"]


def test_resolve_missing_name_reports_unknown(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻行人")])
    with pytest.raises(StoryViolation) as exc:
        catalog.resolve_name(kinds.KIND_CHARACTER, "不存在", where="cmd")
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_rename_keeps_id_and_changes_resolvable_name(make_catalog: MakeCatalog) -> None:
    before = make_catalog([_char("art_c1", "旧名")])
    after = make_catalog([_char("art_c1", "新名")])

    assert before.resolve_name(kinds.KIND_CHARACTER, "旧名", where="t") == "art_c1"
    # 改名后同一 ID 仍有效，只是显示名解析指向新名
    assert after.resolve_name(kinds.KIND_CHARACTER, "新名", where="t") == "art_c1"
    with pytest.raises(StoryViolation):
        after.resolve_name(kinds.KIND_CHARACTER, "旧名", where="t")


def test_require_unknown_artifact_raises(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻行人")])
    with pytest.raises(StoryViolation) as exc:
        catalog.require("art_missing", where="cmd")
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_display_name_per_kind() -> None:
    assert display_name(kinds.KIND_CHARACTER, {"name": "X"}) == "X"
    assert display_name(kinds.KIND_RELATIONSHIP, {"relation_type": "r"}) == ""
    assert display_name(kinds.KIND_CLUE, {"statement": "门锁划痕"}) == "门锁划痕"


def test_deterministic_story_id_is_stable_and_distinct() -> None:
    a = deterministic_story_id(kinds.KIND_CHARACTER, "characters/aki.yaml")
    b = deterministic_story_id(kinds.KIND_CHARACTER, "characters/aki.yaml")
    c = deterministic_story_id(kinds.KIND_CHARACTER, "characters/other.yaml")
    assert a == b
    assert a != c
    assert a.startswith("art_")


def test_entry_decode_returns_model(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻行人")])
    entry = catalog.require("art_c1", where="t")
    assert isinstance(entry, StoryEntry)
    decoded = entry.decode()
    assert isinstance(decoded, Character)
    assert decoded.name == "绫辻行人"
