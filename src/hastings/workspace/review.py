"""有原文依据的写后检查（0.6 V06-C1）。

程序层面的路径、指纹与格式检查随写入执行，不调用模型。这里只处理需要理解剧情的检查：
人物前后是否矛盾、该藏的设定有没有提前说出来、改完之后是否还符合作者的要求。

几条边界：

- **检查实际落盘的内容**：输入是磁盘上的原文与修改结果，不是模型「生成过但没保存」的文字。
- **每条问题都要有原文证据**：证据给出路径、版本、字符区间与引文，区间与引文必须一致；
  跨文件冲突要列出双方出处。推测可以写，但要标成疑点。
- **没读完不能算通过**：无问题只有在目标全文与必需参考都确实提供过时才能成立；否则报告
  状态是「未完成」，不能包装成检查通过。
- **报告是过程记录**：存在应用状态里，不写设定、不产生作品 Git 提交，也不自动修稿。
  来源变化后旧报告标为过期，回看时不调用模型。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path
from hastings.workspace.materials import MaterialLedger, SourceRef, covers

__all__ = [
    "DESCRIPTION_MAX_CHARS",
    "EVIDENCE_MAX_CHARS",
    "MAX_FINDINGS",
    "MAX_REVIEWS",
    "REVIEW_INSTRUCTIONS",
    "ReviewDraft",
    "ReviewEvidence",
    "ReviewFinding",
    "ReviewReport",
    "ReviewRequest",
    "ReviewStatus",
    "ReviewStore",
    "publish_report",
    "validate_report",
]

#: 单份报告最多几条问题；超出说明检查没有收敛，应当缩小范围。
MAX_FINDINGS = 12
DESCRIPTION_MAX_CHARS = 500
EVIDENCE_MAX_CHARS = 160
#: 每个作品保留的报告条数。
MAX_REVIEWS = 50

ReviewStatus = Literal["findings", "no_findings", "incomplete"]
FindingKind = Literal["continuity", "disclosure", "task_mismatch", "other"]
Certainty = Literal["supported", "suspected"]
#: 回看时按当前磁盘算出的报告可用性。
ReportFreshness = Literal["valid", "stale", "source_missing"]

REVIEW_INSTRUCTIONS = """\
你在按作者的检查要求核对作品，只读，不改任何文件。

## 检查什么
- 只用实际落盘的原文核对；不要依据记忆、旧摘要或自己刚才生成的文字下结论。
- 人物说法、回忆、传闻与客观事件要分开：人物说「我当时在客厅」不等于他真的在客厅。
- 叙事时间与事件顺序要分清，不要因为顺序靠后就当成同一时刻。
- 已死亡的角色出现在回忆或转述里不算错误；确实存在的时间线冲突才算。

## 报告怎么给
- 每条问题至少引用一处原文证据：路径、版本、字符区间与引文，区间要和引文一致。
- 跨文件冲突要列出双方的出处。拿不准的写成「疑点」，不要写成确定结论。
- 没有发现问题时，只有在确实读完目标全文与作者指定的参考后才能报告无问题；
  没读完、来源不可用或预算不够时，把没覆盖的部分写进「未覆盖」，报告为未完成。
