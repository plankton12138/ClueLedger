"""T15.2 影响分析工作流测试：真实 Commit 历史 → 建图 → 遍历 → 报告落库。

区别于纯领域遍历测试（``test_impact_traversal.py``）：这里验证工作流编排——
opened 先推进 analyzing 再记录（两次提交）、报告绑定 base_commit_id（不变量 1）、
head 前移后 stale 可判定、review 状态重算复用同一报告 Artifact、空结果合法落库
（不变量 9）、analyzed 状态重复 analyze 幂等重放。
"""

from __future__ import annotations

from typing import Any

import pytest

from hastings.application.errors import ViolationError
from hastings.application.workflows.change_analysis import AnalyzeChangeImpact, AnalyzeIntent
from hastings.domain.changes import error_codes as ec
from hastings.domain.changes import kinds
from hastings.domain.changes import status as st
from hastings.domain.changes.catalog import ChangesCatalog
from hastings.domain.changes.impact import ImpactReport
from hastings.domain.changes.models import ChangeScope, SeedChange
from hastings.domain.repository.ids import ArtifactId, CommitId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange

from .conftest import AUTHOR, head_of, make_advance_command, make_open_command, run_command


def _seed_chain(env: Any, *, kind_overrides: dict[str, str] | None = None) -> tuple[Any, str]:
    """播种动机链 art_a → art_b → art_c；返回 (project, head)。"""
    project = env.handler.create_project("冬馆")
    branch = env.service.get_branch(project.id, "main")
    node_kinds = {
        "art_a": "story.character",
        "art_b": "story.plot_beat",
        "art_c": "story.plot_beat",
    }
    if kind_overrides:
        node_kinds.update(kind_overrides)
    env.service.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=AUTHOR,
            idempotency_key="seed-1",
            summary="播种动机链",
            changes=tuple(
                ArtifactChange(artifact_id=ArtifactId(aid), content={"name": aid}, kind=k)
                for aid, k in node_kinds.items()
            ),
            relations=(
                RelationChange(
                    action="add",
                    source=ArtifactId("art_a"),
                    relation_type="motivates",
                    target=ArtifactId("art_b"),
                ),
                RelationChange(
                    action="add",
                    source=ArtifactId("art_b"),
                    relation_type="motivates",
                    target=ArtifactId("art_c"),
                ),
            ),
        )
    )
    head = head_of(env, project.id)
    assert head is not None
    return project, head


def _open(
    env: Any, project: Any, base: str, *, seed: str = "art_a", key: str = "open-1", **overrides: Any
) -> str:
    command = make_open_command(
        base_commit_id=base,
        seed_changes=(SeedChange(artifact_id=seed, field="role"),),
        **overrides,
    )
    result = run_command(env, project.id, command, key=key)
    assert result.is_committed
    return result.change.artifact_changes[0].artifact_id.value


def _workflow(env: Any) -> AnalyzeChangeImpact:
    return AnalyzeChangeImpact(env.handler, env.service, env.clock, AUTHOR)


def _catalog(env: Any, project: Any) -> ChangesCatalog:
    head = head_of(env, project.id)
    assert head is not None
    return ChangesCatalog.from_snapshot(env.service.get_snapshot(project.id, CommitId(head)))


def test_analyze_from_opened_advances_and_records(changes_env: Any) -> None:
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)

    result = _workflow(env).analyze(AnalyzeIntent(project.id, cs_id, "analyze-1"))

    assert result.status == st.CHANGE_ANALYZED
    assert result.item_count == 2  # b 与 c（motivates forward 传播）
    assert result.commit_id is not None
    catalog = _catalog(env, project)
    entry = catalog.get(result.report_id)
    assert entry is not None and entry.kind == kinds.KIND_IMPACT_REPORT
    report: ImpactReport = entry.model()
    assert report.change_set_id == cs_id
    assert report.base_commit_id == head  # 报告绑定基础版本（不变量 1）
    assert [i.target_artifact_id for i in report.items] == ["art_b", "art_c"]
    model = catalog.require_change_set(cs_id, where="test").model()
    assert model.status == st.CHANGE_ANALYZED
    # 两次提交：open→analyzing（工作流推进）、analyzing→analyzed（记录时同提交迁移）
    assert [(r.from_status, r.to_status) for r in model.history][-2:] == [
        (st.CHANGE_OPENED, st.CHANGE_ANALYZING),
        (st.CHANGE_ANALYZING, st.CHANGE_ANALYZED),
    ]


def test_report_stale_after_head_moves(changes_env: Any) -> None:
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)
    result = _workflow(env).analyze(AnalyzeIntent(project.id, cs_id, "analyze-1"))

    _open(env, project, head, key="open-2", intent="另一个变更集")
    new_head = head_of(env, project.id)
    assert new_head != head

    report: ImpactReport = _catalog(env, project).get(result.report_id).model()
    assert not report.is_stale(head)  # 对 base 不过期
    assert report.is_stale(new_head)  # head 前移后过期（不变量 2）


