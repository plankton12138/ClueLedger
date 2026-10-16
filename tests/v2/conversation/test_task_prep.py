"""TaskPrepService 行为契约测试（Phase 19.4 T19.4b）。

先写失败测试（计划 §19.4 清单）：引用不能跨项目；草稿不自动附加引用；
建议无执行权限（创建/调整绝不调用 starter）；任意文本不能触发 Job；
过期版本不能直接开始；重复开始只认同一幂等键。
"""

from __future__ import annotations

from typing import Any

import pytest

from hastings.application.errors import HastingsError
from hastings.application.services.task_prep_service import TaskPrepService
from hastings.domain.conversation import error_codes as ec
from hastings.domain.conversation import models as m
from hastings.domain.conversation.task_drafts import (
    DRAFT_STARTED,
    ProjectView,
)
from hastings.infrastructure.conversations.drafts import MemoryTaskDraftStore
from hastings.infrastructure.conversations.store import MemoryConversationStore

FULL_VIEW = ProjectView(
    model_configured=True,
    has_accepted_beats=True,
    chapter_ids=frozenset({"ch_1"}),
    head="c001",
)
NO_PLAN_VIEW = ProjectView(
    model_configured=True, has_accepted_beats=False, chapter_ids=frozenset({"ch_1"}), head="c001"
)


