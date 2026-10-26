"""作品元信息（0.5 V05-3A）。

契约：章序来自文件的四位编号，不按列表位置重新编号；显示名称由服务端统一生成，
目录、稿纸、模型目录与材料选择器使用同一份结果。人物标签只来自小传开头的结构化
元信息，格式损坏或未知值给诊断，不猜测身份。

本文件只验证元信息解析与目录/读取结果；本轮材料、索引与检索属于 V05-3B 之后。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.metadata import (
    ROLE_LABELS,
    chinese_number,
    parse_chapter,
    parse_chapters,
    parse_character,
)

# -- 中文编号 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, "一"),
        (3, "三"),
        (10, "十"),
        (11, "十一"),
        (20, "二十"),
        (21, "二十一"),
        (100, "一百"),
        (101, "一百零一"),
        (110, "一百一十"),
        (111, "一百一十一"),
        (1000, "一千"),
        (1001, "一千零一"),
    ],
)
def test_chinese_number_boundaries(value: int, expected: str) -> None:
    assert chinese_number(value) == expected


# -- 章节显示名 -------------------------------------------------------------


def test_regular_chapter_takes_number_from_filename() -> None:
    meta = parse_chapter("正文/0003 书房里的脚印.md", "# 书房里的脚印\n")

    assert meta.number == 3
    assert meta.title == "书房里的脚印"
    assert meta.display_title == "第三章 书房里的脚印"
    assert meta.chapter_type == "regular"


def test_missing_chapter_does_not_shift_later_numbers() -> None:
    metas = parse_chapters(
        "雪山旅馆",
        [
            ("正文/0001 暴雪来客.md", "# 暴雪来客\n"),
            ("正文/0003 书房里的脚印.md", "# 书房里的脚印\n"),
        ],
    )

    assert [meta.display_title for meta in metas] == [
        "第一章 暴雪来客",
        "第三章 书房里的脚印",
    ]


def test_numbered_h2_heading_wins_over_h1() -> None:
    meta = parse_chapter("正文/0003 脚印.md", "## 第三章 书房里的脚印\n\n正文。\n")

    assert meta.title == "书房里的脚印"
    assert meta.display_title == "第三章 书房里的脚印"


def test_repeated_book_title_falls_back_to_filename() -> None:
    metas = parse_chapters(
        "雪山旅馆",
        [
            ("正文/0001 暴雪来客.md", "# 雪山旅馆\n\n正文。\n"),
            ("正文/0002 夜访.md", "# 雪山旅馆\n\n正文。\n"),
        ],
    )

    assert [meta.display_title for meta in metas] == ["第一章 暴雪来客", "第二章 夜访"]
    assert all(any("重复" in item for item in meta.warnings) for meta in metas)


def test_h1_equal_to_work_name_falls_back_to_filename() -> None:
    metas = parse_chapters("雪山旅馆", [("正文/0001 暴雪来客.md", "# 雪山旅馆\n")])

    assert metas[0].display_title == "第一章 暴雪来客"
    assert any("作品名" in item for item in metas[0].warnings)


def test_title_number_conflict_keeps_filename_number() -> None:
    meta = parse_chapter("正文/0003 雪停.md", "# 第五章 雪停\n")

    assert meta.display_title == "第三章 雪停"
    assert any("第五章" in item for item in meta.warnings)


def test_unnumbered_file_does_not_invent_number() -> None:
    meta = parse_chapter("正文/雪停.md", "# 雪停\n")

    assert meta.number is None
    assert meta.display_title == "雪停"
    assert any("编号" in item for item in meta.warnings)


def test_heading_number_does_not_set_chapter_number() -> None:
    """没有四位编号时，标题里的「第三章」只给章名与诊断，不成为章序。"""
    meta = parse_chapter("正文/雪停.md", "# 第三章 雪停\n")

    assert meta.number is None
    assert meta.title == "雪停"
    assert meta.display_title == "雪停"
    assert any("第三章" in item and "四位章序" in item for item in meta.warnings)


def test_chapter_without_any_name_shows_placeholder() -> None:
    meta = parse_chapter("正文/0004.md", "")

    assert meta.title == "（未命名）"
    assert meta.display_title == "第四章（未命名）"


def test_duplicate_numbers_are_reported() -> None:
    metas = parse_chapters(
        "雪山旅馆",
        [("正文/0003 甲.md", "# 甲\n"), ("正文/0003 乙.md", "# 乙\n")],
    )

    assert all(any("两个文件" in item for item in meta.warnings) for meta in metas)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("正文/0000 序章 暴雪前的信.md", "序章 暴雪前的信"),
        ("正文/0000 序章.md", "序章"),
        ("正文/0012 尾声.md", "尾声"),
        ("正文/0012 尾声 雪停了.md", "尾声 雪停了"),
        ("正文/番外 林远的信.md", "番外 林远的信"),
    ],
)
def test_special_chapters_use_explicit_names(path: str, expected: str) -> None:
    assert parse_chapter(path, "").display_title == expected


# -- 人物标签 ---------------------------------------------------------------


def test_roles_are_read_from_front_matter_and_hidden_from_reading() -> None:
    text = "---\nroles: [suspect, culprit]\n---\n# 许衡\n\n身份、往事与关系。\n"

    meta = parse_character("设定集/角色小传/许衡.md", text)

    assert meta.roles == ("suspect", "culprit")
    assert meta.title == "许衡"
    assert meta.display_text.startswith("# 许衡")
    assert "roles" not in meta.display_text
    assert meta.warnings == ()
    assert ROLE_LABELS["culprit"] == "凶手"


def test_character_without_metadata_is_a_plain_file() -> None:
    text = "# 林远\n\n侦探。\n"

    meta = parse_character("设定集/角色小传/林远.md", text)

    assert meta.roles == ()
    assert meta.display_text == text
    assert meta.warnings == ()


def test_unknown_role_is_reported_not_guessed() -> None:
    text = "---\nroles: [suspect, wizard]\n---\n# 许衡\n"

    meta = parse_character("设定集/角色小传/许衡.md", text)

    assert meta.roles == ("suspect",)
    assert any("wizard" in item for item in meta.warnings)


def test_duplicate_keys_are_reported_and_first_value_kept() -> None:
    text = "---\nroles: [suspect]\nroles: [culprit]\n---\n# 许衡\n"

    meta = parse_character("设定集/角色小传/许衡.md", text)

    assert meta.roles == ("suspect",)
    assert any("重复" in item for item in meta.warnings)


def test_unterminated_metadata_stays_readable() -> None:
    text = "---\nroles: [suspect]\n# 许衡\n\n正文。\n"

    meta = parse_character("设定集/角色小传/许衡.md", text)

    assert meta.roles == ()
    assert meta.display_text == text
    assert any("元信息" in item for item in meta.warnings)


def test_block_list_roles_are_supported() -> None:
    text = "---\nroles:\n  - detective\n  - assistant\n---\n# 林远\n"

    assert parse_character("设定集/角色小传/林远.md", text).roles == ("detective", "assistant")


def test_single_role_string_is_supported() -> None:
    text = "---\nroles: victim\n---\n# 陈默\n"

    assert parse_character("设定集/角色小传/陈默.md", text).roles == ("victim",)


def test_repeated_role_is_removed_once() -> None:
    text = "---\nroles: [suspect, suspect, culprit]\n---\n# 许衡\n"

    assert parse_character("设定集/角色小传/许衡.md", text).roles == ("suspect", "culprit")


def test_overlong_metadata_is_not_parsed() -> None:
    text = "---\n" + "note: " + "x" * 4000 + "\n---\n# 许衡\n"

    meta = parse_character("设定集/角色小传/许衡.md", text)

    assert meta.roles == ()
    assert any("元信息" in item for item in meta.warnings)
    assert meta.display_text.startswith("---")


def test_invalid_roles_are_rejected_before_writing(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "雪山旅馆")

    with pytest.raises(WorkspaceError) as excinfo:
        workspace.write(
            "设定集/角色小传/许衡.md",
            "---\nroles: [wizard]\n---\n# 许衡\n",
            expected_digest=None,
        )

    assert excinfo.value.code == "CHARACTER_METADATA_INVALID"
    assert excinfo.value.details["problems"]
    # 拒绝要发生在落盘之前：不能留下一个只能靠猜身份的文件。
    assert not (workspace.root / "设定集/角色小传/许衡.md").exists()


def test_externally_broken_metadata_stays_readable(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    target = workspace.root / "设定集/角色小传/许衡.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\nroles: [wizard]\n---\n# 许衡\n\n正文。\n", encoding="utf-8")

    entry = workspace.catalog().characters[0]

    assert entry.roles == ()
    assert entry.metadata_warnings
    assert workspace.read(entry.path).display_text.startswith("# 许衡")


# -- 目录与读取共用同一份结果 ------------------------------------------------


def test_catalog_and_read_share_display_fields(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    workspace.write("正文/0001 暴雪来客.md", "# 雪山旅馆\n\n正文。\n", expected_digest=None)
    workspace.write(
        "设定集/角色小传/许衡.md",
        "---\nroles: [suspect, culprit]\n---\n# 许衡\n",
        expected_digest=None,
    )

    catalog = workspace.catalog()
    chapter = catalog.chapters[0]
    character = catalog.characters[0]

    assert chapter.display_title == "第一章 暴雪来客"
    assert chapter.chapter_number == 1
    assert any("作品名" in item for item in chapter.metadata_warnings)
    assert chapter.roles == ()
    assert character.roles == ("suspect", "culprit")
    assert character.display_title == "许衡"

    # 读取保留完整原文（含元信息），稿纸用分离后的正文；两者来自同一次解析。
    view = workspace.read(character.path)
    assert view.text.startswith("---")
    assert view.display_text.startswith("# 许衡")
    assert workspace.describe(character.path) == character
