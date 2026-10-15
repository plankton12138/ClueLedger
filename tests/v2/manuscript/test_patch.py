"""T14.4 Agent Patch 与部分接受：提案登记候选编辑，作者逐 hunk 接受/拒绝。

覆盖计划 §6 测试先行清单 7/8（Patch Scope 越界；部分接受）与退出条件
「Agent Patch 无法直接提交」：

- `propose_patch` 只创建提案 Artifact，**不产生任何正文变化**（正文段落/章的
  Revision 不动），因此 Agent 的候选编辑不能自证生效；
- `accept_patch_hunks` 只应用选中的 hunk，未选中的仍为 pending 且可追踪；接受
  用**提案自己的 base** 做乐观并发检查，提案登记后正文被改动则显式冲突；
- `reject_patch` 只回写状态、不改正文；无未决 hunk 时为合法空结果；
- 提案范围是硬约束：引用他章/不存在的段落被拒（`MANUSCRIPT_PATCH_SCOPE`）。
"""

from __future__ import annotations

from typing import Any

import pytest

from hastings.application.errors import ResultCategory, ViolationError
from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.workflows.manual_edit import ManualEditIntent
from hastings.domain.manuscript import commands, operations
from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.models import (
    HUNK_ACCEPTED,
    HUNK_PENDING,
    HUNK_REJECTED,
    PROPOSAL_ACCEPTED,
    PROPOSAL_PARTIAL,
    PROPOSAL_PENDING,
)
from hastings.domain.repository.results import NO_CHANGE

from .conftest import ManualEditEnv

CHAPTER = "art_ch1"
OTHER = "art_ch2"


def _head(env: ManualEditEnv, project_id: Any) -> Any:
    branch = env.service.get_branch(project_id, "main")
    assert branch is not None and branch.head_commit_id is not None
    return branch.head_commit_id


def _import_chapter(
    env: ManualEditEnv,
    project_id: Any,
    *,
    chapter_id: str = CHAPTER,
    title: str = "第一章",
    text: str = "第一段\n\n第二段\n\n第三段",
    key: str = "import",
) -> Any:
    command = manuscript_command(
        commands.import_chapter, chapter_id=chapter_id, title=title, text=text
    )
    return env.handler.execute(command, project_id=project_id, idempotency_key=key, summary="导入")


def _unit_ids(env: ManualEditEnv, project_id: Any, chapter_id: str = CHAPTER) -> list[str]:
    catalog = env.queries.catalog(project_id, _head(env, project_id))
    return [entry.artifact_id for entry in catalog.units_of(chapter_id)]


def _text_of(env: ManualEditEnv, project_id: Any, chapter_id: str = CHAPTER) -> str:
    return env.queries.chapter_text(project_id, _head(env, project_id), chapter_id)


def _propose(
    env: ManualEditEnv,
    project_id: Any,
    *,
    operations_: tuple[operations.EditOperation, ...],
    key: str,
    chapter_id: str = CHAPTER,
) -> Any:
    """登记提案并返回其 execution 结果（提案 ID 取 changed_artifact_ids）。"""
    return env.handler.execute(
        manuscript_command(
            commands.propose_patch, chapter_id=chapter_id, operations=tuple(operations_)
        ),
        project_id=project_id,
        idempotency_key=key,
        summary="Agent Patch",
    )


def _accept(
    env: ManualEditEnv,
    project_id: Any,
    *,
    proposal_id: str,
    hunk_ids: tuple[str, ...],
    key: str,
) -> Any:
    return env.handler.execute(
        manuscript_command(
            commands.accept_patch_hunks, proposal_id=proposal_id, hunk_ids=tuple(hunk_ids)
        ),
        project_id=project_id,
        idempotency_key=key,
        summary="接受 Patch",
    )


def _reject(env: ManualEditEnv, project_id: Any, *, proposal_id: str, key: str) -> Any:
    return env.handler.execute(
        manuscript_command(commands.reject_patch, proposal_id=proposal_id),
        project_id=project_id,
        idempotency_key=key,
        summary="拒绝 Patch",
    )


def _proposal(env: ManualEditEnv, project_id: Any, proposal_id: str) -> Any:
    catalog = env.queries.catalog(project_id, _head(env, project_id))
    return catalog.require_proposal(proposal_id, where="test").model()


def _three_hunks(units: list[str]) -> tuple[operations.EditOperation, ...]:
    return (
        operations.Replace(unit_id=units[0], text="甲"),
        operations.Replace(unit_id=units[1], text="乙"),
        operations.Replace(unit_id=units[2], text="丙"),
    )


