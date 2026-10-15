"""确定性影响遍历（T15.2，modules/change-impact.md §4）。

两阶段分析的第一阶段：沿显式依赖边遍历，得到结构上必然受影响的对象。
本模块是**纯函数**：不调用模型、不触达 Repository（图由 Snapshot 投影）。

遍历规则（§4.2）：
- 边类型决定传播方向（forward：source→target；backward：target→source）
  与默认严重度——规则是可注入策略，不是硬编码；
- 到达 scope 边界（kind 白名单 / roots 白名单 / 跳数上限）即停止，并记录
  被截断项——「没查到」与「查到但被截断」必须可区分；
- 相同目标的多条路径全部保留（展示时可折叠）；
- 环路以「路径内不重复走同一条边/同一个节点」去重，不无限遍历；
- 影响绑定 base_commit_id（报告模型字段），head 变化后可判 stale。

低置信语义边（mentions）在确定性阶段只产生 review 项，不能升 must_fix
（§4.1：确定性结果不能被语义分析删除，反之语义不能冒充结构必然）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hastings.domain.changes.models import ChangeScope
from hastings.domain.repository.models import Snapshot

__all__ = [
    "DIRECTION_BACKWARD",
    "DIRECTION_FORWARD",
    "DEFAULT_PROPAGATION_RULES",
    "EdgeHop",
    "ImpactItem",
    "ImpactStats",
    "ImpactReport",
    "PropagationRule",
    "RelationGraph",
    "TruncatedEntry",
    "traverse_impact",
]

DIRECTION_FORWARD = "forward"
DIRECTION_BACKWARD = "backward"

SEVERITY_MUST_FIX = "must_fix"
SEVERITY_SHOULD_FIX = "should_fix"
SEVERITY_REVIEW = "review"
SEVERITY_UNAFFECTED = "unaffected"

ITEM_OPEN = "open"
ITEM_PLANNED = "planned"
ITEM_RESOLVED = "resolved"
ITEM_ACCEPTED_RISK = "accepted_risk"
ITEM_DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class PropagationRule:
    """单条边类型的传播策略：方向、默认严重度与影响类别（可注入）。"""

    relation_type: str
    direction: str = DIRECTION_FORWARD
    severity: str = SEVERITY_SHOULD_FIX
    impact_kind: str = "structure"


# 默认传播策略（§4.2「边类型决定传播方向与默认严重度」；§4.3 示例的方向语义）
DEFAULT_PROPAGATION_RULES: tuple[PropagationRule, ...] = (
    PropagationRule("motivates", DIRECTION_FORWARD, SEVERITY_SHOULD_FIX, "motivation"),
    PropagationRule("changes_state", DIRECTION_BACKWARD, SEVERITY_SHOULD_FIX, "knowledge"),
    PropagationRule("requires", DIRECTION_BACKWARD, SEVERITY_MUST_FIX, "contradiction"),
    PropagationRule("constrains", DIRECTION_FORWARD, SEVERITY_SHOULD_FIX, "structure"),
    PropagationRule("reveals", DIRECTION_BACKWARD, SEVERITY_MUST_FIX, "contradiction"),
    PropagationRule("sets_up", DIRECTION_FORWARD, SEVERITY_SHOULD_FIX, "setup_payoff"),
    PropagationRule("pays_off", DIRECTION_BACKWARD, SEVERITY_MUST_FIX, "setup_payoff"),
    PropagationRule("renders", DIRECTION_FORWARD, SEVERITY_MUST_FIX, "contradiction"),
    # 低置信语义边：只产生 review，永不升 must_fix（§4.1 / 计划 T15.3）
    PropagationRule("mentions", DIRECTION_FORWARD, SEVERITY_REVIEW, "wording"),
)


@dataclass(frozen=True, slots=True)
class EdgeHop:
    """因果路径上的一跳（版本化关系边）。"""

    source: str
    relation_type: str
    target: str
    edge_id: str = ""

    def to_content(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "relation_type": self.relation_type,
            "target": self.target,
            "edge_id": self.edge_id,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> EdgeHop:
        return cls(
            source=str(content.get("source", "")),
            relation_type=str(content.get("relation_type", "")),
            target=str(content.get("target", "")),
            edge_id=str(content.get("edge_id", "")),
        )


@dataclass(frozen=True, slots=True)
class ImpactItem:
    """一个受影响对象：完整因果路径 + 分级 + 状态（§2 ImpactItem）。"""

    target_artifact_id: str
    severity: str
    impact_kind: str
    path: tuple[EdgeHop, ...]
    reason: str
    status: str = ITEM_OPEN
    confidence: float = 1.0  # 确定性遍历 = 1.0；语义补充 < 1.0（T15.3）
    text_unit_id: str | None = None

    def to_content(self) -> dict[str, Any]:
        return {
            "target_artifact_id": self.target_artifact_id,
            "severity": self.severity,
            "impact_kind": self.impact_kind,
            "path": [hop.to_content() for hop in self.path],
            "reason": self.reason,
            "status": self.status,
            "confidence": self.confidence,
            "text_unit_id": self.text_unit_id,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> ImpactItem:
        return cls(
            target_artifact_id=str(content.get("target_artifact_id", "")),
            severity=str(content.get("severity", SEVERITY_REVIEW)),
            impact_kind=str(content.get("impact_kind", "structure")),
            path=tuple(EdgeHop.from_content(h) for h in (content.get("path") or ())),
            reason=str(content.get("reason", "")),
            status=str(content.get("status", ITEM_OPEN)),
            confidence=float(content.get("confidence", 1.0)),
            text_unit_id=content.get("text_unit_id"),
        )


@dataclass(frozen=True, slots=True)
class TruncatedEntry:
    """到达 scope 边界被截断的记录：没查 ≠ 查不到（§4.2）。"""

    artifact_id: str
    relation_type: str
    reason: str

    def to_content(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "relation_type": self.relation_type,
            "reason": self.reason,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> TruncatedEntry:
        return cls(
            artifact_id=str(content.get("artifact_id", "")),
            relation_type=str(content.get("relation_type", "")),
            reason=str(content.get("reason", "")),
        )


@dataclass(frozen=True, slots=True)
class ImpactStats:
    """分析统计：visited 证明与截断计数（§4.2「输出分析统计」）。"""

    seed_count: int = 0
    nodes_visited: int = 0
    paths_explored: int = 0
    cycles_skipped: int = 0
    truncated_count: int = 0
    item_count: int = 0

    def to_content(self) -> dict[str, Any]:
        return {
            "seed_count": self.seed_count,
            "nodes_visited": self.nodes_visited,
            "paths_explored": self.paths_explored,
            "cycles_skipped": self.cycles_skipped,
            "truncated_count": self.truncated_count,
            "item_count": self.item_count,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> ImpactStats:
        return cls(
            seed_count=int(content.get("seed_count", 0)),
            nodes_visited=int(content.get("nodes_visited", 0)),
            paths_explored=int(content.get("paths_explored", 0)),
            cycles_skipped=int(content.get("cycles_skipped", 0)),
            truncated_count=int(content.get("truncated_count", 0)),
            item_count=int(content.get("item_count", 0)),
        )


@dataclass(frozen=True, slots=True)
class ImpactReport:
    """影响报告：绑定 ChangeSet 与基础 Commit（不变量 1）。"""

    report_id: str
    change_set_id: str
    base_commit_id: str
    items: tuple[ImpactItem, ...] = ()
    truncated: tuple[TruncatedEntry, ...] = ()
    stats: ImpactStats = field(default_factory=ImpactStats)
    analyzed_by: str = ""
    at: str = ""

    def is_stale(self, head_commit_id: str) -> bool:
        """head 前移后报告过期，不能直接执行（不变量 2）。"""
        return head_commit_id != self.base_commit_id

    def to_content(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "change_set_id": self.change_set_id,
            "base_commit_id": self.base_commit_id,
            "items": [item.to_content() for item in self.items],
            "truncated": [t.to_content() for t in self.truncated],
            "stats": self.stats.to_content(),
            "analyzed_by": self.analyzed_by,
            "at": self.at,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> ImpactReport:
        return cls(
            report_id=str(content.get("report_id", "")),
            change_set_id=str(content.get("change_set_id", "")),
            base_commit_id=str(content.get("base_commit_id", "")),
            items=tuple(ImpactItem.from_content(i) for i in (content.get("items") or ())),
            truncated=tuple(
                TruncatedEntry.from_content(t) for t in (content.get("truncated") or ())
            ),
            stats=ImpactStats.from_content(content.get("stats") or {}),
            analyzed_by=str(content.get("analyzed_by", "")),
            at=str(content.get("at", "")),
        )


@dataclass(frozen=True, slots=True)
class RelationGraph:
    """某 Commit 的有效依赖边（Snapshot.relations 已折叠，直接索引）。"""

    edges: tuple[EdgeHop, ...]
    _forward: dict[str, tuple[EdgeHop, ...]] = field(default_factory=dict, repr=False)
    _backward: dict[str, tuple[EdgeHop, ...]] = field(default_factory=dict, repr=False)

    @classmethod
    def from_edges(cls, edges: tuple[EdgeHop, ...]) -> RelationGraph:
        forward: dict[str, list[EdgeHop]] = {}
        backward: dict[str, list[EdgeHop]] = {}
        for edge in edges:
            forward.setdefault(edge.source, []).append(edge)
            backward.setdefault(edge.target, []).append(edge)
        return cls(
            edges=edges,
            _forward={k: tuple(v) for k, v in forward.items()},
            _backward={k: tuple(v) for k, v in backward.items()},
        )

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> RelationGraph:
        return cls.from_edges(
            tuple(
                EdgeHop(
                    source=edge.source.value,
                    relation_type=edge.relation_type,
                    target=edge.target.value,
                    edge_id=edge.id,
                )
                for edge in snapshot.relations
            )
        )

    def forward(self, node: str) -> tuple[EdgeHop, ...]:
        return self._forward.get(node, ())

    def backward(self, node: str) -> tuple[EdgeHop, ...]:
        return self._backward.get(node, ())


def _in_scope(node: str, kind_of: Any, scope: ChangeScope) -> tuple[bool, str]:
    """返回 (是否在范围内, 截断原因)；scope 为空表示不限。"""
    if scope.roots and node not in scope.roots:
        return False, f"scope.roots 白名单不含 {node}"
    if scope.kinds and kind_of is not None:
        kind = kind_of(node)
        if kind is not None and kind not in scope.kinds:
            return False, f"scope.kinds 白名单不含 {kind}"
    return True, ""


def traverse_impact(
    graph: RelationGraph,
    seeds: tuple[str, ...],
    rules: tuple[PropagationRule, ...] = DEFAULT_PROPAGATION_RULES,
    scope: ChangeScope | None = None,
    kind_of: Any = None,
) -> ImpactReport:
    """从种子沿规则边枚举全部简单路径，每个到达节点产生一项 ImpactItem。

    ``kind_of`` 是可选的节点→kind 解析函数（由调用方从 Snapshot 提供）；
    未提供时 kinds 白名单不生效（只按 roots/跳数截断）。
    """
    scope = scope or ChangeScope()
    rule_map = {rule.relation_type: rule for rule in rules}
    items: list[ImpactItem] = []
    truncated: list[TruncatedEntry] = []
    cycles = 0
    explored = 0
    visited_nodes: set[str] = set()

    for seed in seeds:
        # DFS：path 内不重复节点（种子入初始路径，环回种子同样被截住）；
        # 相同目标的不同路径分别保留（菱形）。
        stack: list[tuple[tuple[str, ...], tuple[EdgeHop, ...]]] = [((seed,), ())]
        while stack:
            path_nodes, path_edges = stack.pop()
            node = path_nodes[-1]
            if path_edges:
                explored += 1
            max_hops_reached = scope.max_hops is not None and len(path_edges) >= scope.max_hops
            outgoing: list[tuple[EdgeHop, PropagationRule]] = []
            for edge in graph.forward(node):
                rule = rule_map.get(edge.relation_type)
                if rule is not None and rule.direction == DIRECTION_FORWARD:
                    outgoing.append((edge, rule))
            for edge in graph.backward(node):
                rule = rule_map.get(edge.relation_type)
                if rule is not None and rule.direction == DIRECTION_BACKWARD:
                    outgoing.append((edge, rule))
            for edge, rule in outgoing:
                nxt = edge.target if edge.target != node else edge.source
                # 目标若已在当前路径中（含种子与自环）→ 记录环路，停止该分支
                if nxt in path_nodes:
                    cycles += 1
                    continue
                if max_hops_reached:
                    reason = f"scope.max_hops={scope.max_hops} 截断（已走 {len(path_edges)} 跳）"
                    truncated.append(
                        TruncatedEntry(
                            artifact_id=nxt, relation_type=edge.relation_type, reason=reason
                        )
                    )
                    continue
                in_scope, reason = _in_scope(nxt, kind_of, scope)
                if not in_scope:
                    truncated.append(
                        TruncatedEntry(
                            artifact_id=nxt, relation_type=edge.relation_type, reason=reason
                        )
                    )
                    continue
                new_path = path_edges + (edge,)
                items.append(
                    ImpactItem(
                        target_artifact_id=nxt,
                        severity=rule.severity,
                        impact_kind=rule.impact_kind,
                        path=new_path,
                        reason=_reason_for(seed, new_path),
                    )
                )
                visited_nodes.add(nxt)
                stack.append(((*path_nodes, nxt), new_path))

    stats = ImpactStats(
        seed_count=len(seeds),
        nodes_visited=len(visited_nodes) + len(seeds),
        paths_explored=explored,
        cycles_skipped=cycles,
        truncated_count=len(truncated),
        item_count=len(items),
    )
    return ImpactReport(
        report_id="",  # 由命令层分配
        change_set_id="",
        base_commit_id="",
        items=tuple(items),
        truncated=tuple(truncated),
        stats=stats,
    )


def _reason_for(seed: str, path: tuple[EdgeHop, ...]) -> str:
    """人读的影响原因：种子 + 边类型链（展示与审计共用）。"""
    chain = " -> ".join(f"{hop.relation_type}:{hop.target or hop.source}" for hop in path)
    return f"种子 {seed} 经依赖链传播：{chain}"
