"""审计只返回证据；修复状态由包含修复的真实 Commit 支撑。"""

from __future__ import annotations

from typing import Any

from hastings.domain.repository.models import Snapshot
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story.canon.rules import canon_findings
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.mystery.rules import mystery_findings


def audit_snapshot(snapshot: Snapshot) -> list[dict[str, Any]]:
    catalog = StoryCatalog.from_snapshot(snapshot)
    try:
        seeds = (*canon_findings(catalog), *mystery_findings(catalog, fairness_cutoff=1.0))
    except StoryViolation as exc:
        return [
            {
                "finding_id": content_hash({"code": exc.code, "where": exc.where}),
                "code": exc.code,
                "where": exc.where,
                "why": exc.why,
                "fix": exc.fix,
                "evidence": exc.details,
                "status": "open",
                "severity": "error",
                "evidence_commit_id": snapshot.commit_id.value,
                "fixed_by_commit_id": None,
            }
        ]
    return [
        {
            "finding_id": content_hash({"code": s.code, "where": s.where}),
            "code": s.code,
            "where": s.where,
            "why": s.why,
            "fix": s.fix,
            "evidence": s.details,
            "severity": s.severity_hint,
            "status": "open",
            "evidence_commit_id": snapshot.commit_id.value,
            "fixed_by_commit_id": None,
        }
        for s in seeds
    ]
