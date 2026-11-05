"""章级线索登记最小切片（v1.3 P0/P1）的单元测试。"""

from __future__ import annotations

import json

import pytest

from hastings.workspace.chapter_clues import (
    ClueEntry,
    CluePlan,
    ClueState,
    ResolvesRef,
    aggregate_clues,
    anchor_findings,
    anchor_paragraph,
    clues_path,
    missed_plans,
    open_clue_states,
    para_hash,
    paragraphs,
    parse_planned_clues,
    read_chapter_clues,
    resolve_anchor,
    validate_commit,
    validate_entries,
    write_chapter_clues,
)
from hastings.workspace.errors import WorkspaceError

CH1_TEXT = "老魏把箱子放在桌上。\n箱扣上的漆掉了一块，露出底下的铁色。\n他没再说话。"


def _planted(id_: str = "CL-01", note: str | None = "箱扣缺漆，来历未明") -> ClueEntry:
    return ClueEntry(
        id=id_,
        kind="伏笔",
        status="planted",
        origin="planned",
        note=note,
        anchor=anchor_paragraph("箱扣上的漆掉了一块，露出底下的铁色。", "r1"),
    )


# ---------------------------------------------------------------------------
# 锚定


def test_para_hash_ignores_surrounding_whitespace() -> None:
    assert para_hash("  箱扣上的漆掉了一块。 ") == para_hash("箱扣上的漆掉了一块。")


def test_paragraphs_splits_lines_and_drops_blank() -> None:
    assert paragraphs(CH1_TEXT) == [
        "老魏把箱子放在桌上。",
        "箱扣上的漆掉了一块，露出底下的铁色。",
        "他没再说话。",
    ]


def test_anchor_resolves_against_prose() -> None:
    anchor = anchor_paragraph("箱扣上的漆掉了一块，露出底下的铁色。", "r1")
    assert resolve_anchor(anchor, CH1_TEXT)


def test_anchor_breaks_when_paragraph_rewritten() -> None:
    anchor = anchor_paragraph("箱扣上的漆掉了一块，露出底下的铁色。", "r1")
    rewritten = CH1_TEXT.replace("露出底下的铁色", "露出斑驳的锈迹")
    assert not resolve_anchor(anchor, rewritten)


# ---------------------------------------------------------------------------
# 写入校验（早失败）


def test_valid_entries_pass() -> None:
    validate_entries([_planted()], chapter=1)


def test_planted_requires_anchor() -> None:
    entry = ClueEntry(id="CL-01", kind="伏笔", status="planted", origin="planned")
    with pytest.raises(WorkspaceError) as excinfo:
        validate_entries([entry], chapter=1)
    assert excinfo.value.code == "CLUE_ANCHOR_REQUIRED"


