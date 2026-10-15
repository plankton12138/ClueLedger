"""T15.5 Approval Receipt 测试：作者批准的机器凭证。

覆盖计划 §6「Approval 绑定 base/scope」与 T15.5「测试过期、撤销、次数和 scope」：
- 签发门槛：只有作者、只有 analyzed、必须有报告与计划；重复签发拒绝；
- 指纹契约：范围/报告/计划任一漂移即过期（确认内容可机器校对）；
- 批准即消费：同提交原子生效，一次批准只授权一次执行（次数）；
- 伪造引用与跨变更集盗用一律拒绝（自由文本 ref 自本阶段起不再被接受）；
- 撤销：作者反悔的正规出口，撤销后永久失效，可重新签发。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from hastings.application.errors import ViolationError
from hastings.application.handlers.change_commands import change_set_command
from hastings.application.workflows.change_analysis import AnalyzeChangeImpact, AnalyzeIntent
from hastings.domain.changes import error_codes as ec
from hastings.domain.changes import kinds
from hastings.domain.changes import status as st
from hastings.domain.changes.commands import record_revision_plan, revoke_approval_receipt
from hastings.domain.changes.errors import ChangesViolation
from hastings.domain.changes.impact import EdgeHop, ImpactItem, ImpactReport
from hastings.domain.changes.models import ChangeScope, ChangeSet, SeedChange
from hastings.domain.changes.planning import ExcludedItem, build_revision_plan, set_step_scope
from hastings.domain.changes.receipt import (
    RECEIPT_ACTIVE,
    RECEIPT_CONSUMED,
    RECEIPT_REVOKED,
    ApprovalReceipt,
    confirm_fingerprint,
    mark_receipt,
    require_consumable,
)
from hastings.domain.repository.ids import Actor, ArtifactId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange

from .conftest import (
    AUTHOR,
    catalog_of,
    change_set_of,
    head_of,
    issue_receipt,
    make_advance_command,
    make_open_command,
    plan_of,
    receipt_of,
    record_empty_report,
    record_projection_plan,
    report_of,
    revoke_receipt,
    run_command,
)

_AGENT = Actor(kind="agent", id="writer_bot")


# ---------------------------------------------------------------------------
# 仓库夹具：播种 → 打开 → 分析 → 计划落库（批准的前置全就绪）
# ---------------------------------------------------------------------------
def _seed_one(env: Any, *, aid: str = "art_a", kind: str = "story.character") -> tuple[Any, str]:
    """播种 a→b→c 动机链（种子 aid 沿 motivates 传播出两项影响）。"""
    project = env.handler.create_project("冬馆")
    branch = env.service.get_branch(project.id, "main")
    kinds_map = {aid: kind, "art_b": "story.plot_beat", "art_c": "story.plot_beat"}
    env.service.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=AUTHOR,
            idempotency_key="seed-1",
            summary="播种影响链",
            changes=tuple(
                ArtifactChange(artifact_id=ArtifactId(node), content={"name": node}, kind=k)
                for node, k in kinds_map.items()
            ),
            relations=(
                RelationChange(
                    action="add",
                    source=ArtifactId(aid),
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


def _open(env: Any, project: Any, base: str, *, key: str = "open-1") -> str:
    command = make_open_command(
        base_commit_id=base,
        seed_changes=(SeedChange(artifact_id="art_a", field="role"),),
    )
    result = run_command(env, project.id, command, key=key)
    assert result.is_committed
    return result.change.artifact_changes[0].artifact_id.value


def _analyze(env: Any, project: Any, cs_id: str, *, key: str = "analyze-1") -> Any:
    return AnalyzeChangeImpact(env.handler, env.service, env.clock, AUTHOR).analyze(
        AnalyzeIntent(project.id, cs_id, key)
    )


def _ready(env: Any) -> tuple[Any, str, str]:
    """播种 + 打开 + 分析 + 计划落库；返回 (project, change_set_id, head)。"""
    project, head = _seed_one(env)
    cs_id = _open(env, project, head)
    result = _analyze(env, project, cs_id)
    assert result.status == st.CHANGE_ANALYZED
    record_projection_plan(env, project.id, cs_id, key="plan-1")
    return project, cs_id, head


def _receipt_id(result: Any) -> str:
    return result.change.artifact_changes[0].artifact_id.value


def _approve(env: Any, project: Any, cs_id: str, *, ref: str, key: str = "adv-1") -> Any:
    return run_command(
        env,
        project.id,
        make_advance_command(st.CHANGE_APPROVED, approval_ref=ref)(cs_id),
        key=key,
    )


# ---------------------------------------------------------------------------
# 纯领域夹具：不需要仓库
# ---------------------------------------------------------------------------
def _hop(source: str, rel: str, target: str) -> EdgeHop:
    return EdgeHop(source=source, relation_type=rel, target=target)


def _item(target: str, severity: str, path: tuple[EdgeHop, ...] = ()) -> ImpactItem:
    return ImpactItem(
        target_artifact_id=target,
        severity=severity,
        impact_kind="structure",
        path=path,
        reason="测试项",
    )


def _report(*items: ImpactItem) -> ImpactReport:
    return ImpactReport(
        report_id="rep_1", change_set_id="chg_1", base_commit_id="cmt_base_1", items=items
    )


def _cs(**overrides: Any) -> ChangeSet:
    model = ChangeSet(
        change_set_id="chg_1",
        intent="改动设定",
        source="manual",
        base_commit_id="cmt_base_1",
        status=st.CHANGE_ANALYZED,
    )
    return replace(model, **overrides) if overrides else model


def _confirmed_receipt(**overrides: Any) -> tuple[ChangeSet, ImpactReport, Any, ApprovalReceipt]:
    """一组自洽的（变更集, 报告, 计划, 凭证）：指纹按当前内容计算。"""
    cs = _cs()
    report = _report(_item("art_b", "should_fix", (_hop("art_a", "motivates", "art_b"),)))
    plan = build_revision_plan(report)
    receipt = ApprovalReceipt(
        receipt_id="rcpt_1",
        change_set_id="chg_1",
        base_commit_id=cs.base_commit_id,
        fingerprint=confirm_fingerprint(cs, report, plan),
        brief={"intent": cs.intent},
    )
    if overrides:
        receipt = replace(receipt, **overrides)
    return cs, report, plan, receipt


# ---------------------------------------------------------------------------
# 纯领域：指纹契约与消费校验
# ---------------------------------------------------------------------------
def test_receipt_content_roundtrip() -> None:
    _cs, _report, _plan, receipt = _confirmed_receipt()
    assert ApprovalReceipt.from_content(receipt.to_content()) == receipt


def test_receipt_rejects_unknown_status() -> None:
    with pytest.raises(ChangesViolation):
        ApprovalReceipt(
            receipt_id="rcpt_1",
            change_set_id="chg_1",
            base_commit_id="cmt_base_1",
            fingerprint="x",
            status="weird",
        )


def test_fingerprint_is_deterministic_and_drift_sensitive() -> None:
    cs, report, plan, receipt = _confirmed_receipt()
    assert confirm_fingerprint(cs, report, plan) == receipt.fingerprint  # 同内容 → 同指纹

    # 范围漂移：scope.kinds 变化即失效
    scoped = replace(cs, scope=ChangeScope(kinds=("story.character",)))
    assert confirm_fingerprint(scoped, report, plan) != receipt.fingerprint
    # 基础版本漂移
    assert (
        confirm_fingerprint(replace(cs, base_commit_id="cmt_2"), report, plan)
        != receipt.fingerprint
    )
    # 报告漂移：严重度变化即失效
    drifted = _report(_item("art_b", "must_fix", (_hop("art_a", "motivates", "art_b"),)))
    assert confirm_fingerprint(cs, drifted, plan) != receipt.fingerprint
    # 计划漂移：移出一项即失效
    edited = replace(
        plan,
        steps=(),
        unassigned=(
            ExcludedItem(
                item_index=0, target_artifact_id="art_b", severity="should_fix", reason="移出"
            ),
        ),
    )
    assert confirm_fingerprint(cs, report, edited) != receipt.fingerprint


def test_require_consumable_accepts_intact_receipt() -> None:
    cs, report, plan, receipt = _confirmed_receipt()
    require_consumable(
        receipt, change_set=cs, fingerprint=confirm_fingerprint(cs, report, plan), where="t"
    )  # 不抛即通过


def test_require_consumable_rejects_foreign_change_set() -> None:
    cs, report, plan, receipt = _confirmed_receipt()
    with pytest.raises(ChangesViolation) as exc:
        require_consumable(
            receipt,
            change_set=replace(cs, change_set_id="chg_2"),
            fingerprint=confirm_fingerprint(cs, report, plan),
            where="t",
        )
    assert exc.value.code == ec.CHANGES_RECEIPT_INVALID
    assert "chg_2" in exc.value.why


def test_require_consumable_rejects_consumed_and_revoked() -> None:
    cs, report, plan, receipt = _confirmed_receipt()
    fingerprint = confirm_fingerprint(cs, report, plan)
    with pytest.raises(ChangesViolation) as exc:
        require_consumable(
            mark_receipt(receipt, RECEIPT_CONSUMED, at="t1", note="已批准"),
            change_set=cs,
            fingerprint=fingerprint,
            where="t",
        )
    assert exc.value.code == ec.CHANGES_RECEIPT_CONSUMED
    assert "一次批准只授权一次执行" in exc.value.why

    with pytest.raises(ChangesViolation) as exc:
        require_consumable(
            mark_receipt(receipt, RECEIPT_REVOKED, at="t2", note="作者反悔"),
            change_set=cs,
            fingerprint=fingerprint,
            where="t",
        )
    assert exc.value.code == ec.CHANGES_RECEIPT_REVOKED


def test_require_consumable_rejects_base_and_content_drift() -> None:
    cs, report, plan, receipt = _confirmed_receipt()
    with pytest.raises(ChangesViolation) as exc:
        require_consumable(
            receipt,
            change_set=replace(cs, base_commit_id="cmt_9"),
            fingerprint=confirm_fingerprint(cs, report, plan),
            where="t",
        )
    assert exc.value.code == ec.CHANGES_RECEIPT_STALE
    assert "基础版本" in exc.value.why

    with pytest.raises(ChangesViolation) as exc:
        require_consumable(receipt, change_set=cs, fingerprint="deadbeef", where="t")
    assert exc.value.code == ec.CHANGES_RECEIPT_STALE
    assert "漂移" in exc.value.why


# ---------------------------------------------------------------------------
# 签发门槛
# ---------------------------------------------------------------------------
def test_issue_receipt_records_fingerprint_and_brief(changes_env: Any) -> None:
    env = changes_env
    project, cs_id, head = _ready(env)

    result = issue_receipt(env, project.id, cs_id, key="rcpt-1")

    assert result.is_committed
    ref = _receipt_id(result)
    entry = catalog_of(env, project.id).get(ref)
    assert entry is not None and entry.kind == kinds.KIND_APPROVAL_RECEIPT
    receipt: ApprovalReceipt = entry.model()
    assert receipt.status == RECEIPT_ACTIVE
    assert receipt.change_set_id == cs_id
    assert receipt.base_commit_id == head
    # 指纹与目录当前内容一致（作者确认的就是将执行的内容）
    assert receipt.fingerprint == confirm_fingerprint(
        change_set_of(env, project.id, cs_id),
        report_of(env, project.id, cs_id),
        plan_of(env, project.id, cs_id),
    )
    assert receipt.issued_by == "human:author_test"
    # 确认页简报：变化原因、基础版本、允许对象、Patch 数、影响分布
    brief = receipt.brief
    assert brief["intent"] == "把协助者改为幕后操纵者"
    assert brief["base_commit_id"] == head
    assert brief["scope"] == {"kinds": [], "roots": [], "max_hops": None}
    assert brief["step_count"] == 1  # b、c 同层 → 单步
    assert brief["item_count"] == 2
    assert brief["must_fix_targets"] == []
    pending = asyncio.run(env.repo.pending(project.id.value, 100))
    assert any(e["type"] == "changes.receipt.issued" for e in pending)


def test_issue_requires_analyzed_status(changes_env: Any) -> None:
    env = changes_env
    project, head = _seed_one(env)
    cs_id = _open(env, project, head)
    run_command(env, project.id, make_advance_command(st.CHANGE_ANALYZING)(cs_id), key="w1")

    with pytest.raises(ViolationError) as exc:
        issue_receipt(env, project.id, cs_id, key="rcpt-1")

    assert exc.value.code == ec.CHANGES_INVALID_TRANSITION
    assert "分析" in exc.value.fix


def test_issue_requires_report(changes_env: Any) -> None:
    """手工推进到 analyzed（无报告落库）：确认没有依据，前置拒绝。"""
    env = changes_env
    project, head = _seed_one(env)
    cs_id = _open(env, project, head)
    run_command(env, project.id, make_advance_command(st.CHANGE_ANALYZING)(cs_id), key="w1")
    run_command(env, project.id, make_advance_command(st.CHANGE_ANALYZED)(cs_id), key="w2")

    with pytest.raises(ViolationError) as exc:
        issue_receipt(env, project.id, cs_id, key="rcpt-1")

    assert exc.value.code == ec.CHANGES_INVALID_COMMAND
    assert "分析" in exc.value.fix


def test_issue_requires_plan(changes_env: Any) -> None:
    """有报告无计划：作者确认缺少执行范围，前置拒绝。"""
    env = changes_env
    project, head = _seed_one(env)
    cs_id = _open(env, project, head)
    run_command(env, project.id, make_advance_command(st.CHANGE_ANALYZING)(cs_id), key="w1")
    record_empty_report(env, project.id, cs_id, key="rep-1")

    with pytest.raises(ViolationError) as exc:
        issue_receipt(env, project.id, cs_id, key="rcpt-1")

    assert exc.value.code == ec.CHANGES_INVALID_COMMAND
    assert "计划" in exc.value.fix


def test_issue_rejects_agent_actor(changes_env: Any) -> None:
    env = changes_env
    project, cs_id, _head = _ready(env)

    with pytest.raises(ViolationError) as exc:
        issue_receipt(env, project.id, cs_id, key="rcpt-1", actor=_AGENT)

    assert exc.value.code == ec.CHANGES_APPROVAL_REQUIRED
    assert receipt_of(env, project.id, cs_id) is None  # 未产生凭证


def test_issue_rejects_duplicate_active(changes_env: Any) -> None:
    env = changes_env
    project, cs_id, _head = _ready(env)
    first = issue_receipt(env, project.id, cs_id, key="rcpt-1")
    head_before = head_of(env, project.id)

    with pytest.raises(ViolationError) as exc:
        issue_receipt(env, project.id, cs_id, key="rcpt-2")

    assert exc.value.code == ec.CHANGES_INVALID_COMMAND
    assert _receipt_id(first) in exc.value.why  # 指向已有凭证
    assert head_of(env, project.id) == head_before  # 违规不产生提交


# ---------------------------------------------------------------------------
# 批准即消费
# ---------------------------------------------------------------------------
def test_advance_consumes_receipt_in_same_commit(changes_env: Any) -> None:
    env = changes_env
    project, cs_id, _head = _ready(env)
    ref = _receipt_id(issue_receipt(env, project.id, cs_id, key="rcpt-1"))

    result = _approve(env, project, cs_id, ref=ref)

    assert result.is_committed
    model = change_set_of(env, project.id, cs_id)
    assert model.status == st.CHANGE_APPROVED
    assert model.history[-1].approval_ref == ref  # 凭证引用入审计历史
    receipt = receipt_of(env, project.id, cs_id)
    assert receipt is not None and receipt.status == RECEIPT_CONSUMED  # 同提交内消费
    assert receipt.resolved_reason  # 消费留痕
    pending = asyncio.run(env.repo.pending(project.id.value, 100))
    assert any(e["type"] == "changes.receipt.consumed" for e in pending)


def test_advance_rejects_forged_ref(changes_env: Any) -> None:
    """自由文本凭证引用自 T15.5 起一律拒绝：批准必须是真实签发的凭证。"""
    env = changes_env
    project, cs_id, _head = _ready(env)
    head_before = head_of(env, project.id)

    with pytest.raises(ViolationError) as exc:
        _approve(env, project, cs_id, ref="rcpt_forged", key="adv-bad")

    assert exc.value.code == ec.CHANGES_RECEIPT_INVALID
    assert "签发" in exc.value.fix
    assert change_set_of(env, project.id, cs_id).status == st.CHANGE_ANALYZED  # 状态不变
    assert head_of(env, project.id) == head_before  # 违规不产生提交


def test_advance_rejects_foreign_receipt(changes_env: Any) -> None:
    env = changes_env
    project, cs1, head = _ready(env)
    cs2 = _open(env, project, head, key="open-2")
    _analyze(env, project, cs2, key="analyze-2")
    record_projection_plan(env, project.id, cs2, key="plan-2")
    ref1 = _receipt_id(issue_receipt(env, project.id, cs1, key="rcpt-1"))

    with pytest.raises(ViolationError) as exc:
        _approve(env, project, cs2, ref=ref1, key="adv-2")

    assert exc.value.code == ec.CHANGES_RECEIPT_INVALID
    assert cs1 in exc.value.why  # 指出凭证的真实归属
    assert change_set_of(env, project.id, cs2).status == st.CHANGE_ANALYZED
    assert receipt_of(env, project.id, cs1).status == RECEIPT_ACTIVE  # 未被误消费


def test_reissue_rejected_in_approved(changes_env: Any) -> None:
    """approved 状态不能补签凭证：授权只能发生在分析完成之后、批准之前。"""
    env = changes_env
    project, cs_id, _head = _ready(env)
    ref = _receipt_id(issue_receipt(env, project.id, cs_id, key="rcpt-1"))
    _approve(env, project, cs_id, ref=ref)

    with pytest.raises(ViolationError) as exc:
        issue_receipt(env, project.id, cs_id, key="rcpt-2")

    assert exc.value.code == ec.CHANGES_INVALID_TRANSITION
    assert exc.value.fix


# ---------------------------------------------------------------------------
# 过期（scope/内容漂移）
# ---------------------------------------------------------------------------
def test_stale_when_plan_changes_after_issue(changes_env: Any) -> None:
    """签发后计划被调整：指纹失配，旧凭证不能批准新内容。"""
    env = changes_env
    project, cs_id, _head = _ready(env)
    ref = _receipt_id(issue_receipt(env, project.id, cs_id, key="rcpt-1"))
    # 作者在签发后把 art_c 移出执行范围
    plan = plan_of(env, project.id, cs_id)
    report = report_of(env, project.id, cs_id)
    revised = set_step_scope(plan, report, "step_audit", ("art_b",))
    result = run_command(
        env,
        project.id,
        change_set_command(
            record_revision_plan,
            change_set_id=cs_id,
            plan=revised,
            actor=AUTHOR,
            at=env.clock.now(),
            reason="签发后调整范围",
        ),
        key="plan-2",
    )
    assert result.is_committed

    with pytest.raises(ViolationError) as exc:
        _approve(env, project, cs_id, ref=ref, key="adv-stale")

    assert exc.value.code == ec.CHANGES_RECEIPT_STALE
    assert "漂移" in exc.value.why
    assert "重新签发" in exc.value.fix
    assert change_set_of(env, project.id, cs_id).status == st.CHANGE_ANALYZED


# ---------------------------------------------------------------------------
# 撤销与重新签发
# ---------------------------------------------------------------------------
def test_revoke_blocks_approval(changes_env: Any) -> None:
    env = changes_env
    project, cs_id, _head = _ready(env)
    ref = _receipt_id(issue_receipt(env, project.id, cs_id, key="rcpt-1"))
    result = revoke_receipt(env, project.id, cs_id, key="revoke-1")
    assert result.is_committed
    assert receipt_of(env, project.id, cs_id).status == RECEIPT_REVOKED

    with pytest.raises(ViolationError) as exc:
        _approve(env, project, cs_id, ref=ref, key="adv-1")

    assert exc.value.code == ec.CHANGES_RECEIPT_REVOKED
    assert "撤销" in exc.value.why
    assert change_set_of(env, project.id, cs_id).status == st.CHANGE_ANALYZED


def test_revoke_requires_active_receipt(changes_env: Any) -> None:
    env = changes_env
    project, cs_id, _head = _ready(env)

    with pytest.raises(ViolationError) as exc:
        revoke_receipt(env, project.id, cs_id, key="revoke-0")

    assert exc.value.code == ec.CHANGES_INVALID_COMMAND
    issue_receipt(env, project.id, cs_id, key="rcpt-1")
    revoke_receipt(env, project.id, cs_id, key="revoke-1")
    with pytest.raises(ViolationError) as exc:
        revoke_receipt(env, project.id, cs_id, key="revoke-2")

    assert exc.value.code == ec.CHANGES_INVALID_COMMAND
    assert "active" in exc.value.why


def test_revoke_rejects_agent_actor(changes_env: Any) -> None:
    env = changes_env
    project, cs_id, _head = _ready(env)
    issue_receipt(env, project.id, cs_id, key="rcpt-1")

    with pytest.raises(ViolationError) as exc:
        run_command(
            env,
            project.id,
            change_set_command(
                revoke_approval_receipt,
                change_set_id=cs_id,
                actor=_AGENT,
                at=env.clock.now(),
                reason="agent 撤销",
            ),
            key="revoke-agent",
        )

    assert exc.value.code == ec.CHANGES_APPROVAL_REQUIRED
    assert receipt_of(env, project.id, cs_id).status == RECEIPT_ACTIVE


def test_reissue_after_revoke_allows_approval(changes_env: Any) -> None:
    """撤销后重新签发：复用同一凭证 Artifact（新 Revision），批准放行。"""
    env = changes_env
    project, cs_id, _head = _ready(env)
    first_ref = _receipt_id(issue_receipt(env, project.id, cs_id, key="rcpt-1"))
    revoke_receipt(env, project.id, cs_id, key="revoke-1")

    reissued = issue_receipt(env, project.id, cs_id, key="rcpt-2")

    assert reissued.is_committed
    ref = _receipt_id(reissued)
    assert ref == first_ref  # 同一 Artifact，新 Revision
    receipt = receipt_of(env, project.id, cs_id)
    assert receipt.status == RECEIPT_ACTIVE
    result = _approve(env, project, cs_id, ref=ref)
    assert result.is_committed
    assert change_set_of(env, project.id, cs_id).status == st.CHANGE_APPROVED