- 只报告问题与建议，不自动改稿。作者要求修复时，那是另一个修改任务。
"""


@dataclass(frozen=True, slots=True)
class ReviewEvidence:
    """一处原文依据：路径、版本、字符区间与引文。"""

    path: str
    digest: str
    start: int
    end: int
    quote: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "digest": self.digest,
            "start": self.start,
            "end": self.end,
            "quote": self.quote,
        }


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    kind: FindingKind
    certainty: Certainty
    description: str
    evidence: tuple[ReviewEvidence, ...]
    suggestion: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "certainty": self.certainty,
            "description": self.description,
            "evidence": [item.to_dict() for item in self.evidence],
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """作者明确要求的检查：要求原话、授权引文与目标文件。"""

    requirement: str
    authorization: str
    targets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewDraft:
    """模型提交的报告草稿；结构合法不代表语义判断正确。"""

    target_paths: tuple[str, ...]
    target_digests: tuple[tuple[str, str], ...]
    requirement: str
    status: ReviewStatus
    findings: tuple[ReviewFinding, ...] = ()
    uncovered: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewReport:
    report_id: str
    turn_id: str
    target_paths: tuple[str, ...]
    target_digests: tuple[tuple[str, str], ...]
    requirement: str
    status: ReviewStatus
    findings: tuple[ReviewFinding, ...]
    uncovered: tuple[str, ...]
    created_at: str
    freshness: ReportFreshness = "valid"

    def to_dict(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "turn_id": self.turn_id,
            "target_paths": list(self.target_paths),
            "target_digests": [[path, digest] for path, digest in self.target_digests],
            "requirement": self.requirement,
            "status": self.status,
            "freshness": self.freshness,
            "findings": [item.to_dict() for item in self.findings],
            "uncovered": list(self.uncovered),
            "created_at": self.created_at,
        }

    @classmethod
    def from_raw(cls, raw: object) -> ReviewReport | None:
        if not isinstance(raw, dict):
            return None
        report_id = raw.get("report_id")
        turn_id = raw.get("turn_id")
        requirement = raw.get("requirement")
        status = raw.get("status")
        created = raw.get("created_at")
        if not isinstance(report_id, str) or not report_id:
            return None
        if not isinstance(turn_id, str) or not turn_id:
            return None
        if not isinstance(created, str) or not created:
            return None
        if not isinstance(requirement, str) or not isinstance(status, str):
            return None
        if status not in ("findings", "no_findings", "incomplete"):
            return None
        targets = raw.get("target_paths")
        if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
            return None
        digests = raw.get("target_digests")
        if not isinstance(digests, list):
            return None
        pairs: list[tuple[str, str]] = []
        for item in digests:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not all(isinstance(value, str) for value in item)
            ):
                return None
            pairs.append((item[0], item[1]))
        findings = _findings_from_raw(raw.get("findings"))
        if findings is None:
            return None
        uncovered = raw.get("uncovered")
        if not isinstance(uncovered, list) or not all(isinstance(item, str) for item in uncovered):
            return None
        return cls(
            report_id=report_id,
            turn_id=turn_id,
            target_paths=tuple(targets),
            target_digests=tuple(pairs),
            requirement=requirement,
            status=cast(ReviewStatus, status),
            findings=findings,
            uncovered=tuple(uncovered),
            created_at=created,
        )


class ReviewStore:
    """作品绑定状态目录里的 ``reviews.json``；报告只追加，超出上限丢弃最旧的。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.file = self.root / "reviews.json"

    def read_all(self) -> tuple[ReviewReport, ...]:
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return ()
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return ()
        reports: list[ReviewReport] = []
        for value in items:
            report = ReviewReport.from_raw(value)
            if report is not None:
                reports.append(report)
        return tuple(reports)

    def publish(self, report: ReviewReport) -> ReviewReport:
        reports = [*self.read_all(), report][-MAX_REVIEWS:]
        self._save(reports)
        return report

    def reports(self, workspace: Workspace, *, path: str | None = None) -> tuple[ReviewReport, ...]:
        """回看时按当前磁盘复核目标版本：过期不重跑模型。"""
        rows: list[ReviewReport] = []
        for report in self.read_all():
            if path is not None and path not in report.target_paths:
                continue
            rows.append(_with_freshness(report, self.freshness(report, workspace)))
        return tuple(reversed(rows))

    def freshness(self, report: ReviewReport, workspace: Workspace) -> ReportFreshness:
        for target in report.target_paths:
            expected = dict(report.target_digests).get(target)
            try:
                view = workspace.read(target)
            except WorkspaceError:
                return "source_missing"
            if expected is None or view.digest != expected:
                return "stale"
        return "valid"

    def _save(self, reports: Sequence[ReviewReport]) -> None:
        payload = {"version": 1, "items": [report.to_dict() for report in reports]}
        handle: int | None = None
        temp_name: str | None = None
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                dir=str(self.file.parent), prefix=".reviews-", suffix=".json"
            )
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                handle = None
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temp_name, self.file)
            temp_name = None
        except OSError as exc:
            raise WorkspaceError(
                code="REVIEW_WRITE_FAILED",
                where="review.save",
                why=f"无法保存检查报告：{exc.strerror or exc}",
                fix="检查应用状态目录是否可写；作品内容不受影响",
            ) from exc
        finally:
            if handle is not None:
                os.close(handle)
            if temp_name is not None:
                with suppress(OSError):
                    os.unlink(temp_name)


