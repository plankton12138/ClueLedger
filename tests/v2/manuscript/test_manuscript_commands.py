"""T14.1 正文命令：导入切分、六种 Edit Operation、谱系、拒绝重叠/越界、no-op。

命令是纯函数 ``fn(catalog, new_id, ...) -> ManuscriptChange``，不触碰 Repository；
硬不变量在命令层拒绝（ManuscriptViolation），合法 no-op 返回空结果。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from hastings.domain.manuscript import commands, kinds, operations
from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.errors import ManuscriptViolation
from hastings.domain.manuscript.models import Chapter, TextUnit

MakeCatalog = Callable[..., ManuscriptCatalog]

CHAPTER = "art_ch1"


def _chapter_change(change: commands.ManuscriptChange) -> object:
    return next(c for c in change.artifact_changes if c.artifact_id.value == CHAPTER)


# ---------------------------------------------------------------------------
# 导入：确定性段落切分建立初始 TextUnit
# ---------------------------------------------------------------------------
def test_import_chapter_builds_units_in_paragraph_order(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.import_chapter(
        make_catalog([]),
        next_id,
        chapter_id=CHAPTER,
        title="第一章",
        text="第一段\n\n第二段\n\n第三段",
    )
    kinds_seen = [c.kind for c in change.artifact_changes]
    assert kinds_seen == [
        kinds.KIND_CHAPTER,
        kinds.KIND_TEXT_UNIT,
        kinds.KIND_TEXT_UNIT,
        kinds.KIND_TEXT_UNIT,
    ]
    chapter = _chapter_change(change)
    assert len(chapter.content["text_unit_ids"]) == 3
    texts = [c.content["text"] for c in change.artifact_changes if c.kind == kinds.KIND_TEXT_UNIT]
    assert texts == ["第一段", "第二段", "第三段"]
    assert all(
        c.content["origin"] == "import"
        for c in change.artifact_changes
        if c.kind == kinds.KIND_TEXT_UNIT
    )


def test_import_chapter_blank_text_is_legal_empty(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.import_chapter(
        make_catalog([]), next_id, chapter_id=CHAPTER, title="空章", text="  \n\n"
    )
    # 章仍建立，但没有段落
    assert [c.kind for c in change.artifact_changes] == [kinds.KIND_CHAPTER]
    assert _chapter_change(change).content["text_unit_ids"] == []


def test_import_chapter_missing_title_rejected(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(ManuscriptViolation) as exc:
        commands.import_chapter(make_catalog([]), next_id, chapter_id=CHAPTER, title="  ", text="a")
    assert exc.value.code == ec.MANUSCRIPT_MISSING_REQUIRED_FIELD


# ---------------------------------------------------------------------------
# 六种 Edit Operation
# ---------------------------------------------------------------------------
def test_replace_revises_only_target_unit(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.apply_edits(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        operations=(operations.Replace(unit_id="art_u2", text="改后的第二段"),),
    )
    assert len(change.artifact_changes) == 1
    entry = change.artifact_changes[0]
    assert entry.artifact_id.value == "art_u2"
    assert entry.content["text"] == "改后的第二段"
    assert entry.old_revision_id is not None  # 修订既有对象，绑定基础 revision


def test_replace_with_same_text_is_legal_empty(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.apply_edits(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        operations=(operations.Replace(unit_id="art_u2", text="第二段"),),
    )
    assert change.is_empty()


def test_insert_after_anchor_adds_unit_and_revises_chapter(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.apply_edits(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        operations=(operations.Insert(after_unit_id="art_u2", text="插入段"),),
    )
    new_units = [c for c in change.artifact_changes if c.kind == kinds.KIND_TEXT_UNIT]
    assert len(new_units) == 1
    new_id = new_units[0].artifact_id.value
    ids = _chapter_change(change).content["text_unit_ids"]
    assert ids == ["art_u1", "art_u2", new_id, "art_u3"]


def test_insert_at_chapter_head(make_catalog: MakeCatalog, next_id: Callable[[str], str]) -> None:
    change = commands.apply_edits(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        operations=(operations.Insert(after_unit_id=None, text="序言"),),
    )
    new_id = next(
        c.artifact_id.value for c in change.artifact_changes if c.kind == kinds.KIND_TEXT_UNIT
    )
    assert _chapter_change(change).content["text_unit_ids"][0] == new_id


def test_insert_multiple_paragraphs_creates_multiple_units(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.apply_edits(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        operations=(operations.Insert(after_unit_id="art_u3", text="尾一\n\n尾二"),),
    )
    new_units = [c for c in change.artifact_changes if c.kind == kinds.KIND_TEXT_UNIT]
    assert [c.content["text"] for c in new_units] == ["尾一", "尾二"]
    ids = _chapter_change(change).content["text_unit_ids"]
    assert ids[:3] == ["art_u1", "art_u2", "art_u3"]
    assert ids[3:] == [c.artifact_id.value for c in new_units]


def test_delete_tombstones_unit_and_revises_chapter(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.apply_edits(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        operations=(operations.Delete(unit_id="art_u2"),),
    )
    unit = next(c for c in change.artifact_changes if c.artifact_id.value == "art_u2")
    assert unit.content["state"] == "deleted"
    assert _chapter_change(change).content["text_unit_ids"] == ["art_u1", "art_u3"]


def test_split_creates_second_half_with_lineage(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.apply_edits(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        operations=(operations.Split(unit_id="art_u2", offset=2),),
    )
    first = next(c for c in change.artifact_changes if c.artifact_id.value == "art_u2")
    new_unit = next(c for c in change.artifact_changes if c.kind == kinds.KIND_TEXT_UNIT)
    assert first.content["text"] == "第二"
    assert new_unit.content["text"] == "段"
    assert new_unit.content["derived_from"] == ["art_u2"]
    assert new_unit.content["origin"] == "edit"
    assert _chapter_change(change).content["text_unit_ids"] == [
        "art_u1",
        "art_u2",
        new_unit.artifact_id.value,
        "art_u3",
    ]


def test_merge_joins_first_and_tombstones_second(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.apply_edits(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        operations=(operations.Merge(first_unit_id="art_u1", second_unit_id="art_u2"),),
    )
    first = next(c for c in change.artifact_changes if c.artifact_id.value == "art_u1")
    second = next(c for c in change.artifact_changes if c.artifact_id.value == "art_u2")
    assert first.content["text"] == "第一段\n第二段"
    assert first.content["derived_from"] == ["art_u2"]
    assert second.content["state"] == "deleted"
    assert _chapter_change(change).content["text_unit_ids"] == ["art_u1", "art_u3"]


def test_move_to_head_reorders_chapter_only(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.apply_edits(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        operations=(operations.Move(unit_id="art_u3", after_unit_id=None),),
    )
    assert len(change.artifact_changes) == 1
    assert _chapter_change(change).content["text_unit_ids"] == ["art_u3", "art_u1", "art_u2"]


def test_move_to_current_position_is_legal_empty(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.apply_edits(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        operations=(operations.Move(unit_id="art_u2", after_unit_id="art_u1"),),
    )
    assert change.is_empty()


# ---------------------------------------------------------------------------
# 拒绝重叠与越界
# ---------------------------------------------------------------------------
def test_overlapping_edits_rejected(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(ManuscriptViolation) as exc:
        commands.apply_edits(
            make_catalog(_three()),
            next_id,
            chapter_id=CHAPTER,
            operations=(
                operations.Replace(unit_id="art_u1", text="甲"),
                operations.Delete(unit_id="art_u1"),
            ),
        )
    assert exc.value.code == ec.MANUSCRIPT_OVERLAPPING_EDIT


def test_unknown_unit_rejected(make_catalog: MakeCatalog, next_id: Callable[[str], str]) -> None:
    with pytest.raises(ManuscriptViolation) as exc:
        commands.apply_edits(
            make_catalog(_three()),
            next_id,
            chapter_id=CHAPTER,
            operations=(operations.Replace(unit_id="art_missing", text="甲"),),
        )
    assert exc.value.code == ec.MANUSCRIPT_UNKNOWN_TEXT_UNIT


def test_split_offset_out_of_bounds_rejected(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    for offset in (0, 999):
        with pytest.raises(ManuscriptViolation) as exc:
            commands.apply_edits(
                make_catalog(_three()),
                next_id,
                chapter_id=CHAPTER,
                operations=(operations.Split(unit_id="art_u2", offset=offset),),
            )
        assert exc.value.code == ec.MANUSCRIPT_INVALID_OPERATION


def test_merge_non_adjacent_rejected(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(ManuscriptViolation) as exc:
        commands.apply_edits(
            make_catalog(_three()),
            next_id,
            chapter_id=CHAPTER,
            operations=(operations.Merge(first_unit_id="art_u1", second_unit_id="art_u3"),),
        )
    assert exc.value.code == ec.MANUSCRIPT_INVALID_OPERATION


def test_replace_with_multiple_paragraphs_rejected(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(ManuscriptViolation) as exc:
        commands.apply_edits(
            make_catalog(_three()),
            next_id,
            chapter_id=CHAPTER,
            operations=(operations.Replace(unit_id="art_u2", text="甲\n\n乙"),),
        )
    assert exc.value.code == ec.MANUSCRIPT_INVALID_OPERATION


def test_stale_chapter_base_rejected(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(ManuscriptViolation) as exc:
        commands.apply_edits(
            make_catalog(_three()),
            next_id,
            chapter_id=CHAPTER,
            operations=(operations.Replace(unit_id="art_u2", text="甲"),),
            expected_chapter_base="base_stale",
        )
    assert exc.value.code == ec.MANUSCRIPT_BASE_CONFLICT


def test_matching_chapter_base_is_accepted(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(_three())
    base = catalog.chapter_base(CHAPTER)
    change = commands.apply_edits(
        catalog,
        next_id,
        chapter_id=CHAPTER,
        operations=(operations.Replace(unit_id="art_u2", text="甲"),),
        expected_chapter_base=base,
    )
    assert not change.is_empty()


# ---------------------------------------------------------------------------
# 完整文本兼容：服务器计算 diff 并返回规范化结果
# ---------------------------------------------------------------------------
def test_save_full_text_produces_replace_hunk(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.save_full_text(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        text="第一段\n\n第二段改\n\n第三段",
    )
    assert [h.kind for h in change.hunks] == [operations.OP_REPLACE]
    assert change.hunks[0].unit_ids == ("art_u2",)


def test_save_full_text_inserts_new_trailing_paragraph(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.save_full_text(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        text="第一段\n\n第二段\n\n第三段\n\n第四段",
    )
    assert [h.kind for h in change.hunks] == [operations.OP_INSERT]
    assert change.hunks[0].anchor_unit_id == "art_u3"


def test_save_full_text_hunk_ids_are_stable_across_calls(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    text = "第一段\n\n第二段改\n\n第三段"
    first = commands.save_full_text(make_catalog(_three()), next_id, chapter_id=CHAPTER, text=text)
    second = commands.save_full_text(make_catalog(_three()), next_id, chapter_id=CHAPTER, text=text)
    assert [h.hunk_id for h in first.hunks] == [h.hunk_id for h in second.hunks]


def test_save_full_text_identical_is_legal_empty(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.save_full_text(
        make_catalog(_three()),
        next_id,
        chapter_id=CHAPTER,
        text="第一段\n\n第二段\n\n第三段",
    )
    assert change.is_empty()


def _three() -> list[tuple[str, str, dict[str, object]]]:
    """本模块自足的三段章夹具（与 conftest.three_unit_chapter 同构）。"""
    unit_ids = ("art_u1", "art_u2", "art_u3")
    texts = ("第一段", "第二段", "第三段")
    entries: list[tuple[str, str, dict[str, object]]] = [
        (
            CHAPTER,
            kinds.KIND_CHAPTER,
            Chapter(
                chapter_id=CHAPTER,
                title="第一章",
                order_key="0001",
                text_unit_ids=unit_ids,
            ).to_content(),
        )
    ]
    entries.extend(
        (
            unit_id,
            kinds.KIND_TEXT_UNIT,
            TextUnit(unit_id=unit_id, chapter_id=CHAPTER, text=text, origin="import").to_content(),
        )
        for unit_id, text in zip(unit_ids, texts, strict=True)
    )
    return entries
