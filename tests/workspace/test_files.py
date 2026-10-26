"""文件工作区（0.4 V04-1，计划 M01）。

契约：正文每章一个 Markdown，角色小传一人一文件，线索与真相固定。
本文件只验证文件层——不涉及 Git（V04-2）、模型（V04-3）和界面（V04-4）：

- 新建目录只建立正文，设定通过模块装卸，不预建空设定目录；
- 写入按「上次读到的指纹」生效，过期或被外部改动一律拒绝；
- 路径校验拒绝绝对路径、上级跳转、盘符、反斜杠、``.git`` 与设定集旁路文件；
- 读取接受 BOM 与 CRLF，指纹按实际字节计算，外部编辑因此可被识别；
- 搜索、导入与删除只作用于受管文件，线索与真相不能改名或删除。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import (
    SETTING_PATHS,
    Workspace,
    digest_bytes,
    is_managed_path,
    normalize_newlines,
)


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace.create(tmp_path / "雪山旅馆")


def _overwrite(workspace: Workspace, path: str, text: str) -> None:
    """设定文件按需创建；已存在时用读到的指纹改写。"""
    try:
        expected = workspace.read(path).digest
    except WorkspaceError:
        expected = None
    workspace.write(path, text, expected_digest=expected)


def _code(exc_info: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return exc_info.value.code


# -- 建立与打开 -------------------------------------------------------------


def test_create_builds_only_body_directory(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    assert (workspace.root / "正文").is_dir()
    assert not (workspace.root / "设定集").exists()
    # 新作品不再强制创建线索与真相文件；已有作品里这两份文件仍按原路径读取。
    for path in SETTING_PATHS:
        assert not (workspace.root / path).exists()
    # 作品目录里没有数据库或第二份内容清单。
    assert not [item for item in workspace.root.iterdir() if item.suffix in {".db", ".sqlite"}]


def test_create_keeps_existing_content(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _overwrite(workspace, "设定集/真相.md", "# 真相\n\n凶手是管家。\n")
    view = workspace.read("设定集/真相.md")

    again = Workspace.create(workspace.root)

    assert again.read("设定集/真相.md").digest == view.digest


def test_open_plain_directory_writes_nothing(tmp_path: Path) -> None:
    plain = tmp_path / "随手一个目录"
    plain.mkdir()

    workspace = Workspace.open(plain)

    assert workspace.root == plain.resolve()
    assert list(plain.iterdir()) == []


def test_open_rejects_missing_path_and_file(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError) as missing:
        Workspace.open(tmp_path / "不存在")
    assert _code(missing) == "WORKSPACE_NOT_FOUND"

    file_path = tmp_path / "a.md"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(WorkspaceError) as not_dir:
        Workspace.open(file_path)
    assert _code(not_dir) == "WORKSPACE_NOT_DIRECTORY"


# -- 读写与指纹 -------------------------------------------------------------


def test_write_uses_utf8_lf_and_returns_actual_digest(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    view = workspace.write(
        "正文/0001 雨夜.md", "# 雨夜\r\n\r\n侦探抵达旅馆。\r\n", expected_digest=None
    )

    raw = (workspace.root / "正文/0001 雨夜.md").read_bytes()
    assert b"\r" not in raw
    assert raw.decode("utf-8").startswith("# 雨夜\n\n侦探抵达旅馆。")
    assert view.digest == hashlib.sha256(raw).hexdigest()
    assert view.size == len(raw)
    assert view.text == normalize_newlines(view.text)


def test_read_accepts_bom_and_crlf(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.root / "正文/0001 雨夜.md"
    target.parent.mkdir(exist_ok=True)
    raw = b"\xef\xbb\xbf# \xe9\x9b\xa8\xe5\xa4\x9c\r\n\r\n\xe4\xbe\xa6\xe6\x8e\xa2\r\n"
    target.write_bytes(raw)

    view = workspace.read("正文/0001 雨夜.md")

    assert view.text.startswith("# 雨夜\n")
    assert "\r" not in view.text
    # 指纹按原始字节：BOM 与 CRLF 都算内容差异。
    assert view.digest == digest_bytes(raw)


def test_external_edit_blocks_stale_write_then_succeeds_after_reread(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    first = workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n原稿。\n", expected_digest=None)
    target = workspace.root / "正文/0001 雨夜.md"
    target.write_text("# 雨夜\n\n外部编辑器改过。\n", encoding="utf-8")

    with pytest.raises(WorkspaceError) as stale:
        workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n模型改写。\n", expected_digest=first.digest)
    assert _code(stale) == "FILE_STALE_CONFLICT"
    assert "外部编辑器改过" in target.read_text(encoding="utf-8")

    fresh = workspace.read("正文/0001 雨夜.md")
    assert fresh.digest != first.digest
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n模型改写。\n", expected_digest=fresh.digest)
    assert "模型改写" in target.read_text(encoding="utf-8")


def test_expected_digest_none_only_creates(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n", expected_digest=None)

    with pytest.raises(WorkspaceError) as exists:
        workspace.write("正文/0001 雨夜.md", "# 覆盖\n", expected_digest=None)
    assert _code(exists) == "FILE_EXISTS_CONFLICT"

    with pytest.raises(WorkspaceError) as missing:
        workspace.write("正文/0002 来信.md", "# 来信\n", expected_digest=digest_bytes(b"x"))
    assert _code(missing) == "FILE_NOT_FOUND"


def test_read_missing_file_is_not_found(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(WorkspaceError) as missing:
        workspace.read("正文/0009 无.md")
    assert _code(missing) == "FILE_NOT_FOUND"


def test_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n", expected_digest=None)

    assert sorted(path.name for path in (workspace.root / "正文").iterdir()) == ["0001 雨夜.md"]


# -- 路径边界 ---------------------------------------------------------------

REJECTED_PATHS = [
    "",
    "../x.md",
    "正文/../x.md",
    "正文/./x.md",
    "/abs/正文/0001.md",
    "C:/作品/正文/0001.md",
    "C:正文/0001.md",
    "正文\\0001.md",
    "正文/子目录/0001.md",
    ".git/config",
    "正文/0001.txt",
    "正文/0001.MD",
    "设定集/时间线.md",
    "设定集/角色小传.md.bak",
    "设定集/角色小传.md",
    "正文/CON.md",
    "正文/.md",
    "正文/0001.md ",
]


@pytest.mark.parametrize("path", REJECTED_PATHS)
def test_paths_outside_managed_set_are_rejected(path: str, tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    assert is_managed_path(path) is False
    with pytest.raises(WorkspaceError) as rejected:
        workspace.read(path)
    assert _code(rejected) == "PATH_NOT_MANAGED"


def test_managed_paths_accept_chapters_characters_and_fixed_settings() -> None:
    assert is_managed_path("正文/0001 雨夜.md")
    assert is_managed_path("设定集/角色小传/林远.md")
    assert is_managed_path("设定集/线索.md")
    assert is_managed_path("设定集/真相.md")


def test_symlinked_body_directory_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outside = tmp_path / "外部"
    outside.mkdir()
    (outside / "0001 偷渡.md").write_text("# 偷渡\n", encoding="utf-8")
    body = workspace.root / "正文"
    body.rmdir()
    try:
        body.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建目录符号链接")

    with pytest.raises(WorkspaceError) as escaped:
        workspace.read("正文/0001 偷渡.md")
    assert _code(escaped) == "PATH_NOT_MANAGED"


def test_symlinked_chapter_file_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outside = tmp_path / "外部.md"
    outside.write_text("# 外部\n", encoding="utf-8")
    link = workspace.root / "正文/0001 链接.md"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建文件符号链接")

    with pytest.raises(WorkspaceError) as escaped:
        workspace.read("正文/0001 链接.md")
    assert _code(escaped) == "PATH_NOT_MANAGED"


# -- 章节操作 ---------------------------------------------------------------


def test_create_chapter_numbers_and_reads_title_from_heading(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    first = workspace.create_chapter("雨夜", "# 雨夜\n\n侦探抵达旅馆。\n")
    second = workspace.create_chapter("来信")

    assert first.path == "正文/0001 雨夜.md"
    assert second.path == "正文/0002 来信.md"
    assert (workspace.root / second.path).read_text(encoding="utf-8") == "# 来信\n"
    catalog = workspace.catalog()
    assert [(entry.path, entry.title, entry.order) for entry in catalog.chapters] == [
        ("正文/0001 雨夜.md", "雨夜", "0001"),
        ("正文/0002 来信.md", "来信", "0002"),
    ]


def test_create_chapter_sanitizes_filename(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    view = workspace.create_chapter('第一章: 雨/夜?"', "# 第一章：雨夜\n")

    assert view.path == "正文/0001 第一章_ 雨_夜__.md"
    assert (workspace.root / view.path).is_file()


def test_chapter_title_falls_back_to_filename(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write("正文/0001 雨夜.md", "没有标题的一段正文。\n", expected_digest=None)

    assert workspace.catalog().chapters[0].title == "雨夜"


def test_move_and_delete_chapter_require_digest(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    view = workspace.write("正文/0001 雨夜.md", "# 雨夜\n", expected_digest=None)

    with pytest.raises(WorkspaceError) as stale:
        workspace.move(
            "正文/0001 雨夜.md", "正文/0001 雨夜改.md", expected_digest=digest_bytes(b"x")
        )
    assert _code(stale) == "FILE_STALE_CONFLICT"

    moved = workspace.move("正文/0001 雨夜.md", "正文/0001 雨夜改.md", expected_digest=view.digest)
    assert moved.path == "正文/0001 雨夜改.md"
    assert not (workspace.root / "正文/0001 雨夜.md").exists()

    workspace.write("正文/0002 来信.md", "# 来信\n", expected_digest=None)
    incoming = workspace.read("正文/0002 来信.md")
    with pytest.raises(WorkspaceError) as conflict:
        workspace.move("正文/0002 来信.md", "正文/0001 雨夜改.md", expected_digest=incoming.digest)
    assert _code(conflict) == "FILE_EXISTS_CONFLICT"

    workspace.delete("正文/0001 雨夜改.md", expected_digest=moved.digest)
    assert not (workspace.root / "正文/0001 雨夜改.md").exists()


def test_settings_cannot_be_moved_or_deleted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _overwrite(workspace, "设定集/线索.md", "# 线索\n\n门口有雪。\n")
    view = workspace.read("设定集/线索.md")

    with pytest.raises(WorkspaceError) as moved:
        workspace.move("设定集/线索.md", "正文/0001 线索.md", expected_digest=view.digest)
    assert _code(moved) == "SETTING_PATH_FIXED"

    with pytest.raises(WorkspaceError) as deleted:
        workspace.delete("设定集/线索.md", expected_digest=view.digest)
    assert _code(deleted) == "SETTING_PATH_FIXED"


# -- 搜索 -------------------------------------------------------------------


def test_search_reports_path_line_and_unreadable_files(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n侦探抵达旅馆。\n", expected_digest=None)
    workspace.write("正文/0002 来信.md", "# 来信\n\nDetective 收到信。\n", expected_digest=None)
    _overwrite(workspace, "设定集/线索.md", "# 线索\n\n侦探的伞是湿的。\n")
    (workspace.root / "正文/0003 乱码.md").write_bytes(b"\xff\xfe\x00broken")

    result = workspace.search("侦探")
    assert [(hit.path, hit.line) for hit in result.hits] == [
        ("正文/0001 雨夜.md", 3),
        ("设定集/线索.md", 3),
    ]
    assert result.unreadable == ("正文/0003 乱码.md",)
    assert result.truncated is False

    # 英文按大小写不敏感匹配。
    assert [hit.path for hit in workspace.search("detective").hits] == ["正文/0002 来信.md"]

    # 还有更多命中时 limit 触发截断标记。
    limited = workspace.search("侦探", limit=1)
    assert len(limited.hits) == 1
    assert limited.truncated is True

    # 命中数刚好等于上限时不算截断。
    exact = workspace.search("侦探", limit=2)
    assert len(exact.hits) == 2
    assert exact.truncated is False


def test_search_rejects_blank_query(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(WorkspaceError) as blank:
        workspace.search("   ")
    assert _code(blank) == "SEARCH_QUERY_EMPTY"


def test_undecodable_file_cannot_be_read(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace.root / "正文/0001 乱码.md").write_bytes(b"\xff\xfe\x00broken")

    with pytest.raises(WorkspaceError) as invalid:
        workspace.read("正文/0001 乱码.md")
    assert _code(invalid) == "FILE_ENCODING_INVALID"


# -- 导入 -------------------------------------------------------------------


def test_import_markdown_creates_one_chapter(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    created = workspace.import_text("雨夜.md", "# 雨夜\n\n侦探抵达旅馆。\n".encode())

    assert [view.path for view in created] == ["正文/0001 雨夜.md"]
    assert (workspace.root / "正文/0001 雨夜.md").read_text(encoding="utf-8").startswith("# 雨夜")


def test_import_markdown_with_book_title_does_not_repeat_filename_number(tmp_path: Path) -> None:
    """一级标题是书名时回退到文件名的章名，编号不会被拼进新章名。"""
    workspace = _workspace(tmp_path)

    created = workspace.import_text("0001 暴雪来客.md", "# 雪山旅馆\n\n雪下了一夜。\n".encode())

    assert [view.path for view in created] == ["正文/0001 暴雪来客.md"]
    chapter = workspace.catalog().chapters[0]
    assert chapter.display_title == "第一章 暴雪来客"


def test_import_keeps_special_marker_when_falling_back(tmp_path: Path) -> None:
    """回退只去掉编号：「序章」属于章名，去掉会丢章节类型。"""
    workspace = _workspace(tmp_path)

    created = workspace.import_text(
        "0000 序章 暴雪前的信.md", "# 雪山旅馆\n\n信在雪里放了三天。\n".encode()
    )

    assert [view.path for view in created] == ["正文/0001 序章 暴雪前的信.md"]
    assert workspace.catalog().chapters[0].chapter_type == "prologue"


def test_import_text_splits_chapters_and_never_overwrites(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    data = "第一章 来信\n\n信件来自十年前。\n\n第二章 雪停\n\n雪停了。\n".encode()

    first = workspace.import_text("旧稿.txt", data)
    second = workspace.import_text("旧稿.txt", data)

    assert [view.path for view in first] == ["正文/0001 第一章 来信.md", "正文/0002 第二章 雪停.md"]
    assert [view.path for view in second] == [
        "正文/0003 第一章 来信.md",
        "正文/0004 第二章 雪停.md",
    ]
    assert len(workspace.catalog().chapters) == 4


def test_import_rejects_unsupported_format_and_empty_text(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(WorkspaceError) as unsupported:
        workspace.import_text("稿件.docx", b"whatever")
    assert _code(unsupported) == "IMPORT_FORMAT_INVALID"

    with pytest.raises(WorkspaceError) as empty:
        workspace.import_text("空.txt", b"   \n")
    assert _code(empty) == "IMPORT_EMPTY"

    with pytest.raises(WorkspaceError) as empty_markdown:
        workspace.import_text("空.md", b"\n\n")
    assert _code(empty_markdown) == "IMPORT_EMPTY"
