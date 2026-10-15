"""T15.4 修订计划测试：四层分组、因果约束、环检测与作者手工调整。

纯领域测试（分组/约束/环/拆组/范围/接受风险）+ 命令与工作流测试
（报告→计划落库、幂等重放、前置拒绝）。计划是 ImpactReport 的确定性
投影——同一报告必然得到同一计划；四层分组可能与因果链矛盾（plan 层
两个 Beat 夹住 canon 层一个关系），构建不拒绝，由
``execution_order``/``find_item_cycle``/落库命令显式检测并输出最小环
路径，作者用 split_step/set_step_scope/accept_plan_risk 化解
（不变量 1、4、5）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from hastings.application.errors import ViolationError
from hastings.application.handlers.change_commands import change_set_command
from hastings.application.workflows.change_analysis import AnalyzeChangeImpact, AnalyzeIntent
from hastings.application.workflows.plan_revision import PlanIntent, PlanRevision
from hastings.domain.changes import error_codes as ec
from hastings.domain.changes import kinds
from hastings.domain.changes import status as st
from hastings.domain.changes.catalog import ChangesCatalog
from hastings.domain.changes.commands import record_revision_plan
from hastings.domain.changes.errors import ChangesViolation
from hastings.domain.changes.impact import SEVERITY_MUST_FIX, EdgeHop, ImpactItem, ImpactReport
from hastings.domain.changes.models import SeedChange
from hastings.domain.changes.planning import (
    LAYER_AUDIT,
    LAYER_CANON,
    LAYER_MANUSCRIPT,
    LAYER_PLAN,
    RevisionPlan,
    accept_plan_risk,
    build_revision_plan,
    default_layer_of,
    execution_order,
    find_item_cycle,
    set_step_scope,
    split_step,
)
from hastings.domain.repository.ids import ArtifactId, CommitId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange

from .conftest import (
    AUTHOR,
    head_of,
    issue_receipt,
    make_advance_command,
    make_open_command,
    run_command,
)


# ---------------------------------------------------------------------------
# 纯领域夹具：直接构造报告，不需要仓库
# ---------------------------------------------------------------------------
def _hop(source: str, rel: str, target: str) -> EdgeHop:
    return EdgeHop(source=source, relation_type=rel, target=target)


def _item(
    target: str,
    severity: str,
    path: tuple[EdgeHop, ...] = (),
    *,
    mentioned: str | None = None,
) -> ImpactItem:
    return ImpactItem(
        target_artifact_id=target,
        severity=severity,
        impact_kind="structure",
        path=path,
        reason="测试项",
        mentioned_artifact_id=mentioned,
    )


def _report(*items: ImpactItem) -> ImpactReport:
    return ImpactReport(
        report_id="rep_1", change_set_id="chg_1", base_commit_id="cmt_base_1", items=items
    )


CHAIN_KINDS = {"art_a": "story.character", "art_b": "story.plot_beat"}

# 沙漏形态：plan 层两个 Beat 夹住 canon 层的一个关系（四层分组与因果链矛盾
# 的典型样本，抽象自模块文档 §4.3 示例）
SANDWICH_KINDS = {
    "art_b1": "story.plot_beat",
    "art_rel": "story.relationship",
    "art_b2": "story.plot_beat",
}


def _sandwich_report() -> ImpactReport:
    return _report(
        _item("art_b1", "should_fix", (_hop("art_a", "motivates", "art_b1"),)),
        _item("art_rel", "should_fix", (_hop("art_b1", "changes_state", "art_rel"),)),
        _item("art_b2", "should_fix", (_hop("art_rel", "requires", "art_b2"),)),
    )


def _sandwich_plan() -> RevisionPlan:
    return build_revision_plan(_sandwich_report(), kind_of=SANDWICH_KINDS.get)


def test_default_layer_rules_cover_four_layers() -> None:
    assert default_layer_of("story.character") == LAYER_CANON
    assert default_layer_of("story.relationship") == LAYER_CANON
    assert default_layer_of("narrative.beat") == LAYER_PLAN
    assert default_layer_of("story.plot_beat") == LAYER_PLAN
    assert default_layer_of("story.knowledge") == LAYER_PLAN
    assert default_layer_of("manuscript.text_unit") == LAYER_MANUSCRIPT
    assert default_layer_of("author_model.preference") == LAYER_AUDIT  # 未识别 → 审计层保守兜底
    assert default_layer_of("weird.custom") == LAYER_AUDIT


def test_layers_grouped_in_order_with_constraint_deps() -> None:
    kinds_map = {**CHAIN_KINDS, "tu_1": "manuscript.text_unit"}
    report = _report(
        _item("art_a", "should_fix", (_hop("seed", "motivates", "art_a"),)),
        _item("art_b", "should_fix", (_hop("seed", "motivates", "art_b"),)),
        _item("tu_1", SEVERITY_MUST_FIX, (_hop("art_b", "renders", "tu_1"),)),
    )
    plan = build_revision_plan(report, kind_of=kinds_map.get)

    assert [s.step_id for s in plan.steps] == ["step_canon", "step_plan", "step_manuscript"]
    by_id = {s.step_id: s for s in plan.steps}
    assert by_id["step_canon"].target_artifact_ids == ("art_a",)
    assert by_id["step_manuscript"].depends_on == ("step_plan",)  # beat → tu 因果链
    assert execution_order(plan) == ("step_canon", "step_plan", "step_manuscript")


def test_mention_item_constrains_mentioned_first() -> None:
    """mentions 语义项没有结构路径：约束取「被引用对象先修」。"""
    kinds_map = {**CHAIN_KINDS, "tu_1": "manuscript.text_unit"}
    report = _report(
        _item("art_a", "should_fix", (_hop("seed", "motivates", "art_a"),)),
        _item("tu_1", "should_fix", mentioned="art_a"),
    )
    plan = build_revision_plan(report, kind_of=kinds_map.get)

    by_id = {s.step_id: s for s in plan.steps}
    assert by_id["step_manuscript"].depends_on == ("step_canon",)
    assert execution_order(plan) == ("step_canon", "step_manuscript")


def test_layer_tiebreak_never_overrides_dependencies() -> None:
    """canon 优先只是平局规则：因果约束要求 plan 先修时就按约束来。"""
    report = _report(
        _item("art_b", "should_fix", (_hop("seed", "motivates", "art_b"),)),
        _item("art_a", "should_fix", (_hop("art_b", "changes_state", "art_a"),)),
    )
    plan = build_revision_plan(report, kind_of=CHAIN_KINDS.get)

    assert execution_order(plan) == ("step_plan", "step_canon")


def test_item_cycle_reports_minimal_path() -> None:
    """同层两项互为因果：步投影互相抵消，项级环由 find_item_cycle 单独拦截。"""
    same = {k: "story.character" for k in ("art_x", "art_y")}
    report = _report(
        _item("art_x", "should_fix", (_hop("seed", "constrains", "art_x"),)),
        _item("art_y", "should_fix", (_hop("art_x", "constrains", "art_y"),)),
        _item("art_x", "should_fix", (_hop("art_y", "constrains", "art_x"),)),
    )
    plan = build_revision_plan(report, kind_of=same.get)
    assert execution_order(plan) == ("step_canon",)  # 步级看不到（同步内边被折叠）

    assert find_item_cycle(plan, report) == ("art_x", "art_y")


def test_minimal_cycle_excludes_side_branch() -> None:
    same = {k: "story.character" for k in ("art_x", "art_y", "art_z")}
    report = _report(
        _item("art_x", "should_fix", (_hop("seed", "constrains", "art_x"),)),
        _item("art_y", "should_fix", (_hop("art_x", "constrains", "art_y"),)),
        _item("art_x", "should_fix", (_hop("art_y", "constrains", "art_x"),)),
        _item("art_z", "should_fix", (_hop("art_y", "constrains", "art_z"),)),
    )
    plan = build_revision_plan(report, kind_of=same.get)

    assert find_item_cycle(plan, report) == ("art_x", "art_y")  # 旁支 art_z 不入环


def test_sandwich_step_cycle_detected_with_minimal_path() -> None:
    plan = _sandwich_plan()
    by_id = {s.step_id: s for s in plan.steps}
    # 分组成立但依赖互指：plan 步夹住 canon 步
    assert by_id["step_plan"].depends_on == ("step_canon",)
    assert by_id["step_canon"].depends_on == ("step_plan",)

    with pytest.raises(ChangesViolation) as exc:
        execution_order(plan)
    assert exc.value.code == ec.CHANGES_PLAN_CYCLE
    assert "step_canon → step_plan → step_canon" in exc.value.why
    assert "split_step" in exc.value.fix  # 指向化解手段


def test_split_step_resolves_sandwich_cycle() -> None:
    report = _sandwich_report()
    plan = split_step(_sandwich_plan(), report, "step_plan", (("art_b1",), ("art_b2",)))

    assert [s.step_id for s in plan.steps] == ["step_canon", "step_plan.1", "step_plan.2"]
    assert execution_order(plan) == ("step_plan.1", "step_canon", "step_plan.2")
    by_id = {s.step_id: s for s in plan.steps}
    assert by_id["step_plan.1"].target_artifact_ids == ("art_b1",)
    assert by_id["step_plan.2"].depends_on == ("step_canon",)


@pytest.mark.parametrize(
    "groups",
    [
        (),
        (("art_b1",), ()),  # 空组
        (("art_b1",), ("art_b1",)),  # 目标重复
        (("art_b1",), ("art_missing",)),  # 目标不属于原步
        (("art_b1",), ("art_rel",)),  # 带入其他步的目标
    ],
)
def test_split_step_rejects_bad_partitions(groups: Any) -> None:
    with pytest.raises(ChangesViolation) as exc:
        split_step(_sandwich_plan(), _sandwich_report(), "step_plan", groups)
    assert exc.value.code == ec.CHANGES_INVALID_COMMAND


def test_split_step_rejects_unknown_step() -> None:
    with pytest.raises(ChangesViolation) as exc:
        split_step(_sandwich_plan(), _sandwich_report(), "step_nothing", (("art_b1",),))
    assert exc.value.code == ec.CHANGES_INVALID_COMMAND
    assert "step_nothing" in exc.value.why


def test_set_step_scope_moves_target_out_explicitly() -> None:
    report = _sandwich_report()
    plan = _sandwich_plan()
    b2_index = next(i for i, it in enumerate(report.items) if it.target_artifact_id == "art_b2")

    scoped = set_step_scope(plan, report, "step_plan", ("art_b1",))

    plan_step = scoped.step_of("step_plan")
    assert plan_step is not None and plan_step.target_artifact_ids == ("art_b1",)
    dropped = [e for e in scoped.unassigned if e.item_index == b2_index]
    assert len(dropped) == 1
    assert "范围" in dropped[0].reason  # 不静默删除：留痕可审计
    assert execution_order(scoped) == ("step_plan", "step_canon")


def test_accept_risk_records_evidence_and_keeps_must_fix_visible() -> None:
    """must_fix 接受风险 = 显式留档（不变量 4），不是静默消失。"""
    kinds_map = {**CHAIN_KINDS, "tu_1": "manuscript.text_unit"}
    report = _report(
        _item("art_b", "should_fix", (_hop("seed", "motivates", "art_b"),)),
        _item("tu_1", SEVERITY_MUST_FIX, (_hop("art_b", "renders", "tu_1"),)),
    )
    plan = build_revision_plan(report, kind_of=kinds_map.get)
    tu_index = next(i for i, it in enumerate(report.items) if it.target_artifact_id == "tu_1")

    risky = accept_plan_risk(
        plan, report, (tu_index,), by=AUTHOR, at="2026-10-15T12:00:00+00:00", reason="读者已知内情"
    )

    assert not any(s.layer == LAYER_MANUSCRIPT for s in risky.steps)  # 唯一正文项移出 → 步消失
    entry = next(e for e in risky.unassigned if e.item_index == tu_index)
    assert entry.severity == SEVERITY_MUST_FIX
    assert "接受风险" in entry.reason
    assert risky.risk_acceptances[0].item_index == tu_index
    assert risky.risk_acceptances[0].by == "human:author_test"
    assert "读者已知内情" in risky.risk_acceptances[0].reason


def test_accept_risk_resolves_cycle() -> None:
    report = _sandwich_report()
    rel_index = next(i for i, it in enumerate(report.items) if it.target_artifact_id == "art_rel")

    risky = accept_plan_risk(
        _sandwich_plan(), report, (rel_index,), by=AUTHOR, at="t", reason="关系状态留待复核"
    )

    assert not any(s.layer == LAYER_CANON for s in risky.steps)  # 唯一 canon 项被移出
    assert execution_order(risky) == ("step_plan",)


def test_accept_risk_rejects_unknown_or_uncovered_items() -> None:
    report = _sandwich_report()
    plan = _sandwich_plan()
    with pytest.raises(ChangesViolation) as range_exc:
        accept_plan_risk(plan, report, (99,), by=AUTHOR, at="t", reason="x")
    assert range_exc.value.code == ec.CHANGES_INVALID_COMMAND

    rel_index = next(i for i, it in enumerate(report.items) if it.target_artifact_id == "art_rel")
    risky = accept_plan_risk(plan, report, (rel_index,), by=AUTHOR, at="t", reason="x")
    with pytest.raises(ChangesViolation) as again_exc:
        accept_plan_risk(risky, report, (rel_index,), by=AUTHOR, at="t", reason="x")
    assert again_exc.value.code == ec.CHANGES_INVALID_COMMAND


def test_plan_serialization_roundtrip() -> None:
    report = _sandwich_report()
    plan = split_step(_sandwich_plan(), report, "step_plan", (("art_b1",), ("art_b2",)))
    assert RevisionPlan.from_content(plan.to_content()) == plan


# ---------------------------------------------------------------------------
# 命令与工作流：真实仓库 → 分析报告 → 计划落库
# ---------------------------------------------------------------------------
def _seed_chain(
    env: Any,
    *,
    kinds_map: dict[str, str] | None = None,
    with_text: bool = False,
) -> tuple[Any, str]:
    """播种 a→b→c 传播链（默认角色→Beat→Beat），返回 (project, head)。

    ``with_text`` 追加渲染段落 tu_1 与 ``c --renders--> tu_1``（产生
    must_fix 项，供覆盖校验类测试使用）。
    """
    project = env.handler.create_project("冬馆")
    branch = env.service.get_branch(project.id, "main")
    node_kinds = {
        "art_a": "story.character",
        "art_b": "story.plot_beat",
        "art_c": "story.plot_beat",
    }
    if kinds_map:
        node_kinds.update(kinds_map)
    changes = [
        ArtifactChange(artifact_id=ArtifactId(aid), content={"name": aid}, kind=k)
        for aid, k in node_kinds.items()
    ]
    relations = [
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
    ]
    if with_text:
        changes.append(
            ArtifactChange(
                artifact_id=ArtifactId("tu_1"),
                content={
                    "unit_id": "tu_1",
                    "chapter_id": "ch1",
                    "text": "……",
                    "state": "active",
                },
                kind="manuscript.text_unit",
            )
        )
        relations.append(
            RelationChange(
                action="add",
                source=ArtifactId("art_c"),
                relation_type="renders",
                target=ArtifactId("tu_1"),
            )
        )
    env.service.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=AUTHOR,
            idempotency_key="seed-1",
            summary="播种影响链",
            changes=tuple(changes),
            relations=tuple(relations),
        )
    )
    head = head_of(env, project.id)
    assert head is not None
    return project, head


def _seed_sandwich(env: Any) -> tuple[Any, str]:
    project = env.handler.create_project("冬馆")
    branch = env.service.get_branch(project.id, "main")
    env.service.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=AUTHOR,
            idempotency_key="seed-1",
            summary="播种沙漏依赖",
            changes=(
                ArtifactChange(artifact_id=ArtifactId("art_a"), content={}, kind="story.character"),
                ArtifactChange(
                    artifact_id=ArtifactId("art_b1"), content={}, kind="story.plot_beat"
                ),
                ArtifactChange(
                    artifact_id=ArtifactId("art_rel"), content={}, kind="story.relationship"
                ),
                ArtifactChange(
                    artifact_id=ArtifactId("art_b2"), content={}, kind="story.plot_beat"
                ),
            ),
            relations=(
                RelationChange(
                    action="add",
                    source=ArtifactId("art_a"),
                    relation_type="motivates",
                    target=ArtifactId("art_b1"),
                ),
                RelationChange(
                    action="add",
                    source=ArtifactId("art_rel"),
                    relation_type="changes_state",
                    target=ArtifactId("art_b1"),
                ),
                RelationChange(
                    action="add",
                    source=ArtifactId("art_b2"),
                    relation_type="requires",
                    target=ArtifactId("art_rel"),
                ),
            ),
        )
    )
    head = head_of(env, project.id)
    assert head is not None
    return project, head


def _open(env: Any, project: Any, base: str, *, seed: str = "art_a", key: str = "open-1") -> str:
    command = make_open_command(
        base_commit_id=base,
        seed_changes=(SeedChange(artifact_id=seed, field="role"),),
    )
    result = run_command(env, project.id, command, key=key)
    assert result.is_committed
    return result.change.artifact_changes[0].artifact_id.value


def _analyzed(env: Any, project: Any, cs_id: str) -> None:
    result = AnalyzeChangeImpact(env.handler, env.service, env.clock, AUTHOR).analyze(
        AnalyzeIntent(project.id, cs_id, "analyze-1")
    )
    assert result.status == st.CHANGE_ANALYZED


def _catalog(env: Any, project: Any) -> ChangesCatalog:
    head = head_of(env, project.id)
    assert head is not None
    return ChangesCatalog.from_snapshot(env.service.get_snapshot(project.id, CommitId(head)))


def _planner(env: Any) -> PlanRevision:
    return PlanRevision(env.handler, env.service, env.clock, AUTHOR)


def _kind_map_of(env: Any, project: Any, head: str) -> dict[str, str]:
    return {
        v.artifact.id.value: v.artifact.kind
        for v in env.service.get_snapshot(project.id, CommitId(head)).artifacts.values()
    }


def _record_plan(env: Any, project: Any, cs_id: str, plan: RevisionPlan, key: str) -> Any:
    return env.handler.execute(
        change_set_command(
            record_revision_plan,
            change_set_id=cs_id,
            plan=plan,
            actor=AUTHOR,
            at=env.clock.now(),
            reason="登记修订计划",
        ),
        project_id=project.id,
        idempotency_key=key,
        summary="登记修订计划",
    )


def test_plan_workflow_records_revision_plan(changes_env: Any) -> None:
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)
    _analyzed(env, project, cs_id)
    head_before = head_of(env, project.id)

    result = _planner(env).plan(PlanIntent(project.id, cs_id, "plan-1"))

    assert result.commit_id is not None
    assert result.step_count == 1  # b、c 同为 Beat → 单个 plan 步
    assert result.unassigned_count == 0
    catalog = _catalog(env, project)
    entry = catalog.get(result.plan_id)
    assert entry is not None and entry.kind == kinds.KIND_REVISION_PLAN
    plan: RevisionPlan = entry.model()
    assert plan.change_set_id == cs_id
    assert plan.base_commit_id == head  # 计划绑定基础版本（不变量 1）
    assert head_of(env, project.id) != head_before  # 计划落库产生了新提交


def test_plan_replay_returns_existing_without_commit(changes_env: Any) -> None:
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)
    _analyzed(env, project, cs_id)
    first = _planner(env).plan(PlanIntent(project.id, cs_id, "plan-1"))
    head_after_first = head_of(env, project.id)

    retry = _planner(env).plan(PlanIntent(project.id, cs_id, "plan-1"))

    assert retry.plan_id == first.plan_id
    assert retry.commit_id is None  # 幂等重放：无新提交
    assert head_of(env, project.id) == head_after_first


def test_plan_rejected_before_analysis(changes_env: Any) -> None:
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)

    with pytest.raises(ViolationError) as exc:
        _planner(env).plan(PlanIntent(project.id, cs_id, "plan-1"))

    assert exc.value.code == ec.CHANGES_INVALID_TRANSITION
    assert "分析" in exc.value.fix


def test_plan_requires_report_even_in_analyzed(changes_env: Any) -> None:
    """手工推进到 analyzed（无报告落库）：机制前置拒绝，不让作者空跑。"""
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)
    run_command(env, project.id, make_advance_command(st.CHANGE_ANALYZING)(cs_id), key="w1")
    run_command(env, project.id, make_advance_command(st.CHANGE_ANALYZED)(cs_id), key="w2")
    head_before = head_of(env, project.id)

    with pytest.raises(ViolationError) as exc:
        _planner(env).plan(PlanIntent(project.id, cs_id, "plan-1"))

    assert exc.value.code == ec.CHANGES_INVALID_COMMAND
    assert "分析" in exc.value.fix
    assert head_of(env, project.id) == head_before  # 无副作用


def test_plan_rejected_in_approved(changes_env: Any) -> None:
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)
    _analyzed(env, project, cs_id)
    catalog = _catalog(env, project)
    report = next(e for e in catalog.by_kind(kinds.KIND_IMPACT_REPORT)).model()
    _record_plan(env, project, cs_id, build_revision_plan(report), "plan-1")
    rcpt = issue_receipt(env, project.id, cs_id, key="rcpt-1")
    assert rcpt.is_committed
    ref = rcpt.change.artifact_changes[0].artifact_id.value
    run_command(
        env,
        project.id,
        make_advance_command(st.CHANGE_APPROVED, approval_ref=ref)(cs_id),
        key="w1",
    )

    with pytest.raises(ViolationError) as exc:
        _planner(env).plan(PlanIntent(project.id, cs_id, "plan-1"))

    assert exc.value.code == ec.CHANGES_INVALID_TRANSITION
    assert exc.value.fix


def test_sandwich_cycle_blocks_plan_workflow(changes_env: Any) -> None:
    env = changes_env
    project, head = _seed_sandwich(env)
    cs_id = _open(env, project, head)
    _analyzed(env, project, cs_id)
    head_before = head_of(env, project.id)

    with pytest.raises(ViolationError) as exc:
        _planner(env).plan(PlanIntent(project.id, cs_id, "plan-1"))

    assert exc.value.code == ec.CHANGES_PLAN_CYCLE
    assert "step_canon → step_plan → step_canon" in exc.value.why
    assert head_of(env, project.id) == head_before  # 无副作用，等作者化解


def test_command_rejects_base_mismatch(changes_env: Any) -> None:
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)
    _analyzed(env, project, cs_id)
    catalog = _catalog(env, project)
    report = next(e for e in catalog.by_kind(kinds.KIND_IMPACT_REPORT)).model()
    plan = build_revision_plan(report, kind_of=_kind_map_of(env, project, head).get)
    tampered = replace(plan, base_commit_id="cmt_other")

    with pytest.raises(ViolationError) as exc:
        _record_plan(env, project, cs_id, tampered, "plan-bad")

    assert exc.value.code == ec.CHANGES_INVALID_COMMAND
    assert "base_commit_id" in exc.value.why


def test_command_rejects_must_fix_silently_dropped(changes_env: Any) -> None:
    env = changes_env
    project, head = _seed_chain(env, with_text=True)  # tu_1 沿 renders 是 must_fix
    cs_id = _open(env, project, head)
    _analyzed(env, project, cs_id)
    catalog = _catalog(env, project)
    report = next(e for e in catalog.by_kind(kinds.KIND_IMPACT_REPORT)).model()
    plan = build_revision_plan(report, kind_of=_kind_map_of(env, project, head).get)
    gutted = replace(plan, steps=(), unassigned=())  # 掏空覆盖：must_fix 无处安放

    with pytest.raises(ViolationError) as exc:
        _record_plan(env, project, cs_id, gutted, "plan-gutted")

    assert exc.value.code == ec.CHANGES_INVALID_COMMAND
    assert "must_fix" in exc.value.why


def test_command_rejects_item_cycle(changes_env: Any) -> None:
    """同层互指环：步投影看不到，落库校验用项级环检测拦截。"""
    env = changes_env
    project = env.handler.create_project("冬馆")
    branch = env.service.get_branch(project.id, "main")
    env.service.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=AUTHOR,
            idempotency_key="seed-1",
            summary="播种同层互指环",
            changes=tuple(
                ArtifactChange(artifact_id=ArtifactId(aid), content={}, kind="story.character")
                for aid in ("art_a", "art_x", "art_y")
            ),
            relations=(
                RelationChange(
                    action="add",
                    source=ArtifactId("art_a"),
                    relation_type="motivates",
                    target=ArtifactId("art_x"),
                ),
                RelationChange(
                    action="add",
                    source=ArtifactId("art_a"),
                    relation_type="motivates",
                    target=ArtifactId("art_y"),
                ),
                RelationChange(
                    action="add",
                    source=ArtifactId("art_x"),
                    relation_type="constrains",
                    target=ArtifactId("art_y"),
                ),
                RelationChange(
                    action="add",
                    source=ArtifactId("art_y"),
                    relation_type="constrains",
                    target=ArtifactId("art_x"),
                ),
            ),
        )
    )
    head = head_of(env, project.id)
    assert head is not None
    cs_id = _open(env, project, head)
    _analyzed(env, project, cs_id)
    catalog = _catalog(env, project)
    report = next(e for e in catalog.by_kind(kinds.KIND_IMPACT_REPORT)).model()
    plan = build_revision_plan(report, kind_of=_kind_map_of(env, project, head).get)

    with pytest.raises(ViolationError) as exc:
        _record_plan(env, project, cs_id, plan, "plan-cycle")

    assert exc.value.code == ec.CHANGES_PLAN_CYCLE
    assert "art_x → art_y → art_x" in exc.value.why
