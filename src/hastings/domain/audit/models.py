"""Finding 的状态与证据引用；fixed 不能缺少实际修复 Commit。"""

from dataclasses import asdict, dataclass
from typing import Any

from hastings.domain.changes.errors import violation


@dataclass(frozen=True, slots=True)
class Finding:
    finding_id: str
    code: str
    where: str
    why: str
    fix: str
    evidence: dict[str, Any]
    evidence_commit_id: str
    severity: str = "warning"
    status: str = "open"
    fixed_by_commit_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("open", "fixed", "accepted_risk", "dismissed"):
            raise violation(
                "AUDIT_INVALID_STATUS",
                "audit.finding",
                "Finding 状态无效",
                "使用 open/fixed/accepted_risk/dismissed",
            )
        if self.status == "fixed" and not self.fixed_by_commit_id:
            raise violation(
                "AUDIT_REPAIR_COMMIT_REQUIRED",
                "audit.finding",
                "fixed 缺少修复 Commit",
                "先提交包含对应修复的内容，再重新运行审计",
            )

    def to_content(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_content(cls, body: dict[str, Any]) -> "Finding":
        return cls(**body)
