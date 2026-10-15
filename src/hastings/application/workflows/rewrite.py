"""按章节在独立分支重写，锁定段落不进入 Worker；控制状态持久化。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.clock import Clock
from hastings.application.ports.ids import IdGenerator
from hastings.application.ports.repository import (
    CommitRepository,
    LeaseRepository,
    RepositoryReader,
)
from hastings.application.workflows.change_support import (
    branch_snapshot,
    content,
    reject,
    replay_content,
    require_author,
)
from hastings.domain.changes.execution import (
    PatchProposal,
    StepContext,
    TargetView,
    validate_proposal,
)
from hastings.domain.changes.patch_guard import validate_content_patch
from hastings.domain.changes.planning import PlanStep
from hastings.domain.changes.review import make_hunks
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, JobId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, CreateBranchRequest
from hastings.domain.repository.serialization import content_hash


class RewriteWorkflow:
    def __init__(
        self,
        reader: RepositoryReader,
        committer: CommitRepository,
        leases: LeaseRepository,
        ids: IdGenerator,
        clock: Clock,
        actor: Actor,
    ) -> None:
        self.reader, self.committer, self.leases = reader, committer, leases
        self.ids, self.clock, self.actor = ids, clock, actor

    def open(
        self, project: ProjectId, base: str, locked: tuple[str, ...], key: str
    ) -> dict[str, Any]:
        require_author(self.actor)
        run_id = f"rewrite:{project.value}:{content_hash(key)[:20]}"
        fingerprint = content_hash({"base": base, "locked": sorted(locked)})
        old = replay_content(self.reader, project, key, run_id)
        if old is not None:
            if old["fingerprint"] != fingerprint:
                reject(
                    "CHANGES_REWRITE_CONFLICT",
                    "rewrite.open",
                    "同键的重写范围不同",
                    "新重写使用新的请求键",
                )
            return old
        snapshot = self.reader.get_snapshot(project, CommitId(base))
        for aid in locked:
            view = snapshot.artifacts.get(ArtifactId(aid))
            if view is None or view.artifact.kind != "manuscript.text_unit":
                reject(
                    "CHANGES_INVALID_COMMAND",
                    "rewrite.lock",
                    "锁定目标不是正文单元",
                    "仅锁定所选基础版本中已有的 TextUnit",
                )
        name = f"rewrite/{content_hash(run_id)[:20]}"
        if self.reader.get_branch(project, name) is None:
            self.committer.create_branch(CreateBranchRequest(project, name, CommitId(base)))
        run = {
            "rewrite_id": run_id,
            "base_commit_id": base,
            "branch_name": name,
            "locked_unit_ids": list(locked),
            "status": "opened",
            "completed_chapters": [],
            "fingerprint": fingerprint,
            "at": self.clock.now().isoformat(),
        }
        self._save(project, run, key)
        return run

    def _save(self, project: ProjectId, run: dict[str, Any], key: str) -> None:
        branch, snapshot = branch_snapshot(self.reader, project)
        previous = snapshot.artifacts.get(ArtifactId(run["rewrite_id"]))
        self.committer.commit(
            CommitRequest(
                project,
                branch.id,
                branch.head_commit_id,
                self.actor,
                key,
                f"重写状态：{run['status']}",
                changes=(
                    ArtifactChange(
                        ArtifactId(run["rewrite_id"]),
                        run,
                        previous.revision.id if previous and previous.revision else None,
                        "changes.rewrite",
                    ),
                ),
                events=(
                    (
                        "changes.rewrite.updated",
                        {"rewrite_id": run["rewrite_id"], "status": run["status"]},
                    ),
                ),
            )
        )

    def _control(self, project: ProjectId, run_id: str, status: str, key: str) -> dict[str, Any]:
        require_author(self.actor)
        replay = replay_content(self.reader, project, key, run_id)
        if replay is not None:
            if replay["status"] != status:
                reject(
                    "CHANGES_REWRITE_CONFLICT",
                    "rewrite.control",
                    "同一请求键对应不同控制意图",
                    "新控制操作使用新请求键",
                )
            return replay
        _, snapshot = branch_snapshot(self.reader, project)
        run = content(snapshot, run_id)
        if run["status"] == "abandoned":
            reject(
                "CHANGES_INVALID_TRANSITION", "rewrite.control", "重写已放弃", "建立新的重写分支"
            )
        if status == "opened" and run["status"] not in ("aborted", "failed"):
            reject(
                "CHANGES_INVALID_TRANSITION",
                "rewrite.resume",
                "只有中止或失败任务能恢复",
                "先查看当前任务状态",
            )
        updated = {**run, "status": status}
        self._save(project, updated, key)
        return updated

    def abort(self, project: ProjectId, run_id: str, key: str) -> dict[str, Any]:
        return self._control(project, run_id, "aborted", key)

    def abandon(self, project: ProjectId, run_id: str, key: str) -> dict[str, Any]:
        return self._control(project, run_id, "abandoned", key)

    def resume(self, project: ProjectId, run_id: str, key: str) -> dict[str, Any]:
        return self._control(project, run_id, "opened", key)

    def compare(self, project: ProjectId, run_id: str) -> dict[str, Any]:
        _, main = branch_snapshot(self.reader, project)
        run = content(main, run_id)
        _, branch = branch_snapshot(self.reader, project, run["branch_name"])
        base = self.reader.get_snapshot(project, CommitId(run["base_commit_id"]))
        hunks = []
        for diff in self.reader.diff(base.commit_id, branch.commit_id).artifact_changes:
            hunks.extend(
                make_hunks(
                    diff.artifact_id.value,
                    content(base, diff.artifact_id.value),
                    content(branch, diff.artifact_id.value),
                )
            )
        return {**run, "branch_commit_id": branch.commit_id.value, "hunks": hunks}

    def execute(
        self,
        project: ProjectId,
        run_id: str,
        worker: Callable[[StepContext], PatchProposal],
        key: str,
    ) -> dict[str, Any]:
        require_author(self.actor)
        _, main = branch_snapshot(self.reader, project)
        run = content(main, run_id)
        if run["status"] == "review":
            return run
        if run["status"] not in ("opened", "executing"):
            reject(
                "CHANGES_INVALID_TRANSITION",
                "rewrite.execute",
                "任务未开放执行",
                "中止或失败后由作者恢复；已放弃的重写不可复活",
            )
        lease = self.leases.acquire(f"rewrite:{run_id}", JobId(self.ids.new_id("job")), 900)
        try:
            if run["status"] == "opened":
                self._save(project, {**run, "status": "executing"}, f"{key}:start")
            base = self.reader.get_snapshot(project, CommitId(run["base_commit_id"]))
            groups: dict[str, list[str]] = {}
            for view in base.artifacts.values():
                if (
                    view.revision
                    and view.artifact.kind == "manuscript.text_unit"
                    and view.artifact.id.value not in run["locked_unit_ids"]
                    and view.revision.content.get("state", "active") == "active"
                ):
                    groups.setdefault(view.revision.content["chapter_id"], []).append(
                        view.artifact.id.value
                    )

            def chapter_order(chapter: str) -> tuple[str, str]:
                view = base.artifacts.get(ArtifactId(chapter))
                return (
                    str(view.revision.content.get("order_key", ""))
                    if view and view.revision
                    else "",
                    chapter,
                )

            for chapter in sorted(groups, key=chapter_order):
                aids = groups[chapter]
                # 控制记录每步重读；作者可在 Worker 返回后中止，结果不得再写入分支。
                _, main = branch_snapshot(self.reader, project)
                current = content(main, run_id)
                if current["status"] != "executing":
                    return current
                run = {**run, "status": "executing"}
                step_key = f"{run_id}:chapter:{chapter}"
                if self.reader.find_commit_result(project, step_key) is not None:
                    if chapter not in run["completed_chapters"]:
                        run = {**run, "completed_chapters": [*run["completed_chapters"], chapter]}
                    continue
                branch, snapshot = branch_snapshot(self.reader, project, run["branch_name"])
                targets = tuple(
                    TargetView(aid, "manuscript.text_unit", content(snapshot, aid))
                    for aid in sorted(aids)
                )
                proposal = worker(StepContext(run_id, chapter, "manuscript", chapter, targets))
                validate_proposal(
                    proposal,
                    PlanStep(chapter, "manuscript", chapter, tuple(aids), ()),
                    where="rewrite.execute",
                )
                _, main = branch_snapshot(self.reader, project)
                if content(main, run_id)["status"] != "executing":
                    return content(main, run_id)
                changes = []
                for unit in proposal.patches:
                    old = content(snapshot, unit.artifact_id)
                    validate_content_patch("manuscript.text_unit", old, unit.content)
                    if any(
                        old.get(k) != unit.content.get(k)
                        for k in old.keys() | unit.content.keys()
                        if k != "text"
                    ):
                        reject(
                            "CHANGES_PROPOSAL_OUT_OF_SCOPE",
                            "rewrite.execute",
                            "重写试图修改段落身份或结构",
                            "重写只修改当前章节未锁定单元的 text",
                        )
                    view = snapshot.artifacts[ArtifactId(unit.artifact_id)]
                    assert view.revision
                    changes.append(
                        ArtifactChange(ArtifactId(unit.artifact_id), unit.content, view.revision.id)
                    )
                self.committer.commit(
                    CommitRequest(
                        project,
                        branch.id,
                        branch.head_commit_id,
                        self.actor,
                        step_key,
                        f"重写章节 {chapter}",
                        changes=tuple(changes),
                        required_leases=(
                            (lease.resource_key, lease.owner_job_id.value, lease.version),
                        ),
                        events=(
                            (
                                "changes.rewrite.chapter",
                                {"rewrite_id": run_id, "chapter_id": chapter},
                            ),
                        ),
                    )
                )
                run = {
                    **run,
                    "status": "executing",
                    "completed_chapters": [*run["completed_chapters"], chapter],
                }
                self._save(project, run, f"{step_key}:record")
            run = {**run, "status": "review"}
            self._save(project, run, f"{key}:done")
            return run
        except Exception as exc:
            _, main = branch_snapshot(self.reader, project)
            latest = content(main, run_id)
            if latest["status"] == "executing":
                self._save(
                    project, {**latest, "status": "failed", "error": str(exc)}, f"{key}:failed"
                )
            raise
        finally:
            with suppress(ViolationError):
                self.leases.release(lease)
