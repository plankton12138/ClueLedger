"""T14.1 Edit Operation 的规范化与稳定 hunk ID。

同一个操作在任意次执行/重试中必须得到同一个 hunk_id（稳定身份），
内容不同则必须得到不同 id；这是 T14.4 Patch 可追踪、可重放的前提。
"""

from __future__ import annotations

from hastings.domain.manuscript import operations


def test_replace_hunk_id_is_stable() -> None:
    op = operations.Replace(unit_id="art_u2", text="改后")
    first = operations.normalize_operation(op)
    second = operations.normalize_operation(op)
    assert first.hunk_id == second.hunk_id
    assert first.kind == operations.OP_REPLACE
    assert first.unit_ids == ("art_u2",)


def test_different_text_yields_different_hunk_id() -> None:
    a = operations.normalize_operation(operations.Replace(unit_id="art_u2", text="甲"))
    b = operations.normalize_operation(operations.Replace(unit_id="art_u2", text="乙"))
    assert a.hunk_id != b.hunk_id


def test_different_target_yields_different_hunk_id() -> None:
    a = operations.normalize_operation(operations.Replace(unit_id="art_u2", text="甲"))
    b = operations.normalize_operation(operations.Replace(unit_id="art_u3", text="甲"))
    assert a.hunk_id != b.hunk_id


def test_insert_hunk_records_anchor() -> None:
    hunk = operations.normalize_operation(operations.Insert(after_unit_id="art_u1", text="新段"))
    assert hunk.kind == operations.OP_INSERT
    assert hunk.anchor_unit_id == "art_u1"
    assert hunk.unit_ids == ()


def test_move_hunk_targets_unit_and_anchor() -> None:
    hunk = operations.normalize_operation(operations.Move(unit_id="art_u3", after_unit_id=None))
    assert hunk.kind == operations.OP_MOVE
    assert hunk.unit_ids == ("art_u3",)
    assert hunk.anchor_unit_id is None


def test_modified_unit_ids_excludes_insert_anchor() -> None:
    # insert 只引用锚点、不修改既有段落，不参与重叠判定
    assert operations.modified_unit_ids(operations.Insert(after_unit_id="art_u1", text="x")) == ()
    assert operations.modified_unit_ids(operations.Merge("art_u1", "art_u2")) == (
        "art_u1",
        "art_u2",
    )
