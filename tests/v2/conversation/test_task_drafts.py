"""TaskDraft 域模型与存储测试（Phase 19.4 T19.4a）。

缺失项计算是纯函数：每种缺失都写「哪里缺、为什么、怎样继续」，且缺失为空
是「开始」可用的充分条件。存储钉住双后端一致行为。
"""

from __future__ import annotations

import pytest

from hastings.domain.conversation.task_drafts import (
    DRAFT_ABANDONED,
    DRAFT_ACTIVE,
    DRAFT_STARTED,
    ProjectView,
    TaskDraft,
    missing_items,
)
from hastings.infrastructure.conversations.drafts import (
    MemoryTaskDraftStore,
    SqliteTaskDraftStore,
)

_FULL_VIEW = ProjectView(
    model_configured=True,
    has_accepted_beats=True,
    chapter_ids=frozenset({"ch_1", "ch_2"}),
    head="c001",
)
_EMPTY_VIEW = ProjectView(
    model_configured=False, has_accepted_beats=False, chapter_ids=frozenset(), head=None
)


# -- 缺失项计算 ----------------------------------------------------------------


def test_plan_kind_needs_nothing_but_a_model() -> None:
    assert missing_items("plan", goal="", target_ids=[], base_version=None, view=_FULL_VIEW) == []
    items = missing_items("plan", goal="", target_ids=[], base_version=None, view=_EMPTY_VIEW)
    assert [x["code"] for x in items] == ["TASK_MODEL_NOT_CONFIGURED"]


def test_chapter_draft_requires_beats_and_chapter_target() -> None:
    items = missing_items(
        "chapter_draft", goal="ch_1", target_ids=[], base_version=None, view=_FULL_VIEW
    )
    assert items == []
    items = missing_items(
        "chapter_draft", goal="", target_ids=[], base_version=None, view=_EMPTY_VIEW
    )
    codes = [x["code"] for x in items]
    assert "TASK_NO_BASE" in codes
    assert "TASK_NO_PLAN" in codes
    assert "TASK_NO_CHAPTER_TARGET" in codes
    # 三要素齐备：每条缺失都说明哪里缺、为什么、怎样继续。
    for item in items:
        assert item["why"] and item["fix"] and item["where"]


def test_check_requires_targets() -> None:
    items = missing_items("check", goal="", target_ids=[], base_version=None, view=_FULL_VIEW)
    assert [x["code"] for x in items] == ["TASK_NO_TARGETS"]
    ok = missing_items(
        "check", goal="", target_ids=["story.character:p1:x"], base_version=None, view=_FULL_VIEW
    )
    assert ok == []


def test_targeted_revision_requires_base_version_and_targets() -> None:
    items = missing_items(
        "targeted_revision", goal="", target_ids=[], base_version=None, view=_FULL_VIEW
    )
    codes = [x["code"] for x in items]
    assert "TASK_NO_BASE_VERSION" in codes
    assert "TASK_NO_TARGETS" in codes
    ok = missing_items(
        "targeted_revision", goal="", target_ids=["ch_1"], base_version="c001", view=_FULL_VIEW
    )
    assert ok == []


# -- 域模型往返 ----------------------------------------------------------------


def _draft(**overrides: object) -> TaskDraft:
    base: dict[str, object] = {
        "draft_id": "draft_1",
        "project_id": "p1",
        "conversation_id": "conv_1",
        "message_id": "msg_1",
        "revision": 1,
        "task_kind": "chapter_draft",
        "instruction": "写第一章",
        "goal": "ch_1",
        "missing": [{"code": "TASK_NO_PLAN", "where": "b", "why": "w", "fix": "f"}],
    }
    base.update(overrides)
    return TaskDraft.from_dict(base)  # type: ignore[arg-type]


def test_task_draft_payload_matches_frontend_contract() -> None:
    payload = _draft().to_payload()
    assert set(payload) == {
        "draft_id",
        "revision",
        "task_kind",
        "instruction",
        "goal",
        "target_ids",
        "base_version",
        "refs",
        "basis",
        "missing",
        "source_message_id",
        "job_ref",
    }


def test_task_draft_roundtrip_preserves_fields() -> None:
    draft = _draft(refs=[{"ref_id": "r1", "kind": "chapter", "target_id": "ch_1"}])
    restored = TaskDraft.from_dict(draft.to_dict())
    assert restored == draft


def test_unknown_kind_or_status_is_rejected() -> None:
    with pytest.raises(ValueError):
        _draft(task_kind="execute_everything")
    with pytest.raises(ValueError):
        _draft(status="auto_run")


def test_status_constants() -> None:
    assert {DRAFT_ACTIVE, DRAFT_STARTED, DRAFT_ABANDONED} == {"draft", "started", "abandoned"}


# -- 存储（双后端参数化） ---------------------------------------------------------


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest):  # type: ignore[no-untyped-def]
    if request.param == "memory":
        return MemoryTaskDraftStore()
    return SqliteTaskDraftStore(":memory:")


@pytest.mark.anyio
async def test_store_roundtrip_and_list(store):  # type: ignore[no-untyped-def]
    draft = _draft()
    await store.create_draft(draft)
    assert await store.get_draft("draft_1") == draft
    assert await store.get_draft("draft_missing") is None
    assert [d.draft_id for d in await store.list_drafts("conv_1")] == ["draft_1"]
    assert await store.list_drafts("conv_other") == []


@pytest.mark.anyio
async def test_store_update_replaces_whole_record(store):  # type: ignore[no-untyped-def]
    draft = _draft()
    await store.create_draft(draft)
    updated = TaskDraft.from_dict(
        {**draft.to_dict(), "revision": 2, "status": DRAFT_STARTED, "job_ref": "job_9"}
    )
    await store.update_draft(updated)
    fetched = await store.get_draft("draft_1")
    assert fetched is not None
    assert fetched.revision == 2
    assert fetched.status == DRAFT_STARTED
    assert fetched.job_ref == "job_9"
