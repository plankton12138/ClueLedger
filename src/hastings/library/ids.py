"""库、模块、版本与条目的标识（0.8 B）。

四层身份各有前缀，任何一层都不能靠名字代替 ID：``library_id/module_id/revision_id/entry_id``
才是完整来源。条目标识由作者在头部写明，在模块内稳定；库、模块与版本由服务端分配。
"""

from __future__ import annotations

import re
import secrets

from hastings.workspace.errors import WorkspaceError

__all__ = ["IdKind", "is_id", "new_id", "normalize_entry_id", "source_path"]

IdKind = str

_PREFIX = {
    "library": "lib",
    "module": "mod",
    "revision": "rev",
    "entry": "ent",
    "binding": "bnd",
}

_GENERATED = re.compile(r"^(lib|mod|rev|ent|bnd)_[0-9a-f]{16}$")
_ENTRY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def new_id(kind: IdKind) -> str:
    """生成一个带类型前缀的随机标识；同一进程内不重复的概率足够用于本地库。"""
    prefix = _PREFIX.get(kind)
    if prefix is None:
        raise ValueError(f"未知的标识类型：{kind}")
    return f"{prefix}_{secrets.token_hex(8)}"


def is_id(value: object, kind: IdKind | None = None) -> bool:
    if not isinstance(value, str):
        return False
    match = _GENERATED.match(value)
    if match is None:
        return False
    return kind is None or match.group(1) == _PREFIX[kind]


def normalize_entry_id(value: object, *, where: str) -> str:
    """条目 ID 来自作者头部：只接受字母数字与连字符，不做大小写或空白归一。"""
    text = str(value or "").strip()
    if not _ENTRY_ID.match(text):
        raise WorkspaceError(
            code="RECORD_ID_INVALID",
            where=where,
            why=f"条目 ID 不合法：{text or '（空）'}",
            fix="用字母或数字开头，只含字母、数字、连字符与下划线，最长 64 个字符",
            details={"entry_id": text},
        )
    return text


def source_path(library_id: str, module_id: str, revision_id: str, entry_id: str) -> str:
    """条目在读取记录里的来源标签；不是磁盘路径，只用于账本与事件定位。"""
    return f"library/{library_id}/{module_id}/{revision_id}/{entry_id}"
