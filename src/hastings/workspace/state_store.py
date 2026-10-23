"""作品状态目录里的派生记录文件（0.6 V06-E4）。

每个状态域一个 JSON 文件，结构都是「版本头 + 条目数组」。这里集中处理两件容易写错的
事：

- 读取时版本不匹配按空处理——宁可退回低一级能力，也不猜旧结构怎么迁移；
- 写入用临时文件替换，避免写到一半留下半份记录被当成完整结果。

这些文件都不进作品 Git：它们是从原文与设定算出来的测量结果，删掉可以重建，进版本库
只会变成第二份事实来源。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from hastings.workspace.files import Workspace

__all__ = ["current_chapter", "read_records", "write_records"]


def current_chapter(workspace: Workspace) -> int:
    """已经写到的最大章号；没有章节时为 0。

    「截至第 N 章」这类判断都从它来：章节文件在不在、章号是否可解析，只在这里定义一次。
    """
    numbers = [
        entry.chapter_number
        for entry in workspace.catalog().chapters
        if entry.chapter_number is not None and entry.exists
    ]
    return max(numbers, default=0)


def read_records(
    file: Path, *, record_version: int, recipe_version: int, key: str
) -> tuple[dict[str, object], ...]:
    """读出条目数组；文件缺失、损坏或版本不符都返回空，不抛错。"""
    try:
        payload = json.loads(Path(file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(payload, dict):
        return ()
    if payload.get("record_version") != record_version:
        return ()
    if payload.get("recipe_version") != recipe_version:
        return ()
    raw = payload.get(key)
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, dict))


def write_records(
    file: Path,
    *,
    prefix: str,
    record_version: int,
    recipe_version: int,
    key: str,
    entries: Sequence[dict[str, object]],
) -> None:
    """整份覆盖写入；``prefix`` 只用于临时文件名，方便排查残留。"""
    target = Path(file)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "record_version": record_version,
        "recipe_version": recipe_version,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        key: list(entries),
    }
    handle, temp_name = tempfile.mkstemp(dir=str(target.parent), prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(temp_name)
        raise
