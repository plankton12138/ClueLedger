"""修订建议的实际写入：全书结构（1.2 修订学习 P4）。

分析只给建议；作者在面板里勾选后，服务端再核对一次基线，按补丁定位把结构改掉。
补丁必须能在分析时的结构里唯一定位，应用时再核对当前文件没有被别处改过。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from hastings.workspace.atomic_io import locked
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import STRUCTURE_PATH, FileView, Workspace
from hastings.workspace.revision_analysis import (
    AnalysisDecision,
    RevisionAnalysis,
    RevisionAnalysisStore,
)
from hastings.workspace.structure_io import read_structure_state, write_structure_document

__all__ = ["StructureApplication", "apply_structure_decisions"]


@dataclass(frozen=True, slots=True)
class StructureApplication:
    analysis: RevisionAnalysis
    view: FileView
    applied_indexes: list[int]


def apply_structure_decisions(
    store: RevisionAnalysisStore,
    workspace: Workspace,
    *,
    analysis_id: str,
    decision_indexes: list[int],
    expected_digest: str | None = None,
) -> StructureApplication:
    """把选中的结构建议写入 ``STRUCTURE.md``；同一份建议重复提交返回当前结果。"""
    indexes = sorted({index for index in decision_indexes})
    if not indexes:
        raise _invalid(analysis_id, "没有选择任何结构建议")
    guard = store.path_of(analysis_id).with_suffix(".apply.lock")
    with locked(guard):
        analysis = _require_ready(store, analysis_id)
        selected = _selected_structure(analysis, indexes)
        if all(decision.applied for _, decision in selected):
            return StructureApplication(
                analysis=analysis,
                view=workspace.read(STRUCTURE_PATH),
                applied_indexes=indexes,
            )
        if any(decision.applied for _, decision in selected):
            raise WorkspaceError(
                code="REVISION_APPLICATION_CONFLICT",
                where=f"修订分析/{analysis_id}",
                why="上一次应用只完成了其中一部分",
                fix="刷新后重新分析这次修订，再选择剩下的建议",
                details={"analysis_id": analysis_id, "decision_indexes": indexes},
            )
        state = read_structure_state(workspace)
        if not state.exists:
            raise WorkspaceError(
                code="REVISION_APPLICATION_INVALID",
                where=f"修订分析/{analysis_id}",
                why="作品还没有全书结构，无法应用结构建议",
                fix="先在对话中建立全书结构，再重新分析",
                details={"analysis_id": analysis_id},
            )
        baseline = expected_digest or analysis.structure_digest
        if baseline and state.digest != baseline:
            raise WorkspaceError(
                code="REVISION_APPLICATION_CONFLICT",
                where=f"修订分析/{analysis_id}",
                why="全书结构在分析之后被改过",
                fix="刷新结构后重新分析这次修订；不要用旧建议覆盖新结构",
                details={
                    "analysis_id": analysis_id,
                    "expected": baseline,
                    "current": state.digest,
                },
            )
        text = state.text
        applied: list[int] = []
        for index, decision in selected:
            if text.count(decision.patch_find) != 1:
                raise WorkspaceError(
                    code="REVISION_PATCH_CONFLICT",
                    where=f"修订分析/{analysis_id}",
                    why=f"第 {index + 1} 条建议的定位文本已不能唯一匹配当前结构",
                    fix="刷新结构后重新分析这次修订；保留的建议仍可对照",
                    details={"analysis_id": analysis_id, "decision_index": index},
                )
            text = text.replace(decision.patch_find, decision.patch_replace, 1)
            applied.append(index)
        view = write_structure_document(
            workspace,
            text=text,
            expected_digest=state.digest,
            creating=False,
        )
        decisions = [
            replace(decision, applied=True) if position in applied else decision
            for position, decision in enumerate(analysis.decisions)
        ]
        updated = store.save(
            replace(
                analysis,
                decisions=decisions,
                structure_digest=view.digest,
                updated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        )
        return StructureApplication(analysis=updated, view=view, applied_indexes=applied)


def _require_ready(store: RevisionAnalysisStore, analysis_id: str) -> RevisionAnalysis:
    analysis = store.get(analysis_id)
    if analysis is None:
        raise WorkspaceError(
            code="REVISION_ANALYSIS_NOT_FOUND",
            where=f"修订分析/{analysis_id}",
            why="没有这次分析记录",
            fix="刷新对话后重新发起分析",
            details={"analysis_id": analysis_id},
        )
    if analysis.status != "ready":
        raise WorkspaceError(
            code="REVISION_APPLICATION_INVALID",
            where=f"修订分析/{analysis_id}",
            why="这次分析还没有完成",
            fix="等分析完成后再应用建议",
            details={"analysis_id": analysis_id, "status": analysis.status},
        )
    return analysis


def _selected_structure(
    analysis: RevisionAnalysis, indexes: list[int]
) -> list[tuple[int, AnalysisDecision]]:
    selected: list[tuple[int, AnalysisDecision]] = []
    for index in indexes:
        if index < 0 or index >= len(analysis.decisions):
            raise _invalid(analysis.id, f"没有第 {index + 1} 条建议")
        decision = analysis.decisions[index]
        if decision.target != "structure":
            raise _invalid(analysis.id, f"第 {index + 1} 条建议不是结构建议")
        if decision.patch_error:
            raise _invalid(
                analysis.id, f"第 {index + 1} 条建议无法直接应用：{decision.patch_error}"
            )
        if not decision.patch_find or not decision.patch_replace:
            raise _invalid(analysis.id, f"第 {index + 1} 条建议没有可直接应用的补丁")
        selected.append((index, decision))
    return selected


def _invalid(analysis_id: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="REVISION_APPLICATION_INVALID",
        where=f"修订分析/{analysis_id}",
        why=why,
        fix="在面板里重新选择可应用的结构建议",
        details={"analysis_id": analysis_id},
    )
