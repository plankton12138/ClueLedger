"""T15.6 Branch 执行器测试：批准 → 建分支 → 按步执行 → 失败不动 main。

区别于纯领域测试：这里验证执行工作流编排——revision branch 从 base 建立、
按计划依赖顺序逐步获取 Lease / 调用 Worker / 校验提案 / 提交到分支、
失败保存 Job 状态且主分支故事内容零改动（模块文档不变量 3/5/6）。
Worker 端口第一版用 Fake Worker 返回可预测补丁（计划 T15.6）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from hastings.application.errors import ViolationError
from hastings.application.workflows.change_analysis import AnalyzeChangeImpact, AnalyzeIntent
from hastings.application.workflows.revision import ExecuteIntent, ExecuteRevisionPlan
from hastings.domain.changes import error_codes as ec
from hastings.domain.changes import kinds
from hastings.domain.changes import status as st
from hastings.domain.changes.catalog import ChangesCatalog
from hastings.domain.changes.execution import ExecutionRecord, PatchProposal, PatchUnit
from hastings.domain.changes.models import SeedChange
from hastings.domain.manuscript.models import TextUnit
from hastings.domain.repository import error_codes as repo_ec
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, JobId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange

from .conftest import (
    AUTHOR,
    head_of,
    issue_receipt,
    make_advance_command,
    make_open_command,
    record_projection_plan,
    run_command,
)

EXECUTOR = Actor(kind="agent", id="revision_executor")


# ---------------------------------------------------------------------------
# 种子与前置：a(char) → motivates → b(beat) → motivates → c(beat) → renders → tu_x
# 影响：b/c 落 plan 层一步，tu_x 落 manuscript 层一步，且后者依赖前者。
# ---------------------------------------------------------------------------
def _seed_chain(env: Any) -> tuple[Any, str]:
    project = env.handler.create_project("冬馆")
    branch = env.service.get_branch(project.id, "main")
    unit = TextUnit(unit_id="tu_x", chapter_id="ch1", text="他递出怀表时指尖在抖。")
    env.service.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=AUTHOR,
            idempotency_key="seed-1",
            summary="播种动机链与渲染段落",
            changes=(
                ArtifactChange(
                    artifact_id=ArtifactId("art_a"),
                    content={"name": "art_a", "role": "helper"},
                    kind="story.character",
                ),
                ArtifactChange(
                    artifact_id=ArtifactId("art_b"),
                    content={"name": "art_b"},
                    kind="story.plot_beat",
                ),
                ArtifactChange(
                    artifact_id=ArtifactId("art_c"),
                    content={"name": "art_c"},
                    kind="story.plot_beat",
                ),
                ArtifactChange(
                    artifact_id=ArtifactId("tu_x"),
                    content=unit.to_content(),
                    kind="manuscript.text_unit",
                ),
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
                RelationChange(
                    action="add",
                    source=ArtifactId("art_c"),
                    relation_type="renders",
                    target=ArtifactId("tu_x"),
                ),
            ),
        )
    )
    head = head_of(env, project.id)
    assert head is not None
    return project, head


def _kind_of(env: Any, project: Any) -> Callable[[str], str | None]:
    """目标 ID → kind 解析器：让计划投影按四层分组（两步而非一层）。"""
    head = head_of(env, project.id)
    assert head is not None
    snapshot = env.service.get_snapshot(project.id, CommitId(head))
    return {v.artifact.id.value: v.artifact.kind for v in snapshot.artifacts.values()}.get


def _open(env: Any, project: Any, base: str, *, key: str = "open-1", seed: str = "art_a") -> str:
    result = run_command(
        env,
        project.id,
        make_open_command(
            base_commit_id=base, seed_changes=(SeedChange(artifact_id=seed, field="role"),)
        ),
        key=key,
    )
    assert result.is_committed
    return result.change.artifact_changes[0].artifact_id.value


def _analyze(env: Any, project: Any, cs_id: str, *, key: str = "analyze-1") -> Any:
    return AnalyzeChangeImpact(env.handler, env.service, env.clock, AUTHOR).analyze(
        AnalyzeIntent(project.id, cs_id, key)
    )


def _plan_and_approve(env: Any, project: Any, cs_id: str) -> str:
    record_projection_plan(
        env, project.id, cs_id, key=f"plan-{cs_id}", kind_of=_kind_of(env, project)
    )
    rcpt = issue_receipt(env, project.id, cs_id, key=f"rcpt-{cs_id}")
    assert rcpt.is_committed
    receipt_id = rcpt.change.artifact_changes[0].artifact_id.value
    res = run_command(
        env,
        project.id,
        make_advance_command(st.CHANGE_APPROVED, approval_ref=receipt_id)(cs_id),
        key=f"approve-{cs_id}",
    )
    assert res.is_committed
    return receipt_id


def _catalog(env: Any, project: Any) -> ChangesCatalog:
    head = head_of(env, project.id)
    assert head is not None
    return ChangesCatalog.from_snapshot(env.service.get_snapshot(project.id, CommitId(head)))


def _executor(env: Any) -> ExecuteRevisionPlan:
    return ExecuteRevisionPlan(
        env.handler, env.service, env.service, env.repo, env.ids, env.clock, EXECUTOR
    )


def _content_at(env: Any, project: Any, commit_id: str, artifact_id: str) -> dict[str, Any]:
    view = env.service.get_artifact(ArtifactId(artifact_id), CommitId(commit_id))
    assert view is not None and view.revision is not None
    return dict(view.revision.content)


def _changed_ids(env: Any, project: Any, before: str, after: str) -> set[str]:
    diff = env.service.diff(CommitId(before), CommitId(after))
    return {e.artifact_id.value for e in diff.artifact_changes}


STORY_IDS = {"art_a", "art_b", "art_c", "tu_x"}


def _story_touched(env: Any, project: Any, before: str, after: str) -> set[str]:
    """main 与 base 之间被改动的故事对象（状态/记录类 Artifact 不算）。"""
    return _changed_ids(env, project, before, after) & STORY_IDS


def _approved(env: Any) -> tuple[Any, str, str]:
    """种子 → open → analyze → plan → receipt → approve；返回 (project, head, cs_id)。"""
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)
    result = _analyze(env, project, cs_id)
    assert result.status == st.CHANGE_ANALYZED
    _plan_and_approve(env, project, cs_id)
    return project, head, cs_id


# ---------------------------------------------------------------------------
# Fake Worker：按注册表返回可预测补丁；可注入异常与时钟推进
# ---------------------------------------------------------------------------
@dataclass
class FakeWorker:
    patches: dict[str, tuple[PatchUnit, ...]] = field(default_factory=dict)
    error_on: dict[str, Exception] = field(default_factory=dict)
    on_propose: Callable[[Any], None] | None = None
    seen: list[Any] = field(default_factory=list)

    def propose(self, context: Any) -> PatchProposal:
        self.seen.append(context)
        if self.on_propose is not None:
            self.on_propose(context)
        exc = self.error_on.get(context.step_id)
        if exc is not None:
            raise exc
        return PatchProposal(patches=self.patches.get(context.step_id, ()))


def _full_patches() -> dict[str, tuple[PatchUnit, ...]]:
    return {
        "step_plan": (
            PatchUnit(artifact_id="art_b", content={"name": "art_b", "motive": "操纵者的算计"}),
            PatchUnit(artifact_id="art_c", content={"name": "art_c", "trust": "已被改写"}),
        ),
        "step_manuscript": (
            PatchUnit(
                artifact_id="tu_x",
                content=TextUnit(
                    unit_id="tu_x", chapter_id="ch1", text="他把怀表收了回去，笑意不达眼底。"
                ).to_content(),
            ),
        ),
    }


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_execute_completes_on_revision_branch_and_main_untouched(changes_env: Any) -> None:
    """全链路：两步按依赖顺序提交到 revision branch；main 只动状态与记录。"""
    env = changes_env
    project, base, cs_id = _approved(env)
    worker = FakeWorker(patches=_full_patches())
    main_before = head_of(env, project.id)
    assert main_before is not None

    result = _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec-1"), worker)

    assert result.status == "executed"
    branch = env.service.get_branch(project.id, f"revision/{cs_id}")
    assert branch is not None
    branch_head = branch.head_commit_id
    assert branch_head is not None and branch_head.value != base
    # 分支补丁：恰好三个目标对象（范围守卫=只改步骤目标）
    assert _changed_ids(env, project, base, branch_head.value) == {"art_b", "art_c", "tu_x"}
    # main 故事内容零改动：art_b 仍是种子内容（不变量 6）
    assert _content_at(env, project, head_of(env, project.id) or "", "art_b") == {"name": "art_b"}
    # main 状态推进：approved → executing → review（尾部两条迁移）
    model = _catalog(env, project).require_change_set(cs_id, where="test").model()
    assert model.status == st.CHANGE_REVIEW
    assert [(r.from_status, r.to_status) for r in model.history][-2:] == [
        (st.CHANGE_APPROVED, st.CHANGE_EXECUTING),
        (st.CHANGE_EXECUTING, st.CHANGE_REVIEW),
    ]
    assert head_of(env, project.id) != main_before  # 状态与记录落 main


def test_step_context_carries_repair_context(changes_env: Any) -> None:
    """Worker 只拿到当前步骤所需的 Repair Context：目标现状 + 影响理由。"""
    env = changes_env
    project, _base, cs_id = _approved(env)
    worker = FakeWorker(patches=_full_patches())

    _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec-1"), worker)

    assert [c.step_id for c in worker.seen] == [
        "step_plan",
        "step_manuscript",
    ]  # 依赖序（不变量 5）
    plan_ctx = worker.seen[0]
    assert plan_ctx.layer == "plan"
    assert {t.artifact_id for t in plan_ctx.targets} == {"art_b", "art_c"}
    by_id = {t.artifact_id: t for t in plan_ctx.targets}
    assert by_id["art_b"].kind == "story.plot_beat"
    assert by_id["art_b"].content == {"name": "art_b"}  # 分支头现状（尚未修改）
    assert len(plan_ctx.reasons) == 2 and all(plan_ctx.reasons)  # 影响理由随上下文
    ms_ctx = worker.seen[1]
    assert ms_ctx.layer == "manuscript"
    assert ms_ctx.targets[0].content["text"] == "他递出怀表时指尖在抖。"


def test_execution_record_is_typed_and_ordered(changes_env: Any) -> None:
    """执行记录落 main（kind changes.execution）：可解码、步骤带提交 ID 与顺序。"""
    env = changes_env
    project, base, cs_id = _approved(env)
    worker = FakeWorker(patches=_full_patches())
    result = _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec-1"), worker)

    entries = _catalog(env, project).by_kind(kinds.KIND_EXECUTION_RECORD)
    assert len(entries) == 1
    record: ExecutionRecord = entries[0].model()  # decode 已注册
    assert isinstance(record, ExecutionRecord)
    assert record.change_set_id == cs_id
    assert record.outcome == "completed"
    assert record.job_id == result.job_id
    assert record.branch_name == f"revision/{cs_id}"
    assert [s.step_id for s in record.steps] == ["step_plan", "step_manuscript"]
    assert all(s.outcome == "executed" and s.commit_id for s in record.steps)
    branch_head = env.service.get_branch(project.id, f"revision/{cs_id}").head_commit_id
    assert record.steps[-1].commit_id == (branch_head.value if branch_head else None)
    assert record.base_commit_id == base


def test_wrong_status_rejected_without_side_effect(changes_env: Any) -> None:
    """未批准（analyzed）不得执行：前置拒绝，不建分支不推进（四透镜①）。"""
    env = changes_env
    project, head = _seed_chain(env)
    cs_id = _open(env, project, head)
    _analyze(env, project, cs_id)
    head_before = head_of(env, project.id)

    with pytest.raises(ViolationError) as exc:
        _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec-1"), FakeWorker())

    assert exc.value.code == ec.CHANGES_INVALID_TRANSITION
    assert "批准" in exc.value.why
    assert head_of(env, project.id) == head_before
    assert env.service.get_branch(project.id, f"revision/{cs_id}") is None


def test_no_steps_is_legal_empty_result(changes_env: Any) -> None:
    """no-impact：计划无步骤 → 合法空结果，不建空分支、状态不动（不变量 9）。"""
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
    cs_id = _open(env, project, head, key="open-solo")
    _analyze(env, project, cs_id)
    _plan_and_approve(env, project, cs_id)
    head_before = head_of(env, project.id)

    result = _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec-1"), FakeWorker())

    assert result.status == "no_steps"
    assert env.service.get_branch(project.id, f"revision/{cs_id}") is None  # 不建空分支
    assert head_of(env, project.id) == head_before  # 状态不推进
    model = _catalog(env, project).require_change_set(cs_id, where="test").model()
    assert model.status == st.CHANGE_APPROVED


def test_out_of_scope_proposal_fails_execution(changes_env: Any) -> None:
    """越界提案拒绝：Worker 只能在批准范围内提案（退出条件）；失败留痕。"""
    env = changes_env
    project, base, cs_id = _approved(env)
    worker = FakeWorker(
        patches={
            "step_plan": (
                PatchUnit(artifact_id="art_a", content={"name": "art_a", "role": "manipulator"}),
            )
        }
    )

    result = _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec-1"), worker)

    assert result.status == "failed"
    assert result.error_code == ec.CHANGES_PROPOSAL_OUT_OF_SCOPE
    assert "art_a" in result.error_message
    branch = env.service.get_branch(project.id, f"revision/{cs_id}")
    assert branch is not None and branch.head_commit_id.value == base  # 越界补丁未提交
    model = _catalog(env, project).require_change_set(cs_id, where="test").model()
    assert model.status == st.CHANGE_FAILED
    record = _catalog(env, project).by_kind(kinds.KIND_EXECUTION_RECORD)[0].model()
    assert record.outcome == "failed"
    assert record.error_code == ec.CHANGES_PROPOSAL_OUT_OF_SCOPE
    assert record.steps[-1].outcome == "failed"
    assert _content_at(env, project, head_of(env, project.id) or "", "art_a") == {
        "name": "art_a",
        "role": "helper",
    }  # main 零改动


def test_worker_failure_midway_keeps_main_untouched(changes_env: Any) -> None:
    """中途故障：第一步已提交到分支，main 故事内容不动（不变量 6），失败留痕。"""
    env = changes_env
    project, base, cs_id = _approved(env)
    worker = FakeWorker(
        patches=_full_patches(), error_on={"step_manuscript": RuntimeError("模型超时")}
    )

    result = _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec-1"), worker)

    assert result.status == "failed"
    assert result.error_code == "EXECUTION_WORKER_FAILED"
    assert "模型超时" in result.error_message
    branch = env.service.get_branch(project.id, f"revision/{cs_id}")
    assert branch is not None
    # 分支保留已完成的第一步（可恢复的中间态），main 故事内容不动
    assert _changed_ids(env, project, base, branch.head_commit_id.value) == {"art_b", "art_c"}
    assert _story_touched(env, project, base, head_of(env, project.id) or "") == set()
    record = _catalog(env, project).by_kind(kinds.KIND_EXECUTION_RECORD)[0].model()
    assert record.outcome == "failed"
    assert record.steps[0].outcome == "executed" and record.steps[0].commit_id
    assert record.steps[1].outcome == "failed"
    model = _catalog(env, project).require_change_set(cs_id, where="test").model()
    assert model.status == st.CHANGE_FAILED


def test_lease_expiry_midway_rejects_commit(changes_env: Any) -> None:
    """Lease 丢失后旧 Job 不能提交：续写中过期 → 内核拒绝 → 执行失败（§6 清单）。"""
    env = changes_env
    project, base, cs_id = _approved(env)

    def stall(context: Any) -> None:
        if context.step_id == "step_manuscript":
            env.clock.advance(3600.0)  # 第二步前租约已过期

    worker = FakeWorker(patches=_full_patches(), on_propose=stall)

    result = _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec-1"), worker)

    assert result.status == "failed"
    assert result.error_code == repo_ec.REPO_LEASE_CONFLICT
    branch = env.service.get_branch(project.id, f"revision/{cs_id}")
    assert branch is not None
    assert _changed_ids(env, project, base, branch.head_commit_id.value) == {"art_b", "art_c"}
    assert _story_touched(env, project, base, head_of(env, project.id) or "") == set()
    model = _catalog(env, project).require_change_set(cs_id, where="test").model()
    assert model.status == st.CHANGE_FAILED


def test_lease_held_by_other_job_fails_before_branch(changes_env: Any) -> None:
    """资源被其他 Job 持有：入口失败，不建分支，状态落 failed。"""
    env = changes_env
    project, _base, cs_id = _approved(env)
    env.repo.acquire(f"revision:{project.id.value}:{cs_id}", JobId("job_other"), ttl_seconds=3600.0)

    result = _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec-1"), FakeWorker())

    assert result.status == "failed"
    assert result.error_code == repo_ec.REPO_LEASE_CONFLICT
    assert env.service.get_branch(project.id, f"revision/{cs_id}") is None
    model = _catalog(env, project).require_change_set(cs_id, where="test").model()
    assert model.status == st.CHANGE_FAILED


def test_empty_proposal_step_is_no_change(changes_env: Any) -> None:
    """合法空提案：该步不产生提交（no_change），执行继续完成。"""
    env = changes_env
    project, base, cs_id = _approved(env)
    worker = FakeWorker(patches={"step_plan": _full_patches()["step_plan"], "step_manuscript": ()})

    result = _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec-1"), worker)

    assert result.status == "executed"
    branch = env.service.get_branch(project.id, f"revision/{cs_id}")
    assert branch is not None
    assert _changed_ids(env, project, base, branch.head_commit_id.value) == {"art_b", "art_c"}
    record = _catalog(env, project).by_kind(kinds.KIND_EXECUTION_RECORD)[0].model()
    assert [s.outcome for s in record.steps] == ["executed", "no_change"]
    assert not record.steps[1].commit_id  # 空提案无提交
    model = _catalog(env, project).require_change_set(cs_id, where="test").model()
    assert model.status == st.CHANGE_REVIEW


def test_duplicate_proposal_targets_rejected(changes_env: Any) -> None:
    """同一目标重复提案：结构违规，执行失败留痕（kernel 也会拒，这里提前给三要素）。"""
    env = changes_env
    project, _base, cs_id = _approved(env)
    worker = FakeWorker(
        patches={
            "step_plan": (
                PatchUnit(artifact_id="art_b", content={"name": "art_b", "v": 1}),
                PatchUnit(artifact_id="art_b", content={"name": "art_b", "v": 2}),
            )
        }
    )

    result = _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec-1"), worker)

    assert result.status == "failed"
    assert result.error_code == ec.CHANGES_INVALID_COMMAND
    assert "art_b" in result.error_message
    model = _catalog(env, project).require_change_set(cs_id, where="test").model()
    assert model.status == st.CHANGE_FAILED


def test_interrupted_execution_resumes_without_rerunning_committed_step(changes_env: Any) -> None:
    env = changes_env
    project, _base, cs_id = _approved(env)

    def crash(context: Any) -> None:
        if context.step_id == "step_manuscript":
            raise KeyboardInterrupt("process interrupted")

    with pytest.raises(KeyboardInterrupt):
        _executor(env).execute(
            ExecuteIntent(project.id, cs_id, "resume"),
            FakeWorker(patches=_full_patches(), on_propose=crash),
        )
    env.clock.advance(1000)
    worker = FakeWorker(patches=_full_patches())
    result = _executor(env).execute(ExecuteIntent(project.id, cs_id, "new-client-request"), worker)
    assert result.status == "executed"
    assert [context.step_id for context in worker.seen] == ["step_manuscript"]


def test_expired_approval_cannot_start_execution(changes_env: Any) -> None:
    env = changes_env
    project, _base, cs_id = _approved(env)
    env.clock.advance(3601)
    with pytest.raises(ViolationError) as error:
        _executor(env).execute(ExecuteIntent(project.id, cs_id, "late"), FakeWorker())
    assert error.value.code == "CHANGES_RECEIPT_EXPIRED"
    assert env.service.get_branch(project.id, f"revision/{cs_id}") is None
