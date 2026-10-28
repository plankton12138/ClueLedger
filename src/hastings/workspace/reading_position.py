"""按作品保存阅读位置，不依赖浏览器端口，也不保存正文副本。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path
from hastings.workspace.state import _write_json_atomic


class ReadingPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    path: str | None = None
    digest: str = Field(default="", max_length=128)
    offset: float = Field(default=0, ge=0, le=100_000_000)

    @field_validator("path")
    @classmethod
    def managed_path(cls, value: str | None) -> str | None:
        if value is not None and not is_managed_path(value):
            raise ValueError("阅读位置必须是作品内受管文件")
        return value


def read_position(workspace: Workspace, root: Path) -> ReadingPosition:
    try:
        position = ReadingPosition.model_validate_json(
            (root / "reading-position.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return ReadingPosition()
    if position.path is None:
        return position
    try:
        source = workspace.read(position.path)
    except WorkspaceError:
        return ReadingPosition()
    if source.digest != position.digest:
        return ReadingPosition(path=source.path, digest=source.digest)
    return position


def save_position(workspace: Workspace, root: Path, position: ReadingPosition) -> ReadingPosition:
    if position.path is not None:
        source = workspace.read(position.path)
        if source.digest != position.digest:
            raise WorkspaceError(
                code="READING_SOURCE_CHANGED_CONFLICT",
                where="reading-position.save",
                why="阅读文件已修改，原位置没有保存",
                fix="重新读取文件后继续阅读",
            )
    _write_json_atomic(
        root / "reading-position.json",
        position.model_dump(),
        where="reading-position.save",
        why_prefix="无法保存阅读位置",
    )
    return position