def test_recompute_in_review_reuses_report_artifact(changes_env: Any) -> None:
    """review 状态重算：复用同一报告 Artifact（新 Revision），状态不动。"""
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)
    workflow = _workflow(env)
    first = workflow.analyze(AnalyzeIntent(project.id, cs_id, "analyze-1"))

    for target, extra, key in (
        (st.CHANGE_APPROVED, {"approval_ref": "receipt-1"}, "w1"),
        (st.CHANGE_EXECUTING, {}, "w2"),
        (st.CHANGE_REVIEW, {}, "w3"),
    ):
        res = run_command(env, project.id, make_advance_command(target, **extra)(cs_id), key=key)
        assert res.is_committed
    revision_before = _catalog(env, project).get(first.report_id).revision_id

    env.clock.advance(60.0)  # 真实场景重算发生在晚于首次分析的时刻；时钟冻结会连内容一起冻结
    second = workflow.analyze(AnalyzeIntent(project.id, cs_id, "analyze-2"))

    assert second.status == st.CHANGE_REVIEW  # 重算不改变状态
    assert second.report_id == first.report_id  # 同一 Artifact
    assert second.commit_id is not None
    assert _catalog(env, project).get(first.report_id).revision_id != revision_before


def test_analyze_replay_in_analyzed_returns_existing_report(changes_env: Any) -> None:
    """analyzed 状态重复 analyze = 幂等重放：返回已落库报告，不产生新提交。"""
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)
    workflow = _workflow(env)
    first = workflow.analyze(AnalyzeIntent(project.id, cs_id, "analyze-1"))
    head_after = head_of(env, project.id)

    retry = workflow.analyze(AnalyzeIntent(project.id, cs_id, "analyze-1"))

    assert retry.report_id == first.report_id
    assert retry.item_count == first.item_count
    assert retry.status == st.CHANGE_ANALYZED
    assert retry.commit_id is None  # 无新提交
    assert head_of(env, project.id) == head_after


def test_empty_downstream_is_legal(changes_env: Any) -> None:
    """种子无下游依赖：空报告照常落库，不建空分支也不报错（不变量 9）。"""
    env = changes_env
    project = env.handler.create_project("冬馆")
    branch = env.service.get_branch(project.id, "main")
    env.service.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=AUTHOR,
            idempotency_key="seed-solo",
            summary="播种孤立节点",
            changes=(
                ArtifactChange(
                    artifact_id=ArtifactId("art_d"), content={"name": "d"}, kind="story.character"
                ),
            ),
        )
    )
    head = head_of(env, project.id)
    assert head is not None
    cs_id = _open(env, project, head, seed="art_d")

    result = _workflow(env).analyze(AnalyzeIntent(project.id, cs_id, "analyze-1"))

    assert result.status == st.CHANGE_ANALYZED
    assert result.item_count == 0
    report: ImpactReport = _catalog(env, project).get(result.report_id).model()
    assert report.items == ()


def test_unknown_change_set_rejected_without_side_effect(changes_env: Any) -> None:
    env = changes_env
    project, head = _seed_chain(env)

    with pytest.raises(ViolationError) as exc:
        _workflow(env).analyze(AnalyzeIntent(project.id, "chg_missing", "analyze-1"))

    assert exc.value.code == ec.CHANGES_UNKNOWN_CHANGE_SET
    assert exc.value.fix  # 指向下一步
    assert head_of(env, project.id) == head  # 无副作用


def test_analyze_rejected_in_approved_state(changes_env: Any) -> None:
    """approved 不允许分析：前置拒绝并指向重算时机，不白跑一遍遍历。"""
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)
    run_command(env, project.id, make_advance_command(st.CHANGE_ANALYZING)(cs_id), key="w1")
    run_command(env, project.id, make_advance_command(st.CHANGE_ANALYZED)(cs_id), key="w2")
    run_command(
        env,
        project.id,
        make_advance_command(st.CHANGE_APPROVED, approval_ref="receipt-1")(cs_id),
        key="w3",
    )
    head_before = head_of(env, project.id)

    with pytest.raises(ViolationError) as exc:
        _workflow(env).analyze(AnalyzeIntent(project.id, cs_id, "analyze-1"))

    assert exc.value.code == ec.CHANGES_INVALID_TRANSITION
    assert "executing" in exc.value.fix  # 指向重算时机
    assert head_of(env, project.id) == head_before  # 无副作用


def test_scope_kinds_truncation_recorded_in_report(changes_env: Any) -> None:
    """scope.kinds 白名单截断：被截断项进报告，没查 ≠ 查不到。"""
    env = changes_env
    project, head = _seed_chain(env, kind_overrides={"art_c": "story.chapter"})
    cs_id = _open(
        env,
        project,
        head,
        scope=ChangeScope(kinds=("story.character", "story.plot_beat")),
    )

    result = _workflow(env).analyze(AnalyzeIntent(project.id, cs_id, "analyze-1"))

    assert result.status == st.CHANGE_ANALYZED
    report: ImpactReport = _catalog(env, project).get(result.report_id).model()
    assert [i.target_artifact_id for i in report.items] == ["art_b"]
    assert [t.artifact_id for t in report.truncated] == ["art_c"]
    assert "scope.kinds" in report.truncated[0].reason
    assert report.stats.truncated_count == 1
