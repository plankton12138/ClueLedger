"""timeline_check 纯函数单测：R0/R1/R2 逐规则命中 + 干净账本零误报（P2 T5）。"""

from hastings.bible.models import AlibiClaim, Method, TimelineEntry
from hastings.bible.timeline_check import TimelineConflict, check_timeline


def _entry(
    name: str,
    day: int,
    participants: list[str],
    t_start: str | None = None,
    t_end: str | None = None,
    chapter: str = "ch01",
) -> TimelineEntry:
    return TimelineEntry(
        name=name,
        day=day,
        t_start=t_start,
        t_end=t_end,
        participants=participants,
        chapter=chapter,
    )


def _claim(
    person: str, day: int, t_start: str | None = None, t_end: str | None = None
) -> AlibiClaim:
    return AlibiClaim(person=person, day=day, t_start=t_start, t_end=t_end)


def _method(name: str, claims: list[AlibiClaim]) -> Method:
    return Method(name=name, alibi_claims=claims)


# ---------- R0 数据自检 ----------


def test_r0_rejects_day_below_one() -> None:
    conflicts = check_timeline([_entry("晚宴", 0, ["甲"])])

    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.rule == "R0"
    assert "day=0" in c.message
    assert c.entries == ("晚宴",)


def test_r0_rejects_bad_time_format() -> None:
    conflicts = check_timeline([_entry("晚宴", 3, ["甲"], t_start="9点", t_end="10:00")])

    assert len(conflicts) == 1
    assert conflicts[0].rule == "R0"
    assert "t_start" in conflicts[0].message
    assert "HH:MM" in conflicts[0].message


def test_r0_rejects_crossing_midnight() -> None:
    conflicts = check_timeline([_entry("守夜", 3, ["甲"], t_start="23:00", t_end="01:00")])

    assert len(conflicts) == 1
    assert conflicts[0].rule == "R0"
    assert "跨零点" in conflicts[0].message


def test_r0_rejects_zero_length_interval() -> None:
    """t_start == t_end 是零长区间，与任何时段恒不相交，必须当作数据错误拒收。"""
    conflicts = check_timeline([_entry("晚宴", 3, ["甲"], t_start="21:00", t_end="21:00")])

    assert len(conflicts) == 1
    assert conflicts[0].rule == "R0"
    assert "零长" in conflicts[0].message
    assert "21:00" in conflicts[0].message


def test_r0_rejects_zero_length_alibi_claim() -> None:
    method = _method("密室诡计", [_claim("乙", 3, t_start="21:00", t_end="21:00")])
    conflicts = check_timeline([], [method])

    assert len(conflicts) == 1
    assert conflicts[0].rule == "R0"
    assert "零长" in conflicts[0].message


def test_r0_reports_alibi_claim_data_errors() -> None:
    method = _method("密室诡计", [_claim("乙", 0, t_start="25:00")])
    conflicts = check_timeline([], [method])

    assert len(conflicts) == 2  # day 与时刻两个数据错各自上报
    assert all(c.rule == "R0" for c in conflicts)
    assert all("密室诡计" in c.message and "乙" in c.message for c in conflicts)
    assert any("day=0" in c.message for c in conflicts)
    assert any("25:00" in c.message for c in conflicts)


# ---------- R1 人物分身 ----------


def test_r1_overlap_same_person_same_day() -> None:
    entries = [
        _entry("晚宴", 3, ["甲"], "18:00", "20:00"),
        _entry("书房密会", 3, ["甲"], "19:30", "21:00"),
    ]
    conflicts = check_timeline(entries)

    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.rule == "R1"
    assert c.day == 3
    assert c.persons == ("甲",)
    assert set(c.entries) == {"晚宴", "书房密会"}
    assert "19:30" in c.message


def test_r1_touching_intervals_not_overlap() -> None:
    entries = [
        _entry("晚宴", 3, ["甲"], "18:00", "20:00"),
        _entry("书房密会", 3, ["甲"], "20:00", "21:00"),
    ]
    assert check_timeline(entries) == []


def test_r1_same_person_different_days_ok() -> None:
    entries = [
        _entry("晚宴", 3, ["甲"], "18:00", "20:00"),
        _entry("书房密会", 4, ["甲"], "18:00", "20:00"),
    ]
    assert check_timeline(entries) == []


def test_r1_different_persons_same_day_ok() -> None:
    entries = [
        _entry("晚宴", 3, ["甲"], "18:00", "20:00"),
        _entry("书房密会", 3, ["乙"], "19:30", "21:00"),
    ]
    assert check_timeline(entries) == []


def test_r1_missing_times_treated_as_full_day() -> None:
    entries = [
        _entry("抵达庄园", 3, ["甲"]),
        _entry("书房密会", 3, ["甲"], "22:00", "23:00"),
    ]
    conflicts = check_timeline(entries)

    assert len(conflicts) == 1
    assert conflicts[0].rule == "R1"
    assert "全天" in conflicts[0].message


