"""图谱只读查询（V03-6.1 / M12）：按 Commit 回读统一投影，附展示标签与证据状态。

图谱与目录查询固定同一 Commit（M12）：本模块只取快照、构造 ``StoryCatalog``，
再交给领域纯函数 ``graph_projection`` 投影。**没有写入端口**——图谱不是第二份
可写事实，拖动只改布局偏好（前端事），图中边只能通过正式账本命令改变。

证据状态取同一快照里的 ``evidence.link``（M05）：来源正文变化后发布器会把关联
置为 ``needs_check``，本投影直接读该状态，不另建失效逻辑。
"""

from __future__ import annotations

from typing import Any

from hastings.application.queries.ledger import entry_view
from hastings.application.services.commit_service import CommitService
from hastings.domain.manuscript.evidence import KIND_EVIDENCE_LINK
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.repository.models import Snapshot
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.graph import (
    EVIDENCE_NEEDS_CHECK,
    EVIDENCE_VALID,
    GraphProjection,
    graph_projection,
)

__all__ = ["GraphQueries"]


def _evidence_status(snapshot: Snapshot) -> dict[str, str]:
    """账本 Artifact ID → 证据状态；``needs_check`` 覆盖 ``valid``。"""
    status: dict[str, str] = {}
    for view in snapshot.artifacts.values():
        if view.artifact.kind != KIND_EVIDENCE_LINK or view.revision is None:
            continue
        content = view.revision.content
        target_id = content.get("target_id") if isinstance(content, dict) else None
        if not isinstance(target_id, str) or not target_id:
            continue
        current = str(content.get("status", EVIDENCE_VALID))
        if current == EVIDENCE_NEEDS_CHECK or target_id not in status:
            status[target_id] = (
                EVIDENCE_NEEDS_CHECK if current == EVIDENCE_NEEDS_CHECK else EVIDENCE_VALID
            )
    return status


class GraphQueries:
    """统一图谱投影查询：节点/边都只读，响应固定到实际读取的 Commit。"""

    def __init__(self, repo: CommitService) -> None:
        self._repo = repo

    def graph(self, project_id: str, *, at_commit_id: str | None = None) -> dict[str, Any]:
        pid = ProjectId(project_id)
        commit = CommitId(at_commit_id) if at_commit_id else self._head(pid)
        if commit is None:
            return {"project_id": project_id, "commit_id": None, "nodes": [], "edges": []}
        snapshot = self._repo.get_snapshot(pid, commit)
        catalog = StoryCatalog.from_snapshot(snapshot)
        projection = graph_projection(catalog, evidence_status=_evidence_status(snapshot))
        return {"project_id": project_id, **_payload(projection, catalog)}

    def _head(self, pid: ProjectId) -> CommitId | None:
        branch = self._repo.get_branch(pid, "main")
        return branch.head_commit_id if branch is not None else None


def _payload(projection: GraphProjection, catalog: StoryCatalog) -> dict[str, Any]:
    views = {entry.artifact_id: entry_view(entry) for entry in catalog.entries}
    nodes: list[dict[str, Any]] = []
    for node in projection.nodes:
        view = views.get(node.id, {})
        nodes.append(
            {
                "id": node.id,
                "kind": node.kind,
                "kind_label": view.get("kind_label", node.kind),
                "title": view.get("title", node.id),
                "summary": view.get("summary", ""),
                "revision": node.revision,
                "semantic_layer": node.semantic_layer,
                "evidence_status": node.evidence_status,
                "source_refs": [{"kind": node.kind, "id": node.id, "revision": node.revision}],
            }
        )
    edges = [
        {
            "id": edge.id,
            "source": edge.source,
            "target": edge.target,
            "label": edge.label,
            "relation_type": edge.relation_type,
            "semantic_layer": edge.semantic_layer,
            "evidence_status": edge.evidence_status,
            "declared_by": edge.declared_by,
            "source_refs": [ref.to_payload() for ref in edge.source_refs],
        }
        for edge in projection.edges
    ]
    return {"commit_id": projection.commit_id, "nodes": nodes, "edges": edges}
