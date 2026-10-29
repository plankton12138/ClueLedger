"""统一章节索引（0.8 E）。

契约：

- 一章一份投影，保存摘要、事件观察、证据与时点；同章发布原子替换，重建一章只替换该章贡献。
- 观察保留表达方式（叙述／证词／回忆／推测／梦／未知），不因为出现在正文里就升级成事实；
  人物与作者事件的引用必须是完整模块身份，不同作品的同名人物不能混在一起。
- 两个视图各说各的：章节视图按章序与原文区间，故事视图只对可证明的先后排序，未知与
  矛盾分组显示，不生成看似有效的全序。
- 旧章节摘要与时间锚点只读兼容；某一章重新发布后按章切换，不全量重建。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.story_index import (
    MAX_EVENTS_PER_CHAPTER,
    ChapterProjection,
    EventObservation,
    StoryIndexStore,
    StoryTime,
    build_observation_id,
    validate_projection,
)

_CHAPTER_THREE = "正文/0003 书房里的脚印.md"
_TEXT_THREE = "# 书房\n\n侦探在书房发现一枚脚印。\n\n林远说他一直在客厅。\n"
_CHAPTER_EIGHT = "正文/0008 阁楼.md"
_TEXT_EIGHT = "# 阁楼\n\n管家承认那晚去过书房。\n\n窗外的雪停了。\n"

_REF_LIN = (
    "lib_0123456789abcdef",
    "mod_0123456789abcdef",
    "rev_0123456789abcdef",
    "ent_0123456789abcdef",
)
_REF_EVENT = (
    "lib_0123456789abcdef",
    "mod_fedcba9876543210",
    "rev_fedcba9876543210",
    "ent_0123456789abcdef",
)


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    workspace.write(_CHAPTER_THREE, _TEXT_THREE, expected_digest=None)
    workspace.write(_CHAPTER_EIGHT, _TEXT_EIGHT, expected_digest=None)
    return workspace


def _evidence(workspace: Workspace, path: str, quote: str) -> ObservedEvidence:
    view = workspace.read(path)
    start = view.text.index(quote)
    return ObservedEvidence(
        path=path,
        digest=view.digest,
        start=start,
        end=start + len(quote),
        quote=quote,
    )


def _event(
    workspace: Workspace,
    path: str,
    chapter: int,
    text: str,
    quote: str,
    *,
    mode: str = "narration",
    story_time: StoryTime | None = None,
    speaker: tuple[str, ...] | None = None,
    related: tuple[tuple[str, ...], ...] = (),
    author_event: tuple[str, ...] | None = None,
) -> EventObservation:
    return EventObservation(
        text=text,
        chapter=chapter,
        mode=mode,  # type: ignore[arg-type]
        evidence=(_evidence(workspace, path, quote),),
        story_time=story_time or StoryTime(),
        speaker=speaker,  # type: ignore[arg-type]
        related=related,  # type: ignore[arg-type]
        author_event=author_event,  # type: ignore[arg-type]
    )


def _projection(
    workspace: Workspace,
    path: str,
    chapter: int,
    summary: str,
    events: tuple[EventObservation, ...] = (),
) -> ChapterProjection:
    return ChapterProjection(
        path=path,
        digest=workspace.read(path).digest,
        chapter=chapter,
        summary=summary,
        events=events,
    )


def _code(exc_info: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return exc_info.value.code


# -- 发布 -------------------------------------------------------------------


def test_publish_keeps_other_chapters_and_replaces_only_the_target(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    root = tmp_path / "state"
    store = StoryIndexStore(root)
    three = _projection(workspace, _CHAPTER_THREE, 3, "侦探在书房发现脚印。")
    eight = _projection(workspace, _CHAPTER_EIGHT, 8, "管家承认去过书房。")

    store.publish(three, expected_digest=three.digest)
    # 第二次用新的 store 实例：必须重读文件，不能覆盖掉第三章。
    StoryIndexStore(root).publish(eight, expected_digest=eight.digest)

    stored = StoryIndexStore(root).read_all()
    assert set(stored) == {_CHAPTER_THREE, _CHAPTER_EIGHT}

    changed = _projection(workspace, _CHAPTER_EIGHT, 8, "管家承认当晚去过书房。")
    StoryIndexStore(root).publish(changed, expected_digest=changed.digest)
    reread = StoryIndexStore(root).read_all()

    assert reread[_CHAPTER_THREE].summary == "侦探在书房发现脚印。"
    assert reread[_CHAPTER_EIGHT].summary == "管家承认当晚去过书房。"


def test_publish_rejects_a_stale_source_without_writing(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = StoryIndexStore(tmp_path / "state")
    three = _projection(workspace, _CHAPTER_THREE, 3, "旧摘要。")
    store.publish(three, expected_digest=three.digest)

    workspace.write(
        _CHAPTER_THREE, _TEXT_THREE + "\n他又补了一句。\n", expected_digest=three.digest
    )
    stale = _projection(workspace, _CHAPTER_THREE, 3, "按旧文写的摘要。")

    with pytest.raises(WorkspaceError) as excinfo:
        store.publish(stale, expected_digest=three.digest)
    assert _code(excinfo) == "STORY_INDEX_SOURCE_STALE_CONFLICT"
    assert store.read_all()[_CHAPTER_THREE].summary == "旧摘要。"


def test_publish_refuses_a_projection_for_another_path(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = StoryIndexStore(tmp_path / "state")
    three = _projection(workspace, _CHAPTER_THREE, 3, "侦探在书房发现脚印。")

    with pytest.raises(WorkspaceError) as excinfo:
        store.publish(three, expected_digest=workspace.read(_CHAPTER_EIGHT).digest)
    assert _code(excinfo) == "STORY_INDEX_SOURCE_STALE_CONFLICT"


def test_write_failure_keeps_the_previous_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """原子替换失败时原 envelope 保持完整，不出现「摘要更新但事件没更新」的半状态。"""
    workspace = _workspace(tmp_path)
    store = StoryIndexStore(tmp_path / "state")
    three = _projection(
        workspace,
        _CHAPTER_THREE,
        3,
        "侦探在书房发现脚印。",
        (_event(workspace, _CHAPTER_THREE, 3, "发现脚印", "侦探在书房发现一枚脚印。"),),
    )
    store.publish(three, expected_digest=three.digest)
    before = store.file.read_text(encoding="utf-8")

    eight = _projection(workspace, _CHAPTER_EIGHT, 8, "管家承认去过书房。")

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("busy")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(WorkspaceError) as excinfo:
        store.publish(eight, expected_digest=eight.digest)

    assert _code(excinfo) == "STORY_INDEX_WRITE_FAILED"
    assert store.file.read_text(encoding="utf-8") == before
    assert set(store.read_all()) == {_CHAPTER_THREE}


def test_observation_ids_stay_stable_for_identical_resubmission(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = StoryIndexStore(tmp_path / "state")
    event = _event(workspace, _CHAPTER_THREE, 3, "发现脚印", "侦探在书房发现一枚脚印。")
    first = _projection(workspace, _CHAPTER_THREE, 3, "侦探在书房发现脚印。", (event,))
    store.publish(first, expected_digest=first.digest)
    first_id = store.read_all()[_CHAPTER_THREE].events[0].id

    again = _projection(workspace, _CHAPTER_THREE, 3, "侦探在书房发现脚印。", (event,))
    store.publish(again, expected_digest=again.digest)

    assert store.read_all()[_CHAPTER_THREE].events[0].id == first_id
    assert first_id == build_observation_id(
        3, "发现脚印", event.evidence[0].start, event.evidence[0].end
    )


def test_same_event_in_two_chapters_keeps_both_contributions(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = StoryIndexStore(tmp_path / "state")
    three = _projection(
        workspace,
        _CHAPTER_THREE,
        3,
        "侦探在书房发现脚印。",
        (_event(workspace, _CHAPTER_THREE, 3, "有人去过书房", "侦探在书房发现一枚脚印。"),),
    )
    eight = _projection(
        workspace,
        _CHAPTER_EIGHT,
        8,
        "管家承认去过书房。",
        (
            _event(
                workspace,
                _CHAPTER_EIGHT,
                8,
                "有人去过书房",
                "管家承认那晚去过书房。",
                mode="testimony",
            ),
        ),
    )
    store.publish(three, expected_digest=three.digest)
    store.publish(eight, expected_digest=eight.digest)

    rows = store.read_all()
    three_ids = {item.id for item in rows[_CHAPTER_THREE].events}
    eight_ids = {item.id for item in rows[_CHAPTER_EIGHT].events}
    assert three_ids and eight_ids
    assert not three_ids & eight_ids

    # 重建第八章不影响第三章的观察。
    rebuilt = _projection(workspace, _CHAPTER_EIGHT, 8, "管家承认当晚去过书房。", ())
    store.publish(rebuilt, expected_digest=rebuilt.digest)
    assert {item.id for item in store.read_all()[_CHAPTER_THREE].events} == three_ids


def test_event_keeps_mode_speaker_and_author_event_reference(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = StoryIndexStore(tmp_path / "state")
    event = _event(
        workspace,
        _CHAPTER_EIGHT,
        8,
        "管家说他那晚去过书房",
        "管家承认那晚去过书房。",
        mode="recollection",
        speaker=_REF_LIN,
        related=(_REF_LIN,),
        author_event=_REF_EVENT,
    )
    projection = _projection(workspace, _CHAPTER_EIGHT, 8, "管家回忆那晚去过书房。", (event,))
    store.publish(projection, expected_digest=projection.digest)

    stored = store.read_all()[_CHAPTER_EIGHT].events[0]
    # 回忆既不自动等于虚假，也不自动等于真相：表达方式原样保留。
    assert stored.mode == "recollection"
    assert stored.speaker == _REF_LIN
    assert stored.author_event == _REF_EVENT
    assert [item.to_dict() for item in stored.evidence][0]["quote"] == "管家承认那晚去过书房。"


# -- 校验 -------------------------------------------------------------------


def test_validate_rejects_evidence_that_does_not_match_the_text(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    event = _event(workspace, _CHAPTER_THREE, 3, "发现脚印", "侦探在书房发现一枚脚印。")
    broken = EventObservation(
        text=event.text,
        chapter=event.chapter,
        mode=event.mode,
        evidence=(
            ObservedEvidence(
                path=event.evidence[0].path,
                digest=event.evidence[0].digest,
                start=event.evidence[0].start,
                end=event.evidence[0].end,
                quote="林远说他一直在客厅。",
            ),
        ),
    )
    projection = _projection(workspace, _CHAPTER_THREE, 3, "摘要。", (broken,))

    with pytest.raises(WorkspaceError) as excinfo:
        validate_projection(workspace, projection)
    assert _code(excinfo) == "STORY_INDEX_EVIDENCE_INVALID"


def test_validate_rejects_unknown_mode_and_empty_text(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    bad_mode = _event(
        workspace, _CHAPTER_THREE, 3, "发现脚印", "侦探在书房发现一枚脚印。", mode="rumour"
    )
    with pytest.raises(WorkspaceError) as excinfo:
        validate_projection(
            workspace, _projection(workspace, _CHAPTER_THREE, 3, "摘要。", (bad_mode,))
        )
    assert _code(excinfo) == "STORY_INDEX_EVENT_INVALID"

    empty = _event(workspace, _CHAPTER_THREE, 3, "  ", "侦探在书房发现一枚脚印。")
    with pytest.raises(WorkspaceError) as excinfo:
        validate_projection(
            workspace, _projection(workspace, _CHAPTER_THREE, 3, "摘要。", (empty,))
        )
    assert _code(excinfo) == "STORY_INDEX_EVENT_INVALID"


def test_validate_rejects_incomplete_module_reference(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    partial = _event(
        workspace,
        _CHAPTER_THREE,
        3,
        "发现脚印",
        "侦探在书房发现一枚脚印。",
        speaker=("mod_0123456789abcdef", "ent_0123456789abcdef"),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        validate_projection(
            workspace, _projection(workspace, _CHAPTER_THREE, 3, "摘要。", (partial,))
        )
    assert _code(excinfo) == "STORY_INDEX_EVENT_INVALID"


def test_validate_rejects_an_empty_summary_and_too_many_events(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(WorkspaceError) as excinfo:
        validate_projection(workspace, _projection(workspace, _CHAPTER_THREE, 3, "  "))
    assert _code(excinfo) == "STORY_INDEX_EVIDENCE_INVALID"

    many = tuple(
        _event(workspace, _CHAPTER_THREE, 3, f"事件{i}", "侦探在书房发现一枚脚印。")
        for i in range(MAX_EVENTS_PER_CHAPTER + 1)
    )
    with pytest.raises(WorkspaceError) as excinfo:
        validate_projection(workspace, _projection(workspace, _CHAPTER_THREE, 3, "摘要。", many))
    assert _code(excinfo) == "STORY_INDEX_EVENT_INVALID"