def test_r1_skips_entry_with_invalid_time() -> None:
    """含 R0 错误的条目区间不可信，只报 R0 不报 R1。"""
    entries = [
        _entry("晚宴", 3, ["甲"], "18点", "20:00"),
        _entry("书房密会", 3, ["甲"], "19:00", "21:00"),
    ]
    conflicts = check_timeline(entries)

    assert len(conflicts) == 1
    assert conflicts[0].rule == "R0"


def test_r1_skips_entry_with_crossing_midnight_times() -> None:
    """跨零点条目的倒挂区间不可信：与全天事件不得误报 R1（R3 #1）。"""
    entries = [
        _entry("守夜", 3, ["甲"], "23:00", "01:00"),
        _entry("巡场", 3, ["甲"]),  # 缺时刻按全天处理，倒挂区间与全天必"相交"
    ]
    conflicts = check_timeline(entries)

    assert len(conflicts) == 1
    assert conflicts[0].rule == "R0"


def test_r1_skips_entries_with_day_below_one() -> None:
    """day<1 的条目同样不参与 R1：两条 day=0 事件重叠只各自报 R0。"""
    entries = [
        _entry("事件甲", 0, ["甲"], "18:00", "20:00"),
        _entry("事件乙", 0, ["甲"], "19:00", "21:00"),
    ]
    conflicts = check_timeline(entries)

    assert len(conflicts) == 2
    assert all(c.rule == "R0" for c in conflicts)


def test_r1_duplicate_participant_in_one_entry_no_self_conflict() -> None:
    entries = [_entry("晚宴", 3, ["甲", "甲"], "18:00", "20:00")]
    assert check_timeline(entries) == []


# ---------- R2 不在场破绽（D3 方案 A） ----------


def test_r2_alibi_intersects_real_event() -> None:
    entries = [_entry("晚宴", 3, ["乙"], "18:00", "21:00")]
    method = _method("密室诡计", [_claim("乙", 3, "20:00", "22:00")])
    conflicts = check_timeline(entries, [method])

    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.rule == "R2"
    assert c.day == 3
    assert "密室诡计" in c.message
    assert "晚宴" in c.message


def test_r2_touching_claim_ok() -> None:
    entries = [_entry("晚宴", 3, ["乙"], "18:00", "20:00")]
    method = _method("密室诡计", [_claim("乙", 3, "20:00", "22:00")])
    assert check_timeline(entries, [method]) == []


def test_r2_claim_day_without_events_ok() -> None:
    method = _method("密室诡计", [_claim("乙", 5, "20:00", "22:00")])
    assert check_timeline([], [method]) == []


def test_r2_claim_person_not_in_participants_ok() -> None:
    entries = [_entry("晚宴", 3, ["甲"], "18:00", "21:00")]
    method = _method("密室诡计", [_claim("丙", 3, "20:00", "22:00")])
    assert check_timeline(entries, [method]) == []


def test_r2_missing_claim_times_full_day() -> None:
    entries = [_entry("晚宴", 3, ["乙"], "18:00", "21:00")]
    method = _method("密室诡计", [_claim("乙", 3)])
    conflicts = check_timeline(entries, [method])

    assert len(conflicts) == 1
    assert conflicts[0].rule == "R2"


def test_r2_skips_claim_with_crossing_midnight_times() -> None:
    """跨零点主张的倒挂区间不可信：不与真实事件判 R2（R3 #1）。"""
    entries = [_entry("晚宴", 3, ["乙"], "18:00", "21:00")]
    method = _method("密室诡计", [_claim("乙", 3, "23:00", "01:00")])
    conflicts = check_timeline(entries, [method])

    assert len(conflicts) == 1
    assert conflicts[0].rule == "R0"


# ---------- 干净账本 ----------


def test_clean_timeline_zero_conflicts() -> None:
    entries = [
        _entry("晚宴", 3, ["甲", "乙"], "18:00", "20:00", chapter="ch02"),
        _entry("书房密会", 3, ["甲"], "20:00", "21:00"),
        _entry("晨间散步", 4, ["乙"], "07:00", "08:00"),
    ]
    method = _method(
        "密室诡计",
        [
            _claim("乙", 3, "20:30", "22:00"),
            _claim(
                "甲",
                4,
                "07:00",
                "08:00",
            ),
        ],
    )
    assert check_timeline(entries, [method]) == []


def test_conflicts_have_required_fields() -> None:
    conflicts = check_timeline([_entry("晚宴", 3, ["甲"], "18:00", "20:00")])
    result = check_timeline(
        [
            _entry("晚宴", 3, ["甲"], "18:00", "20:00"),
            _entry("书房密会", 3, ["甲"], "19:00", "21:00"),
        ]
    )

    assert conflicts == []
    c: TimelineConflict = result[0]
    assert isinstance(c.message, str) and c.message
    assert isinstance(c.persons, tuple)
    assert isinstance(c.entries, tuple)
