"""有原文依据的线索推理核对（1.0 S5）。

核对对象是作者保存的推理目标：目标意图、线索集合、必要前提与其他解释在受理时冻结，
正文、计划与线索修订也一并固定。报告只描述「读者已见的正文是否支持这个目标」，
不把作者计划当成前提，也不把未写明的真相当成错误。

几条边界：

- **正文证据来自本轮实际提供的原文**：区间、指纹、引文与读取范围由材料记录核对；
  章节名之后的正文、未读过的区间、作者计划都不能被当成读者已见的依据。
- **没读完不能算通过**：``no_findings`` 要求冻结目标的当前全文都读过；读不完只能
  ``incomplete`` 并写明未覆盖部分。
- **报告属于账本**：与通用检查的最近 50 份列表无关；改动后旧报告保留，只标为需复核。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, Field, JsonValue

from hastings.workspace.clue_models import ClueDesignLink, ClueEvidence, ClueLedger, FileSource
from hastings.workspace.clue_store import ClueLedgerStore, new_id
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import (
    Workspace,
    has_chapter_content,
    is_managed_path,
    managed_kind,
)
from hastings.workspace.materials import MaterialLedger, covers
from hastings.workspace.review import (
    DESCRIPTION_MAX_CHARS,
    MAX_FINDINGS,
    ReviewEvidence,
    require_full_coverage,
    validate_evidence,
)
from hastings.workspace.source_locations import review_evidence_id

__all__ = [
    "CLUE_FINDING_KINDS",
    "CLUE_REVIEW_INSTRUCTIONS",
    "CLUE_REVIEW_MAX_TARGETS",
    "ClueReviewDraft",
    "ClueReviewFinding",
    "ClueReviewReportView",
    "ClueReviewScope",
    "ClueReviewSummaryView",
    "clue_review_report",
    "clue_review_section",
    "clue_review_summaries",
    "freeze_clue_review",
    "publish_clue_review",
]

CLUE_REVIEW_MAX_TARGETS = 10
CLUE_FINDING_KINDS = (
    "inference_gap",
    "alternative_open",
    "premise_undisclosed",
    "counter_evidence",
)
ReviewStatus = Literal["findings", "no_findings", "incomplete"]
Certainty = Literal["supported", "suspected"]
ReportFreshness = Literal["valid", "stale", "source_missing", "target_changed"]

CLUE_REVIEW_INSTRUCTIONS = """\
你在核对作者保存的一组线索推理，只读，不改任何文件。

## 核对什么
- 目标是读者已见的正文能不能支撑这个推理，不是作者打算怎么写；作者计划只说明安排。
- 前提必须已经在正文里给读者看到；只在计划里写过、只在人物小传里写着，都不算已经交代。
- 先按冻结的 intent 选择判断标准：deduce 核对结论是否足够收敛；suspect 只核对是否有
  合理起疑的正文支持，允许较弱或间接依据，不要求排除所有其他解释。不能把「尚不能确定」
  直接写成「不能怀疑」，也不能要求组合中的每条线索单独证明整个目标。
- 章标题、所在场景与相邻句也是叙述上下文。对符合上下文的日常省略不要一律视为零依据；
  若仍有歧义，按疑点说明，不把需要作者选择的加强写法当作必须修复的错误。
- 其他解释没有被排除时按 alternative_open 报出，说明缺哪一处正文；目标是 keep_open 时，
  未排除的解释是作者有意保留的安排，不当作错误。
- 证据之间冲突、或正文出现与目标相反的说法，按 counter_evidence 报出并引用双方出处。
  反证必须针对同一命题与时点；「尚无人证实」、缺少旁证属于支撑不足，不是相反事实。
  未明确为同一时刻的天气或场景变化，也不能直接判为目标的反证。
- 线索之间缺少必要的连接、读者无法从已见正文推出结论，按 inference_gap 报出。
- 人物说法、回忆、传闻只是说法，不等于客观事实；没有给定真相时不推断作者意图。
- 只使用实际读到的原文；不要依据记忆、摘要或自己刚才生成的内容下结论。

## 报告怎么给
- 读完并核对区间后只提交最终完整报告。不要提交探针、测试或占位报告来试字符坐标。
  验证失败时，重新读取原文、修正完整内容后提交；成功发布后停止提交本轮报告并概述结果。
