"""v1.0.7 P3：只读故事时间轴。

发生时间与披露章节分开：同一个横轴只收相对分钟，纵向分组来自真实章序或计划章节。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from hastings.workspace.files import Workspace
from hastings.workspace.story_models import (
    ProseBinding,
    StoryCluePlan,
    StoryEventSpan,
    StoryNode,
    StoryVariant,
)
from hastings.workspace.story_timeline import build_timeline


def book(tmp_path: Path, chapters: dict[str, str]) -> Workspace:
    workspace = Workspace.create(tmp_path / "book")
    for name, text in chapters.items():
        workspace.write(f"正文/{name}", text, expected_digest=None)
    return workspace


def binding(
    workspace: Workspace, path: str, node_id: str, quote: str, binding_id: str
) -> ProseBinding:
    view = workspace.read(path)
    start = view.text.find(quote)
    assert start >= 0
    return ProseBinding(
        id=binding_id,
        node_id=node_id,
        document_id="doc_" + hashlib.sha256(path.encode()).hexdigest()[:24],
        path=path,
        digest=view.digest,
        span_digest=hashlib.sha256(quote.encode()).hexdigest(),
        start=start,
        end=start + len(quote),
        quote=quote,
    )


def test_disclosure_chapter_orders_groups_not_event_time(tmp_path: Path) -> None:
    workspace = book(
        tmp_path,
        {
            "0001 现在.md": "# 第一章\n\n雨夜的码头空着。\n",
            "0003 回溯.md": "# 第三章\n\n二十年前的婚礼。\n",
        },
    )
    variant = StoryVariant(
        id="main",
        name="主方案",
        revision="rev-1",
        time_origin_label="案发当天 00:00",
        clue_plans={"clock": StoryCluePlan(clue_id="clock", kind="truth")},
        nodes={
            "present": StoryNode(
                id="present",
                title="现在的码头",
                action="抵达码头",
                event_span=StoryEventSpan(start_minute=600, end_minute=610),
            ),
            "past": StoryNode(
                id="past",
                title="二十年前",
                action="婚礼",
                event_span=StoryEventSpan(start_minute=-525600, end_minute=-525000),
                reveal_order=0,
            ),
        },
        bindings={
            "b1": binding(workspace, "正文/0001 现在.md", "present", "雨夜的码头空着", "b1"),
            "b2": binding(workspace, "正文/0003 回溯.md", "past", "二十年前的婚礼", "b2"),
        },
    )

    view = build_timeline(workspace, variant)

    assert [group.chapter_number for group in view.groups] == [1, 3]
    assert [group.chapter_kind for group in view.groups] == ["actual", "actual"]
    assert view.groups[0].nodes[0].node_id == "present"
    assert view.groups[1].nodes[0].node_id == "past"
    assert view.domain is not None
    assert view.domain.min < -525000
    assert view.domain.max > 610


def test_same_chapter_nodes_follow_reveal_order_and_planned_chapters(tmp_path: Path) -> None:
    workspace = book(tmp_path, {"0002 夜谈.md": "# 第二章\n\n他先开口。\n\n她后开口。\n"})
    variant = StoryVariant(
        id="main",
        name="主方案",
        revision="rev-1",
        nodes={
            "second": StoryNode(
                id="second",
                title="她后开口",
                action="回答",
                reveal_order=1,
                planned_chapter_number=2,
            ),
            "first": StoryNode(
                id="first",
                title="他先开口",
                action="提问",
                reveal_order=0,
                planned_chapter_number=2,
            ),
            "planned": StoryNode(
                id="planned", title="还没写", action="计划中的对峙", planned_chapter_number=5
            ),
            "floating": StoryNode(id="floating", title="未安排", action="尚未定位"),
        },
        bindings={
            "b1": binding(workspace, "正文/0002 夜谈.md", "first", "他先开口", "b1"),
            "b2": binding(workspace, "正文/0002 夜谈.md", "second", "她后开口", "b2"),
        },
    )

    view = build_timeline(workspace, variant)

    assert [group.chapter_number for group in view.groups] == [2, 5, None]
    assert [group.chapter_kind for group in view.groups] == ["actual", "planned", "unassigned"]
    assert [row.node_id for row in view.groups[0].nodes] == ["first", "second"]
    assert view.groups[1].nodes[0].node_id == "planned"
    assert view.groups[2].nodes[0].node_id == "floating"


def test_unplaced_time_stays_visible_without_a_domain(tmp_path: Path) -> None:
    workspace = book(tmp_path, {"0001 雨夜.md": "# 第一章\n\n有人在等。\n"})
    variant = StoryVariant(
        id="main",
        name="主方案",
        revision="rev-1",
        nodes={
            "later": StoryNode(
                id="later",
                title="晚餐后",
                action="等人",
                time_note="晚餐后",
                planned_chapter_number=1,
            )
        },
        bindings={"b1": binding(workspace, "正文/0001 雨夜.md", "later", "有人在等", "b1")},
    )

    view = build_timeline(workspace, variant)

    row = view.groups[0].nodes[0]
    assert (row.start_minute, row.end_minute) == (None, None)
    assert row.time_note == "晚餐后"
    assert view.domain is None


def test_single_instant_gets_a_readable_domain_but_stays_an_instant(tmp_path: Path) -> None:
    workspace = book(tmp_path, {"0001 雨夜.md": "# 第一章\n\n钟停了。\n"})
    variant = StoryVariant(
        id="main",
        name="主方案",
        revision="rev-1",
        nodes={
            "stop": StoryNode(
                id="stop",
                title="停钟",
                action="看钟",
                event_span=StoryEventSpan(start_minute=-10, end_minute=-10),
                planned_chapter_number=1,
            )
        },
        bindings={"b1": binding(workspace, "正文/0001 雨夜.md", "stop", "钟停了", "b1")},
    )

    view = build_timeline(workspace, variant)

    row = view.groups[0].nodes[0]
    assert (row.start_minute, row.end_minute) == (-10, -10)
    assert (view.domain.min, view.domain.max) == (-11, -9)


def test_cross_midnight_is_continuous_minutes(tmp_path: Path) -> None:
    workspace = book(tmp_path, {"0001 雨夜.md": "# 第一章\n\n前一天的深夜。\n"})
    variant = StoryVariant(
        id="main",
        name="主方案",
        revision="rev-1",
        time_origin_label="案发当天 00:00",
        nodes={
            "before": StoryNode(
                id="before",
                title="前一天 23:50",
                action="离港",
                event_span=StoryEventSpan(start_minute=-10, end_minute=-10),
                planned_chapter_number=1,
                reveal_order=0,
            ),
            "after": StoryNode(
                id="after",
                title="当天 00:10",
                action="登船",
                event_span=StoryEventSpan(start_minute=10, end_minute=10),
                planned_chapter_number=1,
                reveal_order=1,
            ),
        },
        bindings={"b1": binding(workspace, "正文/0001 雨夜.md", "before", "前一天的深夜", "b1")},
    )

    view = build_timeline(workspace, variant)

    rows = view.groups[0].nodes
    assert [row.start_minute for row in rows] == [-10, 10]
    assert view.domain is not None
    assert view.domain.max - view.domain.min > 20


def test_lost_anchor_makes_the_node_need_attention(tmp_path: Path) -> None:
    workspace = book(tmp_path, {"0001 雨夜.md": "# 第一章\n\n有人在等。\n"})
    variant = StoryVariant(
        id="main",
        name="主方案",
        revision="rev-1",
        nodes={"n1": StoryNode(id="n1", title="码头", action="等人")},
        bindings={"b1": binding(workspace, "正文/0001 雨夜.md", "n1", "有人在等", "b1")},
    )
    workspace.write(
        "正文/0001 雨夜.md",
        "# 第一章\n\n码头空了。\n",
        expected_digest=workspace.read("正文/0001 雨夜.md").digest,
    )

    view = build_timeline(workspace, variant)

    row = view.groups[0].nodes[0]
    assert row.status == "attention"
    assert "anchor_missing" in row.reasons
    assert row.binding_ids == ["b1"]