# ---------------------------------------------------------------------------
# 提案不改正文（Agent Patch 无法直接提交）
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_proposal_registers_hunks_without_touching_manuscript(
    edit_env: ManualEditEnv,
) -> None:
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)

    before_text = _text_of(edit_env, project.id)
    result = _propose(edit_env, project.id, operations_=_three_hunks(units), key="patch-1")

    assert result.is_committed
    proposal_id = result.commit.changed_artifact_ids[0].value
    # 该 Commit 只登记了提案 Artifact，没有任何正文对象被改写
    assert len(result.commit.changed_artifact_ids) == 1
    assert before_text == _text_of(edit_env, project.id) == "第一段\n\n第二段\n\n第三段"

    proposal = _proposal(edit_env, project.id, proposal_id)
    assert proposal.source == "agent"
    assert proposal.status() == PROPOSAL_PENDING
    assert len(proposal.hunks) == 3
    assert all(hunk.status == HUNK_PENDING for hunk in proposal.hunks)
    assert proposal.base  # 提案记下所基于的章基础，接受时据此做并发检查


# ---------------------------------------------------------------------------
# 部分接受：只接受中间 hunk，首尾不变
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_accept_only_middle_hunk_keeps_surrounding_paragraphs(
    edit_env: ManualEditEnv,
) -> None:
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)
    proposal_id = (
        _propose(edit_env, project.id, operations_=_three_hunks(units), key="patch-1")
        .commit.changed_artifact_ids[0]
        .value
    )
    middle = _proposal(edit_env, project.id, proposal_id).hunks[1].hunk_id

    accepted = _accept(
        edit_env, project.id, proposal_id=proposal_id, hunk_ids=(middle,), key="acc-1"
    )

    assert accepted.is_committed
    # 只有中间段被改，首尾不变
    assert _text_of(edit_env, project.id) == "第一段\n\n乙\n\n第三段"
    # 未接受的 hunk 状态可追踪：一个 accepted、两个仍 pending
    proposal = _proposal(edit_env, project.id, proposal_id)
    assert [hunk.status for hunk in proposal.hunks] == [
        HUNK_PENDING,
        HUNK_ACCEPTED,
        HUNK_PENDING,
    ]
    assert proposal.status() == PROPOSAL_PENDING


@pytest.mark.anyio
async def test_unaccepted_hunks_stay_trackable_then_can_be_rejected(
    edit_env: ManualEditEnv,
) -> None:
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)
    proposal_id = (
        _propose(edit_env, project.id, operations_=_three_hunks(units), key="patch-1")
        .commit.changed_artifact_ids[0]
        .value
    )
    middle = _proposal(edit_env, project.id, proposal_id).hunks[1].hunk_id
    _accept(edit_env, project.id, proposal_id=proposal_id, hunk_ids=(middle,), key="acc-1")

    # 拒绝剩余未决 hunk（省略 hunk_ids = 拒绝全部未决）
    rejected = _reject(edit_env, project.id, proposal_id=proposal_id, key="rej-1")

    assert rejected.is_committed
    assert _text_of(edit_env, project.id) == "第一段\n\n乙\n\n第三段"  # 拒绝不改正文
    proposal = _proposal(edit_env, project.id, proposal_id)
    assert [hunk.status for hunk in proposal.hunks] == [
        HUNK_REJECTED,
        HUNK_ACCEPTED,
        HUNK_REJECTED,
    ]
    assert proposal.status() == PROPOSAL_PARTIAL

    # 没有未决 hunk 时再次拒绝 → 合法空结果（不提交、不抛异常）
    again = _reject(edit_env, project.id, proposal_id=proposal_id, key="rej-2")
    assert again.status == NO_CHANGE


# ---------------------------------------------------------------------------
# 范围与身份硬约束
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_patch_scope_violation_for_other_chapter_unit(edit_env: ManualEditEnv) -> None:
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    _import_chapter(
        edit_env, project.id, chapter_id=OTHER, title="第二章", text="别章的段落", key="imp2"
    )
    other_unit = _unit_ids(edit_env, project.id, OTHER)[0]
    head_before = _head(edit_env, project.id)

    with pytest.raises(ViolationError) as exc:
        _propose(
            edit_env,
            project.id,
            operations_=(operations.Replace(unit_id=other_unit, text="越界改"),),
            key="patch-scope",
        )

    error = exc.value
    assert error.category == ResultCategory.VIOLATION
    assert error.code == ec.MANUSCRIPT_PATCH_SCOPE
    assert error.where and error.why and error.fix  # 三要素齐全
    assert error.details["unit_id"] == other_unit
    assert error.details["chapter_id"] == CHAPTER
    assert _head(edit_env, project.id) == head_before  # 越界提案不落库


@pytest.mark.anyio
async def test_unknown_hunk_and_unknown_proposal_are_rejected(
    edit_env: ManualEditEnv,
) -> None:
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)
    proposal_id = (
        _propose(edit_env, project.id, operations_=_three_hunks(units), key="patch-1")
        .commit.changed_artifact_ids[0]
        .value
    )

    with pytest.raises(ViolationError) as exc:
        _accept(
            edit_env,
            project.id,
            proposal_id=proposal_id,
            hunk_ids=("hunk_not_in_proposal",),
            key="acc-bad",
        )
    assert exc.value.code == ec.MANUSCRIPT_UNKNOWN_HUNK

    with pytest.raises(ViolationError) as exc2:
        _accept(edit_env, project.id, proposal_id="art_missing", hunk_ids=("x",), key="acc-missing")
    assert exc2.value.code == ec.MANUSCRIPT_UNKNOWN_PROPOSAL


