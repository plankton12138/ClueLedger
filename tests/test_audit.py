"""审计报告聚合测试（P7 T8，计划 D7）：三指标口径 + 四条检查规则 + 泄压阀。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.bible.audit import build_audit_report
from hastings.bible.models import Clue, Foreshadow, Method, TimelineEntry
from hastings.bible.store import StoryWorkspace, save_ledger
from hastings.server.app import create_app
from test_server_app import stub_registry

SLUG = "orient-express"


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    ws = StoryWorkspace(root=tmp_path, slug=SLUG)
    ws.ensure_dirs()
    return ws


def _chapter(workspace: StoryWorkspace, chapter_id: str) -> None:
    # 测试播种直接落盘（save_chapter 是受管更新语义，要求章节已存在）
    workspace.chapters_dir.mkdir(parents=True, exist_ok=True)
    path = workspace.chapters_dir / f"{chapter_id}.md"
    path.write_text(f"# {chapter_id}\n\n正文。", encoding="utf-8")


def test_empty_workspace_is_legal_zero(workspace: StoryWorkspace) -> None:
    _chapter(workspace, "ch01")
    report = build_audit_report(workspace, "ch01")
    assert report.foreshadow_recovery.rate == 0.0
    assert report.foreshadow_recovery.total == 0
    assert report.clue_fairness.total == 0
    assert report.character_consistency is None  # 空时间线 → None（D7 口径）
    assert report.checks == []


def _seed_scenario(workspace: StoryWorkspace) -> None:
    """三指标 + 四规则的标准剧本：3 章、3 伏笔、4 线索、2+1 时间线、1 手法。"""
    for cid in ("ch01", "ch02", "ch03"):
        _chapter(workspace, cid)
    save_ledger(
        workspace.foreshadows_path,
        [
            Foreshadow(name="怀表", status="resolved", expected_resolve_chapter="ch02"),
            Foreshadow(
                name="密室钥匙",
                status="planted",
                plant_chapter="ch01",
                expected_resolve_chapter="ch02",
            ),
            Foreshadow(
                name="烟灰缸",
                status="planted",
                plant_chapter="ch01",
                expected_resolve_chapter="ch05",
            ),
        ],
    )
    save_ledger(
        workspace.clues_path,
        [
            Clue(name="车票", status="interpreted", chapter="ch01"),
            Clue(name="手枪", status="resolved", chapter="ch04"),
            Clue(name="指纹", status="interpreted", chapter=""),
            Clue(name="诱饵", is_red_herring=True, status="resolved", chapter="ch03"),
        ],
    )
    save_ledger(
        workspace.timeline_path,
        [
            TimelineEntry(
                name="宴席",
                day=1,
                t_start="20:00",
                t_end="22:00",
                chapter="ch01",
                participants=["张三"],
            ),
            TimelineEntry(
                name="密室",
                day=1,
                t_start="21:00",
                t_end="23:00",
                chapter="ch02",
                participants=["张三"],
            ),
        ],
    )
    save_ledger(workspace.method_path, [Method(name="密室诡计", status="drafted")])


def test_scenario_metrics(workspace: StoryWorkspace) -> None:
    _seed_scenario(workspace)
    report = build_audit_report(workspace, "ch02")
    # 回收率（stats 复用，全工作区口径）：1/3
    recovery = report.foreshadow_recovery
    assert (recovery.rate, recovery.count, recovery.total) == (pytest.approx(1 / 3), 1, 3)
    # 线索公平性（D7 字面口径）：非红鲱鱼 3 条中 status != planned 的占比
    # （车票/手枪/指纹 均已推进）→ 3/3；呈现时点是否早于揭晓由 clue_planting 检查管
    fairness = report.clue_fairness
    assert (fairness.rate, fairness.count, fairness.total) == (pytest.approx(1.0), 3, 3)
    # 人物一致性：2 条时间线 1 个 R1 冲突 → 1 - 1/2
    assert report.character_consistency is not None
    assert report.character_consistency.rate == pytest.approx(0.5)
    assert (report.character_consistency.conflicts, report.character_consistency.entries) == (1, 2)


def test_scenario_checks_order_and_anchors(workspace: StoryWorkspace) -> None:
    _seed_scenario(workspace)
    report = build_audit_report(workspace, "ch02")
    rules = [c.rule for c in report.checks]
    # 顺序固定（D7）：timeline_conflict → foreshadow_overdue → clue_planting → method_state
    assert rules == [
        "timeline_conflict",
        "foreshadow_overdue",
        "clue_planting",
        "clue_planting",
        "method_state",
    ]

    conflict, overdue, planting_late, planting_missing, method_state = report.checks
    # R1（分身重叠）→ warn；anchor 查首个涉及条目的叙述章
    assert conflict.severity == "warn"
    assert conflict.anchor_name == "宴席"
    assert conflict.anchor_chapter == "ch01"
    # 逾期伏笔 → error，锚定预计回收章
    assert overdue.severity == "error"
    assert overdue.anchor_name == "密室钥匙"
    assert overdue.anchor_chapter == "ch02"
    assert "密室钥匙" in overdue.message and "怎样算对" in overdue.message
    # 公平呈现：手枪（ch04 > 审计章 ch02）与指纹（章节缺失）各一条 → warn
    assert planting_late.severity == "warn"
    assert planting_late.anchor_name == "手枪"
    assert planting_late.anchor_chapter == "ch04"
    assert planting_missing.anchor_name == "指纹"
    assert planting_missing.anchor_chapter is None
    # 手法状态：有 resolved 伏笔而手法全 drafted → warn
    assert method_state.severity == "warn"
    assert method_state.rule == "method_state"
    assert method_state.anchor_name == "密室诡计"


def test_method_state_fires_without_any_method(workspace: StoryWorkspace) -> None:
    _chapter(workspace, "ch01")
    save_ledger(workspace.foreshadows_path, [Foreshadow(name="怀表", status="resolved")])
    report = build_audit_report(workspace, "ch01")
    state = [c for c in report.checks if c.rule == "method_state"]
    assert len(state) == 1
    assert state[0].severity == "warn"
    assert "无手法登记" in state[0].message


def test_no_method_state_when_revealed_or_unresolved(workspace: StoryWorkspace) -> None:
    _chapter(workspace, "ch01")
    save_ledger(workspace.foreshadows_path, [Foreshadow(name="怀表", status="planted")])
    planted = build_audit_report(workspace, "ch01").checks
    assert [c for c in planted if c.rule == "method_state"] == []
    save_ledger(
        workspace.foreshadows_path,
        [Foreshadow(name="怀表", status="resolved"), Foreshadow(name="旧表", status="resolved")],
    )
    save_ledger(workspace.method_path, [Method(name="密室诡计", status="revealed")])
    revealed = build_audit_report(workspace, "ch01").checks
    assert [c for c in revealed if c.rule == "method_state"] == []


def test_r0_conflict_is_error_and_audit_chapter_unparseable(workspace: StoryWorkspace) -> None:
    _chapter(workspace, "ch01")
    save_ledger(
        workspace.timeline_path,
        [TimelineEntry(name="坏数据", day=0, chapter="ch01", participants=["张三"])],  # R0：day<1
    )
    save_ledger(workspace.clues_path, [Clue(name="手枪", status="resolved", chapter="ch01")])
    # 审计章非 chNN（序章）→ 阈值按 0：任何已揭晓线索都算未公平呈现
    report = build_audit_report(workspace, "prologue")
    r0 = [c for c in report.checks if c.rule == "timeline_conflict"]
    assert len(r0) == 1
    assert r0[0].severity == "error"
    planting = [c for c in report.checks if c.rule == "clue_planting"]
    assert len(planting) == 1
    assert planting[0].anchor_name == "手枪"


# ---------- REST 面：泄压阀（_session 守门 + 404/422） ----------


def _client(root: Path) -> TestClient:
    return TestClient(create_app(stub_registry(root)))


def test_audit_endpoint_404_and_422(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get(f"/api/{SLUG}/audit/ch99")
    assert resp.status_code == 404
    assert "怎样算对" in resp.json()["detail"]
    resp = client.get(f"/api/{SLUG}/audit/bad.id")
    assert resp.status_code == 422
    assert "怎样算对" in resp.json()["detail"]
