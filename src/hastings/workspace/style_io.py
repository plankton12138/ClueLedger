"""作者写作约定的读写（1.2 修订学习 P4）。

``STYLE.md`` 位于作品根目录，和 ``STRUCTURE.md`` 同级，但归属不同：结构由模型维护，
写作约定由作者掌握。作者可以随时打开、编辑、清空；模型只能提出建议，经作者确认后
才写入同一份文件。

这里只做文件级的确定性读写：指纹校验、编码与长度检查、原子替换。版本记录与 Git
沿用作品文件的既有机制，不另建一套状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hastings.workspace.assembly import STYLE_INJECT_BUDGET
from hastings.workspace.atomic_io import atomic_write_bytes
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import (
    FileView,
    Workspace,
    decode_text,
    digest_bytes,
    normalize_newlines,
)

__all__ = [
    "STYLE_INJECT_BUDGET",
    "STYLE_LIMIT",
    "STYLE_PATH",
    "StyleState",
    "read_style_state",
    "write_style_document",
]

STYLE_PATH = "STYLE.md"
#: 单份写作约定的字符上限：足够写下几十条规则，又不至于让每次写作都装不进上下文。
STYLE_LIMIT = 12_000


@dataclass(frozen=True, slots=True)
class StyleState:
    exists: bool
    text: str
    digest: str | None
    size: int = 0


def _target(workspace: Workspace) -> Path:
    return Path(workspace.root) / STYLE_PATH


def read_style_state(workspace: Workspace) -> StyleState:
    """读写作约定；文件不存在视为空约定。编码错误如实上报，不按空处理。"""
    target = _target(workspace)
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return StyleState(exists=False, text="", digest=None)
    except OSError as exc:
        raise WorkspaceError(
            code="STYLE_UNREADABLE",
            where=STYLE_PATH,
            why=f"写作约定无法读取：{exc.strerror or exc}",
            fix="确认文件未被其他程序占用后重试",
        ) from exc
    text = decode_text(raw, where=STYLE_PATH)
    if len(text) > STYLE_LIMIT:
        raise WorkspaceError(
            code="STYLE_TOO_LARGE",
            where=STYLE_PATH,
            why=f"写作约定有 {len(text)} 字符，超过 {STYLE_LIMIT} 字符上限",
            fix="压缩成可执行的规则后保存；整份文件都会进入每次写作",
            details={"chars": len(text), "limit": STYLE_LIMIT},
        )
    return StyleState(exists=True, text=text, digest=digest_bytes(raw), size=len(raw))


def write_style_document(
    workspace: Workspace,
    *,
    text: str,
    expected_digest: str | None,
) -> FileView:
    """按指纹前提写入；空文本表示清空约定，文件保留为空文件。"""
    target = _target(workspace)
    exists = target.exists()
    if expected_digest is None and exists:
        raise WorkspaceError(
            code="STYLE_DIGEST_MISMATCH",
            where=STYLE_PATH,
            why="写作约定在打开之后已被创建",
            fix="重新打开当前版本，在最新内容上合并后再保存",
            details={"current_digest": _digest_of(target), "current_text": _text_of(target)},
        )
    if expected_digest is not None and not exists:
        raise WorkspaceError(
            code="STYLE_DIGEST_MISMATCH",
            where=STYLE_PATH,
            why="写作约定在打开之后已被删除",
            fix="重新打开写作约定，确认内容后再保存",
            details={"current_digest": "", "current_text": ""},
        )
    if expected_digest is not None and _digest_of(target) != expected_digest:
        raise WorkspaceError(
            code="STYLE_DIGEST_MISMATCH",
            where=STYLE_PATH,
            why="写作约定在打开之后已被修改",
            fix="比较当前版本与本地编辑稿，合并后再保存",
            details={"current_digest": _digest_of(target), "current_text": _text_of(target)},
        )
    normalized = normalize_newlines(text)
    if "\x00" in normalized:
        raise WorkspaceError(
            code="STYLE_ENCODING_INVALID",
            where=STYLE_PATH,
            why="写作约定包含 NUL 字节",
            fix="用纯文本编辑器重新编辑后保存",
        )
    if len(normalized) > STYLE_LIMIT:
        raise WorkspaceError(
            code="STYLE_TOO_LARGE",
            where=STYLE_PATH,
            why=f"写作约定有 {len(normalized)} 字符，超过 {STYLE_LIMIT} 字符上限",
            fix="压缩成可执行的规则后保存；整份文件都会进入每次写作",
            details={"chars": len(normalized), "limit": STYLE_LIMIT},
        )
    payload = normalized.encode("utf-8")
    atomic_write_bytes(target, payload, prefix=".style-")
    return FileView(
        path=STYLE_PATH,
        text=normalized,
        digest=digest_bytes(payload),
        size=len(payload),
        display_text=normalized,
    )


def _digest_of(target: Path) -> str:
    try:
        return digest_bytes(target.read_bytes())
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def _text_of(target: Path) -> str:
    try:
        return decode_text(target.read_bytes(), where=STYLE_PATH)
    except (OSError, WorkspaceError):
        return ""
