"""T13.4a 结构顺序测试：order_key 优先、ID 兜底，顺序不依赖字典遍历。

关键断言：同 order_key 同级冲突给出可观测警告且顺序仍确定；父级缺失的孤儿
对象进入根层并告警；跨父级同 key 不算冲突。
"""

from __future__ import annotations

from collections.abc import Callable

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.narrative.models import Beat, NarrativeNode
from hastings.domain.story.narrative.order import ordered_beats, sort_key, structural_order

MakeCatalog = Callable[..., StoryCatalog]


def _node(
    artifact_id: str, kind: str, *, parent_id: str | None = None, order_key: str = ""
) -> tuple:
    return (
        artifact_id,
        kind,
        NarrativeNode(
            node_id=artifact_id, kind=kind, parent_id=parent_id, order_key=order_key
        ).to_content(),
    )


def _beat(artifact_id: str, *, parent_id: str, order_key: str = "") -> tuple:
    return (
        artifact_id,
        kinds.KIND_BEAT,
        Beat(beat_id=artifact_id, parent_id=parent_id, order_key=order_key).to_content(),
    )


def _tree() -> list[tuple]:
    return [
        _node("art_series", kinds.KIND_SERIES),
        _node("art_book", kinds.KIND_BOOK, parent_id="art_series"),
        _node("art_arc", kinds.KIND_ARC, parent_id="art_book"),
        _node("art_ch1", kinds.KIND_CHAPTER, parent_id="art_arc", order_key="010"),
        _node("art_ch2", kinds.KIND_CHAPTER, parent_id="art_arc", order_key="020"),
        _node("art_sc1", kinds.KIND_SCENE, parent_id="art_ch1"),
        _beat("art_b2", parent_id="art_sc1", order_key="020"),
        _beat("art_b1", parent_id="art_sc1", order_key="010"),
    ]


def test_structural_order_is_depth_first_by_order_key(make_catalog: MakeCatalog) -> None:
    ordering = structural_order(make_catalog(_tree()))
    assert [e.artifact_id for e in ordering.entries] == [
        "art_series",
        "art_book",
        "art_arc",
        "art_ch1",
        "art_sc1",
        "art_b1",
        "art_b2",
        "art_ch2",
    ]
    assert [e.artifact_id for e in ordering.beats] == ["art_b1", "art_b2"]
    assert ordering.warnings == ()


def test_ordered_beats_matches_structural_order(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(_tree())
    assert [e.artifact_id for e in ordered_beats(catalog)] == ["art_b1", "art_b2"]


def test_tie_break_by_id_is_deterministic(make_catalog: MakeCatalog) -> None:
    entries = [
        _node("art_arc", kinds.KIND_ARC),
        _node("art_chB", kinds.KIND_CHAPTER, parent_id="art_arc", order_key="010"),
        _node("art_chA", kinds.KIND_CHAPTER, parent_id="art_arc", order_key="010"),
    ]
    ordering = structural_order(make_catalog(entries))
    ids = [e.artifact_id for e in ordering.entries]
    assert ids == ["art_arc", "art_chA", "art_chB"]  # 同 key 按 ID 升序
    assert [w.code for w in ordering.warnings] == [ec.STORY_INVALID_COMMAND]


def test_same_order_key_across_parents_is_not_conflict(make_catalog: MakeCatalog) -> None:
    entries = [
        _node("art_arc", kinds.KIND_ARC),
        _node("art_ch1", kinds.KIND_CHAPTER, parent_id="art_arc", order_key="010"),
        _node("art_ch2", kinds.KIND_CHAPTER, parent_id="art_arc", order_key="020"),
        _node("art_sc1", kinds.KIND_SCENE, parent_id="art_ch1", order_key="010"),
    ]
    ordering = structural_order(make_catalog(entries))
    assert ordering.warnings == ()


def test_orphan_parent_goes_to_root_with_warning(make_catalog: MakeCatalog) -> None:
    entries = [
        _node("art_arc", kinds.KIND_ARC),
        _beat("art_b1", parent_id="art_missing", order_key="010"),
    ]
    ordering = structural_order(make_catalog(entries))
    assert [e.artifact_id for e in ordering.entries] == ["art_b1", "art_arc"]
    assert [w.code for w in ordering.warnings] == [ec.STORY_MISSING_PRECONDITION]


def test_unsorted_entries_come_after_sorted(make_catalog: MakeCatalog) -> None:
    entries = [
        _node("art_arc", kinds.KIND_ARC),
        _node("art_ch2", kinds.KIND_CHAPTER, parent_id="art_arc", order_key="020"),
        _node("art_ch1", kinds.KIND_CHAPTER, parent_id="art_arc", order_key="010"),
        _node("art_ch9", kinds.KIND_CHAPTER, parent_id="art_arc", order_key=""),
    ]
    ordering = structural_order(make_catalog(entries))
    assert [e.artifact_id for e in ordering.entries] == [
        "art_arc",
        "art_ch1",
        "art_ch2",
        "art_ch9",
    ]


def test_sort_key_is_total() -> None:
    from hastings.domain.story.catalog import StoryEntry

    def entry(artifact_id: str, order_key: str) -> StoryEntry:
        return StoryEntry(
            artifact_id=artifact_id,
            kind=kinds.KIND_BEAT,
            revision_id="r",
            content={"order_key": order_key},
        )

    assert sort_key(entry("art_a", "010")) < sort_key(entry("art_b", "020"))
    assert sort_key(entry("art_a", "010")) < sort_key(entry("art_z", "010"))
    assert sort_key(entry("art_a", "010")) < sort_key(entry("art_a", ""))