def validate_report(
    draft: ReviewDraft,
    *,
    workspace: Workspace,
    ledger: MaterialLedger,
    allowed_targets: Sequence[str],
) -> None:
    """结构、覆盖与证据校验；任何一条不成立都不发布。"""
    if not draft.target_paths:
        raise _invalid("没有给出检查目标")
    if not draft.requirement.strip():
        raise _invalid("没有记录作者的检查要求")
    for path in draft.target_paths:
        if not is_managed_path(path):
            raise _invalid(f"检查目标不是作品受管路径：{path}")
        if allowed_targets and path not in allowed_targets:
            raise WorkspaceError(
                code="REVIEW_SCOPE_INVALID",
                where=f"review.validate:{path}",
                why="这个文件不在本轮允许检查的范围内",
                fix="只检查作者指定的文件；需要更多文件时由作者重新发起",
                details={"path": path, "allowed": list(allowed_targets)},
            )
    if draft.status == "findings" and not draft.findings:
        raise _invalid("状态是「发现问题」却没有列出任何问题")
    if draft.status == "no_findings" and draft.findings:
        raise _invalid("状态是「没有发现问题」却带了问题条目")
    if draft.status == "incomplete" and not draft.uncovered:
        raise _invalid("状态是「未完成」时要说明哪些部分没有覆盖")
    if len(draft.findings) > MAX_FINDINGS:
        raise _invalid(f"问题超过 {MAX_FINDINGS} 条，请先收敛范围")
    for finding in draft.findings:
        _validate_finding(finding, workspace=workspace, ledger=ledger)
    if draft.status == "no_findings":
        _require_full_coverage(draft.target_paths, workspace=workspace, ledger=ledger)


def publish_report(
    draft: ReviewDraft,
    *,
    workspace: Workspace,
    ledger: MaterialLedger,
    store: ReviewStore,
    turn_id: str,
    allowed_targets: Sequence[str] = (),
) -> ReviewReport:
    """发布报告：先核对目标版本与覆盖，再写缓存。"""
    validate_report(draft, workspace=workspace, ledger=ledger, allowed_targets=allowed_targets)
    for path, digest in draft.target_digests:
        try:
            view = workspace.read(path)
        except WorkspaceError as exc:
            raise WorkspaceError(
                code="CONTEXT_SOURCE_NOT_FOUND",
                where=f"review.publish:{path}",
                why=f"检查目标当前不可用：{path}（{exc.why}）",
                fix="刷新目录后重新检查",
                details={"path": path},
            ) from exc
        if view.digest != digest:
            raise WorkspaceError(
                code="REVIEW_SOURCE_STALE_CONFLICT",
                where=f"review.publish:{path}",
                why="检查期间目标文件被改动，不能发布当前有效的报告",
                fix="重新读取当前内容后再检查",
                details={"path": path, "expected_digest": digest, "current_digest": view.digest},
            )
    report = ReviewReport(
        report_id=f"r_{os.urandom(4).hex()}",
        turn_id=turn_id,
        target_paths=draft.target_paths,
        target_digests=draft.target_digests,
        requirement=draft.requirement.strip(),
        status=draft.status,
        findings=draft.findings,
        uncovered=draft.uncovered,
        created_at=_now(),
    )
    return store.publish(report)


