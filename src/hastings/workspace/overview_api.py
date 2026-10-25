"""故事总览 HTTP 契约；全部读取均不调用模型。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, cast

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from hastings.library.modules import ModuleLibrary
from hastings.workspace.module_facets import overview_error
from hastings.workspace.overview_models import StartUpdate
from hastings.workspace.service import TurnService
from hastings.workspace.state import RecentWorkspaces, conversation_root
from hastings.workspace.story_overview import StoryOverview


def create_overview_router(
    workspaces: RecentWorkspaces,
    library: ModuleLibrary | None,
    service_for: Callable[[str], TurnService],
) -> APIRouter:
    router = APIRouter()

    def overview(
        workspace_id: str, as_of_chapter: int | None, generation: str | None = None
    ) -> StoryOverview:
        result = StoryOverview(
            workspaces.workspace(workspace_id),
            conversation_root(workspaces.root, workspace_id),
            library,
            as_of_chapter,
        )
        result.require_generation(generation)
        return result

    @router.get("/{workspace_id}/story-overview")
    def manifest(
        workspace_id: str,
        as_of_chapter: int | None = Query(None, ge=0),
        chapter_cursor: str | None = None,
        binding_cursor: str | None = None,
        limit: int = Query(40, ge=1, le=100),
    ) -> dict[str, Any]:
        result = overview(workspace_id, as_of_chapter)
        view = result.manifest(chapter_cursor, binding_cursor, limit)
        service = service_for(workspace_id)
        active = service.active_turn()
        view["active_update"] = None
        if active:
            events = service.store.events(active.conversation_id)
            if any(
                e.get("turn_id") == active.turn_id and e.get("purpose") == "organize_overview"
                for e in events
            ):
                view["active_update"] = {
                    "turn_id": active.turn_id,
                    "conversation_id": active.conversation_id,
                }
        return view

    @router.get("/{workspace_id}/story-overview/events")
    def events(
        workspace_id: str,
        generation: str,
        order: Literal["chapter", "story"] = "chapter",
        as_of_chapter: int | None = Query(None, ge=0),
        cursor: str | None = None,
        event_id: str | None = None,
        limit: int = Query(40, ge=1, le=100),
    ) -> dict[str, Any]:
        return overview(workspace_id, as_of_chapter, generation).events(
            order, cursor, limit, event_id
        )

    @router.get("/{workspace_id}/story-overview/bindings/{binding_id}")
    def binding(
        workspace_id: str,
        binding_id: str,
        generation: str,
        view: Literal["content", "characters", "clues"] = "content",
        as_of_chapter: int | None = Query(None, ge=0),
        cursor: str | None = None,
        limit: int = Query(40, ge=1, le=100),
    ) -> dict[str, Any]:
        return overview(workspace_id, as_of_chapter, generation).binding_view(
            binding_id, view, cursor, limit
        )

    @router.get("/{workspace_id}/story-overview/bindings/{binding_id}/facets/{facet_id}")
    def detail(
        workspace_id: str,
        binding_id: str,
        facet_id: str,
        generation: str,
        as_of_chapter: int | None = Query(None, ge=0),
        cursor: str | None = None,
        limit: int = Query(40, ge=1, le=100),
    ) -> dict[str, Any]:
        return overview(workspace_id, as_of_chapter, generation).detail(
            binding_id, facet_id, cursor, limit
        )

    @router.get("/{workspace_id}/story-overview/relations")
    def relations(
        workspace_id: str,
        binding_id: str,
        facet_id: str,
        generation: str,
        as_of_chapter: int | None = Query(None, ge=0),
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return overview(workspace_id, as_of_chapter, generation).relations(
            binding_id, facet_id, cursor
        )

    @router.post("/{workspace_id}/story-overview/updates")
    async def update(workspace_id: str, body: StartUpdate) -> JSONResponse:
        service = service_for(workspace_id)
        # 重放先进入统一受理的身份比较，不拿新清单代数覆盖旧请求。
        from hastings.workspace.module_facets import OverviewStore

        receipts = OverviewStore(service.store.root)
        receipt = receipts.command(body.request_key)
        if receipt:
            if receipt["request"] != body.model_dump():
                raise overview_error("REQUEST_KEY_CONFLICT", "请求键已经用于另一份总览命令")
            return JSONResponse(receipt["response"])
        previous = service.store.find_accepted(body.request_key)
        if previous is None:
            result = overview(workspace_id, body.as_of_chapter, body.observed_generation)
            if all(result.complete(t) for t in result.targets):
                async with service._admission:
                    receipt = receipts.command(body.request_key)
                    if receipt and receipt["request"] != body.model_dump():
                        raise overview_error("REQUEST_KEY_CONFLICT", "请求键已经用于另一份总览命令")
                    result = overview(workspace_id, body.as_of_chapter, body.observed_generation)
                    if all(result.complete(t) for t in result.targets):
                        response = {"state": "noop", "generation": result.generation}
                        receipts.record_noop(body.model_dump(), response)
                        return JSONResponse(response)
        info = await service.accept(
            "更新故事总览",
            body.request_key,
            purpose="organize_overview",
            material_scope={"domain": "overview", "request": body.model_dump()},
        )
        scope = info.material_scope or (previous.material_scope if previous else {}) or {}
        return JSONResponse(
            {
                "state": "accepted",
                "turn_id": info.turn_id,
                "conversation_id": info.conversation_id,
                "generation": body.observed_generation,
                "target_count": len(cast(list[Any], scope.get("targets", []))),
            },
            status_code=202,
        )

    @router.get("/{workspace_id}/story-overview/updates/{turn_id}")
    def progress(workspace_id: str, turn_id: str) -> dict[str, Any]:
        service = service_for(workspace_id)
        for conversation in service.store.conversations():
            events = [
                e for e in service.store.events(conversation.id) if e.get("turn_id") == turn_id
            ]
            started = next(
                (
                    e
                    for e in events
                    if e.get("type") == "turn_started" and e.get("purpose") == "organize_overview"
                ),
                None,
            )
            if started is None:
                continue
            scope = started.get("material_scope") or {}
            ids = [t["id"] for t in scope.get("targets", [])]
            done = {
                e["target_id"]
                for e in events
                if e.get("type") == "overview_target_published"
                and e.get("outcome") != "insufficient"
            }
            current = overview(workspace_id, scope.get("as_of_chapter"))
            current_targets = {t["id"]: t for t in current.targets}
            frozen_targets = {t["id"]: t for t in scope.get("targets", [])}
            done = {
                target_id
                for target_id in done
                if target_id in current_targets
                and current.complete(current_targets[target_id])
                and current.assembly_digest == scope.get("assembly_digest")
                and current_targets[target_id].get("digest")
                == frozen_targets[target_id].get("digest")
            }
            terminal = next((e for e in reversed(events) if e.get("type") == "run_finished"), None)
            status = terminal["status"] if terminal else "running"
            remaining = [target_id for target_id in ids if target_id not in done]
            return {
                "turn_id": turn_id,
                "conversation_id": conversation.id,
                "run_status": status,
                "outcome": "pending" if not terminal else "partial" if remaining else "complete",
                "completed": len(done),
                "total": len(ids),
                "remaining_target_ids": remaining,
                "errors": [e.get("message") for e in events if e.get("type") == "error"],
            }
        raise overview_error("OVERVIEW_REF_UNAVAILABLE_NOT_FOUND", "找不到这次总览更新")

    return router
