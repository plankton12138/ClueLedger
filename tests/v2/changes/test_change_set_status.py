"""T15.1 ChangeSet 与状态机测试。

覆盖计划 §6 第一条「ChangeSet 全状态机」：
- 合法迁移路径全部走通，历史记录 actor/时间/原因/基础 Commit；
- 非法迁移全部拒绝（状态机是硬约束，放命令层不靠 prompt——四透镜②）；
- Analyzed → Approved 仅接受作者用例产生的凭证（缺凭证或 agent actor 拒绝）；
- committed 必须携带产生结果的 Commit ID；
- 内容往返（持久化形状）与跨 Commit 历史保持；
- 幂等重放返回首次结果。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from hastings.application.errors import ViolationError
from hastings.domain.changes import error_codes as ec
from hastings.domain.changes import status as st
from hastings.domain.changes.commands import advance_change_set
from hastings.domain.changes.models import ChangeSet
from hastings.domain.repository.ids import Actor, CommitId, ProjectId
from hastings.domain.repository.results import COMMITTED

from .conftest import AUTHOR, ChangesEnv, head_of, make_open_command

_OPEN_AT = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
_ADVANCE_AT = datetime(2026, 10, 15, 12, 5, 0, tzinfo=UTC)


def _setup(env: ChangesEnv) -> str:
    """建项目并返回其真实 project_id（create_project 生成内部 ID，不是 project_id）。"""
    return env.handler.create_project("冬馆").id.value


def _open(env: ChangesEnv, project_id: str, key: str = "k1") -> str:  # 返回 change_set_id
    result = env.handler.execute(
        make_open_command(),
        project_id=ProjectId(project_id),
        idempotency_key=key,
        summary="打开变更集",
    )
    assert result.is_committed
    return result.change.artifact_changes[0].artifact_id.value


def _read(env: ChangesEnv, project_id: str) -> ChangeSet:
    from hastings.domain.changes.catalog import ChangesCatalog

    head = head_of(env, project_id)
    assert head is not None
    snapshot = env.service.get_snapshot(ProjectId(project_id), CommitId(head))
    catalog = ChangesCatalog.from_snapshot(snapshot)
    entries = catalog.by_kind("changes.change_set")
    assert len(entries) == 1
    return entries[0].model()


def _advance(
    env: ChangesEnv,
    project_id: str,
    change_set_id: str,
    target: str,
    key: str,
    **overrides: object,
) -> object:
    from hastings.application.handlers.change_commands import change_set_command

    kwargs: dict[str, object] = {
        "change_set_id": change_set_id,
        "target": target,
        "actor": AUTHOR,
        "at": _ADVANCE_AT,
        "reason": f"推进到 {target}",
    }
    kwargs.update(overrides)
    return env.handler.execute(
        change_set_command(advance_change_set, **kwargs),
        project_id=ProjectId(project_id),
        idempotency_key=key,
        summary=f"推进到 {target}",
    )


_key_counter = iter(range(1000, 9999))


def _walk_to(env: ChangesEnv, project_id: str, change_set_id: str, targets: list[str]) -> None:
    """依次推进（每个目标一个独立幂等键），失败时报出目标便于定位。"""
    for target in targets:
        kwargs: dict[str, object] = {}
        if target == st.CHANGE_APPROVED:
            kwargs["approval_ref"] = "rcpt_author_1"
        if target == st.CHANGE_COMMITTED:
            kwargs["commit_id"] = "cmt_result_1"
        key = f"k{next(_key_counter)}"
        result = _advance(env, project_id, change_set_id, target, key, **kwargs)
        assert result.is_committed, f"推进到 {target} 失败"


class TestOpenChangeSet:
    def test_open_creates_artifact_with_history_and_event(self, changes_env: ChangesEnv) -> None:
        project_id = _setup(changes_env)
        cs_id = _open(changes_env, project_id)

        model = _read(changes_env, project_id)
        assert model.change_set_id == cs_id
        assert model.status == st.CHANGE_OPENED
        assert model.intent == "把协助者改为幕后操纵者"
        assert model.source == "manual"
        assert model.base_commit_id == "cmt_base_1"
        assert [s.artifact_id for s in model.seed_changes] == ["art_char_1"]
        # 历史首条记录：进入 opened 的完整审计（actor/时间/原因/基础 Commit）
        assert len(model.history) == 1
        record = model.history[0]
        assert record.to_status == st.CHANGE_OPENED
        assert record.actor_kind == "human" and record.actor_id == "author_test"
        assert record.at == _OPEN_AT.isoformat()
        assert record.reason == "作者发起设定修改"
        assert record.base_commit_id == "cmt_base_1"

        # 事件与 Commit 同事务落盘（ADR-007）：opened 事件可在 Outbox 中读到
        pending = asyncio.run(changes_env.repo.pending(project_id, 100))
        assert any(e["type"] == "changes.change_set.opened" for e in pending)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"intent": ""},  # 空意图
            {"source": "unknown"},  # 非法来源
            {"base_commit_id": ""},  # 空 base
        ],
    )
    def test_open_rejects_invalid_arguments(
        self, changes_env: ChangesEnv, kwargs: dict[str, object]
    ) -> None:
        project_id = _setup(changes_env)
        with pytest.raises(ViolationError) as exc:
            changes_env.handler.execute(
                make_open_command(**kwargs),
                project_id=ProjectId(project_id),
                idempotency_key="k_bad",
                summary="打开变更集",
            )
        assert exc.value.code == ec.CHANGES_INVALID_COMMAND
        assert head_of(changes_env, project_id) is None  # 违规不产生提交


class TestLegalPath:
    def test_full_path_open_to_committed(self, changes_env: ChangesEnv) -> None:
        project_id = _setup(changes_env)
        cs_id = _open(changes_env, project_id)
        _walk_to(
            changes_env,
            project_id,
            cs_id,
            [
                st.CHANGE_ANALYZING,
                st.CHANGE_ANALYZED,
                st.CHANGE_APPROVED,
                st.CHANGE_EXECUTING,
                st.CHANGE_REVIEW,
                st.CHANGE_COMMITTED,
            ],
        )

        model = _read(changes_env, project_id)
        assert model.status == st.CHANGE_COMMITTED
        # opened 首条 + 6 次推进
        assert [r.to_status for r in model.history] == [
            st.CHANGE_OPENED,
            st.CHANGE_ANALYZING,
            st.CHANGE_ANALYZED,
            st.CHANGE_APPROVED,
            st.CHANGE_EXECUTING,
            st.CHANGE_REVIEW,
            st.CHANGE_COMMITTED,
        ]
        last = model.history[-1]
        assert last.from_status == st.CHANGE_REVIEW
        assert last.commit_id == "cmt_result_1"  # 提交迁移记录产生结果的 Commit
        assert last.approval_ref == ""  # 非批准迁移不带凭证
        approved = model.history[3]
        assert approved.approval_ref == "rcpt_author_1"  # 批准凭证入历史
        for record in model.history:
            assert record.at and record.reason and record.base_commit_id  # 审计四件套齐全

    def test_failed_then_retry(self, changes_env: ChangesEnv) -> None:
        project_id = _setup(changes_env)
        cs_id = _open(changes_env, project_id)
        _walk_to(changes_env, project_id, cs_id, [st.CHANGE_ANALYZING])
        _walk_to(changes_env, project_id, cs_id, [st.CHANGE_FAILED])
        _walk_to(changes_env, project_id, cs_id, [st.CHANGE_ANALYZING, st.CHANGE_ANALYZED])
        assert _read(changes_env, project_id).status == st.CHANGE_ANALYZED

    @pytest.mark.parametrize(
        "from_path",
        [
            [st.CHANGE_ANALYZING, st.CHANGE_ANALYZED],
            [
                st.CHANGE_ANALYZING,
                st.CHANGE_ANALYZED,
                st.CHANGE_APPROVED,
                st.CHANGE_EXECUTING,
                st.CHANGE_REVIEW,
            ],
        ],
    )
    def test_abandon_is_legal_from_analyzed_and_review(
        self, changes_env: ChangesEnv, from_path: list[str]
    ) -> None:
        project_id = _setup(changes_env)
        cs_id = _open(changes_env, project_id)
        _walk_to(changes_env, project_id, cs_id, from_path)
        _walk_to(changes_env, project_id, cs_id, [st.CHANGE_ABANDONED])
        assert _read(changes_env, project_id).status == st.CHANGE_ABANDONED


class TestIllegalTransitions:
    @pytest.mark.parametrize(
        ("from_path", "target"),
        [
            ([], st.CHANGE_APPROVED),  # opened → approved：跳过分析
            ([], st.CHANGE_COMMITTED),  # opened → committed：跳过一切
            ([st.CHANGE_ANALYZING], st.CHANGE_APPROVED),  # analyzing → approved
            ([st.CHANGE_ANALYZING, st.CHANGE_ANALYZED], st.CHANGE_EXECUTING),  # 跳过批准
            ([st.CHANGE_ANALYZING, st.CHANGE_ANALYZED, st.CHANGE_APPROVED], st.CHANGE_REVIEW),
            ([st.CHANGE_ANALYZING], st.CHANGE_COMMITTED),  # analyzing → committed
            ([st.CHANGE_ANALYZING, st.CHANGE_ANALYZED], st.CHANGE_COMMITTED),
            ([], st.CHANGE_ABANDONED),  # opened 不可放弃（严格按状态图）
            (
                [
                    st.CHANGE_ANALYZING,
                    st.CHANGE_ANALYZED,
                    st.CHANGE_APPROVED,
                    st.CHANGE_EXECUTING,
                    st.CHANGE_REVIEW,
                    st.CHANGE_COMMITTED,
                ],
                st.CHANGE_ANALYZING,
            ),
        ],
    )
    def test_illegal_transition_rejected_and_state_unchanged(
        self, changes_env: ChangesEnv, from_path: list[str], target: str
    ) -> None:
        project_id = _setup(changes_env)
        cs_id = _open(changes_env, project_id)
        _walk_to(changes_env, project_id, cs_id, from_path)
        before = _read(changes_env, project_id)
        head_before = head_of(changes_env, project_id)

        kwargs: dict[str, object] = {}
        if target == st.CHANGE_APPROVED:
            kwargs["approval_ref"] = "rcpt_author_1"
        if target == st.CHANGE_COMMITTED:
            kwargs["commit_id"] = "cmt_x"
        with pytest.raises(ViolationError) as exc:
            _advance(changes_env, project_id, cs_id, target, "k_illegal", **kwargs)

        assert exc.value.code == ec.CHANGES_INVALID_TRANSITION
        assert exc.value.where and exc.value.why and exc.value.fix  # 三要素
        after = _read(changes_env, project_id)
        assert after.status == before.status  # 状态不变
        assert len(after.history) == len(before.history)  # 历史不追加
        assert head_of(changes_env, project_id) == head_before  # 违规不产生提交


class TestApprovalGate:
    def test_approve_requires_receipt(self, changes_env: ChangesEnv) -> None:
        project_id = _setup(changes_env)
        cs_id = _open(changes_env, project_id)
        _walk_to(changes_env, project_id, cs_id, [st.CHANGE_ANALYZING, st.CHANGE_ANALYZED])

        with pytest.raises(ViolationError) as exc:
            _advance(changes_env, project_id, cs_id, st.CHANGE_APPROVED, "k_no_rcpt")
        assert exc.value.code == ec.CHANGES_APPROVAL_REQUIRED
        assert _read(changes_env, project_id).status == st.CHANGE_ANALYZED

    def test_approve_rejects_agent_actor(self, changes_env: ChangesEnv) -> None:
        project_id = _setup(changes_env)
        cs_id = _open(changes_env, project_id)
        _walk_to(changes_env, project_id, cs_id, [st.CHANGE_ANALYZING, st.CHANGE_ANALYZED])

        with pytest.raises(ViolationError) as exc:
            _advance(
                changes_env,
                project_id,
                cs_id,
                st.CHANGE_APPROVED,
                "k_agent",
                approval_ref="rcpt_self",
                actor=Actor(kind="agent", id="writer_bot"),
            )
        assert exc.value.code == ec.CHANGES_APPROVAL_REQUIRED
        # Agent 不能为自己的计划签发批准（四透镜②：授权是硬约束不是提示）

    def test_committed_requires_commit_id(self, changes_env: ChangesEnv) -> None:
        project_id = _setup(changes_env)
        cs_id = _open(changes_env, project_id)
        _walk_to(
            changes_env,
            project_id,
            cs_id,
            [
                st.CHANGE_ANALYZING,
                st.CHANGE_ANALYZED,
                st.CHANGE_APPROVED,
                st.CHANGE_EXECUTING,
                st.CHANGE_REVIEW,
            ],
        )
        with pytest.raises(ViolationError) as exc:
            _advance(changes_env, project_id, cs_id, st.CHANGE_COMMITTED, "k_no_cmt")
        assert exc.value.code == ec.CHANGES_INVALID_COMMAND


class TestPersistence:
    def test_content_roundtrip(self) -> None:
        from hastings.domain.changes.commands import ChangeScope
        from hastings.domain.changes.models import SeedChange, TransitionRecord

        model = ChangeSet(
            change_set_id="art_cs1",
            intent="修改协助者角色",
            source="agent_proposal",
            base_commit_id="cmt_9",
            status=st.CHANGE_REVIEW,
            history=(
                TransitionRecord(
                    from_status="",
                    to_status=st.CHANGE_OPENED,
                    actor_kind="human",
                    actor_id="a1",
                    at="2026-10-15T12:00:00+00:00",
                    reason="打开",
                    base_commit_id="cmt_9",
                ),
            ),
            seed_changes=(SeedChange(artifact_id="art_c1", field="role", reason="评审结论"),),
            scope=ChangeScope(kinds=("narrative.beat",), roots=("art_arc1",), max_hops=4),
        )
        assert ChangeSet.from_content(model.to_content()) == model

    def test_history_persists_across_commits(self, changes_env: ChangesEnv) -> None:
        project_id = _setup(changes_env)
        cs_id = _open(changes_env, project_id)
        _walk_to(changes_env, project_id, cs_id, [st.CHANGE_ANALYZING, st.CHANGE_ANALYZED])

        model = _read(changes_env, project_id)
        assert [r.to_status for r in model.history] == [
            st.CHANGE_OPENED,
            st.CHANGE_ANALYZING,
            st.CHANGE_ANALYZED,
        ]

    def test_replay_same_key_returns_first_result(self, changes_env: ChangesEnv) -> None:
        project_id = _setup(changes_env)
        first = env_open_result = changes_env.handler.execute(
            make_open_command(),
            project_id=ProjectId(project_id),
            idempotency_key="k_replay",
            summary="打开变更集",
        )
        assert env_open_result.is_committed
        replay = changes_env.handler.execute(
            make_open_command(),
            project_id=ProjectId(project_id),
            idempotency_key="k_replay",
            summary="打开变更集",
        )
        assert replay.status == COMMITTED
        assert replay.commit_id == first.commit_id  # 重放返回首次结果，不产生新 Commit