@pytest.mark.anyio
async def test_accept_after_manuscript_moved_conflicts(edit_env: ManualEditEnv) -> None:
    """提案登记后正文被他处改动 → 接受不得静默落在旧基础上。"""
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)
    proposal_id = (
        _propose(edit_env, project.id, operations_=_three_hunks(units), key="patch-1")
        .commit.changed_artifact_ids[0]
        .value
    )
    proposal = _proposal(edit_env, project.id, proposal_id)
    middle = proposal.hunks[1].hunk_id

    # 作者先自己改了同一段（正文基础移动）
    await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="save-moved",
            operations=(operations.Replace(unit_id=units[1], text="作者改"),),
        )
    )

    with pytest.raises(ViolationError) as exc:
        _accept(edit_env, project.id, proposal_id=proposal_id, hunk_ids=(middle,), key="acc-stale")

    error = exc.value
    assert error.code == ec.MANUSCRIPT_BASE_CONFLICT
    assert error.details["expected"] == proposal.base
    assert error.details["current"] != proposal.base
    assert error.details["current_text"] == "第一段\n\n作者改\n\n第三段"
    # 冲突时正文保持作者版本，Patch 未被强制落盘
    assert _text_of(edit_env, project.id) == "第一段\n\n作者改\n\n第三段"


# ---------------------------------------------------------------------------
# 连续逐 hunk 接受：本提案自己的改动不得被当成外部编辑（评审 #4）
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_accept_remaining_hunks_after_own_change_does_not_conflict(
    edit_env: ManualEditEnv,
) -> None:
    """先接受中间 hunk，再接受未改动的首段 hunk：不得报 MANUSCRIPT_BASE_CONFLICT。

    首次接受改变了正文基础；提案必须记住「自己造成的移动」，使剩余 hunk 仍可
    基于应用后的基础继续接受，而不是把本提案的编辑误判为外部冲突。
    """
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)
    proposal_id = (
        _propose(edit_env, project.id, operations_=_three_hunks(units), key="patch-1")
        .commit.changed_artifact_ids[0]
        .value
    )
    proposal = _proposal(edit_env, project.id, proposal_id)
    first, middle, last = (hunk.hunk_id for hunk in proposal.hunks)

    # 先接受中间 hunk：正文基础随之移动
    _accept(edit_env, project.id, proposal_id=proposal_id, hunk_ids=(middle,), key="acc-mid")
    assert _text_of(edit_env, project.id) == "第一段\n\n乙\n\n第三段"

    # 再接受未改动的首段 hunk：基础已被本提案自己的上一次接受移动，不得冲突
    accepted_first = _accept(
        edit_env, project.id, proposal_id=proposal_id, hunk_ids=(first,), key="acc-first"
    )
    assert accepted_first.is_committed
    assert _text_of(edit_env, project.id) == "甲\n\n乙\n\n第三段"

    # 依次接受最后一个 hunk 也应成功；全部接受后提案状态为 accepted
    accepted_last = _accept(
        edit_env, project.id, proposal_id=proposal_id, hunk_ids=(last,), key="acc-last"
    )
    assert accepted_last.is_committed
    assert _text_of(edit_env, project.id) == "甲\n\n乙\n\n丙"
    proposal = _proposal(edit_env, project.id, proposal_id)
    assert [hunk.status for hunk in proposal.hunks] == [HUNK_ACCEPTED] * 3
    assert proposal.status() == PROPOSAL_ACCEPTED


@pytest.mark.anyio
async def test_external_edit_after_own_accept_still_conflicts(
    edit_env: ManualEditEnv,
) -> None:
    """本提案接受后再被他处改动 → 后续接受仍需冲突：区分「自己的改动」与「外部编辑」。"""
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)
    proposal_id = (
        _propose(edit_env, project.id, operations_=_three_hunks(units), key="patch-1")
        .commit.changed_artifact_ids[0]
        .value
    )
    first, middle, _last = (
        hunk.hunk_id for hunk in _proposal(edit_env, project.id, proposal_id).hunks
    )

    _accept(edit_env, project.id, proposal_id=proposal_id, hunk_ids=(middle,), key="acc-mid")
    # 作者在他处改动末段（非本提案已接受的变化）
    await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="save-moved",
            operations=(operations.Replace(unit_id=units[2], text="外部改"),),
        )
    )

    with pytest.raises(ViolationError) as exc:
        _accept(edit_env, project.id, proposal_id=proposal_id, hunk_ids=(first,), key="acc-stale")
    assert exc.value.code == ec.MANUSCRIPT_BASE_CONFLICT
    assert _text_of(edit_env, project.id) == "第一段\n\n乙\n\n外部改"
