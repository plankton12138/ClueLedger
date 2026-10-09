"""章节保存管线（P5 T2，计划 D3）：先归档，后写主文件。

布局：``chapters/.revisions/<chapter_id>/NNN.md`` + ``NNN.json``
（元数据 = ts / hunks / stats）。版本号 = 归档数 + 1——agent 初写稿
即 revision 1。顺序固定：

1. 读主文件旧版、算段落 hunk；空 diff 为合法 no-op（不归档、不 bump）；
2. 归档先落 JSON 后落 MD（编号以 MD 计数，中途失败重试可自愈）；
3. 归档全部成功后才写主文件，且以临时文件 + ``os.replace`` 原子替换——
   归档或覆盖任一步失败，主文件都保持原样（作者可直接重试）。
"""

from __future__ import annotations

import contextlib
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
    任何一步 IO 故障 → OSError 原样上抛，主文件保持原样（REST 转 500）。
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
    _replace_atomically(path, content)
    return SavedChapter(revision=revision + 1, hunks=hunks)


def _replace_atomically(path: Path, content: str) -> None:
    """主文件原子覆盖：先写同目录临时文件再 replace。

    ``Path.write_text`` 直接覆盖会先 truncate——中途 OSError 会留下空文件或
    半截正文。改为临时文件 + ``os.replace``（同目录同卷即原子）：失败时主文件
    保持原样，旧版也已在 .revisions 归档，作者可直接重试保存（R3-⑦）。
    """
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


__all__ = [
    "SavedChapter",
    "current_revision",
    "revisions_dir",
    "save_chapter",
]
