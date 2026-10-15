"""冻结分支差异，由作者选择 hunk；正文、状态、残余报告在一个 Commit 生效。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from hastings.application.ports.clock import Clock
from hastings.application.ports.repository import CommitRepository, RepositoryReader
from hastings.application.workflows.change_support import (
    branch_snapshot,
    content,
    reject,
    replay_content,
    require_author,
)
from hastings.domain.changes import kinds
from hastings.domain.changes.errors import ChangesViolation
from hastings.domain.changes.impact import ImpactReport, RelationGraph, traverse_impact
from hastings.domain.changes.models import ChangeSet, TransitionRecord
from hastings.domain.changes.patch_guard import validate_content_patch
from hastings.domain.changes.planning import RevisionPlan
from hastings.domain.changes.receipt import ApprovalReceipt, confirm_fingerprint
from hastings.domain.changes.review import PatchSet, apply_hunks, make_hunks
from hastings.domain.changes.scope import expand_scope
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId
from hastings.domain.repository.models import Snapshot
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.domain.repository.serialization import content_hash


class ReviewRevision:
    def __init__(
        self, reader: RepositoryReader, committer: CommitRepository, clock: Clock, actor: Actor
    ) -> None:
        self.reader, self.committer, self.clock, self.actor = reader, committer, clock, actor

    def _require_authorization(self, snapshot: Snapshot, model: ChangeSet) -> None:
        bodies = {
            v.artifact.kind: v.revision.content
            for v in snapshot.artifacts.values()
            if v.revision
            and v.revision.content.get("change_set_id") == model.change_set_id
            and v.artifact.kind
            in (kinds.KIND_APPROVAL_RECEIPT, kinds.KIND_IMPACT_REPORT, kinds.KIND_REVISION_PLAN)
        }
        if not all(
            k in bodies
            for k in (
                kinds.KIND_APPROVAL_RECEIPT,
                kinds.KIND_IMPACT_REPORT,
                kinds.KIND_REVISION_PLAN,
            )
        ):
            reject(
                "CHANGES_APPROVAL_REQUIRED",
                "review.authorization",
                "审阅缺少批准依据",
                "先完成报告、计划与作者批准",
            )
        receipt = ApprovalReceipt.from_content(bodies[kinds.KIND_APPROVAL_RECEIPT])
        fingerprint = confirm_fingerprint(
            model,
            ImpactReport.from_content(bodies[kinds.KIND_IMPACT_REPORT]),
            RevisionPlan.from_content(bodies[kinds.KIND_REVISION_PLAN]),
        )
        if receipt.status != "consumed" or receipt.fingerprint != fingerprint:
            reject(
                "CHANGES_RECEIPT_STALE",
                "review.authorization",
                "审阅依据已偏离作者批准内容",
                "在新变更集中重新审阅范围并批准；不得直接合并漂移计划",
            )

    def prepare(self, project: ProjectId, change_set_id: str, key: str) -> dict[str, Any]:
        patch_id = f"patchset:{change_set_id}"
        replay = replay_content(self.reader, project, key, patch_id)
        if replay is not None:
            return replay
        branch, snapshot = branch_snapshot(self.reader, project)
        model = ChangeSet.from_content(content(snapshot, change_set_id))
        if model.status != "review":
            reject(
                "CHANGES_INVALID_TRANSITION",
                "review.prepare",
                "只有 review 状态能审阅",
                "先完成批准计划的分支执行",
            )
        self._require_authorization(snapshot, model)
        _, proposed = branch_snapshot(self.reader, project, f"revision/{change_set_id}")
        base = self.reader.get_snapshot(project, CommitId(model.base_commit_id))
        plan = next(
            RevisionPlan.from_content(v.revision.content)
            for v in snapshot.artifacts.values()
            if v.revision
            and v.artifact.kind == kinds.KIND_REVISION_PLAN
            and v.revision.content.get("change_set_id") == change_set_id
        )
        allowed = {aid for step in plan.steps for aid in step.target_artifact_ids}
        hunks: list[dict[str, Any]] = []
        bases = {}
        for diff in self.reader.diff(base.commit_id, proposed.commit_id).artifact_changes:
            aid = diff.artifact_id.value
            if aid not in allowed:
                reject(
                    "CHANGES_PROPOSAL_OUT_OF_SCOPE",
                    "review.prepare",
                    f"分支含未批准对象 {aid}",
                    "仅审阅批准计划产生的分支",
                )
            old, new = content(base, aid), content(proposed, aid)
            bases[aid] = old
            hunks.extend(make_hunks(aid, old, new))
        patch = PatchSet(
            patch_id, change_set_id, model.base_commit_id, proposed.commit_id.value, hunks, bases
        ).to_content()
        previous = snapshot.artifacts.get(ArtifactId(patch_id))
        self.committer.commit(
            CommitRequest(
                project,
                branch.id,
                branch.head_commit_id,
                self.actor,
                key,
                "冻结修订差异供作者审阅",
                changes=(
                    ArtifactChange(
                        ArtifactId(patch_id),
                        patch,
                        previous.revision.id if previous and previous.revision else None,
                        "changes.patch_set",
                    ),
                ),
                events=(("changes.review.prepared", {"change_set_id": change_set_id}),),
            )
        )
        return patch

    def merge(
        self,
        project: ProjectId,
        change_set_id: str,
        selected: tuple[str, ...],
        risks: dict[str, str],
        key: str,
        expected_head: str | None,
    ) -> dict[str, Any]:
        require_author(self.actor)
        result_id = f"review:{change_set_id}:{content_hash(key)[:20]}"
        fingerprint = content_hash({"selected": sorted(selected), "risks": risks})
        replay = replay_content(self.reader, project, key, result_id)
        if replay is not None:
            if replay["fingerprint"] != fingerprint:
                reject(
                    "CHANGES_REVIEW_CONFLICT",
                    "review.merge",
                    "同一请求键对应不同审阅决定",
                    "重试保持原选择，新决定使用新请求键",
                )
            return replay
        branch, snapshot = branch_snapshot(self.reader, project)
        if expected_head != snapshot.commit_id.value:
            reject(
                "CHANGES_REVIEW_CONFLICT",
                "review.merge",
                "主分支已变化，确认页已过期",
                "重新读取最新差异并确认",
            )
        model = ChangeSet.from_content(content(snapshot, change_set_id))
        if model.status != "review":
            reject(
                "CHANGES_INVALID_TRANSITION",
                "review.merge",
                "当前变更集不处于 review",
                "先执行计划并审阅分支差异",
            )
        patch = content(snapshot, f"patchset:{change_set_id}")
        self._require_authorization(snapshot, model)
        _, proposed = branch_snapshot(self.reader, project, f"revision/{change_set_id}")
        if patch["branch_commit_id"] != proposed.commit_id.value:
            reject(
                "CHANGES_REVIEW_CONFLICT",
                "review.merge",
                "分支内容在审阅后发生变化",
                "重新生成差异并确认",
            )
        known = {h["hunk_id"] for h in patch["hunks"]}
        if len(set(selected)) != len(selected) or not set(selected) <= known:
            reject(
                "CHANGES_INVALID_COMMAND",
                "review.merge",
                "选择了未知或重复 hunk",
                "仅传入当前差异返回的唯一 hunk_id",
            )
        reports = [
            v
            for v in snapshot.artifacts.values()
            if v.revision
            and v.artifact.kind == kinds.KIND_IMPACT_REPORT
            and v.revision.content.get("change_set_id") == change_set_id
        ]
        report_view = reports[0]
        assert report_view.revision
        report = ImpactReport.from_content(report_view.revision.content)
        if not set(risks) <= {i.target_artifact_id for i in report.items} or any(
            not r.strip() for r in risks.values()
        ):
            reject(
                "CHANGES_INVALID_COMMAND",
                "review.risks",
                "风险对象未知或缺少原因",
                "只为报告中的对象接受风险，并写明作者判断",
            )
        changes = []
        accepted: set[str] = set()
        for aid in patch["bases"]:
            hunks = [
                h for h in patch["hunks"] if h["artifact_id"] == aid and h["hunk_id"] in selected
            ]
            if not hunks:
                continue
            try:
                merged = apply_hunks(patch["bases"][aid], content(snapshot, aid), hunks)
            except ChangesViolation as exc:
                reject(exc.code, exc.where, exc.why, exc.fix)
                raise AssertionError("unreachable") from exc
            view = snapshot.artifacts[ArtifactId(aid)]
            assert view.revision
            try:
                validate_content_patch(view.artifact.kind, dict(view.revision.content), merged)
            except ChangesViolation as exc:
                reject(exc.code, exc.where, exc.why, exc.fix)
            changes.append(ArtifactChange(ArtifactId(aid), merged, view.revision.id))
            all_ids = {h["hunk_id"] for h in patch["hunks"] if h["artifact_id"] == aid}
            if all_ids <= set(selected):
                accepted.add(aid)
        # 在候选快照上重遍历；只有完整接受该对象的 Patch 才视为已处理。
        candidate = _project(snapshot, changes)
        fresh = traverse_impact(
            RelationGraph.from_snapshot(candidate),
            tuple(s.artifact_id for s in model.seed_changes),
            scope=expand_scope(model.scope, candidate),
            kind_of=lambda aid: (
                candidate.artifacts[ArtifactId(aid)].artifact.kind
                if ArtifactId(aid) in candidate.artifacts
                else None
            ),
        )
        items = list(report.items)
        signatures = {(i.target_artifact_id, i.path, i.mentioned_artifact_id) for i in items}
        items.extend(
            i
            for i in fresh.items
            if (i.target_artifact_id, i.path, i.mentioned_artifact_id) not in signatures
        )
        plan = next(
            RevisionPlan.from_content(v.revision.content)
            for v in snapshot.artifacts.values()
            if v.revision
            and v.artifact.kind == kinds.KIND_REVISION_PLAN
            and v.revision.content.get("change_set_id") == change_set_id
        )
        risk_indexes = {r.item_index for r in plan.risk_acceptances}
        items = [
            replace(
                i,
                status="accepted_risk"
                if i.target_artifact_id in risks
                else "resolved"
                if i.target_artifact_id in accepted
                else "accepted_risk"
                if i.status == "accepted_risk" or index in risk_indexes
                else "open",
            )
            for index, i in enumerate(items)
        ]
        updated_report = replace(
            report,
            items=tuple(items),
            stats=replace(
                report.stats,
                nodes_visited=fresh.stats.nodes_visited,
                paths_explored=fresh.stats.paths_explored,
                cycles_skipped=fresh.stats.cycles_skipped,
                item_count=len(items),
            ),
        )
        transition = TransitionRecord(
            "review",
            "committed",
            self.actor.kind,
            self.actor.id,
            self.clock.now().isoformat(),
            "作者审阅并提交选中补丁",
            model.base_commit_id,
        )
        updated_model = replace(model, status="committed", history=(*model.history, transition))
        result = {
            "change_set_id": change_set_id,
            "status": "committed",
            "fingerprint": fingerprint,
            "selected": list(selected),
            "risks": risks,
            "items": [i.to_content() for i in items],
            "base_commit_id": model.base_commit_id,
            "branch_commit_id": patch["branch_commit_id"],
        }
        model_view = snapshot.artifacts[ArtifactId(change_set_id)]
        assert model_view.revision
        changes.extend(
            (
                ArtifactChange(
                    report_view.artifact.id, updated_report.to_content(), report_view.revision.id
                ),
                ArtifactChange(
                    ArtifactId(change_set_id), updated_model.to_content(), model_view.revision.id
                ),
                ArtifactChange(ArtifactId(result_id), result, kind="changes.review"),
            )
        )
        self.committer.commit(
            CommitRequest(
                project,
                branch.id,
                branch.head_commit_id,
                self.actor,
                key,
                "接受作者选中的修订补丁",
                changes=tuple(changes),
                events=(
                    (
                        "changes.review.committed",
                        {
                            "change_set_id": change_set_id,
                            "selected": list(selected),
                            "open_count": sum(i.status == "open" for i in items),
                            "risks": risks,
                        },
                    ),
                ),
            )
        )
        return result


def _project(snapshot: Snapshot, changes: list[ArtifactChange]) -> Snapshot:
    artifacts = dict(snapshot.artifacts)
    for change in changes:
        view = artifacts[change.artifact_id]
        assert view.revision
        artifacts[change.artifact_id] = replace(
            view, revision=replace(view.revision, content=change.content)
        )
    return replace(snapshot, artifacts=artifacts)