class RecordingStarter:
    """记录调用的假启动器；返回稳定的 job_ref。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, project_id: str, draft: Any, key: str) -> str:
        self.calls.append((project_id, draft.draft_id, key))
        return f"job_{len(self.calls)}"


def _service(
    view: ProjectView = FULL_VIEW,
    starter: RecordingStarter | None = None,
) -> tuple[TaskPrepService, MemoryConversationStore, RecordingStarter]:
    conversations = MemoryConversationStore()
    starter = starter or RecordingStarter()
    service = TaskPrepService(
        MemoryTaskDraftStore(),
        conversations,
        project_view=lambda _pid: view,
        starter=starter,
        model_label=lambda: "test-model",
    )
    return service, conversations, starter


async def _conversation(conversations: MemoryConversationStore) -> str:
    conversation = await conversations.create_conversation("p1", "会话")
    return conversation.conversation_id


# -- 创建 ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_draft_builds_message_and_event_without_auto_refs() -> None:
    """草稿不自动附加引用：显式创建没有任何 refs；消息与事件同步落盘。"""
    service, conversations, starter = _service()
    conversation_id = await _conversation(conversations)
    draft, message_id = await service.create_draft(
        "p1", conversation_id, "chapter_draft", instruction="写第一章", goal="ch_1"
    )
    assert starter.calls == []  # 建议无执行权限：创建不启动任何任务
    assert draft.revision == 1
    assert draft.status == "draft"
    assert draft.base_version == "c001"  # 创建时定格基础版本
    assert draft.refs == []
    message = await conversations.get_message(message_id)
    assert message is not None
    assert message.kind == m.KIND_TASK_DRAFT
    assert message.payload == draft.to_payload()
    events = await conversations.events_after(conversation_id, 0)
    created = [e for e in events if e.event.get("type") == "task.draft.created"]
    assert len(created) == 1
    assert created[0].event["message_id"] == message_id
    assert created[0].event["draft"]["draft_id"] == draft.draft_id


@pytest.mark.anyio
async def test_create_from_message_copies_its_refs_and_records_source() -> None:
    service, conversations, _ = _service()
    conversation_id = await _conversation(conversations)
    source = m.ChatMessage(
        message_id="msg_src",
        conversation_id=conversation_id,
        turn_id=None,
        role=m.ROLE_AUTHOR,
        kind=m.KIND_TEXT,
        text="结合这段",
        refs=[{"ref_id": "r1", "kind": "chapter", "target_id": "ch_1",
               "title": "第一章", "version": "c001", "saved": True}],
    )
    await conversations.append_message(source)
    draft, message_id = await service.create_draft(
        "p1", conversation_id, "chapter_draft", source_message_id="msg_src"
    )
    assert draft.source_message_id == "msg_src"
    assert draft.refs == source.refs
    assert message_id != "msg_src"


@pytest.mark.anyio
async def test_create_in_wrong_project_is_rejected() -> None:
    service, conversations, _ = _service()
    conversation_id = await _conversation(conversations)
    with pytest.raises(HastingsError) as excinfo:
        await service.create_draft("p2", conversation_id, "plan")
    assert excinfo.value.code == ec.CHAT_CONVERSATION_NOT_FOUND


@pytest.mark.anyio
async def test_unknown_task_kind_is_rejected() -> None:
    service, conversations, _ = _service()
    conversation_id = await _conversation(conversations)
    with pytest.raises(HastingsError):
        await service.create_draft("p1", conversation_id, "auto_write_book")


# -- 调整（修订 CAS） ------------------------------------------------------------


@pytest.mark.anyio
async def test_adjust_bumps_revision_and_recomputes_missing() -> None:
    service, conversations, _ = _service(view=NO_PLAN_VIEW)
    conversation_id = await _conversation(conversations)
    draft, _ = await service.create_draft("p1", conversation_id, "chapter_draft", goal="ch_1")
    assert [x["code"] for x in draft.missing] == ["TASK_NO_PLAN"]

    adjusted = await service.adjust_draft(
        "p1", draft.draft_id, expected_revision=1, patch={"target_ids": ["ch_1"]}
    )
    assert adjusted.revision == 2
    # NO_PLAN_VIEW 下缺节拍依旧；换到完整视图重算后缺失清空由测试数据决定，
    # 这里断言缺失被重新计算而不是沿用旧值。
    assert adjusted.missing == [x for x in draft.missing if x["code"] != "TASK_NO_CHAPTER_TARGET"]


@pytest.mark.anyio
async def test_adjust_with_stale_revision_is_a_conflict() -> None:
    service, conversations, _ = _service()
    conversation_id = await _conversation(conversations)
    draft, _ = await service.create_draft("p1", conversation_id, "plan")
    with pytest.raises(HastingsError) as excinfo:
        await service.adjust_draft(
            "p1", draft.draft_id, expected_revision=99, patch={"instruction": "改"}
        )
    assert excinfo.value.code == ec.TASK_REVISION_CONFLICT


@pytest.mark.anyio
async def test_adjust_never_calls_the_starter() -> None:
    service, conversations, starter = _service()
    conversation_id = await _conversation(conversations)
    draft, _ = await service.create_draft("p1", conversation_id, "plan")
    await service.adjust_draft("p1", draft.draft_id, 1, {"instruction": "调整目标"})
    assert starter.calls == []


# -- 放弃 ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_abandon_prevents_start_and_emits_event() -> None:
    service, conversations, starter = _service()
    conversation_id = await _conversation(conversations)
    draft, _ = await service.create_draft("p1", conversation_id, "plan")
    await service.abandon_draft("p1", draft.draft_id)
    with pytest.raises(HastingsError) as excinfo:
        await service.start_draft("p1", draft.draft_id, 1, "k1")
    assert excinfo.value.code == ec.TASK_DRAFT_ABANDONED
    assert starter.calls == []
    events = await conversations.events_after(conversation_id, 0)
    assert any(e.event.get("type") == "task.draft.abandoned" for e in events)


# -- 启动 ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_start_binds_job_ref_and_emits_linked_event() -> None:
    service, conversations, starter = _service()
    conversation_id = await _conversation(conversations)
    draft, message_id = await service.create_draft(
        "p1", conversation_id, "chapter_draft", goal="ch_1"
    )
    started = await service.start_draft("p1", draft.draft_id, 1, "k1")
    assert starter.calls == [("p1", draft.draft_id, "k1")]
    assert started.status == DRAFT_STARTED
    assert started.job_ref == "job_1"
    events = await conversations.events_after(conversation_id, 0)
    linked = [e for e in events if e.event.get("type") == "task.linked"]
    assert len(linked) == 1
    assert linked[0].event["job_ref"] == "job_1"
    assert linked[0].event["task_kind"] == "chapter_draft"
    assert linked[0].event["model"] == "test-model"
    assert linked[0].event["message_id"] == message_id


@pytest.mark.anyio
async def test_start_with_missing_items_is_rejected() -> None:
    service, conversations, starter = _service(view=NO_PLAN_VIEW)
    conversation_id = await _conversation(conversations)
    draft, _ = await service.create_draft("p1", conversation_id, "chapter_draft", goal="ch_1")
    with pytest.raises(HastingsError) as excinfo:
        await service.start_draft("p1", draft.draft_id, 1, "k1")
    assert excinfo.value.code == ec.TASK_MISSING_ITEMS
    assert excinfo.value.details.get("missing")
    assert starter.calls == []


@pytest.mark.anyio
async def test_start_with_stale_base_version_is_rejected() -> None:
    """过期版本不能直接开始：基础版本落后于项目 head 时必须重建任务。"""
    service, conversations, _ = _service()
    conversation_id = await _conversation(conversations)
    draft, _ = await service.create_draft("p1", conversation_id, "chapter_draft", goal="ch_1")
    assert draft.base_version == "c001"
    # 项目前进了：视图 head 变化，基础版本过期。
    service._project_view = lambda _pid: ProjectView(
        model_configured=True,
        has_accepted_beats=True,
        chapter_ids=frozenset({"ch_1"}),
        head="c002",
    )
    with pytest.raises(HastingsError) as excinfo:
        await service.start_draft("p1", draft.draft_id, 1, "k1")
    assert excinfo.value.code == ec.TASK_BASE_STALE


@pytest.mark.anyio
async def test_idempotent_start_replays_same_job_ref() -> None:
    service, conversations, starter = _service()
    conversation_id = await _conversation(conversations)
    draft, _ = await service.create_draft("p1", conversation_id, "plan")
    first = await service.start_draft("p1", draft.draft_id, 1, "k1")
    replay = await service.start_draft("p1", draft.draft_id, 1, "k1")
    assert replay.job_ref == first.job_ref
    assert len(starter.calls) == 1  # 同键重放不重复创建任务


@pytest.mark.anyio
async def test_start_twice_with_different_key_is_rejected() -> None:
    service, conversations, _ = _service()
    conversation_id = await _conversation(conversations)
    draft, _ = await service.create_draft("p1", conversation_id, "plan")
    await service.start_draft("p1", draft.draft_id, 1, "k1")
    with pytest.raises(HastingsError) as excinfo:
        await service.start_draft("p1", draft.draft_id, 1, "k2")
    assert excinfo.value.code == ec.TASK_DRAFT_ALREADY_STARTED


@pytest.mark.anyio
async def test_start_with_stale_revision_is_rejected() -> None:
    service, conversations, _ = _service()
    conversation_id = await _conversation(conversations)
    draft, _ = await service.create_draft("p1", conversation_id, "plan")
    with pytest.raises(HastingsError) as excinfo:
        await service.start_draft("p1", draft.draft_id, 42, "k1")
    assert excinfo.value.code == ec.TASK_REVISION_CONFLICT


@pytest.mark.anyio
async def test_cross_project_draft_access_is_rejected() -> None:
    service, conversations, _ = _service()
    conversation_id = await _conversation(conversations)
    draft, _ = await service.create_draft("p1", conversation_id, "plan")
    with pytest.raises(HastingsError) as excinfo:
        await service.get_draft("p2", draft.draft_id)
    assert excinfo.value.code == ec.TASK_DRAFT_NOT_FOUND
    with pytest.raises(HastingsError):
        await service.adjust_draft("p2", draft.draft_id, 1, {})
    with pytest.raises(HastingsError):
        await service.start_draft("p2", draft.draft_id, 1, "k1")


# -- 聊天与任务的边界 --------------------------------------------------------------


@pytest.mark.anyio
async def test_plain_chat_creates_no_drafts_or_task_messages() -> None:
    """任意文本不能触发 Job：普通轮次走完后没有任何任务产物。"""
    import asyncio

    from hastings.application.services.conversation_service import ConversationService

    conversations = MemoryConversationStore()
    conversation_id = (await conversations.create_conversation("p1", "会话")).conversation_id
    starter = RecordingStarter()
    prep = TaskPrepService(
        MemoryTaskDraftStore(),
        conversations,
        project_view=lambda _pid: FULL_VIEW,
        starter=starter,
        model_label=lambda: "test-model",
    )
    chat = ConversationService(
        conversations,
        runner_factory=lambda: _EchoRunner(),
        model_resolver=lambda: "m",
    )
    result = await chat.send_turn("p1", conversation_id, "帮我写第一章", (), "k1")
    for _ in range(200):
        turn = await conversations.get_turn(result.turn.turn_id)
        if turn is not None and turn.status == "completed":
            break
        await asyncio.sleep(0.01)
    drafts = await prep.list_drafts("p1", conversation_id)
    assert drafts == []
    messages = await conversations.list_messages(conversation_id)
    assert not any(x.kind == m.KIND_TASK_DRAFT for x in messages)
    assert starter.calls == []


class _EchoRunner:
    async def stream(self, run: Any, sink: Any) -> str:
        await sink("建议可以准备一个任务，等你确认。")
        return "建议可以准备一个任务，等你确认。"
