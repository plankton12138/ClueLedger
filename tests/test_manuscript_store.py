"""章节保存管线测试（P5 T2，计划 D3）：.revisions 归档 + 写主文件。

钉住的不变量：
- revision = 归档数 + 1；agent 初写稿即 revision 1；
- 变更保存：旧版先落 .revisions/<id>/NNN.md + NNN.json（ts/hunks/stats），
  后写主文件，返回新 revision 与 hunks；编号连续；
- 空 diff 是合法 no-op：不归档、不写盘、revision 不变；
- 归档阶段失败（.revisions 被文件占位）→ OSError 抛出且主文件未动；
- 写盘阶段失败（tmp 路径被占位）→ 主文件保持原样（原子替换，不留半写）。
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from hastings.bible.store import StoryWorkspace
from hastings.manuscript.diff import DiffHunk
from hastings.manuscript.store import (
    SavedChapter,
    current_revision,
    revisions_dir,
    save_chapter,
)

SLUG = "orient-express"
CHAPTER_ID = "ch03"


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    ws = StoryWorkspace(root=tmp_path, slug=SLUG)
    ws.ensure_dirs()
    (ws.chapters_dir / f"{CHAPTER_ID}.md").write_text(
        "# 第3章 旧标题\n\n火车在九点四十七分抵达。\n", encoding="utf-8"
    )
    return ws


def _save(
    workspace: StoryWorkspace,
    content: str,
    *,
    now: Callable[[], float] | None = None,
) -> SavedChapter:
    if now is None:
        return save_chapter(workspace, CHAPTER_ID, content)
    return save_chapter(workspace, CHAPTER_ID, content, now=now)


# --- current_revision ---


def test_current_revision_starts_at_one(workspace: StoryWorkspace) -> None:
    assert current_revision(workspace, CHAPTER_ID) == 1
    assert revisions_dir(workspace, CHAPTER_ID).name == CHAPTER_ID


def test_current_revision_counts_archives(workspace: StoryWorkspace) -> None:
    directory = revisions_dir(workspace, CHAPTER_ID)
    directory.mkdir(parents=True)
    (directory / "001.md").write_text("旧版", encoding="utf-8")
    (directory / "002.md").write_text("更旧版", encoding="utf-8")
    (directory / "notes.txt").write_text("非归档，不计入", encoding="utf-8")

    assert current_revision(workspace, CHAPTER_ID) == 3


# --- save_chapter：归档 + 写入 ---


def test_save_changed_chapter_archives_then_writes(workspace: StoryWorkspace) -> None:
    new_content = "# 第3章 新标题\n\n火车在十点零七分抵达。\n"

    saved = _save(workspace, new_content, now=lambda: 1_700_000_000.0)

    assert saved.revision == 2
    assert saved.hunks == [
        DiffHunk(
            kind="modified",
            old_index=0,
            new_index=0,
            old_text="# 第3章 旧标题",
            new_text="# 第3章 新标题",
        ),
        DiffHunk(
            kind="modified",
            old_index=1,
            new_index=1,
            old_text="火车在九点四十七分抵达。",
            new_text="火车在十点零七分抵达。",
        ),
    ]
    # 主文件已更新
    assert (workspace.chapters_dir / f"{CHAPTER_ID}.md").read_text(encoding="utf-8") == new_content
    # 旧版归档：md 原文 + json 元数据（ts/hunks/stats）
    directory = revisions_dir(workspace, CHAPTER_ID)
    assert (directory / "001.md").read_text(encoding="utf-8") == (
        "# 第3章 旧标题\n\n火车在九点四十七分抵达。\n"
    )
    metadata = json.loads((directory / "001.json").read_text(encoding="utf-8"))
    assert metadata["ts"] == 1_700_000_000.0
    assert metadata["hunks"][0]["kind"] == "modified"
    assert metadata["hunks"][0]["old_index"] == 0
    assert metadata["stats"] == {"chars": len(new_content), "paragraphs": 2}


def test_save_second_revision_continues_numbering(workspace: StoryWorkspace) -> None:
    _save(workspace, "# 第3章\n\n第一次修改。\n")
    second = _save(workspace, "# 第3章\n\n第二次修改。\n")

    assert second.revision == 3
    directory = revisions_dir(workspace, CHAPTER_ID)
    assert (directory / "001.md").exists()
    assert (directory / "002.md").exists()
    assert (directory / "001.json").exists()
    assert (directory / "002.json").exists()


def test_save_identical_content_is_noop(workspace: StoryWorkspace) -> None:
    original = (workspace.chapters_dir / f"{CHAPTER_ID}.md").read_text(encoding="utf-8")

    saved = _save(workspace, original)

    assert saved.revision == 1
    assert saved.hunks == []
    assert not revisions_dir(workspace, CHAPTER_ID).exists()  # 未归档、未建目录


def test_save_missing_chapter_raises_file_not_found(workspace: StoryWorkspace) -> None:
    with pytest.raises(FileNotFoundError):
        save_chapter(workspace, "ch99", "# 第99章\n\n正文。")


def test_save_archive_failure_leaves_main_file_untouched(workspace: StoryWorkspace) -> None:
    # .revisions 被普通文件占位：mkdir 必抛 OSError，归档阶段失败
    blocked = workspace.chapters_dir / ".revisions"
    blocked.write_text("占位文件", encoding="utf-8")
    main_path = workspace.chapters_dir / f"{CHAPTER_ID}.md"
    mtime_before = main_path.stat().st_mtime_ns
    content_before = main_path.read_text(encoding="utf-8")

    with pytest.raises(OSError):
        _save(workspace, "# 第3章\n\n写不进去的新内容。\n")

    # 主文件未动（先归档后写主文件的顺序保证）
    assert main_path.stat().st_mtime_ns == mtime_before
    assert main_path.read_text(encoding="utf-8") == content_before


def test_save_atomic_replace_failure_leaves_main_file_untouched(
    workspace: StoryWorkspace,
) -> None:
    # R3-⑦：主文件写入改为原子替换（tmp 写 + rename）。让 tmp 路径被目录占位，
    # write_text 必抛 OSError，验证主文件保持原样、不出现半写状态。
    (workspace.chapters_dir / f"{CHAPTER_ID}.md.tmp").mkdir()
    main_path = workspace.chapters_dir / f"{CHAPTER_ID}.md"
    mtime_before = main_path.stat().st_mtime_ns
    content_before = main_path.read_text(encoding="utf-8")

    with pytest.raises(OSError):
        _save(workspace, "# 第3章\n\n写不进去的新内容。\n")

    # 主文件未被半写内容污染（原子替换保证）
    assert main_path.stat().st_mtime_ns == mtime_before
    assert main_path.read_text(encoding="utf-8") == content_before
