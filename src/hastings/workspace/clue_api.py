"""线索专用接口：只读视图、作者计划关联与推理目标；不调用模型。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from hastings.library.modules import ModuleLibrary
from hastings.workspace.clue_api_models import (
    CreateClueRequest,
    LinkDesignRequest,
    SaveInferenceRequest,
    UnlinkDesignRequest,
)
from hastings.workspace.clue_designs import (
    create_clue_identity,
    link_clue_design,
    save_clue_inference,
    unlink_clue_design,
)
from hastings.workspace.clue_models import InferenceStatement
from hastings.workspace.clue_reviews import ClueReviewReportView, clue_review_report
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clue_views import (
    ClueDetail,
    ClueInferenceView,
    ClueList,
    clue_detail,
    clue_inference_view,
    clue_list,
)
from hastings.workspace.source_locations import SourceLocation, resolve_source_location
from hastings.workspace.state import RecentWorkspaces, conversation_root


def create_clue_router(
    workspaces: RecentWorkspaces, library: ModuleLibrary | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/file-workspaces", tags=["clues"])

    @router.get("/{workspace_id}/clues", response_model=ClueList)
    def list_clues(
        workspace_id: str,
        chapter_path: str | None = Query(default=None, max_length=512),
        as_of_chapter: int | None = Query(default=None, ge=0),
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=20, ge=1, le=50),
        cursor: str = Query(default="", max_length=4096),
    ) -> ClueList:
        return clue_list(
            workspaces.workspace(workspace_id),
            conversation_root(workspaces.root, workspace_id),
            chapter_path=chapter_path,
            as_of_chapter=as_of_chapter,
            query=q,
            limit=limit,
            cursor=cursor,
        )

    @router.get("/{workspace_id}/clues/{clue_id}", response_model=ClueDetail)
    def get_clue(
        workspace_id: str, clue_id: str, as_of_chapter: int | None = Query(default=None, ge=0)
    ) -> ClueDetail:
        return clue_detail(
            workspaces.workspace(workspace_id),
            conversation_root(workspaces.root, workspace_id),
            clue_id,
            as_of_chapter=as_of_chapter,
            library=library,
        )

    @router.post("/{workspace_id}/clues", response_model=ClueDetail)
    def create_clue(
        workspace_id: str,
        body: CreateClueRequest,
        as_of_chapter: int | None = Query(default=None, ge=0),
    ) -> ClueDetail:
        workspace = workspaces.workspace(workspace_id)
        root = conversation_root(workspaces.root, workspace_id)
        result = create_clue_identity(
            ClueLedgerStore(root),
            title=body.title,
            request_key=body.request_key,
            expected_revision=body.expected_revision,
            created_by="author",
        )
        return clue_detail(
            workspace,
            root,
            str(result["clue_id"]),
            as_of_chapter=as_of_chapter,
            library=library,
        )

    @router.put("/{workspace_id}/clues/{clue_id}/design", response_model=ClueDetail)
    def put_clue_design(
        workspace_id: str,
        clue_id: str,
        body: LinkDesignRequest,
        as_of_chapter: int | None = Query(default=None, ge=0),
    ) -> ClueDetail:
        workspace = workspaces.workspace(workspace_id)
        root = conversation_root(workspaces.root, workspace_id)
        link_clue_design(
            workspace,
            ClueLedgerStore(root),
            clue_id=clue_id,
            source=body.source,
            start=body.start,
            end=body.end,
            quote=body.quote,
            relation=body.relation,
            request_key=body.request_key,
            expected_revision=body.expected_revision,
            created_by="author",
            library=library,
        )
        return clue_detail(
            workspace, root, clue_id, as_of_chapter=as_of_chapter, library=library
        )

    @router.post("/{workspace_id}/clues/{clue_id}/design/unlink", response_model=ClueDetail)
    def post_unlink_design(
        workspace_id: str,
        clue_id: str,
        body: UnlinkDesignRequest,
        as_of_chapter: int | None = Query(default=None, ge=0),
    ) -> ClueDetail:
        workspace = workspaces.workspace(workspace_id)
        root = conversation_root(workspaces.root, workspace_id)
        unlink_clue_design(
            ClueLedgerStore(root),
            clue_id=clue_id,
            link_id=body.link_id,
            request_key=body.request_key,
            expected_revision=body.expected_revision,
        )
        return clue_detail(
            workspace, root, clue_id, as_of_chapter=as_of_chapter, library=library
        )

    @router.post("/{workspace_id}/clue-inferences", response_model=ClueInferenceView)
    def post_inference(workspace_id: str, body: SaveInferenceRequest) -> ClueInferenceView:
        root = conversation_root(workspaces.root, workspace_id)
        result = save_clue_inference(
            ClueLedgerStore(root),
            clue_ids=body.clue_ids,
            goal=body.goal,
            intent=body.intent,
            premises=[
                InferenceStatement(
                    text=item.text,
                    clue_ids=list(item.clue_ids),
                    design_link_ids=list(item.design_link_ids),
                )
                for item in body.premises
            ],
            alternatives=[
                InferenceStatement(
                    text=item.text,
                    clue_ids=list(item.clue_ids),
                    design_link_ids=list(item.design_link_ids),
                )
                for item in body.alternatives
            ],
            design_link_ids=body.design_link_ids,
            request_key=body.request_key,
            expected_revision=body.expected_revision,
            inference_id=body.inference_id,
            inference_revision=body.inference_revision,
            created_by="author",
            request_source="author",
        )
        workspace = workspaces.workspace(workspace_id)
        return clue_inference_view(
            workspace,
            root,
            str(result["inference_id"]),
            library=library,
        )

    @router.get("/{workspace_id}/clue-reviews/{report_id}", response_model=ClueReviewReportView)
    def get_clue_review(
        workspace_id: str,
        report_id: str,
        as_of_chapter: int | None = Query(default=None, ge=0),
    ) -> ClueReviewReportView:
        return clue_review_report(
            workspaces.workspace(workspace_id),
            ClueLedgerStore(conversation_root(workspaces.root, workspace_id)),
            report_id,
            as_of_chapter=as_of_chapter,
        )

    @router.get("/{workspace_id}/source-locations/{evidence_id}", response_model=SourceLocation)
    def source_location(
        workspace_id: str, evidence_id: str, as_of_chapter: int | None = Query(default=None, ge=0)
    ) -> SourceLocation:
        return resolve_source_location(
            workspaces.workspace(workspace_id),
            conversation_root(workspaces.root, workspace_id),
            evidence_id,
            as_of_chapter=as_of_chapter,
            library=library,
        )

    return router
