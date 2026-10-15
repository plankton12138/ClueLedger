"""V1 → V2 迁移 REST 面（T17.5）：状态、dry-run、确认落盘。

迁移流程（plan §4）在此成为产品流：扫描 → dry-run 报告 → 用户确认 run_id →
导入新 V2 项目 → 落盘映射（``architecture_version: v2`` + ``v2_project_id``）。
旧工作区自始至终只读；映射写盘后旧写路径门（REST 409 / 工具拒绝桩 / CLI 拒绝）
即刻生效——「迁移完成后禁止再走 V1 写路径」由既有 gating 承担，本模块不另设门。

端点挂在 slug 命名空间（``/api/{slug}/v2-migration/*``）：迁移的对象是 V1 工作区，
身份体系仍是 slug；产物（V2 项目）走 ``/api/v2`` 的 project_id 体系。两套身份
以 settings.yaml 的映射键衔接，不再引入第三套映射。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hastings.application.errors import ViolationError
from hastings.bible.store import StoryWorkspace
from hastings.domain.repository.ids import Actor
from hastings.interfaces.importers.v1_workspace_importer import V1WorkspaceImporter
from hastings.server.registry import WorkspaceRegistry
from hastings.server.v2 import V2Runtime
from hastings.settings import (
    record_v2_migration,
    v2_db_path,
    v2_project_id,
    workspace_architecture_version,
)

__all__ = ["create_migration_router"]

_ACTOR = Actor(kind="human", id="migration")


class MigrationApplyRequest(BaseModel):
    """确认落盘请求：``confirm_run_id`` 必须来自一次 dry-run 的报告。"""

    confirm_run_id: str


def _workspace_or_404(registry: WorkspaceRegistry, slug: str) -> StoryWorkspace:
    workspace = StoryWorkspace(root=registry.root, slug=slug)  # 构造即校验 slug
    if not workspace.story_dir.exists():
        raise HTTPException(status_code=404, detail=f"工作区不存在：{slug}")
    return workspace


def create_migration_router(registry: WorkspaceRegistry, runtime: V2Runtime) -> APIRouter:
    router = APIRouter(tags=["v2-migration"])
    importer = V1WorkspaceImporter(runtime.service, runtime.service, actor=_ACTOR)

    @router.get("/api/{slug}/v2-migration/status")
    def status(slug: str) -> dict[str, Any]:
        workspace = _workspace_or_404(registry, slug)
        project_id = v2_project_id(workspace)
        return {
            "slug": slug,
            "architecture_version": workspace_architecture_version(workspace),
            "v2_project_id": project_id,
            "migrated": project_id is not None,
        }

    @router.post("/api/{slug}/v2-migration/dry-run")
    def dry_run(slug: str) -> dict[str, Any]:
        """扫描工作区并产出迁移报告；不写 Repository、不改源文件。"""
        workspace = _workspace_or_404(registry, slug)
        _refuse_if_migrated(workspace, slug)
        report = importer.dry_run(workspace)
        return _report_payload(report)

    @router.post("/api/{slug}/v2-migration/apply")
    def apply(slug: str, req: MigrationApplyRequest) -> dict[str, Any]:
        """确认落盘：导入新 V2 项目 + 写映射；旧工作区保持只读原样。"""
        workspace = _workspace_or_404(registry, slug)
        _refuse_if_migrated(workspace, slug)
        project = runtime.create_project(slug)
        try:
            result = importer.apply(
                workspace, project_id=project, confirm_run_id=req.confirm_run_id
            )
        except ViolationError:
            # 导入未落成不写映射：工作区保持 V1 可用（迁移失败不切换 active）。
            raise
        record_v2_migration(workspace, project.value, db_path=runtime.db_path)
        return {
            "slug": slug,
            "project_id": project.value,
            "status": result.status,
            "commit_id": result.commit_id,
            "new_head": result.new_head,
            "replayed": result.replayed,
            "validation_ok": result.validation.ok,
            "validation_failures": [asdict(e) for e in result.validation.failures],
            "report": _report_payload(result.report),
        }

    def _refuse_if_migrated(workspace: StoryWorkspace, slug: str) -> None:
        existing = v2_project_id(workspace)
        if existing is not None:
            raise ViolationError(
                code="MIGRATION_ALREADY_DONE",
                where=f"v2.migration({slug})",
                why=f"工作区已迁移到 V2 项目 {existing}，重复迁移会制造分叉副本",
                fix="直接使用已有 V2 项目（GET /api/{slug}/v2-migration/status 可查）；"
                "如需重新迁移，新建空项目后手工导入",
                details={"v2_project_id": existing},
            )

    return router


def _report_payload(report: Any) -> dict[str, Any]:
    """迁移报告 → JSON（dataclass 树整体展开，前端拿全量证据）。"""
    payload = asdict(report)
    payload["ok"] = report.ok
    return payload


def v2_migration_db_path(workspace: StoryWorkspace) -> str | None:
    """便捷读取：迁移映射中的数据库路径（CLI 装配 V2 读取端用）。"""
    return v2_db_path(workspace)