- 每条问题至少引用一处已读原文：路径、版本、字符区间与引文，区间要和引文一致。
- 依据只能来自 `正文/` 下的章节文件；设定、计划、真相与角色小传是作者侧信息，
  可以用来理解安排，但不能当作读者已见的证据提交。
- 确定程度 supported 表示正文已足够支撑这条判断，suspected 表示只是疑点。
- 没有发现问题时，只有冻结目标的当前全文都读完才能报告 no_findings；
  没读完、来源不可用或预算不够时报告 incomplete，并逐条写明未覆盖的部分。
- 只报告问题与建议，不改正文，也不自动修改线索、计划或推理目标。
"""


_INTENT_LABEL = {
    "deduce": "推出结论（需要与目标相称的支撑）",
    "suspect": "怀疑（可以接受较弱依据）",
    "keep_open": "有意保留（未排除的解释不算错误）",
    "undecided": "尚未决定",
}


def clue_review_section(scope: Mapping[str, object], ledger: ClueLedger | None) -> str:
    """把冻结范围写进本轮提示：模型要能原样带回目标标识，而不是猜。"""
    parsed = ClueReviewScope.from_dict(scope)
    titles = {
        clue_id: ledger.clues[clue_id].title
        for clue_id in parsed.clue_ids
        if ledger is not None and clue_id in ledger.clues
    }

    def label(clue_id: str) -> str:
        title = titles.get(clue_id)
        return f"{title}（{clue_id}）" if title else clue_id

    lines = [
        "## 本轮冻结的核对范围",
        f"- 推理目标：{parsed.goal}",
        f"- 目标标识：{parsed.inference_id}（版本 {parsed.inference_revision}）",
        f"- 目标性质：{_INTENT_LABEL.get(parsed.intent, parsed.intent)}",
        f"- 线索：{'、'.join(label(clue_id) for clue_id in parsed.clue_ids)}",
    ]
    if parsed.premises:
        lines.append("- 作者列出的必要前提：" + "；".join(parsed.premises))
    if parsed.alternatives:
        lines.append("- 作者列出的其他解释：" + "；".join(parsed.alternatives))
    lines.append(
        f"- 核对目标章（截至第 {parsed.as_of_chapter} 章）：{'、'.join(parsed.target_paths)}"
    )
    lines.append(
        "提交报告时把上面的目标标识原样填进 clue_analysis.inference_id，"
        "target_paths 只写这些目标章；目标与范围由受理时冻结，不能自行扩大。"
    )
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ClueReviewScope:
    """受理时冻结的核对范围；模型与界面都不能扩大它。"""

    ledger_revision: str
    inference_id: str
    inference_revision: str
    goal: str
    intent: str
    clue_ids: tuple[str, ...]
    clue_revisions: tuple[tuple[str, str], ...]
    premises: tuple[str, ...]
    alternatives: tuple[str, ...]
    design_links: tuple[tuple[str, str], ...]
    as_of_chapter: int
    target_paths: tuple[str, ...]
    target_digests: tuple[tuple[str, str], ...]
    directory: tuple[tuple[str, int, str], ...]

    @property
    def target_map(self) -> dict[str, str]:
        return dict(self.target_digests)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "ledger_revision": self.ledger_revision,
            "inference_id": self.inference_id,
            "inference_revision": self.inference_revision,
            "goal": self.goal,
            "intent": self.intent,
            "clue_ids": list(self.clue_ids),
            "clue_revisions": [[key, value] for key, value in self.clue_revisions],
            "premises": list(self.premises),
            "alternatives": list(self.alternatives),
            "design_links": [[key, value] for key, value in self.design_links],
            "as_of_chapter": self.as_of_chapter,
            "target_paths": list(self.target_paths),
            "target_digests": [[path, digest] for path, digest in self.target_digests],
            "directory": [[path, chapter, digest] for path, chapter, digest in self.directory],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ClueReviewScope:
        def pairs(value: object) -> tuple[tuple[str, str], ...]:
            rows: list[tuple[str, str]] = []
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, list) and len(item) == 2:
                        rows.append((str(item[0]), str(item[1])))
            return tuple(rows)

        directory: list[tuple[str, int, str]] = []
        raw_directory = raw.get("directory")
        if isinstance(raw_directory, list):
            for item in raw_directory:
                if isinstance(item, list) and len(item) == 3:
                    directory.append((str(item[0]), int(str(item[1])), str(item[2])))
        return cls(
            ledger_revision=str(raw.get("ledger_revision") or ""),
            inference_id=str(raw.get("inference_id") or ""),
            inference_revision=str(raw.get("inference_revision") or ""),
            goal=str(raw.get("goal") or ""),
            intent=str(raw.get("intent") or "undecided"),
            clue_ids=tuple(str(item) for item in cast(list[object], raw.get("clue_ids") or [])),
            clue_revisions=pairs(raw.get("clue_revisions")),
            premises=tuple(str(item) for item in cast(list[object], raw.get("premises") or [])),
            alternatives=tuple(
                str(item) for item in cast(list[object], raw.get("alternatives") or [])
            ),
            design_links=pairs(raw.get("design_links")),
            as_of_chapter=int(str(raw.get("as_of_chapter") or 0)),
            target_paths=tuple(
                str(item) for item in cast(list[object], raw.get("target_paths") or [])
            ),
            target_digests=pairs(raw.get("target_digests")),
            directory=tuple(directory),
        )


@dataclass(frozen=True, slots=True)
class ClueReviewFinding:
    kind: str
    certainty: str
    description: str
    suggestion: str
    evidence: tuple[ReviewEvidence, ...]
    clue_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind,
            "certainty": self.certainty,
            "description": self.description,
            "suggestion": self.suggestion,
            "clue_ids": list(self.clue_ids),
            "evidence": [cast(JsonValue, item.to_dict()) for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class ClueReviewDraft:
    status: str
    findings: tuple[ClueReviewFinding, ...] = ()
    uncovered: tuple[str, ...] = ()


class ClueReviewReportView(BaseModel):
    """按当前来源算出的报告视图；``freshness`` 不是保存时的状态。"""

    report_id: str
    turn_id: str = ""
    inference_id: str
    inference_revision: str = ""
    goal: str
    intent: str
    clue_ids: list[str] = Field(default_factory=list)
    as_of_chapter: int
    status: ReviewStatus
    findings: list[dict[str, object]] = Field(default_factory=list)
    uncovered: list[str] = Field(default_factory=list)
    coverage: list[dict[str, object]] = Field(default_factory=list)
    target_paths: list[str] = Field(default_factory=list)
    created_at: str = ""
    freshness: ReportFreshness = "valid"
    reasons: list[str] = Field(default_factory=list)
    later_chapters: int = 0
    truncated: bool = False


class ClueReviewSummaryView(BaseModel):
    report_id: str
    inference_id: str
    status: ReviewStatus
    freshness: ReportFreshness
    created_at: str = ""


# -- 冻结 -------------------------------------------------------------------


def freeze_clue_review(
    workspace: Workspace,
    store: ClueLedgerStore,
    *,
    inference_id: str,
    inference_revision: str,
    clue_ids: list[str] | tuple[str, ...],
    as_of_chapter: int,
    chapter_paths: list[str] | tuple[str, ...],
) -> ClueReviewScope:
    """核对前按真实账本与当前正文固定范围；任何一项不成立都不进入受理。"""
    snapshot = store.read()
    ledger = snapshot.ledger
    if ledger is None or inference_id not in ledger.inferences:
        raise _not_found("要核对的推理目标不存在")
    inference = ledger.inferences[inference_id]
    if inference.inference_revision != inference_revision:
        raise _revision_conflict("推理目标在读取后已经变化")
    if set(clue_ids) != set(inference.clue_ids):
        raise _scope_error(
            "本次核对的线索与已保存目标不一致",
            "从已保存目标重新发起核对；缩小范围请先编辑目标",
            {"saved": list(inference.clue_ids), "submitted": list(clue_ids)},
        )
    if as_of_chapter < 0:
        raise _scope_error("核对截止章不能小于零", "选择作者当前写作位置再核对")
    ordered = tuple(dict.fromkeys(str(path) for path in chapter_paths))
    if not ordered:
        raise _target_required("核对需要至少一章有正文的目标")
    if len(ordered) > CLUE_REVIEW_MAX_TARGETS:
        raise _scope_error(
            f"一次最多核对 {CLUE_REVIEW_MAX_TARGETS} 章",
            "把目标拆成几次核对，或先在目标里收敛线索",
            {"paths": list(ordered)},
        )
    catalog = {
        row.path: row.chapter_number
        for row in workspace.catalog().chapters
        if row.exists and row.chapter_number is not None
    }
    digests: list[tuple[str, str]] = []
    for path in ordered:
        if not is_managed_path(path) or managed_kind(path) != "chapter" or path not in catalog:
            raise _scope_error(
                f"核对目标不是本作品的正文文件：{path}",
                "只选择正文目录中的章节；设定与角色小传作为参考，不放进核对目标",
            )
        chapter = int(str(catalog[path]))
        if chapter > as_of_chapter:
            raise _scope_error(
                f"核对目标超出截止章：{path}",
                "把截止章改成目标所在章，或从目标里去掉这一章",
                {"path": path, "chapter": chapter, "as_of_chapter": as_of_chapter},
            )
        view = workspace.read(path)
        if not has_chapter_content(view.text):
            raise _scope_error(
                f"核对目标还没有正文：{path}",
                "先写这一章的正文，或改选有内容的章节",
                {"path": path},
            )
        digests.append((path, view.digest))
    directory: list[tuple[str, int, str]] = []
    for path, chapter in sorted(catalog.items(), key=lambda item: int(str(item[1]))):
        number = int(str(chapter))
        if number > as_of_chapter:
            continue
        try:
            directory.append((path, number, workspace.read(path).digest))
        except WorkspaceError:
            continue
    return ClueReviewScope(
        ledger_revision=snapshot.revision,
        inference_id=inference_id,
        inference_revision=inference.inference_revision,
        goal=inference.goal,
        intent=inference.intent,
        clue_ids=tuple(inference.clue_ids),
        clue_revisions=tuple(
            (clue_id, ledger.clues[clue_id].clue_revision) for clue_id in inference.clue_ids
        ),
        premises=tuple(item.text for item in inference.premises),
        alternatives=tuple(item.text for item in inference.alternatives),
        design_links=tuple(
            (link.link_id, _source_key(link))
            for link in ledger.design_links.values()
            if link.clue_id in set(inference.clue_ids)
        ),
        as_of_chapter=as_of_chapter,
        target_paths=ordered,
        target_digests=tuple(digests),
        directory=tuple(directory),
    )


# -- 发布 -------------------------------------------------------------------


def publish_clue_review(
    scope: ClueReviewScope,
    draft: ClueReviewDraft,
    *,
    requirement: str,
    workspace: Workspace,
    ledger: MaterialLedger,
    store: ClueLedgerStore,
    turn_id: str,
) -> ClueReviewReportView:
    """校验并发布报告；覆盖由真实读取记录算，模型不能自报已读全文。"""
    _validate(scope, draft, requirement=requirement, workspace=workspace, ledger=ledger)
    coverage = _coverage(scope, workspace=workspace, ledger=ledger)
    created_at = _now()

    def apply(row: ClueLedger) -> dict[str, JsonValue]:
        report_id = new_id("cr")
        row.reviews[report_id] = cast(
            dict[str, JsonValue],
            {
                "schema_version": 1,
                "report_id": report_id,
                "turn_id": turn_id,
                "scope": scope.to_dict(),
                "requirement": requirement.strip(),
                "status": draft.status,
                "findings": [item.to_dict() for item in draft.findings],
                "uncovered": list(draft.uncovered),
                "coverage": coverage,
                "created_at": created_at,
            },
        )
        return {"report_id": report_id}

    result = store.mutate(
        expected_revision=store.read().revision,
        request_key=f"{turn_id}:clue-review",
        kind="publish_clue_review",
        payload={
            "inference_id": scope.inference_id,
            "inference_revision": scope.inference_revision,
            "targets": list(scope.target_paths),
            "status": draft.status,
            "findings": [item.to_dict() for item in draft.findings],
            "uncovered": list(draft.uncovered),
        },
        apply=apply,
    )
    return clue_review_report(workspace, store, str(result["report_id"]))


def clue_review_report(
    workspace: Workspace,
    store: ClueLedgerStore,
    report_id: str,
    *,
    as_of_chapter: int | None = None,
) -> ClueReviewReportView:
    """按当前来源复核一份报告；越界的结论不投影给更早的章界。"""
    snapshot = store.read()
    ledger = snapshot.ledger
    raw = ledger.reviews.get(report_id) if ledger is not None else None
    if ledger is None or not isinstance(raw, dict):
        raise _not_found("要查看的核对报告不存在")
    scope_raw = raw.get("scope")
    if not isinstance(scope_raw, dict):
        raise _invalid("报告缺少冻结范围")
    scope = cast(dict[str, object], scope_raw)
    freshness, reasons = _freshness(scope, ledger, workspace)
    view = _view(raw, scope, freshness=freshness, reasons=reasons, workspace=workspace)
    view = _with_evidence_ids(view, workspace=workspace, snapshot=snapshot)
    if as_of_chapter is not None and as_of_chapter < view.as_of_chapter:
        view = _project(view, as_of_chapter=as_of_chapter, workspace=workspace)
    return view


def clue_review_summaries(
    workspace: Workspace,
    store: ClueLedgerStore,
    *,
    clue_ids: list[str] | tuple[str, ...],
    as_of_chapter: int,
) -> list[ClueReviewSummaryView]:
    """线索详情用的最近核对摘要；只含当前章界能看到的报告。"""
    ledger = store.read().ledger
    if ledger is None:
        return []
    wanted = set(clue_ids)
    rows: list[ClueReviewSummaryView] = []
    for raw in ledger.reviews.values():
        if not isinstance(raw, dict):
            continue
        scope = ClueReviewScope.from_dict(cast(dict[str, object], raw.get("scope") or {}))
        if not wanted & set(scope.clue_ids) or scope.as_of_chapter > as_of_chapter:
            continue
        freshness, _ = _freshness(scope.to_dict(), ledger, workspace)
        rows.append(
            ClueReviewSummaryView(
                report_id=str(raw.get("report_id") or ""),
                inference_id=scope.inference_id,
                status=cast(ReviewStatus, raw.get("status") or "incomplete"),
                freshness=freshness,
                created_at=str(raw.get("created_at") or ""),
            )
        )
    return sorted(rows, key=lambda item: item.created_at, reverse=True)


# -- 校验 -------------------------------------------------------------------


def _validate(
    scope: ClueReviewScope,
    draft: ClueReviewDraft,
    *,
    requirement: str,
    workspace: Workspace,
    ledger: MaterialLedger,
) -> None:
    if not requirement.strip():
        raise _invalid("没有记录作者的核对要求")
    if draft.status not in ("findings", "no_findings", "incomplete"):
        raise _invalid("报告状态只能是 findings、no_findings 或 incomplete")
    if draft.status == "findings" and not draft.findings:
        raise _invalid("状态是「发现问题」却没有列出任何问题")
    if draft.status == "no_findings" and draft.findings:
        raise _invalid("状态是「没有发现问题」却带了问题条目")
    if draft.status == "incomplete" and not draft.uncovered:
        raise _invalid("状态是「未完成」时要说明哪些部分没有覆盖")
    if len(draft.findings) > MAX_FINDINGS:
        raise _invalid(f"问题超过 {MAX_FINDINGS} 条，请先收敛范围")
    catalog = {
        row.path: row.chapter_number
        for row in workspace.catalog().chapters
        if row.chapter_number is not None
    }
    for finding in draft.findings:
        if finding.kind not in CLUE_FINDING_KINDS:
            raise _invalid(f"不支持的问题类别：{finding.kind}")
        if finding.certainty not in ("supported", "suspected"):
            raise _invalid(f"不支持的确定程度：{finding.certainty}")
        for field, text in (("问题描述", finding.description), ("修改建议", finding.suggestion)):
            if not text.strip():
                raise _invalid(f"{field}为空")
            if len(text) > DESCRIPTION_MAX_CHARS:
                raise _invalid(f"{field}超过 {DESCRIPTION_MAX_CHARS} 字符")
        if not finding.evidence:
            raise _invalid("每条问题至少要有一处原文证据")
        if len(finding.evidence) > 4:
            raise _invalid("单条问题最多引用 4 处证据")
        if not set(finding.clue_ids) <= set(scope.clue_ids):
            raise _invalid("问题引用了本次核对目标之外的线索")
        for evidence in finding.evidence:
            validate_evidence(evidence, workspace=workspace, ledger=ledger)
            chapter = catalog.get(evidence.path)
            if chapter is None:
                # 设定、计划与真相是作者侧信息；核对只以读者已见的正文为依据。
                raise _invalid(
                    f"核对依据只能是正文：{evidence.path} 不是正文目录中的章节文件"
                )
            if int(chapter) > scope.as_of_chapter:
                raise _invalid(f"证据所在章节超出台账截止章：{evidence.path}")
    if draft.status == "no_findings":
        require_full_coverage(scope.target_paths, workspace=workspace, ledger=ledger)
        # 作者指定为参考的材料若没读完，同样不能报告「没有发现问题」。
        ledger.require_references_complete(workspace)


def _coverage(
    scope: ClueReviewScope, *, workspace: Workspace, ledger: MaterialLedger
) -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []
    for path in scope.target_paths:
        expected = scope.target_map.get(path, "")
        try:
            view = workspace.read(path)
        except WorkspaceError:
            rows.append(
                {
                    "path": path,
                    "complete": False,
                    "read_chars": 0,
                    "total_chars": 0,
                    "note": "来源不可读",
                }
            )
            continue
        coverage = ledger.coverage(path)
        if coverage is None or coverage.ref.digest != expected or view.digest != expected:
            rows.append(
                {
                    "path": path,
                    "complete": False,
                    "read_chars": 0,
                    "total_chars": len(view.text),
                    "note": "本轮没有读取这个目标的当前版本",
                }
            )
            continue
        read = sum(end - start for start, end in coverage.spans)
        rows.append(
            {
                "path": path,
                "complete": coverage.complete or covers(coverage.spans, 0, len(view.text)),
                "read_chars": read,
                "total_chars": len(view.text),
                "note": "" if coverage.complete else "只提供了部分原文",
            }
        )
    return rows


def _freshness(
    scope_raw: Mapping[str, object], ledger: ClueLedger, workspace: Workspace
) -> tuple[ReportFreshness, list[str]]:
    scope = ClueReviewScope.from_dict(scope_raw)
    missing: list[str] = []
    stale: list[str] = []
    for path, digest in scope.target_digests:
        try:
            view = workspace.read(path)
        except WorkspaceError:
            missing.append(path)
            continue
        if view.digest != digest:
            stale.append(path)
    added, removed = _directory_diff(scope, workspace)
    reasons: list[str] = []
    if missing:
        reasons.append("正文文件已删除或不可读：" + "、".join(missing))
    if stale:
        reasons.append("正文已修改：" + "、".join(stale))
    if added:
        reasons.append("截止章内的正文清单新增：" + "、".join(added))
    if removed:
        reasons.append("截止章内的正文清单删除：" + "、".join(removed))
    inference = ledger.inferences.get(scope.inference_id)
    changed = inference is None or inference.inference_revision != scope.inference_revision
    changed = changed or any(
        ledger.clues.get(clue_id) is None
        or ledger.clues[clue_id].clue_revision != revision
        for clue_id, revision in scope.clue_revisions
    )
    if not changed:
        current_links = {
            (link.link_id, _source_key(link))
            for link in ledger.design_links.values()
            if link.clue_id in set(scope.clue_ids)
        }
        changed = current_links != set(scope.design_links)
    if changed:
        reasons.append("推理目标、线索或计划依据已变化，需要重新核对")
    if missing:
        return "source_missing", reasons
    if stale or added or removed:
        return "stale", reasons
    if changed:
        return "target_changed", reasons
    return "valid", reasons


def _directory_diff(
    scope: ClueReviewScope, workspace: Workspace
) -> tuple[list[str], list[str]]:
    """截止章内正文清单的增删；清单变了，旧报告的覆盖结论就不再成立。"""
    frozen = {path for path, _, _ in scope.directory}
    current = {
        row.path
        for row in workspace.catalog().chapters
        if row.exists
        and row.chapter_number is not None
        and int(row.chapter_number) <= scope.as_of_chapter
    }
    return sorted(current - frozen), sorted(frozen - current)


# -- 视图 -------------------------------------------------------------------


def _view(
    raw: Mapping[str, object],
    scope_raw: Mapping[str, object],
    *,
    freshness: ReportFreshness,
    reasons: list[str],
    workspace: Workspace,
) -> ClueReviewReportView:
    scope = ClueReviewScope.from_dict(scope_raw)
    later = _later_chapters(workspace, scope.as_of_chapter)
    findings = raw.get("findings")
    uncovered = raw.get("uncovered")
    coverage = raw.get("coverage")
    status = str(raw.get("status") or "incomplete")
    return ClueReviewReportView(
        report_id=str(raw.get("report_id") or ""),
        turn_id=str(raw.get("turn_id") or ""),
        inference_id=scope.inference_id,
        inference_revision=scope.inference_revision,
        goal=scope.goal,
        intent=scope.intent,
        clue_ids=list(scope.clue_ids),
        as_of_chapter=scope.as_of_chapter,
        status=cast(
            ReviewStatus,
            status if status in ("findings", "no_findings", "incomplete") else "incomplete",
        ),
        findings=[cast(dict[str, object], item) for item in findings if isinstance(item, dict)]
        if isinstance(findings, list)
        else [],
        uncovered=[str(item) for item in uncovered] if isinstance(uncovered, list) else [],
        coverage=[cast(dict[str, object], item) for item in coverage if isinstance(item, dict)]
        if isinstance(coverage, list)
        else [],
        target_paths=list(scope.target_paths),
        created_at=str(raw.get("created_at") or ""),
        freshness=freshness,
        reasons=reasons,
        later_chapters=later,
    )


def _with_evidence_ids(
    view: ClueReviewReportView, *, workspace: Workspace, snapshot: object
) -> ClueReviewReportView:
    """给每条依据补上可解析的定位标识；界面据此打开原文，不自己拼坐标。"""
    from hastings.workspace.clue_store import ClueSnapshot

    assert isinstance(snapshot, ClueSnapshot)
    findings: list[dict[str, object]] = []
    for finding_index, finding in enumerate(view.findings):
        rows = finding.get("evidence")
        if not isinstance(rows, list):
            findings.append(finding)
            continue
        enriched: list[object] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                enriched.append(row)
                continue
            try:
                saved = ClueEvidence.model_validate(row)
            except ValueError:
                enriched.append(row)
                continue
            enriched.append(
                {
                    **row,
                    "evidence_id": review_evidence_id(
                        workspace,
                        snapshot,
                        report_id=view.report_id,
                        finding=finding_index,
                        index=index,
                        evidence=saved,
                    ),
                }
            )
        findings.append({**finding, "evidence": enriched})
    return view.model_copy(update={"findings": findings})


def _project(
    view: ClueReviewReportView, *, as_of_chapter: int, workspace: Workspace
) -> ClueReviewReportView:
    """更早的章界只保留当时可见的目标与依据，不把后章结论先送到界面。"""
    catalog = {
        row.path: row.chapter_number
        for row in workspace.catalog().chapters
        if row.chapter_number is not None
    }
    paths = [path for path in view.target_paths if _visible(path, catalog, as_of_chapter)]
    findings: list[dict[str, object]] = []
    for item in view.findings:
        evidence = item.get("evidence")
        rows = (
            [cast(dict[str, object], value) for value in evidence if isinstance(value, dict)]
            if isinstance(evidence, list)
            else []
        )
        if rows and all(
            _visible(str(row.get("path") or ""), catalog, as_of_chapter) for row in rows
        ):
            findings.append({**item, "evidence": rows})
    coverage = [
        row
        for row in view.coverage
        if _visible(str(row.get("path") or ""), catalog, as_of_chapter)
    ]
    return view.model_copy(
        update={
            "target_paths": paths,
            "findings": findings,
            "coverage": coverage,
            "truncated": True,
        }
    )


def _visible(
    path: str, catalog: Mapping[str, int | None], as_of_chapter: int
) -> bool:
    chapter = catalog.get(path)
    return chapter is None or int(chapter) <= as_of_chapter


def _later_chapters(workspace: Workspace, as_of_chapter: int) -> int:
    return sum(
        1
        for row in workspace.catalog().chapters
        if row.chapter_number is not None and int(row.chapter_number) > as_of_chapter
    )


# -- 工具 -------------------------------------------------------------------


def _source_key(link: ClueDesignLink) -> str:
    source = link.source
    return source.digest if isinstance(source, FileSource) else source.content_digest


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _not_found(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="CLUE_REVIEW_NOT_FOUND",
        where="clues.review",
        why=why,
        fix="重新读取线索与推理目标后再核对",
    )


def _revision_conflict(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="CLUE_REVISION_CONFLICT",
        where="clues.review",
        why=why,
        fix="重新读取线索与目标后，用新的请求键发起核对",
    )


def _target_required(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="CLUE_REVIEW_TARGET_REQUIRED",
        where="clues.review",
        why=why,
        fix="选择至少一章已有正文的章节作为核对目标",
    )


def _scope_error(why: str, fix: str, details: dict[str, object] | None = None) -> WorkspaceError:
    return WorkspaceError(
        code="CLUE_SCOPE_INVALID",
        where="clues.review",
        why=why,
        fix=fix,
        details=details,
    )


def _invalid(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="REVIEW_EVIDENCE_INVALID",
        where="clues.review",
        why=why,
        fix="按实际读到的原文与冻结目标重新提交；没读完就报告为未完成",
    )
