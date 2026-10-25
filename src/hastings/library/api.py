"""模块库的 HTTP 接口（0.8 B）。

这些路由只操作全局库：浏览预设类型与实例、发布不可变版本、另存变体、可恢复删除。
作品接口不提供全库读写；作者在界面上的操作与 agent 的库工具最终都走同一套服务。

字段统一 snake_case；错误沿用 ``{"error": {code, where, why, fix, details}}``。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hastings.library.modules import DEFAULT_PAGE_SIZE, ModuleLibrary
from hastings.library.presets import presets_view

__all__ = ["create_library_router"]


class EntryPayload(BaseModel):
    entry_id: str = ""
    kind: str = ""
    title: str = ""
    body: str = ""
    aliases: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)


class CreateModuleRequest(BaseModel):
    kind: str
    name: str
    entries: list[EntryPayload] = Field(default_factory=list)
    request_key: str


class PublishRequest(BaseModel):
    base_revision: str | None = None
    expected_head: str | None = None
    entries: list[EntryPayload]
    request_key: str


class ForkRequest(BaseModel):
    base_revision: str | None = None
    name: str
    request_key: str


class VersionRequest(BaseModel):
    expected_head: str | None = None
    request_key: str


def _rows(entries: list[EntryPayload]) -> list[dict[str, Any]]:
    return [entry.model_dump() for entry in entries]


def create_library_router(library: ModuleLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/module-library", tags=["module-library"])

    @router.get("/presets")
    def presets() -> list[dict[str, object]]:
        return presets_view()

    @router.get("/modules")
    def modules(
        kind: str | None = None,
        q: str = "",
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        include_deleted: bool = False,
    ) -> dict[str, object]:
        return library.modules(
            kind=kind,
            q=q,
            cursor=cursor,
            limit=limit,
            include_deleted=include_deleted,
            include_preview=True,
        )

    @router.post("/modules")
    def create(payload: CreateModuleRequest) -> dict[str, object]:
        return library.create_module(
            kind=payload.kind,
            name=payload.name,
            entries=_rows(payload.entries),
            request_key=payload.request_key,
        )

    @router.get("/modules/{module_id}/revisions")
    def revisions(
        module_id: str, cursor: str | None = None, limit: int = DEFAULT_PAGE_SIZE
    ) -> dict[str, object]:
        return library.revisions(module_id=module_id, cursor=cursor, limit=limit)

    @router.get("/modules/{module_id}/revisions/{revision_id}")
    def revision(module_id: str, revision_id: str) -> dict[str, object]:
        return library.revision(module_id=module_id, revision_id=revision_id).view()

    @router.get("/modules/{module_id}/revisions/{revision_id}/records/{entry_id}")
    def read_record(
        module_id: str,
        revision_id: str,
        entry_id: str,
        start: int = 0,
        end: int | None = None,
    ) -> dict[str, object]:
        return library.read_entry(
            module_id=module_id,
            revision_id=revision_id,
            entry_id=entry_id,
            start=start,
            end=end,
        )

    @router.post("/modules/{module_id}/revisions")
    def publish(module_id: str, payload: PublishRequest) -> dict[str, object]:
        return library.publish(
            module_id=module_id,
            base_revision=payload.base_revision,
            expected_head=payload.expected_head,
            entries=_rows(payload.entries),
            request_key=payload.request_key,
        ).view()

    @router.post("/modules/{module_id}/fork")
    def fork(module_id: str, payload: ForkRequest) -> dict[str, object]:
        return library.fork(
            module_id=module_id,
            base_revision=payload.base_revision,
            name=payload.name,
            request_key=payload.request_key,
        )

    @router.post("/modules/{module_id}/trash")
    def trash(module_id: str, payload: VersionRequest) -> dict[str, object]:
        return library.trash(
            module_id=module_id,
            expected_head=payload.expected_head,
            request_key=payload.request_key,
        )

    @router.post("/modules/{module_id}/restore")
    def restore(module_id: str, payload: VersionRequest) -> dict[str, object]:
        return library.restore(
            module_id=module_id,
            expected_head=payload.expected_head,
            request_key=payload.request_key,
        )

    return router
