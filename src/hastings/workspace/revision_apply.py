"""修订建议的实际写入：全书结构（1.2 修订学习 P4）。

分析只给建议；作者在面板里勾选后，服务端再核对一次基线，按补丁定位把结构改掉。
补丁必须能在分析时的结构里唯一定位，应用时再核对当前文件没有被别处改过。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from hastings.library.changes import ChangeEdit, ChangeOperation, ModuleChangeService
from hastings.library.modules import ModuleLibrary
from hastings.library.records import parse_entry
from hastings.workspace.atomic_io import locked
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import STRUCTURE_PATH, FileView, Workspace
from hastings.workspace.module_assembly import AssemblyStore
from hastings.workspace.revision_analysis import (
    AnalysisDecision,
    RevisionAnalysis,
    RevisionAnalysisStore,
)
from hastings.workspace.structure_io import read_structure_state, write_structure_document

__all__ = [
    "ModuleApplication",
    "StructureApplication",
    "apply_module_decisions",
    "apply_structure_decisions",
]


@dataclass(frozen=True, slots=True)
class StructureApplication:
    analysis: RevisionAnalysis
    view: FileView
    applied_indexes: list[int]


@dataclass(frozen=True, slots=True)
class ModuleApplication:
    analysis: RevisionAnalysis
    change_set_id: str
    assembly: dict[str, object]
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


def apply_module_decisions(
    store: RevisionAnalysisStore,
    workspace: Workspace,
    library: ModuleLibrary,
    *,
    analysis_id: str,
    decision_indexes: list[int],
    expected_digest: str | None = None,
) -> ModuleApplication:
    """把选中的模块条目建议写成当前作品的新版本，并切换本作品绑定。"""
    indexes = sorted({index for index in decision_indexes})
    if not indexes:
        raise _invalid(analysis_id, "没有选择任何模块建议")
    guard = store.path_of(analysis_id).with_suffix(".module.lock")
    with locked(guard):
        analysis = _require_ready(store, analysis_id)
        selected = _selected_modules(analysis, indexes)
        if all(decision.applied for _, decision in selected):
            return ModuleApplication(
                analysis=analysis,
                change_set_id="",
                assembly=AssemblyStore(workspace.root).read().view(),
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
        current_digest = AssemblyStore(workspace.root).digest()
        baseline = expected_digest or analysis.assembly_digest
        if baseline and current_digest != baseline:
            raise WorkspaceError(
                code="REVISION_APPLICATION_CONFLICT",
                where=f"修订分析/{analysis_id}",
                why="作品模块在分析之后被改过",
                fix="刷新作品模块后重新分析这次修订；不要用旧建议覆盖新版本",
                details={
                    "analysis_id": analysis_id,
                    "expected": baseline,
                    "current": current_digest,
                },
            )
        operations = _module_operations(workspace, library, analysis_id, selected)
        turn_id = f"revision:{analysis_id}"
        service = ModuleChangeService(library, workspace)
        prepared = service.prepare(
            turn_id=turn_id,
            operations=operations,
            base_digest=current_digest,
            request_key=(
                f"revision-module:{analysis_id}:" + "-".join(str(index) for index in indexes)
            ),
        )
        change_set_id = str(prepared["change_set_id"])
        result = service.apply(
            change_set_id=change_set_id,
            turn_id=turn_id,
            expected_digest=current_digest,
        )
        if result.get("applied") is not True:
            error = result.get("error")
            detail = error.get("why") if isinstance(error, dict) else "模块版本没有应用"
            raise WorkspaceError(
                code="REVISION_APPLICATION_CONFLICT",
                where=f"修订分析/{analysis_id}",
                why=str(detail or "模块版本没有应用"),
                fix="检查模块版本后重新分析；已保存的内容仍可继续使用",
                details={"analysis_id": analysis_id, "change_set_id": change_set_id},
            )
        decisions = [
            replace(decision, applied=True) if position in indexes else decision
            for position, decision in enumerate(analysis.decisions)
        ]
        updated = store.save(
            replace(
                analysis,
                decisions=decisions,
                assembly_digest=AssemblyStore(workspace.root).digest() or "",
                updated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        )
        raw_assembly = result.get("assembly")
        assembly_payload: dict[str, object] = (
            {str(key): value for key, value in raw_assembly.items()}
            if isinstance(raw_assembly, dict)
            else {}
        )
        return ModuleApplication(
            analysis=updated,
            change_set_id=change_set_id,
            assembly=assembly_payload,
            applied_indexes=indexes,
        )


def _selected_modules(
    analysis: RevisionAnalysis, indexes: list[int]
) -> list[tuple[int, AnalysisDecision]]:
    selected: list[tuple[int, AnalysisDecision]] = []
    for index in indexes:
        if index < 0 or index >= len(analysis.decisions):
            raise _invalid(analysis.id, f"没有第 {index + 1} 条建议")
        decision = analysis.decisions[index]
        if decision.target != "module_entry":
            raise _invalid(analysis.id, f"第 {index + 1} 条建议不是模块建议")
        if decision.patch_error:
            raise _invalid(
                analysis.id, f"第 {index + 1} 条建议无法直接应用：{decision.patch_error}"
            )
        if not decision.patch_find or not decision.patch_replace:
            raise _invalid(analysis.id, f"第 {index + 1} 条建议没有可直接应用的补丁")
        selected.append((index, decision))
    return selected


def _module_operations(
    workspace: Workspace,
    library: ModuleLibrary,
    analysis_id: str,
    selected: list[tuple[int, AnalysisDecision]],
) -> tuple[ChangeOperation, ...]:
    """按模块合并条目补丁；每个模块只提交一项更新，并核对当前绑定版本。"""
    assembly = AssemblyStore(workspace.root).read()
    grouped: dict[str, dict[str, object]] = {}
    for index, decision in selected:
        binding = next(
            (
                item
                for item in assembly.bindings
                if item.module.module_id == decision.module_id
                and item.module.revision_id == decision.revision_id
            ),
            None,
        )
        if binding is None:
            raise WorkspaceError(
                code="REVISION_APPLICATION_CONFLICT",
                where=f"修订分析/{analysis_id}",
                why=f"第 {index + 1} 条建议指向的模块版本已不在当前作品里",
                fix="刷新作品模块后重新分析这次修订",
                details={"analysis_id": analysis_id, "decision_index": index},
            )
        try:
            raw, digest = library.storage.read_entry(
                decision.module_id, decision.revision_id, decision.entry_id
            )
            draft = parse_entry(raw, module_kind=library.module(decision.module_id).kind)
        except WorkspaceError as exc:
            raise WorkspaceError(
                code="REVISION_APPLICATION_CONFLICT",
                where=f"修订分析/{analysis_id}",
                why=f"第 {index + 1} 条建议的条目已不可读：{exc.why}",
                fix="刷新作品模块后重新分析这次修订",
                details={"analysis_id": analysis_id, "decision_index": index},
            ) from exc
        if digest != decision.entry_digest:
            raise WorkspaceError(
                code="REVISION_APPLICATION_CONFLICT",
                where=f"修订分析/{analysis_id}",
                why=f"第 {index + 1} 条建议的条目内容在分析之后变过",
                fix="重新读取模块条目后再分析；旧补丁不会覆盖新内容",
                details={"analysis_id": analysis_id, "decision_index": index},
            )
        if draft.body.count(decision.patch_find) != 1:
            raise WorkspaceError(
                code="REVISION_PATCH_CONFLICT",
                where=f"修订分析/{analysis_id}",
                why=f"第 {index + 1} 条建议的定位文本已不能唯一匹配条目正文",
                fix="刷新模块后重新分析这次修订；保留的建议仍可对照",
                details={"analysis_id": analysis_id, "decision_index": index},
            )
        group = grouped.setdefault(
            decision.module_id,
            {
                "binding_id": binding.id,
                "module_id": decision.module_id,
                "base_revision": decision.revision_id,
                "entries": {},
            },
        )
        entries = group["entries"]
        assert isinstance(entries, dict)
        state = entries.get(decision.entry_id)
        if not isinstance(state, dict):
            state = {"base_digest": decision.entry_digest, "body": draft.body}
            entries[decision.entry_id] = state
        body = str(state["body"])
        if body.count(decision.patch_find) != 1:
            raise WorkspaceError(
                code="REVISION_PATCH_CONFLICT",
                where=f"修订分析/{analysis_id}",
                why=f"第 {index + 1} 条建议与同一条目的前一条建议重叠",
                fix="重新分析这次修订，或分开应用相互重叠的建议",
                details={"analysis_id": analysis_id, "decision_index": index},
            )
        state["body"] = body.replace(decision.patch_find, decision.patch_replace, 1)
    operations: list[ChangeOperation] = []
    for position, group in enumerate(grouped.values()):
        entries = group["entries"]
        assert isinstance(entries, dict)
        edits = tuple(
            ChangeEdit(
                entry_id=str(entry_id),
                base_digest=str(state["base_digest"]),
                body=str(state["body"]),
            )
            for entry_id, state in entries.items()
            if isinstance(state, dict)
        )
        operations.append(
            ChangeOperation(
                operation_id=f"rev{position}",
                action="update",
                binding_id=str(group["binding_id"]),
                module_id=str(group["module_id"]),
                base_revision=str(group["base_revision"]),
                edits=edits,
            )
        )
    return tuple(operations)
