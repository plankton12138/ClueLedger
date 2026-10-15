"""将检查证据和修复 Commit 关联保存；重复审计不会凭空消除 Finding。"""

from __future__ import annotations

import json
from typing import Any

from hastings.application.ports.clock import Clock
from hastings.application.ports.repository import CommitRepository, RepositoryReader
from hastings.application.workflows.change_support import branch_snapshot, replay_content
from hastings.domain.audit.models import Finding
from hastings.domain.audit.rules import audit_snapshot
from hastings.domain.repository.ids import Actor, ArtifactId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest


class AuditWorkflow:
    def __init__(
        self, reader: RepositoryReader, committer: CommitRepository, clock: Clock, actor: Actor
    ) -> None:
        self.reader, self.committer, self.clock, self.actor = reader, committer, clock, actor

    def run(self, project: ProjectId, key: str) -> dict[str, Any]:
        audit_id = f"audit:{project.value}"
        replay = replay_content(self.reader, project, key, audit_id)
        if replay is not None:
            return replay
        branch, snapshot = branch_snapshot(self.reader, project)
        findings = audit_snapshot(snapshot)
        current = {f["finding_id"] for f in findings}
        previous = snapshot.artifacts.get(ArtifactId(audit_id))
        if previous and previous.revision:
            for old in previous.revision.content["findings"]:
                if old["finding_id"] in current:
                    continue
                if old["status"] == "fixed":
                    findings.append(old)
                    continue
                evidence = json.dumps([old["where"], old["evidence"]], ensure_ascii=False)
                repair_commit = None
                after_evidence = False
                for commit in self.reader.commit_history(project, snapshot.commit_id):
                    if commit.id.value == old["evidence_commit_id"]:
                        after_evidence = True
                        continue
                    if not after_evidence or not commit.parent_ids:
                        continue
                    delta = self.reader.diff(commit.parent_ids[0], commit.id)
                    relevant = any(
                        d.artifact_id.value in evidence
                        and not d.kind.startswith(("changes.", "audit."))
                        for d in delta.artifact_changes
                    )
                    if relevant and not any(
                        f["finding_id"] == old["finding_id"]
                        for f in audit_snapshot(self.reader.get_snapshot(project, commit.id))
                    ):
                        repair_commit = commit.id.value
                        break
                # 没有相关内容 Commit，即使报告漏项也不能宣称 fixed。
                findings.append(
                    {
                        **old,
                        "status": "fixed" if repair_commit else "open",
                        "fixed_by_commit_id": repair_commit,
                    }
                )
        findings = [Finding.from_content(f).to_content() for f in findings]
        result = {
            "audit_id": audit_id,
            "checked_commit_id": snapshot.commit_id.value,
            "findings": findings,
            "at": self.clock.now().isoformat(),
        }
        self.committer.commit(
            CommitRequest(
                project,
                branch.id,
                branch.head_commit_id,
                self.actor,
                key,
                "保存确定性审计结果",
                changes=(
                    ArtifactChange(
                        ArtifactId(audit_id),
                        result,
                        previous.revision.id if previous and previous.revision else None,
                        "audit.report",
                    ),
                ),
                events=(
                    (
                        "audit.completed",
                        {
                            "open_count": sum(f["status"] == "open" for f in findings),
                            "checked_commit_id": snapshot.commit_id.value,
                        },
                    ),
                ),
            )
        )
        return result
