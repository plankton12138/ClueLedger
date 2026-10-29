"""状态文件的原子写入与跨进程锁（1.0.1 P1 提取）。

线索账本与构思结构都需要同一套保护：进程内线程锁 + 跨进程文件锁，写入走
「临时文件 + fsync + 替换」。之前这套逻辑只在线索账本里，第二份状态要复用时
抽出来，避免两份实现慢慢长歪。

Windows 上另一进程正在读旧文件时替换会短暂失败，这里按短间隔重试同一个替换，
不放弃已经写好的内容。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import BinaryIO

__all__ = ["atomic_write_bytes", "atomic_write_json", "locked"]

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """同一路径的进程内互斥，再加一层跨进程文件锁。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCKS_GUARD:
        lock = _THREAD_LOCKS.setdefault(str(path.resolve()), threading.Lock())
    with lock, _open_lock(path) as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        _file_lock(stream, unlock=False)
        try:
            yield
        finally:
            _file_lock(stream, unlock=True)


def atomic_write_json(path: Path, value: object, *, prefix: str = ".state-") -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    atomic_write_bytes(path, text.encode(), prefix=prefix)


def atomic_write_bytes(path: Path, data: bytes, *, prefix: str = ".state-") -> None:
    """写临时文件、刷盘、再替换；失败时不留半份内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)


def _open_lock(path: Path) -> BinaryIO:
    """打开锁文件；Windows 上新建或查毒扫描期间会短暂占用，稍后重试同一操作。"""
    deadline = time.monotonic() + 2.0
    while True:
        try:
            return path.open("a+b")
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _replace(source: str, target: Path) -> None:
    deadline = time.monotonic() + 2.0
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def _file_lock(stream: BinaryIO, *, unlock: bool) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(  # type: ignore[attr-defined]
            stream.fileno(),
            fcntl.LOCK_UN if unlock else fcntl.LOCK_EX,  # type: ignore[attr-defined]
        )
