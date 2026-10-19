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
    METHOD_TRUTH_NOT_RECORDED,
    PERSPECTIVE_AUTHOR,
    PERSPECTIVE_CHARACTER,
    CharacterGraph,
    ClueState,
    GraphEdge,
    GraphNode,
    GraphProjection,
    MethodCompleteness,
    MethodGap,
    MethodTruthState,
    character_projection,
    clue_projection,
    graph_projection,
    method_projection,
)
from hastings.domain.story.narrative.order import last_beat_of_node
from hastings.domain.story.timeline import (
    GROUP_BY_CHARACTER,
    ORDERING_NOTE,
    TESTIMONY_TIME_NOTE,
    TIMELINE_NO_EVENTS,
    TestimonyTrack,
    TimelineEventState,
    TimelineGap,
    TimelineTrack,
    timeline_projection,
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

        resolved_beat, position = _resolve_position(
            catalog,
            at_beat_id=at_beat_id,
            at_chapter_id=at_chapter_id,
            where="graphs.characters.at_chapter_id",
        )

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

    def clues(
        self,
        project_id: str,
        *,
        at_commit_id: str | None = None,
        at_beat_id: str | None = None,
        at_chapter_id: str | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """线索图视图（只读）：线索子图 + 计划/实现分离 + 叙述位置筛选。

        「截至某章」先解析为该章内最后一个节拍再筛选，与 ``at_beat_id`` 互斥；
        每条线索分别报出计划出现与已在正文出现的节拍，后文线索以
        ``within_position=False`` 标出，由调用方决定是否渲染。
        """
        if at_beat_id is not None and at_chapter_id is not None:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                "graphs.clues.position",
                "at_beat_id 与 at_chapter_id 互斥，不能同时给出",
                "二者取其一：按节拍或按章节指定叙述位置",
            )
        pid = ProjectId(project_id)
        commit = CommitId(at_commit_id) if at_commit_id else self._head(pid)
        if commit is None:
            return _empty_clues(project_id)
        snapshot = self._repo.get_snapshot(pid, commit)
        catalog = StoryCatalog.from_snapshot(snapshot)

        resolved_beat, position = _resolve_position(
            catalog,
            at_beat_id=at_beat_id,
            at_chapter_id=at_chapter_id,
            where="graphs.clues.at_chapter_id",
        )
        graph = clue_projection(
            catalog,
            at_beat_id=resolved_beat,
            focus=focus,
            evidence_status=_evidence_status(snapshot),
        )

        focus_payload: dict[str, Any] | None = None
        if focus is not None:
            focus_payload = {"node_id": focus, "neighbor_ids": _neighbors(graph.edges, focus)}

        return {
            "project_id": project_id,
            "commit_id": graph.commit_id,
            "position": position,
            "focus": focus_payload,
            "clues": [_clue_payload(state) for state in graph.clues],
            "nodes": _node_payload(graph.nodes, catalog),
            "edges": [_edge_base(edge) for edge in graph.edges],
        }

    def _head(self, pid: ProjectId) -> CommitId | None:
        branch = self._repo.get_branch(pid, "main")
        return branch.head_commit_id if branch is not None else None

    def method(
        self,
        project_id: str,
        *,
        at_commit_id: str | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """手法图视图（只读）：既有真相文字与关系子图 + 缺口 + 完整度。

        不新增步骤/条件/证据对象，也不按模型措辞补边；未记录的内容以
        ``gaps`` 逐条报出，完整度只统计三段文字是否已写（不冒充逻辑证明）。
        """
        pid = ProjectId(project_id)
        commit = CommitId(at_commit_id) if at_commit_id else self._head(pid)
        if commit is None:
            return _empty_method(project_id)
        snapshot = self._repo.get_snapshot(pid, commit)
        catalog = StoryCatalog.from_snapshot(snapshot)
        graph = method_projection(
            catalog, focus=focus, evidence_status=_evidence_status(snapshot)
        )
        focus_payload: dict[str, Any] | None = None
        if focus is not None:
            focus_payload = {"node_id": focus, "neighbor_ids": _neighbors(graph.edges, focus)}
        return {
            "project_id": project_id,
            "commit_id": graph.commit_id,
            "focus": focus_payload,
            "truths": [_method_truth_payload(item) for item in graph.truths],
            "gaps": [_method_gap_payload(item) for item in graph.gaps],
            "completeness": {
                "recorded": graph.completeness.recorded,
                "total": graph.completeness.total,
                "note": graph.completeness.note,
            },
            "nodes": _node_payload(graph.nodes, catalog),
            "edges": [_edge_base(edge) for edge in graph.edges],
        }

    def timeline(
        self,
        project_id: str,
        *,
        at_commit_id: str | None = None,
        group_by: str = GROUP_BY_CHARACTER,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """时间线视图（只读）：真实事件分轨 + 证词/认知分轨 + 缺口。

        事件时间原样显示、不排序；叙述位置只来自角色获知事件。``focus`` 只收窄
        展示范围，不改事件内容（筛选不改变事实）。
        """
        pid = ProjectId(project_id)
        commit = CommitId(at_commit_id) if at_commit_id else self._head(pid)
        if commit is None:
            return _empty_timeline(project_id, group_by)
        snapshot = self._repo.get_snapshot(pid, commit)
        catalog = StoryCatalog.from_snapshot(snapshot)
        graph = timeline_projection(
            catalog,
            group_by=group_by,
            focus=focus,
            evidence_status=_evidence_status(snapshot),
        )
        focus_payload: dict[str, Any] | None = None
        if focus is not None:
            focus_payload = {"node_id": focus, "neighbor_ids": _neighbors(graph.edges, focus)}
        return {
            "project_id": project_id,
            "commit_id": graph.commit_id,
            "group_by": graph.group_by,
            "ordering": {
                "story_time": "as_recorded",
                "narrative_order": "testimony_only",
                "note": ORDERING_NOTE,
            },
            "testimony_note": TESTIMONY_TIME_NOTE,
            "events": [_timeline_event_payload(event) for event in graph.events],
            "tracks": [_timeline_track_payload(track) for track in graph.tracks],
            "testimonies": [_testimony_track_payload(track) for track in graph.testimonies],
            "gaps": [_timeline_gap_payload(gap) for gap in graph.gaps],
            "focus": focus_payload,
            "nodes": _node_payload(graph.nodes, catalog),
            "edges": [_edge_base(edge) for edge in graph.edges],
        }


def _resolve_position(
    catalog: StoryCatalog,
    *,
    at_beat_id: str | None,
    at_chapter_id: str | None,
    where: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """把「截至某章/某节拍」解析为叙述位置：返回末节拍与展示用位置描述。

    章不经章节序号冒充位置，而是先解析为该章内按结构顺序的最后一个节拍，
    人物图与线索图共用这一份解析口径。
    """
    if at_chapter_id is not None:
        resolved = last_beat_of_node(catalog, at_chapter_id, where=where)
        chapter = catalog.get(at_chapter_id)
        label = display_name(chapter.kind, chapter.content) if chapter else ""
        return resolved, {
            "kind": "chapter",
            "at_chapter_id": at_chapter_id,
            "at_beat_id": resolved,
            "label": label or at_chapter_id,
        }
    if at_beat_id is not None:
        beat = catalog.get(at_beat_id)
        label = display_name(beat.kind, beat.content) if beat else ""
        return at_beat_id, {
            "kind": "beat",
            "at_chapter_id": None,
            "at_beat_id": at_beat_id,
            "label": label or at_beat_id,
        }
    return None, None


def _clue_payload(state: ClueState) -> dict[str, Any]:
    return {
        "clue_id": state.clue_id,
        "statement": state.statement,
        "necessity": state.necessity,
        "points_to": list(state.points_to),
        "planned_beats": list(state.planned_beats),
        "disclosed_beats": list(state.disclosed_beats),
        "status": state.status,
        "first_position": state.first_position,
        "within_position": state.within_position,
    }


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


def _empty_clues(project_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "commit_id": None,
        "position": None,
        "focus": None,
        "clues": [],
        "nodes": [],
        "edges": [],
    }


def _method_truth_payload(state: MethodTruthState) -> dict[str, Any]:
    return {
        "truth_id": state.truth_id,
        "culprit_id": state.culprit_id,
        "motive": state.motive,
        "method": state.method,
        "cover_up": state.cover_up,
        "accomplice_ids": list(state.accomplice_ids),
        "true_timeline": list(state.true_timeline),
        "missing_fields": list(state.missing_fields),
    }


def _method_gap_payload(gap: MethodGap) -> dict[str, Any]:
    return {
        "code": gap.code,
        "where": gap.where,
        "why": gap.why,
        "fix": gap.fix,
        "truth_id": gap.truth_id,
    }


def _empty_method(project_id: str) -> dict[str, Any]:
    completeness = MethodCompleteness(recorded=0, total=0)
    return {
        "project_id": project_id,
        "commit_id": None,
        "focus": None,
        "truths": [],
        "gaps": [
            {
                "code": METHOD_TRUTH_NOT_RECORDED,
                "where": "graphs.method",
                "why": "账本里还没有「真相」条目，手法结构无从投影",
                "fix": "在账本新建真相条目，填写凶手、手法、动机与掩盖方式；缺口只表示未记录",
                "truth_id": None,
            }
        ],
        "completeness": {
            "recorded": completeness.recorded,
            "total": completeness.total,
            "note": completeness.note,
        },
        "nodes": [],
        "edges": [],
    }


def _timeline_event_payload(event: TimelineEventState) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "time_text": event.time_text,
        "time_status": event.time_status,
        "place_id": event.place_id,
        "participants": list(event.participants),
        "before_state": event.before_state,
        "after_state": event.after_state,
    }


def _timeline_track_payload(track: TimelineTrack) -> dict[str, Any]:
    return {
        "key": track.key,
        "kind": track.kind,
        "id": track.id,
        "label": track.label,
        "event_ids": list(track.event_ids),
    }


def _testimony_track_payload(track: TestimonyTrack) -> dict[str, Any]:
    return {
        "character_id": track.character_id,
        "label": track.label,
        "items": [
            {
                "knowledge_id": item.knowledge_id,
                "character_id": item.character_id,
                "fact_id": item.fact_id,
                "mode": item.mode,
                "mode_label": item.mode_label,
                "note": item.note,
                "at_beat_id": item.at_beat_id,
                "position": item.position,
                "claimed_event_id": item.claimed_event_id,
                "claimed_event_time": item.claimed_event_time,
            }
            for item in track.items
        ],
    }


def _timeline_gap_payload(gap: TimelineGap) -> dict[str, Any]:
    return {
        "code": gap.code,
        "where": gap.where,
        "why": gap.why,
        "fix": gap.fix,
        "target_id": gap.target_id,
    }


def _empty_timeline(project_id: str, group_by: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "commit_id": None,
        "group_by": group_by,
        "ordering": {
            "story_time": "as_recorded",
            "narrative_order": "testimony_only",
            "note": ORDERING_NOTE,
        },
        "testimony_note": TESTIMONY_TIME_NOTE,
        "events": [],
        "tracks": [],
        "testimonies": [],
        "gaps": [
            {
                "code": TIMELINE_NO_EVENTS,
                "where": "graphs.timeline",
                "why": "账本里还没有时间线事件",
                "fix": "在账本新建时间线事件，填写时间、地点与参与者；时间可以只写「时间未定」",
                "target_id": None,
            }
        ],
        "focus": None,
        "nodes": [],
        "edges": [],
    }
