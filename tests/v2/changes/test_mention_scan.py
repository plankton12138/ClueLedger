"""T15.3 正文引用扫描（mentions）纯领域测试。

验证 modules/change-impact.md §4.1 第二阶段的第一块约束：
- 显式结构边优先：确定性已覆盖的段落不再重复报告；
- mentions 永不 must_fix：高置信 should_fix、低置信 review；
- source/confidence 可追溯：项携带 text_unit_id，reason 记别名与置信度；
- 无命中是合法空结果。
"""

from __future__ import annotations

from hastings.domain.changes.impact import (
    SEVERITY_MUST_FIX,
    SEVERITY_REVIEW,
    SEVERITY_SHOULD_FIX,
    ImpactItem,
)
from hastings.domain.changes.mentions import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXPLICIT,
    MentionCandidate,
    MentionScanStats,
    TextUnitRef,
    scan_text_mentions,
)

CULPRIT = MentionCandidate(artifact_id="art_culprit", aliases=("雾岛彻", "岛爷"))
HELPER = MentionCandidate(artifact_id="art_helper", aliases=("林岚",))


def _units(*texts: str) -> tuple[TextUnitRef, ...]:
    return tuple(TextUnitRef(text_unit_id=f"tu_{n + 1:02d}", text=t) for n, t in enumerate(texts))


def test_explicit_alias_hit_is_should_fix_with_confidence() -> None:
    result = scan_text_mentions((CULPRIT,), _units("雾岛彻把怀表放回口袋。"))

    assert result.stats.hit_count == 1
    item = result.items[0]
    assert item.target_artifact_id == "tu_01"
    assert item.text_unit_id == "tu_01"  # source 可追溯
    assert item.severity == SEVERITY_SHOULD_FIX
    assert item.confidence == CONFIDENCE_EXPLICIT
    assert item.impact_kind == "wording"
    assert "雾岛彻" in item.reason  # 依据别名记录在案


def test_single_char_alias_is_low_confidence_review() -> None:
    """单字别名歧义（中文无词边界）：降 review，不冒充确定也不丢弃。"""
    single = MentionCandidate(artifact_id="art_single", aliases=("岚",))
    result = scan_text_mentions((single,), _units("山岚之间有一道灯火。"))

    assert result.items[0].severity == SEVERITY_REVIEW
    assert result.items[0].confidence == CONFIDENCE_AMBIGUOUS


def test_multiple_aliases_take_strongest() -> None:
    """多别名同时命中：取置信度最高、并列取最长者，每（段落，候选）至多一项。"""
    result = scan_text_mentions((CULPRIT,), _units("雾岛彻——大家都叫他岛爷。"))

    assert result.stats.hit_count == 1
    assert "雾岛彻" in result.items[0].reason  # 2 字别名与 2 字别名并列，先声明者优先


def test_covered_units_are_skipped_not_duplicated() -> None:
    """renders 等结构信号优先：确定性已覆盖的段落不产生重复 mention 项。"""
    result = scan_text_mentions(
        (CULPRIT, HELPER),
        _units("雾岛彻出现。", "林岚不在场。"),
        covered_ids=frozenset({"tu_01"}),
    )

    assert [i.target_artifact_id for i in result.items] == ["tu_02"]
    assert result.stats.skipped_covered == 1
    assert result.stats.unit_count == 2


def test_multiple_candidates_in_same_unit_are_separate_items() -> None:
    result = scan_text_mentions((HELPER, CULPRIT), _units("林岚跟着雾岛彻走进门厅。"))

    assert [i.target_artifact_id for i in result.items] == ["tu_01", "tu_01"]
    assert {i.mentioned_artifact_id for i in result.items} == {"art_helper", "art_culprit"}
    assert {i.reason for i in result.items} >= {"正文引用「林岚」（置信 0.9）"}
    assert all(i.confidence == CONFIDENCE_EXPLICIT for i in result.items)


def test_no_hits_is_legal_empty_result() -> None:
    result = scan_text_mentions((CULPRIT,), _units("今天天气很好。", "院里的猫睡了。"))

    assert result.items == ()
    assert result.stats.hit_count == 0
    assert result.stats.unit_count == 2


def test_empty_inputs_are_legal_empty_result() -> None:
    no_candidates = scan_text_mentions((), _units("任意文本"))
    no_units = scan_text_mentions((CULPRIT,), ())

    assert no_candidates.items == () and no_units.items == ()
    assert no_candidates.stats == MentionScanStats(unit_count=1)
    assert no_units.stats == MentionScanStats(candidate_count=1)


def test_mention_items_never_must_fix() -> None:
    """硬规则：mentions 任何置信度都不产生 must_fix（语义不冒充结构必然）。"""
    result = scan_text_mentions((CULPRIT, HELPER), _units("雾岛彻与林岚、岚。"))

    assert result.items
    assert all(item.severity != SEVERITY_MUST_FIX for item in result.items)


def test_items_sorted_deterministically() -> None:
    """跨段落与候选的输出顺序确定：按（段落 ID, 候选 ID）排序。"""
    units = _units("林岚在场。", "雾岛彻也在。")
    result = scan_text_mentions((CULPRIT, HELPER), units)

    assert [i.target_artifact_id for i in result.items] == ["tu_01", "tu_02"]
    assert "林岚" in result.items[0].reason
    assert "雾岛彻" in result.items[1].reason


def test_alias_superset_takes_longer_match() -> None:
    """长短别名并存且都命中：取更长的（更具体），不以短名重复计项。"""
    family = MentionCandidate(artifact_id="art_family", aliases=("岛", "岛爷"))
    result = scan_text_mentions((family,), _units("岛爷坐在窗边。"))

    assert result.stats.hit_count == 1
    assert "岛爷" in result.items[0].reason
    assert result.items[0].severity == SEVERITY_SHOULD_FIX


def test_must_fix_only_from_structural_edges() -> None:
    """对照：结构项（确定性遍历产物）才是 must_fix；语义项与之共存不覆盖。"""
    structural = ImpactItem(
        target_artifact_id="tu_01",
        severity=SEVERITY_MUST_FIX,
        impact_kind="contradiction",
        path=(),
        reason="renders 边传播",
    )
    scan = scan_text_mentions((CULPRIT,), _units("雾岛彻冷笑。"), covered_ids=frozenset({"tu_01"}))

    assert structural.severity == SEVERITY_MUST_FIX  # 结构信号保持不变
    assert scan.items == ()  # 同段落的语义观察被跳过，而不是降级合并
