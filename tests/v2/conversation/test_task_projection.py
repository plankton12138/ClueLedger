"""候选投影与候选接受测试（Phase 19.5 T19.5a）。

先写失败测试（计划 §19.5 清单）：等待审阅不自动接受（投影只落候选消息，
接受只能走显式入口）；任务取消后迟到输出无效（取消的 Job 永不产出候选）；
候选接受幂等（重复接受返回同一 Commit，不产生重复结果消息）；非候选消息
不能被接受。
"""

from __future__ import annotations

from typing import Any

import pytest

from hastings.application.errors import HastingsError
from hastings.application.services.task_prep_service import TaskPrepService
from hastings.application.services.task_projection import ChatTaskProjector
from hastings.domain.conversation import error_codes as ec
from hastings.domain.conversation import models as m
from hastings.domain.conversation.task_drafts import (
    DRAFT_STARTED,
    ProjectView,
    TaskDraft,
)
from hastings.infrastructure.conversations.drafts import MemoryTaskDraftStore
from hastings.infrastructure.conversations.store import MemoryConversationStore

VIEW = ProjectView(model_configured=True, has_accepted_beats=True, head="c001")


class RecordingAccepter:
    """记录调用的假接受器：返回稳定 Commit，重放同值。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, job_ref: str, expected_head: str) -> dict[str, Any]:
        self.calls.append((job_ref, expected_head))
        return {"job_id": job_ref, "status": "accepted", "commit_id": f"c-{job_ref}"}


def _prep(
    accepter: RecordingAccepter | None = None,
) -> tuple[TaskPrepService, MemoryConversationStore, RecordingAccepter]:
    conversations = MemoryConversationStore()
    accepter = accepter or RecordingAccepter()
    service = TaskPrepService(
        MemoryTaskDraftStore(),
        conversations,
        project_view=lambda _pid: VIEW,
        accepter=accepter,
        model_label=lambda: "test-model",
    )
    return service, conversations, accepter


def _started_draft(
    conversation_id: str,
    message_id: str,
    *,
    job_ref: str = "job_1",
) -> TaskDraft:
    return TaskDraft(
        draft_id=f"draft_{job_ref}",
        project_id="p1",
        conversation_id=conversation_id,
        message_id=message_id,
        revision=1,
        task_kind="chapter_draft",
        instruction="写第一章",
        goal="ch_1",
        job_ref=job_ref,
        status=DRAFT_STARTED,
        model_binding={"profile": "test", "model": "m1", "api": "openai-completions"},
    )


async def _seed_draft(
    conversations: MemoryConversationStore, prep: TaskPrepService
) -> TaskDraft:
    conversation_id = (await conversations.create_conversation("p1", "会话")).conversation_id
    draft = _started_draft(conversation_id, "msg_draft")
    await prep._drafts.create_draft(draft)  # type: ignore[attr-defined]
    return draft


def _job(state: str, output: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "job_id": "job_1",
        "state": state,
        "output": output,
        "payload": {
            "project_id": "p1",
            "context": {"target_ids": ["ch_1"], "instruction": "写第一章"},
        },
    }


# -- 投影 ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_waiting_job_projects_candidate_message_once() -> None:
    service, conversations, _ = _prep()
    draft = await _seed_draft(conversations, service)
    jobs: dict[str, dict[str, Any]] = {
        "job_1": _job("waiting", {"kind": "manuscript_patch", "rationale": "按节拍展开"})
    }
    projector = ChatTaskProjector(
        service._drafts, conversations, job_getter=jobs.get  # type: ignore[attr-defined]
    )
    projected = await projector.sync()
    assert projected == 1
    events = await conversations.events_after(draft.conversation_id, 0)
    ready = [e for e in events if e.event.get("type") == "candidate.ready"]
    assert len(ready) == 1
    payload = ready[0].event["candidate"]
    assert payload["job_ref"] == "job_1"
    assert payload["accepted"] is False
    assert payload["target_ids"] == ["ch_1"]
    messages = await conversations.list_messages(draft.conversation_id)
    candidates = [x for x in messages if x.kind == m.KIND_CANDIDATE]
    assert len(candidates) == 1
    assert candidates[0].payload["summary"] == "按节拍展开"
    # 第二次同步不重复投影（同一 Job 只有一条候选）。
    assert await projector.sync() == 0
    events = await conversations.events_after(draft.conversation_id, 0)
    assert len([e for e in events if e.event.get("type") == "candidate.ready"]) == 1


@pytest.mark.anyio
async def test_cancelled_job_never_projects_a_candidate() -> None:
    """任务取消后迟到输出无效：取消态不产出候选，落定为终态。"""
    service, conversations, _ = _prep()
    draft = await _seed_draft(conversations, service)
    jobs: dict[str, dict[str, Any]] = {"job_1": _job("cancelled", {"kind": "manuscript_patch"})}
    projector = ChatTaskProjector(
        service._drafts, conversations, job_getter=jobs.get  # type: ignore[attr-defined]
    )
    await projector.sync()
    messages = await conversations.list_messages(draft.conversation_id)
    assert not any(x.kind == m.KIND_CANDIDATE for x in messages)
    events = await conversations.events_after(draft.conversation_id, 0)
    assert not any(e.event.get("type") == "candidate.ready" for e in events)


@pytest.mark.anyio
async def test_terminal_completed_job_is_settled_without_candidate() -> None:
    service, conversations, _ = _prep()
    draft = await _seed_draft(conversations, service)
    jobs: dict[str, dict[str, Any]] = {"job_1": _job("completed", {"kind": "finding_set"})}
    projector = ChatTaskProjector(
        service._drafts, conversations, job_getter=jobs.get  # type: ignore[attr-defined]
    )
    await projector.sync()
    fetched = await service._drafts.get_draft(draft.draft_id)  # type: ignore[attr-defined]
    assert fetched is not None and fetched.job_settled


# -- 接受 ---------------------------------------------------------------------


async def _candidate_message(
    conversations: MemoryConversationStore, conversation_id: str
) -> m.ChatMessage:
    message = m.ChatMessage(
        message_id="msg_cand",
        conversation_id=conversation_id,
        turn_id=None,
        role=m.ROLE_SYSTEM,
        kind=m.KIND_CANDIDATE,
        text="",
        payload={
            "job_ref": "job_1",
            "summary": "按节拍展开",
            "target_ids": ["ch_1"],
            "change_ref": None,
            "accepted": False,
        },
    )
    await conversations.append_message(message)
    return message


@pytest.mark.anyio
async def test_accept_creates_result_message_and_event() -> None:
    service, conversations, accepter = _prep()
    conversation_id = (await conversations.create_conversation("p1", "会话")).conversation_id
    await _candidate_message(conversations, conversation_id)
    result = await service.accept_candidate(
        "p1", conversation_id, "msg_cand", expected_head="c001", idempotency_key="k1"
    )
    assert accepter.calls == [("job_1", "c001")]
    assert result["commit_id"] == "c-job_1"
    assert result["job_ref"] == "job_1"
    events = await conversations.events_after(conversation_id, 0)
    accepted = [e for e in events if e.event.get("type") == "result.accepted"]
    assert len(accepted) == 1
    messages = await conversations.list_messages(conversation_id)
    results = [x for x in messages if x.kind == m.KIND_RESULT]
    assert len(results) == 1
    candidate = next(x for x in messages if x.kind == m.KIND_CANDIDATE)
    assert candidate.payload is not None and candidate.payload["accepted"] is True


@pytest.mark.anyio
async def test_accept_replays_return_same_result_without_duplicates() -> None:
    service, conversations, accepter = _prep()
    conversation_id = (await conversations.create_conversation("p1", "会话")).conversation_id
    await _candidate_message(conversations, conversation_id)
    first = await service.accept_candidate(
        "p1", conversation_id, "msg_cand", expected_head="c001", idempotency_key="k1"
    )
    replay = await service.accept_candidate(
        "p1", conversation_id, "msg_cand", expected_head="c001", idempotency_key="k2"
    )
    assert replay == first
    # 已接受后不再调用工作流接受入口：以既有结果为准（重复接受一次 Job）。
    assert accepter.calls == [("job_1", "c001")]
    messages = await conversations.list_messages(conversation_id)
    assert len([x for x in messages if x.kind == m.KIND_RESULT]) == 1


@pytest.mark.anyio
async def test_accept_rejects_non_candidate_message() -> None:
    service, conversations, accepter = _prep()
    conversation_id = (await conversations.create_conversation("p1", "会话")).conversation_id
    await conversations.append_message(
        m.ChatMessage(
            message_id="msg_text",
            conversation_id=conversation_id,
            turn_id=None,
            role=m.ROLE_ASSISTANT,
            kind=m.KIND_TEXT,
            text="普通回复",
        )
    )
    with pytest.raises(HastingsError) as excinfo:
        await service.accept_candidate(
            "p1", conversation_id, "msg_text", expected_head="c001", idempotency_key="k1"
        )
    assert excinfo.value.code == ec.CHAT_NOT_A_CANDIDATE
    assert accepter.calls == []


@pytest.mark.anyio
async def test_accept_validates_conversation_ownership() -> None:
    service, conversations, _ = _prep()
    conversation_id = (await conversations.create_conversation("p1", "会话")).conversation_id
    await _candidate_message(conversations, conversation_id)
    with pytest.raises(HastingsError) as excinfo:
        await service.accept_candidate(
            "p2", conversation_id, "msg_cand", expected_head="c001", idempotency_key="k1"
        )
    assert excinfo.value.code == ec.CHAT_CONVERSATION_NOT_FOUND


@pytest.mark.anyio
async def test_accept_without_expected_head_is_rejected() -> None:
    service, conversations, accepter = _prep()
    conversation_id = (await conversations.create_conversation("p1", "会话")).conversation_id
    await _candidate_message(conversations, conversation_id)
    with pytest.raises(HastingsError) as excinfo:
        await service.accept_candidate(
            "p1", conversation_id, "msg_cand", expected_head="", idempotency_key="k1"
        )
    assert excinfo.value.code == ec.CHAT_MISSING_BASE_VERSION
    assert accepter.calls == []