def _validate_finding(
    finding: ReviewFinding, *, workspace: Workspace, ledger: MaterialLedger
) -> None:
    if finding.kind not in ("continuity", "disclosure", "task_mismatch", "other"):
        raise _invalid(f"不支持的问题类别：{finding.kind}")
    if finding.certainty not in ("supported", "suspected"):
        raise _invalid(f"不支持的确定程度：{finding.certainty}")
    for field, text in (
        ("问题描述", finding.description),
        ("修改建议", finding.suggestion),
    ):
        if not text.strip():
            raise _invalid(f"{field}为空")
        if len(text) > DESCRIPTION_MAX_CHARS:
            raise _invalid(f"{field}超过 {DESCRIPTION_MAX_CHARS} 字符")
    if not finding.evidence:
        raise _invalid("每条问题至少要有一处原文证据")
    if len(finding.evidence) > 4:
        raise _invalid("单条问题最多引用 4 处证据")
    for evidence in finding.evidence:
        _validate_evidence(evidence, workspace=workspace, ledger=ledger)


def _validate_evidence(
    evidence: ReviewEvidence, *, workspace: Workspace, ledger: MaterialLedger
) -> None:
    if not is_managed_path(evidence.path):
        raise _invalid(f"证据路径不是作品受管路径：{evidence.path}")
    try:
        view = workspace.read(evidence.path)
    except WorkspaceError as exc:
        raise WorkspaceError(
            code="CONTEXT_SOURCE_NOT_FOUND",
            where=f"review.evidence:{evidence.path}",
            why=f"证据所在文件当前不可用：{evidence.path}（{exc.why}）",
            fix="重新读取该文件后再给证据",
            details={"path": evidence.path},
        ) from exc
    if view.digest != evidence.digest:
        raise WorkspaceError(
            code="REVIEW_SOURCE_STALE_CONFLICT",
            where=f"review.evidence:{evidence.path}",
            why="证据引用的版本已经不是当前版本",
            fix="按当前内容重新摘录引文",
            details={
                "path": evidence.path,
                "expected_digest": evidence.digest,
                "current_digest": view.digest,
            },
        )
    if evidence.start < 0 or evidence.end <= evidence.start or evidence.end > len(view.text):
        raise _invalid(f"证据区间无效：{evidence.path}")
    if not evidence.quote.strip() or len(evidence.quote) > EVIDENCE_MAX_CHARS:
        raise _invalid(f"证据引文为空或超过 {EVIDENCE_MAX_CHARS} 字符")
    if _collapse(evidence.quote) != _collapse(view.text[evidence.start : evidence.end]):
        raise _invalid(f"证据引文与声明的原文区间不一致：{evidence.path}")
    coverage = ledger.coverage(evidence.path)
    if coverage is None or coverage.ref.digest != view.digest:
        raise WorkspaceError(
            code="REVIEW_EVIDENCE_INVALID",
            where=f"review.evidence:{evidence.path}",
            why="这条证据的文件本轮没有读取过，不能作为依据",
            fix="先 read_file 读取该文件，再引用其中的原文",
            details={"path": evidence.path},
        )
    if not covers(coverage.spans, evidence.start, evidence.end):
        # 读过文件不等于读过这一段：证据区间必须落在实际提供的原文里。
        raise WorkspaceError(
            code="REVIEW_EVIDENCE_INVALID",
            where=f"review.evidence:{evidence.path}",
            why="这条证据所在的区间本轮没有提供给模型",
            fix="先 read_file 读到包含这段原文的区间，再引用",
            details={
                "path": evidence.path,
                "start": evidence.start,
                "end": evidence.end,
                "read": [list(span) for span in coverage.spans],
            },
        )


