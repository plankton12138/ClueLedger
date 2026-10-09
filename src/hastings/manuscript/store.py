"""章节保存管线（P5 T2，计划 D3）：先归档，后写主文件。

布局：``chapters/.revisions/<chapter_id>/NNN.md`` + ``NNN.json``
（元数据 = ts / hunks / stats）。版本号 = 归档数 + 1——agent 初写稿
即 revision 1。顺序固定：

1. 读主文件旧版、算段落 hunk；空 diff 为合法 no-op（不归档、不 bump）；
2. 归档先落 JSON 后落 MD（编号以 MD 计数，中途失败重试可自愈）；
3. 归档全部成功后才写主文件——归档阶段任何异常，主文件都不动。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from hastings.bible.store import StoryWorkspace
from hastings.manuscript.diff import DiffHunk, diff_chapters, split_paragraphs


@dataclass(frozen=True, slots=True)
class SavedChapter:
    """一次保存的结果：revision 为保存后的当前版本，hunks 为空即 no-op。"""

    revision: int
    hunks: list[DiffHunk]


def revisions_dir(workspace: StoryWorkspace, chapter_id: str) -> Path:
    """章节归档目录：chapters/.revisions/<chapter_id>。"""
    return workspace.chapters_dir / ".revisions" / chapter_id


def _archive_count(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.glob("*.md") if path.stem.isdigit())


def current_revision(workspace: StoryWorkspace, chapter_id: str) -> int:
    """当前版本号 = 归档数 + 1（无归档即初版 revision 1）。"""
    return 1 + _archive_count(revisions_dir(workspace, chapter_id))


def save_chapter(
    workspace: StoryWorkspace,
    chapter_id: str,
    content: str,
    *,
    now: Callable[[], float] = time.time,
) -> SavedChapter:
    """受管保存：归档旧版后写入新正文。

    章节不存在 → FileNotFoundError（REST 转 404）；
    归档阶段 IO 故障 → OSError 原样上抛，主文件未动（REST 转 500）。
    """
    path = workspace.chapters_dir / f"{chapter_id}.md"
    old = path.read_text(encoding="utf-8")
    hunks = diff_chapters(old, content)
    revision = 1 + _archive_count(revisions_dir(workspace, chapter_id))
    if not hunks:
        return SavedChapter(revision=revision, hunks=[])

    directory = revisions_dir(workspace, chapter_id)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{revision:03d}"  # 旧版（当前 revision）的归档编号
    metadata = {
        "ts": now(),
        "hunks": [asdict(hunk) for hunk in hunks],
        "stats": {
            "chars": len(content),
            "paragraphs": len(split_paragraphs(content)),
        },
    }
    (directory / f"{stem}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (directory / f"{stem}.md").write_text(old, encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    return SavedChapter(revision=revision + 1, hunks=hunks)


__all__ = [
    "SavedChapter",
    "current_revision",
    "revisions_dir",
    "save_chapter",
]
