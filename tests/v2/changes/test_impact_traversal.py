"""T15.2 确定性影响遍历测试。

覆盖计划 §6：多跳路径与环路去重、多路径保留、scope 截断。影响分析是
纯函数：不依赖模型、不写 Repository（模块文档 §4.1「确定性传播」）。
"""

from __future__ import annotations

from hastings.domain.changes.impact import EdgeHop, RelationGraph, traverse_impact
from hastings.domain.repository.ids import ArtifactId, CommitId, ProjectId
from hastings.domain.repository.models import RelationRevision, Snapshot


def _graph(*edges: tuple[str, str, str]) -> RelationGraph:
    """从 (source, relation_type, target) 列表构造图，边 ID 自动编号。"""
    return RelationGraph.from_edges(
        tuple(
            EdgeHop(source=s, relation_type=t, target=o, edge_id=f"e{i}")
            for i, (s, t, o) in enumerate(edges)
        )
    )


class TestTraversal:
    def test_multi_hop_reaches_manuscript_with_full_path(self) -> None:
        graph = _graph(("art_c1", "motivates", "art_b1"), ("art_b1", "renders", "art_u1"))
        report = traverse_impact(graph, seeds=("art_c1",))

        targets = {item.target_artifact_id: item for item in report.items}
        assert set(targets) == {"art_b1", "art_u1"}
        beat = targets["art_b1"]
        assert (beat.severity, beat.impact_kind) == ("should_fix", "motivation")
        assert beat.path == (EdgeHop("art_c1", "motivates", "art_b1", "e0"),)
        unit = targets["art_u1"]
        assert (unit.severity, unit.impact_kind) == ("must_fix", "contradiction")
        assert len(unit.path) == 2  # 完整因果路径保留
        assert unit.path[-1] == EdgeHop("art_b1", "renders", "art_u1", "e1")
        assert all(item.status == "open" and item.confidence == 1.0 for item in report.items)
        assert report.stats.nodes_visited == 3 and report.stats.item_count == 2

    def test_cycle_terminates_without_infinite_loop(self) -> None:
        # 全部用 forward 边构造环：a→b→c→a
        graph = _graph(
            ("art_a", "motivates", "art_b"),
            ("art_b", "motivates", "art_c"),
            ("art_c", "motivates", "art_a"),
        )
        report = traverse_impact(graph, seeds=("art_a",))

        assert {item.target_artifact_id for item in report.items} == {"art_b", "art_c"}
        assert report.stats.cycles_skipped >= 1  # 回到种子的边被记录为环路，不再展开

    def test_self_loop_does_not_report_seed(self) -> None:
        graph = _graph(("art_c1", "motivates", "art_c1"))
        report = traverse_impact(graph, seeds=("art_c1",))
        assert report.items == ()
        assert report.stats.cycles_skipped >= 1

    def test_diamond_keeps_both_paths(self) -> None:
        # motivates 与 sets_up 都是 forward 边，从种子分叉后经 renders 汇合到 art_t
        graph = _graph(
            ("art_c1", "motivates", "art_x"),
            ("art_c1", "sets_up", "art_y"),
            ("art_x", "renders", "art_t"),
            ("art_y", "renders", "art_t"),
        )
        report = traverse_impact(graph, seeds=("art_c1",))

        to_t = [item for item in report.items if item.target_artifact_id == "art_t"]
        assert len(to_t) == 2  # 相同目标的两条路径全部保留（展示时可折叠）
        paths = {tuple(h.edge_id for h in item.path) for item in to_t}
        assert len(paths) == 2

    def test_scope_kinds_whitelist_truncates(self) -> None:
        from hastings.domain.changes.commands import ChangeScope

        graph = _graph(("art_c1", "motivates", "art_b1"), ("art_b1", "renders", "art_u1"))
        kinds_of = {
            "art_c1": "story.character",
            "art_b1": "narrative.beat",
            "art_u1": "manuscript.text_unit",
        }
        report = traverse_impact(
            graph,
            seeds=("art_c1",),
            scope=ChangeScope(kinds=("narrative.beat",)),
            kind_of=kinds_of.get,
        )

        assert {item.target_artifact_id for item in report.items} == {"art_b1"}
        assert len(report.truncated) == 1
        assert report.truncated[0].artifact_id == "art_u1"
        assert "scope" in report.truncated[0].reason

    def test_scope_roots_limit_targets(self) -> None:
        from hastings.domain.changes.commands import ChangeScope

        graph = _graph(("art_c1", "motivates", "art_b1"), ("art_b1", "renders", "art_u1"))
        report = traverse_impact(graph, seeds=("art_c1",), scope=ChangeScope(roots=("art_b1",)))

        assert {item.target_artifact_id for item in report.items} == {"art_b1"}
        assert any(t.artifact_id == "art_u1" for t in report.truncated)

    def test_max_hops_truncates_beyond_limit(self) -> None:
        from hastings.domain.changes.commands import ChangeScope

        graph = _graph(("art_c1", "motivates", "art_b1"), ("art_b1", "renders", "art_u1"))
        report = traverse_impact(graph, seeds=("art_c1",), scope=ChangeScope(max_hops=1))

        assert {item.target_artifact_id for item in report.items} == {"art_b1"}
        assert any(t.artifact_id == "art_u1" for t in report.truncated)

    def test_backward_rule_propagates_from_target_to_source(self) -> None:
        # beat --reveals--> fact：事实变了，揭示它的节拍受影响（reveals 是 backward）
        graph = _graph(("art_b1", "reveals", "art_fact"))
        report = traverse_impact(graph, seeds=("art_fact",))

        assert [item.target_artifact_id for item in report.items] == ["art_b1"]
        assert report.items[0].impact_kind == "contradiction"

    def test_forward_traversal_does_not_cross_backward_rule(self) -> None:
        # 从 beat 出发不沿 reveals 前向传播（方向由边类型决定）
        graph = _graph(("art_b1", "reveals", "art_fact"))
        report = traverse_impact(graph, seeds=("art_b1",))
        assert report.items == ()

    def test_mentions_stays_review(self) -> None:
        graph = _graph(("art_c1", "mentions", "art_u1"))
        report = traverse_impact(graph, seeds=("art_c1",))
        assert report.items[0].severity == "review"  # 低置信语义边不升 must_fix
        assert report.items[0].impact_kind == "wording"

    def test_unknown_edge_type_ignored(self) -> None:
        graph = _graph(("art_c1", "supersedes", "art_old"))
        report = traverse_impact(graph, seeds=("art_c1",))
        assert report.items == ()  # supersedes 不是传播边

    def test_no_downstream_is_legal_empty(self) -> None:
        graph = _graph(("art_other", "motivates", "art_b"))
        report = traverse_impact(graph, seeds=("art_lonely",))
        assert report.items == ()  # 合法空结果：保存摘要，不建修订分支
        assert report.stats.seed_count == 1

    def test_rules_are_injectable_policy(self) -> None:
        from hastings.domain.changes.impact import PropagationRule

        custom = (
            PropagationRule(
                "custom_edge", direction="forward", severity="review", impact_kind="style"
            ),
        )
        graph = _graph(("art_c1", "custom_edge", "art_t"))
        report = traverse_impact(graph, seeds=("art_c1",), rules=custom)
        assert report.items[0].severity == "review"
        # 默认规则不含 custom_edge
        default_report = traverse_impact(graph, seeds=("art_c1",))
        assert default_report.items == ()


class TestRelationGraphFromSnapshot:
    def test_from_snapshot_uses_folded_relations(self) -> None:
        edge = RelationRevision(
            id="e0",
            commit_id=CommitId("cmt1"),
            action="add",
            source=ArtifactId("art_c1"),
            relation_type="motivates",
            target=ArtifactId("art_b1"),
        )
        snapshot = Snapshot(
            project_id=ProjectId("p1"), commit_id=CommitId("cmt1"), artifacts={}, relations=(edge,)
        )
        graph = RelationGraph.from_snapshot(snapshot)
        hops = graph.forward("art_c1")
        assert len(hops) == 1
        assert (hops[0].source, hops[0].relation_type, hops[0].target, hops[0].edge_id) == (
            "art_c1",
            "motivates",
            "art_b1",
            "e0",
        )