def test_retired_requires_note() -> None:
    entry = ClueEntry(
        id="CL-02",
        kind="信息",
        status="retired",
        origin="planned",
        anchor=anchor_paragraph("他没再说话。", "r1"),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        validate_entries([entry], chapter=1)
    assert excinfo.value.code == "CLUE_RETIRED_NOTE_REQUIRED"


def test_emergent_requires_note() -> None:
    entry = ClueEntry(
        id="EM-1-1",
        kind="物件",
        status="planted",
        origin="emergent",
        anchor=anchor_paragraph("老魏把箱子放在桌上。", "r1"),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        validate_entries([entry], chapter=1)
    assert excinfo.value.code == "CLUE_EMERGENT_NOTE_REQUIRED"


def test_duplicate_id_rejected() -> None:
    with pytest.raises(WorkspaceError) as excinfo:
        validate_entries([_planted(), _planted()], chapter=1)
    assert excinfo.value.code == "CLUE_DUPLICATE_ID"


def test_resolved_requires_resolves() -> None:
    entry = ClueEntry(id="CL-01", kind="伏笔", status="resolved", origin="planned")
    with pytest.raises(WorkspaceError) as excinfo:
        validate_entries([entry], chapter=2)
    assert excinfo.value.code == "CLUE_RESOLVES_REQUIRED"


def test_self_resolution_rejected() -> None:
    entry = ClueEntry(
        id="CL-01",
        kind="伏笔",
        status="resolved",
        origin="planned",
        resolves=ResolvesRef(clue_id="CL-01", planted_in=1),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        validate_entries([entry], chapter=1)
    assert excinfo.value.code == "CLUE_SELF_RESOLUTION"


def test_resolution_ahead_of_chapter_rejected() -> None:
    entry = ClueEntry(
        id="CL-09",
        kind="伏笔",
        status="resolved",
        origin="planned",
        resolves=ResolvesRef(clue_id="CL-01", planted_in=3),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        validate_entries([entry], chapter=2)
    assert excinfo.value.code == "CLUE_RESOLUTION_AHEAD"


def test_same_chapter_resolution_requires_local_planted() -> None:
    resolve_entry = ClueEntry(
        id="CL-01",
        kind="伏笔",
        status="resolved",
        origin="planned",
        resolves=ResolvesRef(
            clue_id="CL-01",
            planted_in=1,
            anchor=anchor_paragraph("箱扣上的漆掉了一块，露出底下的铁色。", "r1"),
        ),
    )
    # 同章自埋自收（同一条目指向自己本章埋设）→ 拒绝
    with pytest.raises(WorkspaceError) as excinfo:
        validate_entries([resolve_entry], chapter=1)
    assert excinfo.value.code == "CLUE_SELF_RESOLUTION"
    # 同章回收他条线索但缺 planted 条目 → 空响
    stray = ClueEntry(
        id="CL-77",
        kind="悬念",
        status="resolved",
        origin="planned",
        resolves=ResolvesRef(clue_id="CL-01", planted_in=1),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        validate_entries([stray], chapter=1)
    assert excinfo.value.code == "CLUE_ORPHAN_RESOLUTION"
    # 同章文件内既有 planted 又有 resolved（两个条目）→ 合法
    planted = _planted()
    resolved = ClueEntry(
        id="CL-01-收",
        kind="伏笔",
        status="resolved",
        origin="planned",
        resolves=ResolvesRef(clue_id="CL-01", planted_in=1),
    )
    validate_entries([planted, resolved], chapter=1)


# ---------------------------------------------------------------------------
# 落盘与读取


def test_write_and_read_roundtrip(tmp_path) -> None:
    write_chapter_clues(tmp_path, 1, "r1", [_planted()])
    document = read_chapter_clues(tmp_path, 1)
    assert document is not None
    assert document.revision == "r1"
    assert document.entries[0].id == "CL-01"
    assert document.entries[0].anchor is not None
    assert resolve_anchor(document.entries[0].anchor, CH1_TEXT)


def test_missing_file_reads_as_none(tmp_path) -> None:
    assert read_chapter_clues(tmp_path, 3) is None


def test_corrupt_file_raises_with_code(tmp_path) -> None:
    path = clues_path(tmp_path, 1)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(WorkspaceError) as excinfo:
        read_chapter_clues(tmp_path, 1)
    assert excinfo.value.code == "CLUE_FILE_CORRUPT"


def test_write_rejects_invalid_entries(tmp_path) -> None:
    entry = ClueEntry(id="CL-01", kind="伏笔", status="planted", origin="planned")
    with pytest.raises(WorkspaceError) as excinfo:
        write_chapter_clues(tmp_path, 1, "r1", [entry])
    assert excinfo.value.code == "CLUE_ANCHOR_REQUIRED"
    assert not clues_path(tmp_path, 1).exists()


def test_file_is_machine_readable_json(tmp_path) -> None:
    write_chapter_clues(tmp_path, 1, "r1", [_planted()])
    raw = json.loads(clues_path(tmp_path, 1).read_text(encoding="utf-8"))
    assert raw["schema"] == 1
    assert raw["chapter"] == 1
    assert raw["entries"][0]["anchor"]["para_prefix"].startswith("箱扣上的漆掉了一块")


# ---------------------------------------------------------------------------
# 聚合与对账


def _retired_style_planted(id_: str, note: str) -> ClueEntry:
    return ClueEntry(
        id=id_,
        kind="信息",
        status="planted",
        origin="planned",
        note=note,
        anchor=anchor_paragraph("他没再说话。", "r1"),
    )


def _write_ch1(tmp_path) -> None:
    write_chapter_clues(
        tmp_path, 1, "r1", [_planted(), _retired_style_planted("CL-04", "老周随身带怀表")]
    )


def _resolve_entry(id_: str, planted_in: int, note: str) -> ClueEntry:
    return ClueEntry(
        id=id_,
        kind="伏笔",
        status="resolved",
        origin="planned",
        note=note,
        resolves=ResolvesRef(clue_id="CL-01", planted_in=planted_in),
    )


def test_cross_chapter_resolution_merges(tmp_path) -> None:
    _write_ch1(tmp_path)
    write_chapter_clues(tmp_path, 5, "r5", [_resolve_entry("CL-01", 1, "老魏说出箱子来历")])
    aggregate = aggregate_clues(tmp_path, [1, 5])
    state = aggregate.states["CL-01"]
    assert state.status == "resolved"
    assert state.planted_in == 1
    assert state.resolved_in == 5
    assert not any(f.code == "CLUE_ORPHAN_RESOLUTION" for f in aggregate.findings)


def test_orphan_resolution_reported(tmp_path) -> None:
    write_chapter_clues(
        tmp_path,
        2,
        "r2",
        [_resolve_entry("CL-99", 1, "无中生有的回收")],
    )
    aggregate = aggregate_clues(tmp_path, [2])
    assert any(f.code == "CLUE_ORPHAN_RESOLUTION" for f in aggregate.findings)


def test_orphaned_anchor_detected_against_prose(tmp_path) -> None:
    _write_ch1(tmp_path)
    rewritten = CH1_TEXT.replace("露出底下的铁色", "露出斑驳的锈迹")
    aggregate = aggregate_clues(tmp_path, [1], prose_by_chapter={1: rewritten})
    assert any(f.code == "CLUE_ORPHANED_ANCHOR" for f in aggregate.findings)
    intact = aggregate_clues(tmp_path, [1], prose_by_chapter={1: CH1_TEXT})
    assert not any(f.code == "CLUE_ORPHANED_ANCHOR" for f in intact.findings)


def test_not_planted_and_unresolved_reported(tmp_path) -> None:
    _write_ch1(tmp_path)
    plans = [
        CluePlan(id="CL-01", plant_chapter=1, resolve_chapter=2),
        CluePlan(id="CL-03", plant_chapter=2, resolve_chapter=5),
        CluePlan(id="CL-04", plant_chapter=1, resolve_chapter=None),
    ]
    aggregate = aggregate_clues(tmp_path, [1, 2], plans=plans)
    codes = {f.code for f in aggregate.findings}
    assert "CLUE_NOT_PLANTED" in codes  # CL-03 哑火
    assert "CLUE_UNRESOLVED" in codes  # CL-01 悬枪（计划第 2 章收）
    # CL-04 只声明埋、未声明收，且已落实 → 不报
    assert all("CL-04" not in f.message for f in aggregate.findings)


def test_retired_stops_the_gun(tmp_path) -> None:
    _write_ch1(tmp_path)
    write_chapter_clues(
        tmp_path,
        2,
        "r2",
        [
            ClueEntry(
                id="CL-01",
                kind="伏笔",
                status="retired",
                origin="planned",
                note="作者裁决：改为纯细节不收",
            )
        ],
    )
    aggregate = aggregate_clues(
        tmp_path, [1, 2], plans=[CluePlan(id="CL-01", plant_chapter=1, resolve_chapter=3)]
    )
    assert aggregate.states["CL-01"].status == "retired"
    assert not any(f.code == "CLUE_UNRESOLVED" for f in aggregate.findings)


def test_missing_chapter_file_is_silent_in_aggregate(tmp_path) -> None:
    _write_ch1(tmp_path)
    aggregate = aggregate_clues(tmp_path, [1, 2, 3])
    assert set(aggregate.states) == {"CL-01", "CL-04"}
    assert not aggregate.findings


# ---------------------------------------------------------------------------
# 提交门（空响拦截点）


def test_commit_gate_blocks_orphan_resolution(tmp_path) -> None:
    _write_ch1(tmp_path)
    stray = ClueEntry(
        id="CL-99",
        kind="悬念",
        status="resolved",
        origin="planned",
        resolves=ResolvesRef(clue_id="CL-99", planted_in=1),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        validate_commit(tmp_path, 2, [stray])
    assert excinfo.value.code == "CLUE_ORPHAN_RESOLUTION"


def test_commit_gate_allows_cross_chapter_resolution(tmp_path) -> None:
    _write_ch1(tmp_path)
    validate_commit(tmp_path, 5, [_resolve_entry("CL-01", 1, "老魏说出箱子来历")])


def test_commit_gate_allows_same_chapter_pair(tmp_path) -> None:
    validate_commit(
        tmp_path,
        1,
        [
            _planted(),
            ClueEntry(
                id="CL-01-收",
                kind="伏笔",
                status="resolved",
                origin="planned",
                resolves=ResolvesRef(clue_id="CL-01", planted_in=1),
            ),
        ],
    )


def test_commit_gate_checks_chapter_rules_first(tmp_path) -> None:
    entry = ClueEntry(id="CL-01", kind="伏笔", status="planted", origin="planned")
    with pytest.raises(WorkspaceError) as excinfo:
        validate_commit(tmp_path, 1, [entry])
    assert excinfo.value.code == "CLUE_ANCHOR_REQUIRED"


# ---------------------------------------------------------------------------
# 结构层「计划线索」解析与计划未落实（P1）


PLAN_STRUCTURE = """## 铁律
- 短句。

## 全书结构
第一卷：旧案重提

## 计划线索
CL-01 箱扣缺漆：第1章埋、第5章收
- CL-03 来的人：第 2 章埋
随便写的一行，没有编号
CL-04 老花镜：第2章埋
"""


def test_parse_planned_clues_from_section() -> None:
    plans = parse_planned_clues(PLAN_STRUCTURE)
    assert [(p.id, p.plant_chapter, p.resolve_chapter) for p in plans] == [
        ("CL-01", 1, 5),
        ("CL-03", 2, None),
        ("CL-04", 2, None),
    ]
    assert plans[0].note == "箱扣缺漆"


def test_parse_planned_clues_stops_at_next_section() -> None:
    text = "## 计划线索\nCL-01 箱子：第1章埋\n## 其他\nCL-02 不该被读到：第1章埋\n"
    plans = parse_planned_clues(text)
    assert [p.id for p in plans] == ["CL-01"]


def test_parse_planned_clues_skips_malformed_and_duplicates() -> None:
    text = "## 计划线索\nCL-01 有编号没埋章\nCL-02 正常：第1章埋\nCL-02 重复：第2章埋\n"
    plans = parse_planned_clues(text)
    assert [(p.id, p.plant_chapter) for p in plans] == [("CL-02", 1)]


def test_parse_planned_clues_without_section_is_empty() -> None:
    assert parse_planned_clues("## 铁律\n- 短句。\n") == []


def test_missed_plans_reports_plant_and_resolve() -> None:
    plans = [
        CluePlan(id="CL-01", plant_chapter=2, resolve_chapter=5, note="箱扣缺漆"),
        CluePlan(id="CL-02", plant_chapter=2, resolve_chapter=2),
        CluePlan(id="CL-03", plant_chapter=3),
    ]
    findings = missed_plans(plans, 2, [_planted("CL-02")])
    assert {f.code for f in findings} == {"CLUE_PLAN_MISSED"}
    messages = " ".join(f.message for f in findings)
    assert "CL-01" in messages and "CL-03" not in messages


def test_missed_plans_quiet_when_registered() -> None:
    plans = [CluePlan(id="CL-01", plant_chapter=2)]
    assert missed_plans(plans, 2, [_planted("CL-01")]) == []
    assert missed_plans(plans, 3, []) == []


# ---------------------------------------------------------------------------
# P2：聚合容错与起草清单


def test_anchor_findings_prefix_helps_relocate() -> None:
    entry = _planted()
    rewritten = CH1_TEXT.replace("露出底下的铁色", "露出斑驳的锈迹")
    findings = anchor_findings(1, [entry], rewritten)
    assert [f.code for f in findings] == ["CLUE_ORPHANED_ANCHOR"]
    assert "箱扣上的漆掉了一块" in findings[0].message
    assert anchor_findings(1, [entry], CH1_TEXT) == []


def test_aggregate_reports_corrupt_file_without_raising(tmp_path) -> None:
    _write_ch1(tmp_path)
    path = clues_path(tmp_path, 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 不是 json", encoding="utf-8")
    aggregate = aggregate_clues(tmp_path, [1, 2])
    assert "CL-01" in aggregate.states  # 好文件仍然计入
    corrupt = [f for f in aggregate.findings if f.code == "CLUE_FILE_CORRUPT"]
    assert corrupt and "第 2 章" in corrupt[0].message


def _state(id_: str, status: str, planted_in: int) -> ClueState:
    return ClueState(
        id=id_,
        kind="伏笔",
        origin="planned",
        status=status,
        planted_in=planted_in,
        last_chapter=planted_in,
    )


def test_open_clue_states_excludes_resolved_and_retired() -> None:
    states = {
        "CL-01": _state("CL-01", "planted", 1),
        "CL-02": _state("CL-02", "resolved", 1),
        "CL-03": _state("CL-03", "retired", 2),
        "CL-04": _state("CL-04", "advanced", 2),
    }
    opened, total = open_clue_states(states, 3)
    assert [state.id for state in opened] == ["CL-01", "CL-04"]
    assert total == 2


def test_open_clue_states_prefers_near_resolution_then_age() -> None:
    states = {
        "CL-09": _state("CL-09", "planted", 3),
        "CL-01": _state("CL-01", "planted", 1),
        "CL-05": _state("CL-05", "planted", 2),
    }
    plans = [
        CluePlan(id="CL-09", plant_chapter=3, resolve_chapter=4),
        CluePlan(id="CL-05", plant_chapter=2, resolve_chapter=6),
    ]
    opened, _ = open_clue_states(states, 3, plans)
    assert [state.id for state in opened] == ["CL-09", "CL-05", "CL-01"]


def test_open_clue_states_truncates_and_reports_total() -> None:
    states = {f"CL-{i:02d}": _state(f"CL-{i:02d}", "planted", 1) for i in range(1, 6)}
    opened, total = open_clue_states(states, 3, limit=2)
    assert len(opened) == 2
    assert total == 5


def test_open_clue_states_ignores_later_chapters() -> None:
    states = {"CL-01": _state("CL-01", "planted", 5)}
    assert open_clue_states(states, 3) == ([], 0)
