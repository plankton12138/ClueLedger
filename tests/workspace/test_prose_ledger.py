"""1.2 事实层：正文台账的登记、回退、落盘与确定性对账。

台账由事务层维护、模型只读；对账是装配层第一道防线，纯函数离线可测
（2026-11-03 最小试验：被动注入事实模型不对账 0/3，对账不可下放给模型）。
"""

from __future__ import annotations

import json

from hastings.workspace.prose_ledger import (
    earliest_missing,
    empty_ledger,
    load_ledger,
    reconcile,
    record_chapter,
    remove_chapter,
    save_ledger,
)


def _ledger_with(*chapters: int):
    ledger = empty_ledger()
    for chapter in chapters:
        ledger = record_chapter(ledger, chapter, f"r{chapter}")
    return ledger


def test_空台账无任何登记() -> None:
    ledger = empty_ledger()

    assert ledger.chapters == {}
    assert earliest_missing(ledger, up_to=3) == 1


def test_登记与覆盖取最新修订() -> None:
    ledger = record_chapter(empty_ledger(), 1, "r1")
    ledger = record_chapter(ledger, 1, "r2")
    ledger = record_chapter(ledger, 2, "r5")

    assert ledger.chapters == {1: "r2", 2: "r5"}


def test_撤销回退一章的登记() -> None:
    ledger = _ledger_with(1, 2, 3)

    rolled = remove_chapter(ledger, 2)

    assert rolled.chapters == {1: "r1", 3: "r3"}
    assert remove_chapter(rolled, 9) is rolled  # 未登记章回退是恒等


def test_最早缺章() -> None:
    assert earliest_missing(_ledger_with(1, 2), up_to=4) == 3
    assert earliest_missing(_ledger_with(1, 2, 3, 4), up_to=4) is None
    assert earliest_missing(empty_ledger(), up_to=1) == 1


def test_对账_缺章改派最早缺章() -> None:
    decision = reconcile(_ledger_with(1, 2), target_chapter=4)

    assert decision.action == "redirect"
    assert decision.target_to == 3
    assert decision.skipped == ()
    assert "第 3 章" in decision.reason


def test_对账_无缺章放行() -> None:
    decision = reconcile(_ledger_with(1, 2, 3), target_chapter=4)

    assert decision.action == "write"
    assert decision.target_to == 4
    assert decision.skipped == ()


def test_对账_重写已入档章是合法路径() -> None:
    decision = reconcile(_ledger_with(1, 2), target_chapter=2)

    assert decision.action == "write"
    assert decision.target_to == 2


def test_对账_作者明确跳过放行并记录() -> None:
    decision = reconcile(_ledger_with(1), target_chapter=4, allow_skip=True)

    assert decision.action == "write"
    assert decision.skipped == (2, 3)
    assert "跳过" in decision.reason


def test_对账_缺第一章同样拦截() -> None:
    decision = reconcile(empty_ledger(), target_chapter=1)

    assert decision.action == "write"  # 目标章之前没有章，第 1 章本身就是最早可写章
    assert decision.target_to == 1


def test_台账落盘往返(tmp_path) -> None:
    path = tmp_path / "prose-ledger.json"
    ledger = _ledger_with(1, 2)

    save_ledger(path, ledger)
    restored = load_ledger(path)

    assert restored.chapters == {1: "r1", 2: "r2"}
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema"] == 1
    assert on_disk["chapters"] == {"1": "r1", "2": "r2"}


def test_台账缺失或损坏按空处理(tmp_path) -> None:
    missing = tmp_path / "absent.json"
    broken = tmp_path / "broken.json"
    broken.write_text("{不是 JSON", encoding="utf-8")

    assert load_ledger(missing).chapters == {}
    assert load_ledger(broken).chapters == {}


def test_未知结构的台账不带病运行(tmp_path) -> None:
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"schema": 99, "chapters": {"1": "r1"}}), encoding="utf-8")

    assert load_ledger(path).chapters == {}
