"""对话里的线索引用：受理时冻结作者看到的版本，正文依据与作者计划分开。

引用本身不是正文依据：它只告诉服务端「作者指的是哪一条线索、哪个版本、看到哪一章」。
服务端按当前账本与截止章重建读者已见的部分，并把作者计划、推理目标和核对报告单独分区。
版本、章界或目标在受理后变化时，这一轮按冲突失败，不静默换成新版本。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from hastings.library.modules import ModuleLibrary
from hastings.workspace.clue_models import ClueReference
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clue_views import clue_detail
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace

__all__ = [
    "MAX_CLUE_REFERENCES",
    "ClueReferenceInput",
    "ClueReferenceSnapshot",
    "clue_references_section",
    "freeze_clue_references",
]

MAX_CLUE_REFERENCES = 10


@dataclass(frozen=True, slots=True)
class ClueReferenceInput:
    """客户端提交的引用：只给标识与看到的版本，不给正文。"""

    workspace_lineage: str
    clue_id: str
    clue_revision: str
    browse_as_of_chapter: int
    inference_id: str | None = None
    inference_revision: str | None = None
    report_id: str | None = None

    def identity(self) -> dict[str, object]:
        return {
            "workspace_lineage": self.workspace_lineage,
            "clue_id": self.clue_id,
            "clue_revision": self.clue_revision,
            "browse_as_of_chapter": self.browse_as_of_chapter,
            "inference_id": self.inference_id,
            "inference_revision": self.inference_revision,
            "report_id": self.report_id,
        }


@dataclass(frozen=True, slots=True)
class ClueReferenceSnapshot:
    """一份引用的冻结内容；``reader`` 才是正文依据，``plan`` 只说明安排。"""

    workspace_lineage: str
    clue_id: str
    clue_title: str
    clue_revision: str
    browse_as_of_chapter: int
    reader_cutoff: int
    reader: tuple[dict[str, object], ...]
    plan: tuple[dict[str, object], ...]
    inference: dict[str, object] | None
    report: dict[str, object] | None
    warnings: tuple[str, ...]

    def identity(self) -> dict[str, object]:
        inference = self.inference or {}
        report = self.report or {}
        return {
            "workspace_lineage": self.workspace_lineage,
            "clue_id": self.clue_id,
            "clue_revision": self.clue_revision,
            "browse_as_of_chapter": self.browse_as_of_chapter,
            "reader_cutoff": self.reader_cutoff,
            "inference_id": inference.get("inference_id"),
            "inference_revision": inference.get("inference_revision"),
            "report_id": report.get("report_id"),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.identity(),
            "clue_title": self.clue_title,
            "reader": [dict(item) for item in self.reader],
            "plan": [dict(item) for item in self.plan],
            "inference": dict(self.inference) if self.inference else None,
            "report": dict(self.report) if self.report else None,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> ClueReferenceSnapshot:
        def rows(value: object) -> tuple[dict[str, object], ...]:
            if not isinstance(value, list):
                return ()
            return tuple(cast(dict[str, object], item) for item in value if isinstance(item, dict))

        inference = raw.get("inference")
        report = raw.get("report")
        warnings = raw.get("warnings")
        return cls(
            workspace_lineage=str(raw.get("workspace_lineage") or ""),
            clue_id=str(raw.get("clue_id") or ""),
            clue_title=str(raw.get("clue_title") or ""),
            clue_revision=str(raw.get("clue_revision") or ""),
            browse_as_of_chapter=int(str(raw.get("browse_as_of_chapter") or 0)),
            reader_cutoff=int(str(raw.get("reader_cutoff") or 0)),
            reader=rows(raw.get("reader")),
            plan=rows(raw.get("plan")),
            inference=cast(dict[str, object], inference)
            if isinstance(inference, dict)
            else None,
            report=cast(dict[str, object], report) if isinstance(report, dict) else None,
            warnings=tuple(str(item) for item in warnings) if isinstance(warnings, list) else (),
        )


def freeze_clue_references(
    workspace: Workspace,
    root: Path | None,
    references: tuple[ClueReference, ...] | tuple[ClueReferenceInput, ...],
    *,
    writer_cutoff: int,
    library: ModuleLibrary | None = None,
) -> tuple[ClueReferenceSnapshot, ...]:
    """按当前账本核对每一份引用；任何一项不成立都不进入受理。"""
    if not references:
        return ()
    if root is None:
        raise _input_error("这一轮没有可核对线索引用的作品状态目录", "在作品会话里引用线索")
    if len(references) > MAX_CLUE_REFERENCES:
        raise _input_error(
            f"一次最多引用 {MAX_CLUE_REFERENCES} 条线索，这次有 {len(references)} 条",
            "减少引用，或在对话里说明要看哪几条",
        )
    store = ClueLedgerStore(root)
    snapshot = store.read()
    ledger = snapshot.ledger
    frozen: list[ClueReferenceSnapshot] = []
    seen: set[tuple[str, str]] = set()
    for reference in references:
        clue_id = reference.clue_id
        revision = reference.clue_revision
        if (clue_id, revision) in seen:
            continue
        seen.add((clue_id, revision))
        if ledger is None or clue_id not in ledger.clues:
            raise WorkspaceError(
                code="CLUE_NOT_FOUND",
                where="clues.reference",
                why=f"引用的线索当前不存在：{clue_id}",
                fix="刷新线索详情后重新引用",
                details={"clue_id": clue_id},
            )
        if reference.workspace_lineage != ledger.workspace_lineage:
            raise WorkspaceError(
                code="CLUE_ACCESS_DENIED",
                where="clues.reference",
                why="引用的线索不属于当前作品",
                fix="切回原作品，或从当前作品重新选择线索",
                details={"clue_id": clue_id},
            )
        identity = ledger.clues[clue_id]
        if identity.clue_revision != revision:
            raise WorkspaceError(
                code="CLUE_REFERENCE_STALE_CONFLICT",
                where="clues.reference",
                why=f"线索在读取后已经变化：{identity.title}",
                fix="保留草稿并重新读取线索，确认后继续发送",
                details={
                    "clue_id": clue_id,
                    "expected": revision,
                    "current": identity.clue_revision,
                },
            )
        cutoff = max(0, min(reference.browse_as_of_chapter, writer_cutoff))
        detail = clue_detail(workspace, root, clue_id, as_of_chapter=cutoff, library=library)
        reader: list[dict[str, object]] = []
        for observation in detail.observations:
            for basis_id in observation.evidence_ids:
                basis = next(
                    (item for item in detail.evidence if item.evidence_id == basis_id), None
                )
                if basis is None:
                    continue
                reader.append(
                    {
                        "observation_id": observation.observation_id,
                        "chapter": observation.chapter,
                        "kind": observation.kind,
                        "mode": observation.mode,
                        "text": observation.text,
                        "quote": basis.quote,
                        "path": basis.path,
                        "freshness": basis.freshness,
                        "usable": basis.freshness == "valid",
                    }
                )
        plan_rows: list[dict[str, object]] = [
            {
                "link_id": design.link_id,
                "relation": design.relation,
                "quote": design.quote,
                "label": design.label,
                "freshness": design.freshness,
            }
            for design in detail.author_designs
        ]
        plan = tuple(plan_rows)
        warnings = list(detail.warnings)
        stale = [row for row in reader if not row["usable"]]
        if stale:
            warnings.append("部分正文依据的来源已变化，只能作为旧引文，不能证明当前内容。")
        inference: dict[str, object] | None = None
        report: dict[str, object] | None = None
        if reference.inference_id:
            view = next(
                (item for item in detail.inferences if item.inference_id == reference.inference_id),
                None,
            )
            if view is None:
                raise WorkspaceError(
                    code="CLUE_NOT_FOUND",
                    where="clues.reference",
                    why=f"引用的推理目标不属于这条线索：{reference.inference_id}",
                    fix="重新读取线索详情后选择目标",
                    details={"clue_id": clue_id, "inference_id": reference.inference_id},
                )
            if (
                reference.inference_revision
                and view.inference_revision != reference.inference_revision
            ):
                raise WorkspaceError(
                    code="CLUE_REFERENCE_STALE_CONFLICT",
                    where="clues.reference",
                    why="推理目标在读取后已经变化",
                    fix="保留草稿并重新读取目标，确认后继续发送",
                    details={
                        "inference_id": view.inference_id,
                        "expected": reference.inference_revision,
                        "current": view.inference_revision,
                    },
                )
            inference = {
                "inference_id": view.inference_id,
                "inference_revision": view.inference_revision,
                "goal": view.goal,
                "intent": view.intent,
                "premises": [item.text for item in view.premises],
                "alternatives": [item.text for item in view.alternatives],
            }
            if view.warnings:
                warnings.extend(view.warnings)
        if reference.report_id:
            from hastings.workspace.clue_reviews import clue_review_report

            try:
                reviewed = clue_review_report(
                    workspace, store, reference.report_id, as_of_chapter=cutoff
                )
            except WorkspaceError as exc:
                raise WorkspaceError(
                    code="CLUE_REFERENCE_STALE_CONFLICT",
                    where="clues.reference",
                    why=f"引用的核对报告当前不可用：{reference.report_id}",
                    fix="保留草稿并重新读取报告，或去掉这条报告引用",
                    details={"report_id": reference.report_id},
                ) from exc
            if inference is None or reviewed.inference_id != inference["inference_id"]:
                raise WorkspaceError(
                    code="CLUE_REFERENCE_STALE_CONFLICT",
                    where="clues.reference",
                    why="核对报告与引用的推理目标不一致",
                    fix="重新读取线索与报告后选择一致的引用",
                    details={"report_id": reference.report_id},
                )
            report = {
                "report_id": reviewed.report_id,
                "status": reviewed.status,
                "freshness": reviewed.freshness,
                "uncovered": list(reviewed.uncovered),
                "as_of_chapter": reviewed.as_of_chapter,
            }
            if reviewed.freshness != "valid":
                warnings.append("引用的核对报告需要复核，报告内容不是当前正文的依据。")
        frozen.append(
            ClueReferenceSnapshot(
                workspace_lineage=str(reference.workspace_lineage),
                clue_id=clue_id,
                clue_title=identity.title,
                clue_revision=revision,
                browse_as_of_chapter=max(0, reference.browse_as_of_chapter),
                reader_cutoff=cutoff,
                reader=tuple(reader),
                plan=plan,
                inference=inference,
                report=report,
                warnings=tuple(dict.fromkeys(warnings)),
            )
        )
    return tuple(frozen)


def clue_references_section(clues: tuple[ClueReferenceSnapshot, ...]) -> str:
    """系统提示里的线索引用区：正文依据、作者计划与核对结果分开说明。"""
    if not clues:
        return ""
    lines = ["- 本轮线索引用（引用只表示作者选中的身份与版本，不是原文依据）："]
    for item in clues:
        lines.append(
            f"  - 「{item.clue_title}」`{item.clue_id}`｜第 {item.reader_cutoff} 章截止"
            f"｜版本 `{item.clue_revision}`"
        )
        usable = [row for row in item.reader if row.get("usable")]
        stale = [row for row in item.reader if not row.get("usable")]
        if usable:
            lines.append("    - 读者已见（只来自这些正文引文）：")
            for row in usable:
                lines.append(
                    f"      - 第 {row.get('chapter')} 章 · {row.get('kind')}"
                    f"：「{row.get('quote')}」（`{row.get('path')}`）"
                )
        else:
            lines.append("    - 读者已见：当前截止章内没有可用的正文引文。")
        if stale:
            lines.append("    - 来源已变化的旧引文（只能作为历史，不能证明当前内容）：")
            for row in stale:
                lines.append(f"      - 「{row.get('quote')}」（`{row.get('path')}`）")
        if item.plan:
            lines.append("    - 作者计划（说明作者打算怎么写，不是读者已见）：")
            for row in item.plan:
                label = str(row.get("label") or "")
                relation = str(row.get("relation") or "")
                suffix = f"，关系：{relation}" if relation else ""
                lines.append(f"      - {label}：「{row.get('quote')}」{suffix}")
        if item.inference:
            intent = str(item.inference.get("intent") or "")
            lines.append(
                f"    - 作者推理目标（计划判断，不是正文事实）：{item.inference.get('goal')}"
                f"（{intent}）"
            )
            premises = item.inference.get("premises")
            if isinstance(premises, list) and premises:
                lines.append("      - 必要前提：" + "；".join(str(value) for value in premises))
            alternatives = item.inference.get("alternatives")
            if isinstance(alternatives, list) and alternatives:
                lines.append(
                    "      - 其他解释：" + "；".join(str(value) for value in alternatives)
                )
        if item.report:
            lines.append(
                f"    - 已有核对报告（只说明上次结论，不是原文依据）："
                f"{item.report.get('status')}，来源 {item.report.get('freshness')}"
            )
            uncovered = item.report.get("uncovered")
            if isinstance(uncovered, list) and uncovered:
                lines.append("      - 未覆盖：" + "；".join(str(value) for value in uncovered))
        for warning in item.warnings:
            lines.append(f"    - 注意：{warning}")
    lines.append(
        "  引用不授予任何写入权限；作者计划、推理目标与旧报告都不能替代当前正文依据。"
    )
    return "\n".join(lines)


def _input_error(why: str, fix: str) -> WorkspaceError:
    return WorkspaceError(
        code="CLUE_REFERENCE_INVALID",
        where="clues.reference",
        why=why,
        fix=fix,
    )
