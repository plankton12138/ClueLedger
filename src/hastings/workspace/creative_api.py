"""创作辅助与候选接口（1.0.9 P3）。

两条入口共享同一候选存储与模型选择：作品内入口读当前构思与正文，全局入口只读请求
明确给出的草稿。候选按标识读取；停止只取消本次生成，不删除已产生的部分文本。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path

from hastings.workspace.creative_assist import CreativeAssistInput, CreativeAssistService
from hastings.workspace.creative_candidates import CreativeCandidateStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.service_hub import WorkspaceServices
from hastings.workspace.state import RecentWorkspaces, conversation_root
from hastings.workspace.writing_api_models import CreativeAssistRequest

__all__ = ["create_creative_router"]

_GLOBAL_KINDS = frozenset({"new_workspace", "workspace_title", "idea", "module"})


def create_creative_router(
    workspaces: RecentWorkspaces, hub: WorkspaceServices | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["creative"])

    def provider_factory() -> Any:
        if hub is None:
            raise WorkspaceError(
                code="MODEL_UNAVAILABLE",
                where="creative-assist",
                why="当前没有接入模型服务",
                fix="先在设置中完成模型连接与选择",
            )
        try:
            return hub.provider_factory()
        except Exception as exc:
            raise WorkspaceError(
                code="MODEL_UNAVAILABLE",
                where="creative-assist",
                why=f"没有可用模型：{exc}",
                fix="先在设置中完成模型连接与选择",
            ) from exc

    def workspace_service(workspace_id: str) -> CreativeAssistService:
        workspaces.get(workspace_id)
        workspace = workspaces.workspace(workspace_id)
        state_root = conversation_root(workspaces.root, workspace_id)
        return CreativeAssistService(
            state_root=state_root,
            workspace=workspace,
            candidates=CreativeCandidateStore(state_root),
            provider_factory=provider_factory,
            workspace_id=workspace_id,
        )

    def global_service() -> CreativeAssistService:
        state_root = workspaces.root / "global-writing"
        return CreativeAssistService(
            state_root=state_root,
            workspace=None,
            candidates=CreativeCandidateStore(state_root),
            provider_factory=provider_factory,
            workspace_id="",
        )

    @router.post("/creative-assists", status_code=202)
    async def start_global_assist(body: CreativeAssistRequest) -> dict[str, Any]:
        if (
            body.target.kind not in _GLOBAL_KINDS
            or body.target.workspace_id
            or body.target.chapter_id
        ):
            raise WorkspaceError(
                code="ASSIST_TARGET_INVALID",
                where="creative-assist",
                why="全局入口只接受尚未绑定作品的命名草稿或全局模块",
                fix="选择作品内目标，或改为全局草稿后重试",
            )
        candidate = await global_service().start(
            CreativeAssistInput(
                kind=body.kind,
                target=body.target,
                base=body.base,
                selection=body.selection,
                author_note=body.author_note,
                draft_text=body.draft_text,
                request_key=body.request_key,
            )
        )
        return candidate.model_dump(mode="json")

    @router.get("/creative-assists/{candidate_id}")
    def read_global_candidate(
        candidate_id: str = Path(min_length=1, max_length=64),
    ) -> dict[str, Any]:
        service = global_service()
        return service.recover(service.candidates.read(candidate_id)).model_dump(mode="json")

    @router.post("/creative-assists/{candidate_id}/stop")
    async def stop_global_candidate(
        candidate_id: str = Path(min_length=1, max_length=64),
    ) -> dict[str, Any]:
        stopped = await global_service().stop(candidate_id)
        return stopped.model_dump(mode="json")

    @router.post("/file-workspaces/{workspace_id}/creative-assists", status_code=202)
    async def start_workspace_assist(
        workspace_id: str, body: CreativeAssistRequest
    ) -> dict[str, Any]:
        if body.target.kind == "new_workspace":
            raise WorkspaceError(
                code="ASSIST_TARGET_INVALID",
                where="creative-assist",
                why="新建作品尚未有作品标识，只能从全局入口请求",
                fix="改用 /api/creative-assists",
            )
        candidate = await workspace_service(workspace_id).start(
            CreativeAssistInput(
                kind=body.kind,
                target=body.target,
                base=body.base,
                selection=body.selection,
                author_note=body.author_note,
                draft_text=body.draft_text,
                request_key=body.request_key,
            )
        )
        return candidate.model_dump(mode="json")

    @router.get("/file-workspaces/{workspace_id}/creative-assists/{candidate_id}")
    def read_candidate(
        workspace_id: str,
        candidate_id: str = Path(min_length=1, max_length=64),
    ) -> dict[str, Any]:
        service = workspace_service(workspace_id)
        candidate = service.recover(service.candidates.read(candidate_id))
        if candidate.workspace_id not in (None, workspace_id):
            raise WorkspaceError(
                code="CANDIDATE_NOT_FOUND",
                where=f"candidate:{candidate_id}",
                why="候选不属于当前作品",
                fix="从当前作品重新请求模型候选",
            )
        return candidate.model_dump(mode="json")

    @router.post("/file-workspaces/{workspace_id}/creative-assists/{candidate_id}/stop")
    async def stop_candidate(
        workspace_id: str,
        candidate_id: str = Path(min_length=1, max_length=64),
    ) -> dict[str, Any]:
        service = workspace_service(workspace_id)
        candidate = service.recover(service.candidates.read(candidate_id))
        if candidate.workspace_id not in (None, workspace_id):
            raise WorkspaceError(
                code="CANDIDATE_NOT_FOUND",
                where=f"candidate:{candidate_id}",
                why="候选不属于当前作品",
                fix="从当前作品重新请求模型候选",
            )
        stopped = await service.stop(candidate_id)
        return stopped.model_dump(mode="json")

    return router