def _require_full_coverage(
    targets: Sequence[str], *, workspace: Workspace, ledger: MaterialLedger
) -> None:
    for path in targets:
        try:
            view = workspace.read(path)
        except WorkspaceError as exc:
            raise WorkspaceError(
                code="CONTEXT_SOURCE_NOT_FOUND",
                where=f"review.coverage:{path}",
                why=f"检查目标当前不可用：{path}（{exc.why}）",
                fix="刷新目录后重新检查",
                details={"path": path},
            ) from exc
        coverage = ledger.coverage(path)
        if coverage is None or coverage.ref.digest != view.digest or not coverage.complete:
            raise WorkspaceError(
                code="REVIEW_EVIDENCE_INVALID",
                where=f"review.coverage:{path}",
                why="没有读完目标全文，不能报告「没有发现问题」",
                fix="读完目标全文再发布；读不完就报告为未完成，并写明没覆盖的部分",
                details={"path": path},
            )
    ledger.require_references_complete(workspace)


def _findings_from_raw(raw: object) -> tuple[ReviewFinding, ...] | None:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return None
    findings: list[ReviewFinding] = []
    for value in raw:
        if not isinstance(value, dict):
            return None
        kind = value.get("kind")
        certainty = value.get("certainty")
        description = value.get("description")
        suggestion = value.get("suggestion")
        evidence_raw = value.get("evidence")
        if kind not in ("continuity", "disclosure", "task_mismatch", "other"):
            return None
        if certainty not in ("supported", "suspected"):
            return None
        if not isinstance(description, str) or not isinstance(suggestion, str):
            return None
        if not isinstance(evidence_raw, list):
            return None
        evidence: list[ReviewEvidence] = []
        for item in evidence_raw:
            if not isinstance(item, dict):
                return None
            path = item.get("path")
            digest = item.get("digest")
            start = item.get("start")
            end = item.get("end")
            quote = item.get("quote")
            if not isinstance(path, str) or not isinstance(digest, str):
                return None
            if not isinstance(start, int) or not isinstance(end, int):
                return None
            if not isinstance(quote, str):
                return None
            evidence.append(
                ReviewEvidence(path=path, digest=digest, start=start, end=end, quote=quote)
            )
        findings.append(
            ReviewFinding(
                kind=cast(FindingKind, kind),
                certainty=cast(Certainty, certainty),
                description=description,
                evidence=tuple(evidence),
                suggestion=suggestion,
            )
        )
    return tuple(findings)


def _with_freshness(report: ReviewReport, freshness: ReportFreshness) -> ReviewReport:
    return ReviewReport(
        report_id=report.report_id,
        turn_id=report.turn_id,
        target_paths=report.target_paths,
        target_digests=report.target_digests,
        requirement=report.requirement,
        status=report.status,
        findings=report.findings,
        uncovered=report.uncovered,
        created_at=report.created_at,
        freshness=freshness,
    )


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _invalid(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="REVIEW_EVIDENCE_INVALID",
        where="review.validate",
        why=why,
        fix="按实际读到的原文与作者的检查要求重新提交；没读完就报告为未完成",
    )


def target_digests(workspace: Workspace, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """读取目标当前版本；缺失时按检查目标不可用失败。"""
    pairs: list[tuple[str, str]] = []
    for path in paths:
        try:
            view = workspace.read(path)
        except WorkspaceError as exc:
            raise WorkspaceError(
                code="CONTEXT_SOURCE_NOT_FOUND",
                where=f"review.target:{path}",
                why=f"检查目标当前不可用：{path}（{exc.why}）",
                fix="刷新目录后重新发起检查",
                details={"path": path},
            ) from exc
        pairs.append((view.path, view.digest))
    return tuple(pairs)


def evidence_ref(workspace: Workspace, path: str) -> SourceRef:
    """证据校验用的版本引用；调用方已确认文件存在。"""
    view = workspace.read(path)
    return SourceRef(view.path, view.digest)
