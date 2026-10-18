"""图谱只读查询（V03-6.1 / M12）：按 Commit 回读统一投影，附展示标签与证据状态。

图谱与目录查询固定同一 Commit（M12）：本模块只取快照、构造 ``StoryCatalog``，
再交给领域纯函数 ``graph_projection`` 投影。**没有写入端口**——图谱不是第二份
可写事实，拖动只改布局偏好（前端事），图中边只能通过正式账本命令改变。

证据状态取同一快照里的 ``evidence.link``（M05）：来源正文变化后发布器会把关联
置为 ``needs_check``，本投影直接读该状态，不另建失效逻辑。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from hastings.application.queries.ledger import entry_view
from hastings.application.services.commit_service import CommitService
from hastings.domain.manuscript.evidence import KIND_EVIDENCE_LINK
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.repository.models import Snapshot
from hastings.domain.story import error_codes as ec
from hastings.domain.story.catalog import StoryCatalog, display_name
from hastings.domain.story.errors import violation
from hastings.domain.story.graph import (
    EVIDENCE_NEEDS_CHECK,
    EVIDENCE_VALID,
    PERSPECTIVE_AUTHOR,
    PERSPECTIVE_CHARACTER,
    CharacterGraph,
    GraphEdge,
    GraphNode,
    GraphProjection,
    character_projection,
    graph_projection,
)
from hastings.domain.story.narrative.order import last_beat_of_node

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

    def characters(
        self,
        project_id: str,
        *,
        at_commit_id: str | None = None,
        perspective: str | None = None,
        at_beat_id: str | None = None,
        at_chapter_id: str | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """人物图视图（只读）：视角 + 叙述位置 + 局部聚焦。

        「截至某章」先解析为该章内最后一个节拍，再与 ``at_beat_id`` 走同一折叠
        路径；二者互斥，绝不混用一个章节下标冒充叙述位置。响应显式声明当前
        视角与范围，未记录的关联留缺口，不合成「不知道」。
        """
        if at_beat_id is not None and at_chapter_id is not None:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                "graphs.characters.position",
                "at_beat_id 与 at_chapter_id 互斥，不能同时给出",
                "二者取其一：按节拍或按章节指定叙述位置",
            )
        pid = ProjectId(project_id)
        commit = CommitId(at_commit_id) if at_commit_id else self._head(pid)
        if commit is None:
            return _empty_characters(project_id)
        snapshot = self._repo.get_snapshot(pid, commit)
        catalog = StoryCatalog.from_snapshot(snapshot)

        resolved_beat = at_beat_id
        position: dict[str, Any] | None = None
        if at_chapter_id is not None:
            resolved_beat = last_beat_of_node(
                catalog, at_chapter_id, where="graphs.characters.at_chapter_id"
            )
            chapter = catalog.get(at_chapter_id)
            label = display_name(chapter.kind, chapter.content) if chapter else ""
            position = {
                "kind": "chapter",
                "at_chapter_id": at_chapter_id,
                "at_beat_id": resolved_beat,
                "label": label or at_chapter_id,
            }
        elif at_beat_id is not None:
            beat = catalog.get(at_beat_id)
            label = display_name(beat.kind, beat.content) if beat else ""
            position = {
                "kind": "beat",
                "at_chapter_id": None,
                "at_beat_id": at_beat_id,
                "label": label or at_beat_id,
            }

        status = _evidence_status(snapshot)
        graph = character_projection(
            catalog, perspective=perspective, at_beat_id=resolved_beat, evidence_status=status
        )
        projection = graph_projection(catalog, evidence_status=status)

        focus_payload: dict[str, Any] | None = None
        if focus is not None:
            if focus not in {node.id for node in projection.nodes}:
                raise violation(
                    ec.STORY_UNKNOWN_ARTIFACT,
                    "graphs.characters.focus",
                    f"聚焦对象 {focus!r} 不在当前快照中",
                    "改用快照内存在的对象 ID",
                    node_id=focus,
                )
            # 作者全知按统一投影取邻居（可含线索/地点/场景）；角色视角只在本
            # 视图过滤后的边集内取，避免借局部关联提前泄露后续信息。
            view_edges = (
                projection.edges
                if graph.perspective_mode == PERSPECTIVE_AUTHOR
                else tuple(edge.edge for edge in graph.edges)
            )
            focus_payload = {"node_id": focus, "neighbor_ids": _neighbors(view_edges, focus)}

        return {
            "project_id": project_id,
            "commit_id": graph.commit_id,
            "perspective": _perspective_payload(graph, catalog),
            "position": position,
            "nodes": _node_payload(graph.nodes, catalog),
            "edges": [
                {
                    **_edge_base(edge.edge),
                    "public_state": edge.public_state,
                    "true_state": edge.true_state,
                    "true_state_hidden": edge.true_state_hidden,
                }
                for edge in graph.edges
            ],
            "focus": focus_payload,
        }

    def _head(self, pid: ProjectId) -> CommitId | None:
        branch = self._repo.get_branch(pid, "main")
        return branch.head_commit_id if branch is not None else None


def _node_payload(nodes: Iterable[GraphNode], catalog: StoryCatalog) -> list[dict[str, Any]]:
    views = {entry.artifact_id: entry_view(entry) for entry in catalog.entries}
    result: list[dict[str, Any]] = []
    for node in nodes:
        view = views.get(node.id, {})
        result.append(
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
    return result


def _edge_base(edge: GraphEdge) -> dict[str, Any]:
    return {
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


def _payload(projection: GraphProjection, catalog: StoryCatalog) -> dict[str, Any]:
    return {
        "commit_id": projection.commit_id,
        "nodes": _node_payload(projection.nodes, catalog),
        "edges": [_edge_base(edge) for edge in projection.edges],
    }


def _perspective_payload(graph: CharacterGraph, catalog: StoryCatalog) -> dict[str, Any]:
    if graph.perspective_mode == PERSPECTIVE_CHARACTER:
        entry = catalog.get(graph.perspective_id) if graph.perspective_id else None
        name = display_name(entry.kind, entry.content) if entry else ""
        return {
            "mode": PERSPECTIVE_CHARACTER,
            "character_id": graph.perspective_id,
            "label": f"{name or graph.perspective_id}视角",
        }
    return {"mode": PERSPECTIVE_AUTHOR, "character_id": None, "label": "作者全知"}


def _neighbors(edges: Iterable[GraphEdge], node_id: str) -> list[str]:
    found: set[str] = set()
    for edge in edges:
        if edge.source == node_id:
            found.add(edge.target)
        elif edge.target == node_id:
            found.add(edge.source)
    return sorted(found)


def _empty_characters(project_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "commit_id": None,
        "perspective": {"mode": PERSPECTIVE_AUTHOR, "character_id": None, "label": "作者全知"},
        "position": None,
        "nodes": [],
        "edges": [],
        "focus": None,
    }
