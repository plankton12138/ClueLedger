"""灵感使用记录（0.8 I5）。

契约：引用与展开以受理事件为准，写入以核对过的关联为准；索引是派生物，可以从
会话日志重建；翻页期间数据变了就要求重读，不返回错位的一页。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from tests.workspace.test_chat import ScriptedProvider, _snapshot, _text_turn

from hastings.workspace import idea_activity
from hastings.workspace.activity import ActivityStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.idea_activity import IdeaActivityIndex
from hastings.workspace.ideas import IdeaStore, IdeaUseDraft
from hastings.workspace.materials import ContextRequest, ContextSnapshot, IdeaReference
from hastings.workspace.service import RunResult, TurnInfo, TurnService
from hastings.workspace.state import ConversationStore, conversation_root

STATE = "state"


def _setup(tmp_path: Path) -> tuple[Workspace, TurnService, ScriptedProvider]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    store = ConversationStore(conversation_root(tmp_path / STATE, "w1"))
    provider = ScriptedProvider(
        [_text_turn("展开结果。"), _text_turn("第二次。"), _text_turn("第三次。")]
    )
    return workspace, TurnService(workspace, store, lambda: _snapshot(provider)), provider


def _index(workspace: Workspace, tmp_path: Path) -> IdeaActivityIndex:
    return IdeaActivityIndex(workspace, conversation_root(tmp_path / STATE, "w1"))


def test_references_come_from_accepted_events(tmp_path: Path) -> None:
    workspace, service, _ = _setup(tmp_path)
    idea = IdeaStore(workspace).create(
        body="灯塔在退潮时露出第二层台阶。", request_key="idea-key-1"
    )
    reference = (idea["idea_id"], idea["revision_id"])

    async def scenario() -> None:
        first = await service.accept(
            "看看这条灵感",
            "k-ref-0001",
            context=ContextRequest(idea_references=(reference,)),
        )
        await service.wait(first.turn_id)

    asyncio.run(scenario())

    page = _index(workspace, tmp_path).page(str(idea["idea_id"]))
    kinds = sorted(str(item["kind"]) for item in page["items"])
    assert kinds == ["referenced"]
    assert page["complete"] is True
    assert page["total"] == 1
    for item in page["items"]:
        assert item["revision_id"] == idea["revision_id"]
        assert item["conversation_id"]


def test_index_rebuilds_from_the_event_log(tmp_path: Path) -> None:
    workspace, service, _ = _setup(tmp_path)
    idea = IdeaStore(workspace).create(body="一句话。", request_key="idea-key-1")

    async def scenario() -> None:
        info = await service.accept(
            "看看这条灵感",
            "k-ref-0001",
            context=ContextRequest(idea_references=((idea["idea_id"], idea["revision_id"]),)),
        )
        await service.wait(info.turn_id)

    asyncio.run(scenario())
    index = _index(workspace, tmp_path)
    first = index.page(str(idea["idea_id"]))
    assert first["total"] == 1

    # 索引是派生物：删掉之后按会话日志重建，结果一致。
    index.path.unlink()
    rebuilt = index.page(str(idea["idea_id"]))
    assert [item["id"] for item in rebuilt["items"]] == [item["id"] for item in first["items"]]
    assert rebuilt["complete"] is True


def test_cursor_is_invalidated_when_activity_changes(tmp_path: Path) -> None:
    workspace, service, _ = _setup(tmp_path)
    idea = IdeaStore(workspace).create(body="一句话。", request_key="idea-key-1")
    reference = ((idea["idea_id"], idea["revision_id"]),)

    async def accept(key: str, text: str) -> None:
        info = await service.accept(text, key, context=ContextRequest(idea_references=reference))
        await service.wait(info.turn_id)

    asyncio.run(accept("k-first-0001", "第一次"))
    asyncio.run(accept("k-second-001", "第二次"))
    index = _index(workspace, tmp_path)
    page = index.page(str(idea["idea_id"]), limit=1)
    cursor = page["next_cursor"]
    assert cursor

    asyncio.run(accept("k-third-0001", "第三次"))
    with pytest.raises(WorkspaceError) as excinfo:
        index.page(str(idea["idea_id"]), cursor=str(cursor), limit=1)
    assert excinfo.value.code == "QUERY_CURSOR_STALE_CONFLICT"


def test_applied_use_reports_position_validity(tmp_path: Path) -> None:
    workspace, _, _ = _setup(tmp_path)
    body = "灵感原文在中间。"
    workspace.write("正文/0001 雨夜.md", f"开头。{body}结尾。", expected_digest=None)
    idea = IdeaStore(workspace).create(body=body, request_key="idea-key-1")
    view = workspace.read("正文/0001 雨夜.md")
    start = view.text.find(body)

    IdeaStore(workspace).add_use(
        str(idea["idea_id"]),
        expected_digest=str(idea["digest"]),
        use=IdeaUseDraft(
            idea_revision_id=str(idea["revision_id"]),
            kind="applied",
            target_path="正文/0001 雨夜.md",
            target_digest=view.digest,
            quote=body,
            start=start,
            end=start + len(body),
            turn_id="t1",
        ),
        request_key="link-key-1",
    )

    index = _index(workspace, tmp_path)
    applied = next(
        item for item in index.page(str(idea["idea_id"]))["items"] if item["kind"] == "applied"
    )
    assert applied["validity"] == "current"

    # 正文被改动：路径还在，但引文位置已不能核对。
    workspace.write("正文/0001 雨夜.md", "完全换掉的内容。", expected_digest=view.digest)
    changed = next(
        item for item in index.page(str(idea["idea_id"]))["items"] if item["kind"] == "applied"
    )
    assert changed["validity"] == "changed"


def test_reverted_commit_stops_counting_as_a_current_write(tmp_path: Path) -> None:
    workspace, _, _ = _setup(tmp_path)
    body = "灵感原文在中间。"
    workspace.write("正文/0001 雨夜.md", f"开头。{body}结尾。", expected_digest=None)
    idea = IdeaStore(workspace).create(body=body, request_key="idea-key-1")
    view = workspace.read("正文/0001 雨夜.md")
    start = view.text.find(body)
    IdeaStore(workspace).add_use(
        str(idea["idea_id"]),
        expected_digest=str(idea["digest"]),
        use=IdeaUseDraft(
            idea_revision_id=str(idea["revision_id"]),
            kind="applied",
            target_path="正文/0001 雨夜.md",
            target_digest=view.digest,
            quote=body,
            start=start,
            end=start + len(body),
            turn_id="t1",
            commit="c-written",
        ),
        request_key="link-key-1",
    )
    index = _index(workspace, tmp_path)
    assert next(item for item in index.page(str(idea["idea_id"]))["items"])["validity"] == "current"

    # 撤销那次提交：正文被还原，这条写入不能继续显示为有效。
    with ActivityStore(conversation_root(tmp_path / STATE, "w1")).operation(
        "撤销版本", target_commit="c-written"
    ):
        pass
    reverted = next(
        item for item in index.page(str(idea["idea_id"]))["items"] if item["kind"] == "applied"
    )
    assert reverted["validity"] == "reverted"


def test_missing_conversation_keeps_the_record_but_marks_it_unavailable(tmp_path: Path) -> None:
    workspace, service, _ = _setup(tmp_path)
    idea = IdeaStore(workspace).create(body="一句话。", request_key="idea-key-1")

    async def scenario() -> None:
        info = await service.accept(
            "看看这条灵感",
            "k-ref-0001",
            context=ContextRequest(idea_references=((idea["idea_id"], idea["revision_id"]),)),
        )
        await service.wait(info.turn_id)

    asyncio.run(scenario())
    index = _index(workspace, tmp_path)
    first = index.page(str(idea["idea_id"]))["items"][0]
    assert first["conversation_available"] is True

    # 会话日志被删掉：记录留着，界面要能说明这次讨论已经打不开。
    root = conversation_root(tmp_path / STATE, "w1")
    (root / "conversations.json").unlink()
    for path in root.glob("*.jsonl"):
        path.unlink()
    orphan = index.page(str(idea["idea_id"]))["items"][0]
    assert orphan["conversation_available"] is False
    assert orphan["conversation_title"] == first["conversation_title"]


def test_conversation_title_is_read_when_the_page_is_built(tmp_path: Path) -> None:
    workspace, service, _ = _setup(tmp_path)
    idea = IdeaStore(workspace).create(body="一句话。", request_key="idea-key-1")

    async def scenario() -> str:
        info = await service.accept(
            "看看这条灵感",
            "k-ref-0001",
            context=ContextRequest(idea_references=((idea["idea_id"], idea["revision_id"]),)),
        )
        await service.wait(info.turn_id)
        return info.conversation_id

    conversation_id = asyncio.run(scenario())
    index = _index(workspace, tmp_path)
    index.page(str(idea["idea_id"]))
    ConversationStore(conversation_root(tmp_path / STATE, "w1")).rename(conversation_id, "灯塔那条")
    assert index.page(str(idea["idea_id"]))["items"][0]["conversation_title"] == "灯塔那条"


def test_partial_rebuild_does_not_hand_out_a_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, service, _ = _setup(tmp_path)
    idea = IdeaStore(workspace).create(body="一句话。", request_key="idea-key-1")
    reference = ((idea["idea_id"], idea["revision_id"]),)

    async def accept(key: str, text: str) -> None:
        info = await service.accept(text, key, context=ContextRequest(idea_references=reference))
        await service.wait(info.turn_id)

    asyncio.run(accept("k-first-0001", "第一次"))
    asyncio.run(accept("k-second-001", "第二次"))
    monkeypatch.setattr(idea_activity, "SCAN_BATCH", 1)

    index = _index(workspace, tmp_path)
    partial = index.page(str(idea["idea_id"]))
    assert partial["complete"] is False
    assert partial["next_cursor"] is None

    # 没有游标就再读一次：索引继续回放，扫完之后才允许按游标翻页。
    while not index.page(str(idea["idea_id"]))["complete"]:
        pass
    done = index.page(str(idea["idea_id"]), limit=1)
    assert done["total"] == 2
    assert done["next_cursor"]


def _snapshot_with(idea: dict[str, object], body: str) -> ContextSnapshot:
    return ContextSnapshot(
        selected_path=None,
        references=(),
        observed_digests={},
        ideas=(
            IdeaReference(
                idea_id=str(idea["idea_id"]),
                revision_id=str(idea["revision_id"]),
                title="",
                body=body,
            ),
        ),
    )


def test_verified_application_is_recorded_only_when_the_text_appears(tmp_path: Path) -> None:
    workspace, service, _ = _setup(tmp_path)
    body = "灯塔在退潮时露出第二层台阶。"
    workspace.write("正文/0001 雨夜.md", f"开头。{body}结尾。", expected_digest=None)
    idea = IdeaStore(workspace).create(body=body, request_key="idea-key-1")
    info = TurnInfo(
        turn_id="t1",
        conversation_id="c1",
        started_at="2026-10-25T10:00:00+08:00",
        context=_snapshot_with(idea, body),
    )
    result = RunResult(
        status="completed",
        recording="committed",
        changed_paths=("正文/0001 雨夜.md",),
        commit="abc",
    )

    service._record_idea_applications(info, result)  # noqa: SLF001
    uses = IdeaStore(workspace).get(str(idea["idea_id"]))["uses"]
    assert len(uses) == 1
    assert uses[0]["kind"] == "applied"
    assert uses[0]["quote"] == body

    # 同一次调用再跑一遍不重复登记。
    service._record_idea_applications(info, result)  # noqa: SLF001
    assert len(IdeaStore(workspace).get(str(idea["idea_id"]))["uses"]) == 1

    # 正文里没有原文的轮次不登记「已写入」。
    other_body = "这段话没有写进正文。"
    other = IdeaStore(workspace).create(body=other_body, request_key="idea-key-2")
    missing = TurnInfo(
        turn_id="t2",
        conversation_id="c1",
        started_at="2026-10-25T10:05:00+08:00",
        context=_snapshot_with(other, other_body),
    )
    service._record_idea_applications(missing, result)  # noqa: SLF001
    assert IdeaStore(workspace).get(str(other["idea_id"]))["uses"] == []


def test_failed_application_is_deferred_and_repaired_on_the_next_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, service, _ = _setup(tmp_path)
    body = "灯塔在退潮时露出第二层台阶。"
    workspace.write("正文/0001 雨夜.md", f"开头。{body}结尾。", expected_digest=None)
    idea = IdeaStore(workspace).create(body=body, request_key="idea-key-1")
    info = TurnInfo(
        turn_id="t1",
        conversation_id="c1",
        started_at="2026-10-25T10:00:00+08:00",
        context=_snapshot_with(idea, body),
    )
    result = RunResult(
        status="completed",
        recording="committed",
        changed_paths=("正文/0001 雨夜.md",),
        commit="abc",
    )

    def broken(*args: object, **kwargs: object) -> dict[str, object]:
        raise WorkspaceError(
            code="IDEA_LOCKED",
            where="ideas.write",
            why="条目正在被另一处修改",
            fix="稍后重试",
        )

    monkeypatch.setattr(IdeaStore, "add_use", broken)
    service._record_idea_applications(info, result)  # noqa: SLF001
    assert IdeaStore(workspace).get(str(idea["idea_id"]))["uses"] == []
    # 正文没有因此重跑，只留下一条待补记录。
    document = json.loads(
        (conversation_root(tmp_path / STATE, "w1") / "idea-activity.json").read_text(
            encoding="utf-8"
        )
    )
    assert [row["idea_id"] for row in document["pending"]] == [idea["idea_id"]]

    monkeypatch.undo()
    applied = [
        item
        for item in _index(workspace, tmp_path).page(str(idea["idea_id"]))["items"]
        if item["kind"] == "applied"
    ]
    assert len(applied) == 1
    assert applied[0]["validity"] == "current"
    assert applied[0]["commit"] == "abc"

    # 修好之后再读一次不会重复登记。
    again = [
        item
        for item in _index(workspace, tmp_path).page(str(idea["idea_id"]))["items"]
        if item["kind"] == "applied"
    ]
    assert len(again) == 1


def test_deferred_entry_is_dropped_when_the_quote_is_gone(tmp_path: Path) -> None:
    workspace, _, _ = _setup(tmp_path)
    workspace.write("正文/0001 雨夜.md", "这里没有那句话。", expected_digest=None)
    idea = IdeaStore(workspace).create(body="被改掉的原文。", request_key="idea-key-1")

    index = _index(workspace, tmp_path)
    index.defer(
        str(idea["idea_id"]),
        revision_id=str(idea["revision_id"]),
        target_path="正文/0001 雨夜.md",
        quote="被改掉的原文。",
        commit="abc",
        turn_id="t1",
    )
    page = index.page(str(idea["idea_id"]))
    assert page["items"] == []
    # 待办被丢弃，不会每读一次都重试同一条对不上的记录。
    assert 'pending": []' in index.path.read_text(encoding="utf-8")
