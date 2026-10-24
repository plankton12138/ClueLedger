"""故事线计划叠加（0.8 E）。

时间线与剧情安排模块里的条目是作者计划，不是正文观察。这里把它们只读地叠到故事资料页，
按模块筛选；不写入章节索引，也不把计划当成已经发生的事实。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hastings.library.modules import ModuleLibrary
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import Assembly, AssemblyStore

__all__ = ["PLAN_KINDS", "planned_overlay"]

PLAN_KINDS = frozenset({"timeline", "plot"})
MAX_PLANS = 200


def planned_overlay(workspace: Workspace, library: ModuleLibrary | None) -> dict[str, object]:
    """读取当前装配里的计划条目；没有库或没有计划时返回空列表。"""
    assembly = AssemblyStore(Path(workspace.root)).read()
    return planned_overlay_for(assembly, library)


def planned_overlay_for(assembly: Assembly, library: ModuleLibrary | None) -> dict[str, object]:
    plans: list[dict[str, object]] = []
    modules: list[dict[str, object]] = []
    warnings: list[str] = []
    if library is None:
        return {"plans": plans, "plan_modules": modules, "warnings": warnings}
    for binding in assembly.bindings:
        if len(plans) >= MAX_PLANS:
            warnings.append(f"计划超过 {MAX_PLANS} 条，只显示前面的部分。")
            break
        try:
            revision = library.revision(
                module_id=binding.module.module_id,
                revision_id=binding.module.revision_id,
            )
        except WorkspaceError as exc:
            warnings.append(
                f"计划模块「{binding.label or binding.module.module_id}」不可用：{exc.why}"
            )
            continue
        if revision.module_kind not in PLAN_KINDS:
            continue
        modules.append(
            {
                "binding_id": binding.id,
                "module_id": revision.module_id,
                "revision_id": revision.revision_id,
                "kind": revision.module_kind,
                "name": revision.module_name or binding.label,
            }
        )
        for entry in revision.entries:
            if len(plans) >= MAX_PLANS:
                warnings.append(f"计划超过 {MAX_PLANS} 条，只显示前面的部分。")
                break
            plans.append(_plan_row(binding.id, revision, entry))

    plans.sort(
        key=lambda item: (
            int(str(item.get("planned_chapter") or 0)),
            str(item.get("module_name") or ""),
            str(item.get("entry_id") or ""),
        )
    )
    return {"plans": plans, "plan_modules": modules, "warnings": warnings}


def _plan_row(binding_id: str, revision: Any, entry: Any) -> dict[str, object]:
    fields: Mapping[str, Any] = entry.fields if isinstance(entry.fields, Mapping) else {}
    planned = fields.get("planned_chapter")
    try:
        planned_chapter = int(str(planned)) if planned not in (None, "") else None
    except (TypeError, ValueError):
        planned_chapter = None
    body = getattr(entry, "body", "") or ""
    return {
        "binding_id": binding_id,
        "module_id": revision.module_id,
        "revision_id": revision.revision_id,
        "module_kind": revision.module_kind,
        "module_name": revision.module_name,
        "entry_id": entry.entry_id,
        "title": entry.title,
        "story_time": str(fields.get("story_time") or ""),
        "planned_chapter": planned_chapter,
        "decision": str(fields.get("decision") or ""),
        "excerpt": body.strip()[:160],
    }
