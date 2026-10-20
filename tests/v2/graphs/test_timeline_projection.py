"""时间线领域投影（V03-7.6）：真实事件/证词分轨、时间原样、叙述位置不冒充。

验收口径（计划 M12 时间线行）：事件按人物/地点分轨；真实事件与证词分开；
不确定时间保留原文；不用章节顺序代替故事时间，不把所指事件的真实时间当成证词时间。
"""

from __future__ import annotations

from hastings.domain.story import kinds
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.timeline import (
    GROUP_BY_PLACE,
    timeline_projection,
)


def _entry(kind: str, artifact_id: str, content: dict) -> StoryEntry:
    return StoryEntry(artifact_id=artifact_id, kind=kind, revision_id="rev_1", content=content)


def _catalog(*entries: StoryEntry) -> StoryCatalog:
    return StoryCatalog(commit_id="c1", entries=tuple(entries))


def _beat(beat_id: str, order_key: str) -> StoryEntry:
    return _entry(
        kinds.KIND_BEAT,
        beat_id,
        {"beat_id": beat_id, "title": beat_id, "order_key": order_key},
    )


def test_without_events_reports_gap() -> None:
    graph = timeline_projection(_catalog(_entry(kinds.KIND_CHARACTER, "ch1", {"name": "姐姐"})))
    assert graph.events == ()
    assert graph.tracks == ()
    assert [gap.code for gap in graph.gaps] == ["TIMELINE_NO_EVENTS"]


def test_events_split_by_character_and_place_without_reordering() -> None:
    catalog = _catalog(
        _entry(kinds.KIND_CHARACTER, "ch1", {"name": "姐姐"}),
        _entry(kinds.KIND_CHARACTER, "ch2", {"name": "侦探"}),
        _entry(kinds.KIND_PLACE, "p1", {"name": "书房"}),
        _entry(
            kinds.KIND_TIMELINE_EVENT,
            "e2",
            {"time": "晚上九点", "place_id": "p1", "participants": ["ch1", "ch2"]},
        ),
        _entry(
            kinds.KIND_TIMELINE_EVENT,
            "e1",
            {"time": "八点到九点之间", "place_id": "p1", "participants": ["ch1"]},
        ),
    )
    by_character = timeline_projection(catalog)
    assert [track.key for track in by_character.tracks] == ["character:ch1", "character:ch2"]
    # 账本顺序（按 artifact_id 稳定）而不是时间或章节顺序。
    assert by_character.tracks[0].event_ids == ("e1", "e2")
    assert by_character.events[0].time_text == "八点到九点之间"  # 区间原文保留
    assert by_character.events[0].time_status == "recorded"

    by_place = timeline_projection(catalog, group_by=GROUP_BY_PLACE)
    assert [track.key for track in by_place.tracks] == ["place:p1"]
    assert by_place.tracks[0].event_ids == ("e1", "e2")


def test_missing_place_and_participants_are_gaps_not_guesses() -> None:
    graph = timeline_projection(
        _catalog(
            _entry(kinds.KIND_TIMELINE_EVENT, "e1", {"time": "时间未定"}),
        )
    )
    codes = [gap.code for gap in graph.gaps]
    assert "TIMELINE_PLACE_NOT_RECORDED" in codes
    assert "TIMELINE_PARTICIPANTS_NOT_RECORDED" in codes
    assert graph.tracks == ()


def test_testimony_keeps_mode_and_does_not_borrow_true_event_time() -> None:
    graph = timeline_projection(
        _catalog(
            _beat("b1", "1"),
            _beat("b2", "2"),
            _entry(kinds.KIND_CHARACTER, "ch1", {"name": "姐姐"}),
            _entry(kinds.KIND_PLACE, "p1", {"name": "书房"}),
            _entry(
                kinds.KIND_TIMELINE_EVENT,
                "e1",
                {"time": "晚上九点", "place_id": "p1", "participants": ["ch1"]},
            ),
            _entry(kinds.KIND_TRUTH_FACT, "f1", {"statement": "九点到达", "event_id": "e1"}),
            _entry(
                kinds.KIND_KNOWLEDGE,
                "k1",
                {
                    "character_id": "ch1",
                    "fact_id": "f1",
                    "mode": "suspects",
                    "note": "她说自己九点到",
                    "at_beat_id": "b2",
                },
            ),
        )
    )
    track = graph.testimonies[0]
    item = track.items[0]
    assert track.character_id == "ch1"
    assert item.mode == "suspects"
    assert item.mode_label == "怀疑"  # 证词保留原类型，不升级为真相
    assert item.note == "她说自己九点到"
    assert item.position == 1  # 叙述位置来自获知节拍 b2，不是章节序号
    assert item.claimed_event_id == "e1"
    assert item.claimed_event_time == "晚上九点"  # 单独标注为所指事件的真实时间


def test_testimony_without_event_or_valid_position_reports_gap() -> None:
    graph = timeline_projection(
        _catalog(
            _entry(kinds.KIND_CHARACTER, "ch1", {"name": "姐姐"}),
            _entry(kinds.KIND_TRUTH_FACT, "f1", {"statement": "没有事件的断言"}),
            _entry(
                kinds.KIND_KNOWLEDGE,
                "k1",
                {
                    "character_id": "ch1",
                    "fact_id": "f1",
                    "mode": "misbelieves",
                    "at_beat_id": "b_missing",
                },
            ),
        )
    )
    codes = [gap.code for gap in graph.gaps]
    assert "TIMELINE_TESTIMONY_EVENT_NOT_RECORDED" in codes
    assert "TIMELINE_TESTIMONY_POSITION_UNRESOLVED" in codes
    assert graph.testimonies[0].items[0].position is None


def test_focus_narrows_display_without_changing_event_facts() -> None:
    catalog = _catalog(
        _entry(kinds.KIND_CHARACTER, "ch1", {"name": "姐姐"}),
        _entry(kinds.KIND_CHARACTER, "ch2", {"name": "侦探"}),
        _entry(kinds.KIND_PLACE, "p1", {"name": "书房"}),
        _entry(kinds.KIND_PLACE, "p2", {"name": "走廊"}),
        _entry(
            kinds.KIND_TIMELINE_EVENT,
            "e1",
            {"time": "九点", "place_id": "p1", "participants": ["ch1"]},
        ),
        _entry(
            kinds.KIND_TIMELINE_EVENT,
            "e2",
            {"time": "十点", "place_id": "p2", "participants": ["ch2"]},
        ),
    )
    full = timeline_projection(catalog)
    focused = timeline_projection(catalog, focus="ch1")
    assert [track.id for track in focused.tracks] == ["ch1"]
    assert [event.event_id for event in focused.events] == ["e1"]
    # 同一事件在两种视图里的内容完全一致（筛选不改事实）。
    assert focused.events[0] == next(e for e in full.events if e.event_id == "e1")

    by_event = timeline_projection(catalog, focus="e2")
    assert [track.id for track in by_event.tracks] == ["ch2"]

    try:
        timeline_projection(catalog, focus="missing")
    except StoryViolation as exc:
        assert exc.code == "STORY_UNKNOWN_ARTIFACT"
    else:  # pragma: no cover
        raise AssertionError("未知聚焦对象应被拒绝")
