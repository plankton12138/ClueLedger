"""EditMark 注册表测试（P5 T3，计划 D4）：作者草稿锁的内存态。

钉住的不变量：
- mark 记录 holder 与加锁时刻（holder 服务端固定 "user"）；
- 同章重复 acquire 覆盖旧 mark（单用户无竞态，最后一次编辑为准）；
- release 幂等：未持锁再释放无害，返回 False。
"""

from hastings.manuscript.write_guard import EditMarkRegistry


def test_acquire_records_holder_and_ts() -> None:
    marks = EditMarkRegistry()

    mark = marks.acquire("ch03", holder="user", now=lambda: 1_700_000_000.0)

    assert mark.chapter_id == "ch03"
    assert mark.holder == "user"
    assert mark.ts == 1_700_000_000.0
    assert marks.mark_of("ch03") == mark


def test_release_removes_mark_and_reports_whether_held() -> None:
    marks = EditMarkRegistry()
    marks.acquire("ch03")

    assert marks.release("ch03") is True
    assert marks.mark_of("ch03") is None
    assert marks.release("ch03") is False  # 幂等：未持锁再释放无害


def test_reacquire_overwrites_stale_mark() -> None:
    marks = EditMarkRegistry()
    marks.acquire("ch03", now=lambda: 1.0)

    marks.acquire("ch03", now=lambda: 2.0)

    mark = marks.mark_of("ch03")
    assert mark is not None
    assert mark.ts == 2.0
