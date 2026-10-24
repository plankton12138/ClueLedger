"""全局模块库的磁盘状态与锁（0.8 B）。

库是作者内容，不放在应用可清理的缓存里：``library.json`` 只记 ``library_id`` 与格式版本，
模块与版本按目录展开，发布目录不可改写。这里只处理「文件在哪、怎么写、锁多久」，
业务判断在 ``modules.py``。

写文件的顺序固定为：先写 staging 并核对内容，再整目录移入 ``revisions/``，最后在模块锁内
更新 ``module.json``。中途崩溃最多留下一个未登记的版本目录，不会被查询读到。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hastings.library.ids import is_id, new_id
from hastings.workspace.errors import WorkspaceError

__all__ = [
    "DEFAULT_LIBRARY_DIR",
    "LIBRARY_FORMAT",
    "LibraryIdentity",
    "LibraryStorage",
    "atomic_write_text",
    "now_iso",
]

LIBRARY_FORMAT = 1
LIBRARY_FILE = "library.json"
DEFAULT_LIBRARY_DIR = Path.home() / "Documents" / "ClueLedger" / "ModuleLibrary"

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def now_iso() -> str:
    """库内时间统一用带时区的 ISO 文本，便于排序与阅读。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


def atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + 原子替换；失败时清理临时文件，不留下半份内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_retry(Path(temp), path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temp)
        raise


def _replace_with_retry(source: Path, target: Path, *, attempts: int = 10) -> None:
    """原子替换带短暂重试。

    Windows 上目录/文件刚写完时可能被索引或扫描进程短暂占用，替换会报「拒绝访问」。
    这种占用通常在几十毫秒内释放，重试比让发布失败更符合实际；重试耗尽仍按原错误上报。
    """
    for index in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if index == attempts - 1:
                raise
            time.sleep(0.02 * (index + 1))


@dataclass(frozen=True, slots=True)
class LibraryIdentity:
    library_id: str
    root: Path
    created_at: str


class LibraryStorage:
    """一个本地库的文件布局；不缓存内容，读到的就是磁盘上的。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser()

    # -- 建库与身份 ---------------------------------------------------------

    def existing(self) -> LibraryIdentity | None:
        """库不存在返回 None；文件损坏直接报错，不按空库继续写。"""
        path = self.root / LIBRARY_FILE
        if not path.is_file():
            return None
        payload = _read_json(path, where="library.identity", code="LIBRARY_INVALID")
        library_id = payload.get("library_id")
        if not is_id(library_id, "library"):
            raise WorkspaceError(
                code="LIBRARY_INVALID",
                where="library.identity",
                why=f"{LIBRARY_FILE} 里的 library_id 不可识别：{library_id!r}",
                fix="从备份恢复这个文件，或确认这个目录确实是模块库",
                details={"path": str(path)},
            )
        return LibraryIdentity(
            library_id=str(library_id),
            root=self.root,
            created_at=str(payload.get("created_at") or ""),
        )

    def create(self) -> LibraryIdentity:
        """首次写入时建库；已存在则原样返回，不重新生成 ID。"""
        existing = self.existing()
        if existing is not None:
            return existing
        identity = LibraryIdentity(
            library_id=new_id("library"), root=self.root, created_at=now_iso()
        )
        try:
            (self.root / "modules").mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                self.root / LIBRARY_FILE,
                json.dumps(
                    {
                        "schema_version": LIBRARY_FORMAT,
                        "library_id": identity.library_id,
                        "created_at": identity.created_at,
                        "requests": {},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
        except OSError as exc:
            raise WorkspaceError(
                code="LIBRARY_WRITE_FAILED",
                where="library.create",
                why=f"无法在 {self.root} 建库：{exc.strerror or exc}",
                fix="换一个有写权限的位置，或检查磁盘剩余空间",
                details={"root": str(self.root)},
            ) from exc
        return identity

    def read_document(self) -> dict[str, Any] | None:
        """库根状态：身份与新建请求的幂等结果。库不存在返回 None。"""
        path = self.root / LIBRARY_FILE
        if not path.is_file():
            return None
        return _read_json(path, where="library.document", code="LIBRARY_INVALID")

    def write_document(self, payload: Mapping[str, object]) -> None:
        try:
            atomic_write_text(
                self.root / LIBRARY_FILE,
                json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
            )
        except OSError as exc:
            raise WorkspaceError(
                code="LIBRARY_WRITE_FAILED",
                where="library.document",
                why=f"无法写入库状态：{exc.strerror or exc}",
                fix="检查库目录权限与磁盘空间后重试",
                details={"root": str(self.root)},
            ) from exc

    # -- 模块 ---------------------------------------------------------------

    def module_dir(self, module_id: str) -> Path:
        _check_module_id(module_id)
        return self.root / "modules" / module_id

    def module_ids(self) -> tuple[str, ...]:
        base = self.root / "modules"
        if not base.is_dir():
            return ()
        return tuple(
            sorted(
                item.name for item in base.iterdir() if item.is_dir() and is_id(item.name, "module")
            )
        )

    def read_module(self, module_id: str) -> dict[str, Any]:
        path = self.module_dir(module_id) / "module.json"
        if not path.is_file():
            raise WorkspaceError(
                code="MODULE_NOT_FOUND",
                where=f"library.module:{module_id}",
                why="这个模块不在库里，或只剩发布版本目录",
                fix="刷新模块列表；需要恢复时从备份导入",
                details={"module_id": module_id},
            )
        return _read_json(path, where=f"library.module:{module_id}", code="LIBRARY_INVALID")

    def write_module(self, module_id: str, payload: Mapping[str, object]) -> None:
        try:
            atomic_write_text(
                self.module_dir(module_id) / "module.json",
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            )
        except OSError as exc:
            raise WorkspaceError(
                code="LIBRARY_WRITE_FAILED",
                where=f"library.module:{module_id}",
                why=f"无法写入模块状态：{exc.strerror or exc}",
                fix="检查库目录权限与磁盘空间后重试",
                details={"module_id": module_id},
            ) from exc

    # -- 版本 ---------------------------------------------------------------

    def revision_dir(self, module_id: str, revision_id: str) -> Path:
        _check_module_id(module_id)
        _check_revision_id(revision_id)
        return self.module_dir(module_id) / "revisions" / revision_id

    def revision_ids(self, module_id: str) -> tuple[str, ...]:
        base = self.module_dir(module_id) / "revisions"
        if not base.is_dir():
            return ()
        return tuple(
            sorted(
                item.name
                for item in base.iterdir()
                if item.is_dir() and is_id(item.name, "revision")
            )
        )

    def revision_exists(self, module_id: str, revision_id: str) -> bool:
        if not is_id(revision_id, "revision"):
            return False
        return (self.module_dir(module_id) / "revisions" / revision_id).is_dir()

    def read_manifest(self, module_id: str, revision_id: str) -> dict[str, Any]:
        path = self.revision_dir(module_id, revision_id) / "manifest.json"
        if not path.is_file():
            raise WorkspaceError(
                code="MODULE_REVISION_NOT_FOUND",
                where=f"library.revision:{module_id}/{revision_id}",
                why="指定版本不存在",
                fix="刷新版本列表；不要用最新版替代已固定的版本",
                details={"module_id": module_id, "revision_id": revision_id},
            )
        return _read_json(
            path, where=f"library.revision:{module_id}/{revision_id}", code="MODULE_CONTENT_INVALID"
        )

    def entry_path(self, module_id: str, revision_id: str, entry_id: str) -> Path:
        return self.revision_dir(module_id, revision_id) / "entries" / f"{entry_id}.md"

    def read_entry(self, module_id: str, revision_id: str, entry_id: str) -> tuple[str, str]:
        """返回条目原文与磁盘字节指纹；文件缺失或不可读按内容损坏处理。"""
        path = self.entry_path(module_id, revision_id, entry_id)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise WorkspaceError(
                code="MODULE_CONTENT_INVALID",
                where=f"library.record:{module_id}/{revision_id}/{entry_id}",
                why=f"条目文件读不到：{exc.strerror or exc}",
                fix="从备份恢复这一版；不能在同一 revision 下改写内容",
                details={"module_id": module_id, "revision_id": revision_id, "entry_id": entry_id},
            ) from exc
        return data.decode("utf-8"), hashlib.sha256(data).hexdigest()

    def write_revision(
        self,
        module_id: str,
        revision_id: str,
        manifest: Mapping[str, object],
        entries: Mapping[str, str],
    ) -> bool:
        """整目录发布一个版本；已存在则核对后复用，返回是否真的写入。"""
        target = self.revision_dir(module_id, revision_id)
        if target.exists():
            existing = self.read_manifest(module_id, revision_id)
            if dict(existing) != dict(manifest):
                raise WorkspaceError(
                    code="MODULE_CONTENT_INVALID",
                    where=f"library.revision:{module_id}/{revision_id}",
                    why="同名版本已经存在，内容与本次发布不同",
                    fix="重新发起一次发布；已发布的版本不能改写",
                    details={"module_id": module_id, "revision_id": revision_id},
                )
            return False

        staging = self.root / "staging" / module_id / revision_id
        if staging.exists():
            shutil.rmtree(staging)
        try:
            entries_dir = staging / "entries"
            entries_dir.mkdir(parents=True, exist_ok=True)
            for entry_id, text in entries.items():
                (entries_dir / f"{entry_id}.md").write_text(text, encoding="utf-8", newline="\n")
                written = (entries_dir / f"{entry_id}.md").read_bytes()
                if written != text.encode("utf-8"):
                    raise WorkspaceError(
                        code="LIBRARY_WRITE_FAILED",
                        where=f"library.revision:{module_id}/{revision_id}",
                        why="写入的条目与准备的内容不一致",
                        fix="检查磁盘剩余空间后重试；不要用已损坏的版本作为当前版本",
                        details={"entry_id": entry_id},
                    )
            atomic_write_text(
                staging / "manifest.json",
                json.dumps(dict(manifest), ensure_ascii=False, indent=2) + "\n",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            _replace_with_retry(staging, target)
        except OSError as exc:
            raise WorkspaceError(
                code="LIBRARY_WRITE_FAILED",
                where=f"library.revision:{module_id}/{revision_id}",
                why=f"发布版本时写入失败：{exc.strerror or exc}",
                fix="检查库目录权限与磁盘空间；重试时会复用已完成的版本",
                details={"module_id": module_id, "revision_id": revision_id},
            ) from exc
        finally:
            if staging.exists():
                with suppress(OSError):
                    shutil.rmtree(staging)
        return True

    # -- 锁 -----------------------------------------------------------------

    @contextmanager
    def root_lock(self) -> Iterator[None]:
        """库级写入（建库、新建模块、变体）用根锁；与模块锁互不复用。"""
        with self._locked(self.root / ".lock"):
            yield

    @contextmanager
    def lock(self, module_id: str) -> Iterator[None]:
        """同一模块串行写入：进程内线程锁 + 锁文件，跨进程也不并发改 head。"""
        with self._locked(self.module_dir(module_id) / ".lock"):
            yield

    @contextmanager
    def _locked(self, path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        key = str(path.resolve())
        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
        with thread_lock:
            handle = open(path, "a+b")  # noqa: SIM115 - 生命周期与 with 一致
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                _lock_file(handle)
                yield
            finally:
                with suppress(OSError):
                    _unlock_file(handle)
                handle.close()


def _read_json(path: Path, *, where: str, code: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WorkspaceError(
            code=code,
            where=where,
            why=f"读不到文件：{exc.strerror or exc}",
            fix="确认库路径可访问；文件损坏时从备份恢复，不要用空内容覆盖",
            details={"path": str(path)},
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError(
            code=code,
            where=where,
            why=f"文件不是可解析的 JSON：{exc}",
            fix="保留原文件并从备份恢复；不要把无法解析的文件当作空库",
            details={"path": str(path)},
        ) from exc
    if not isinstance(payload, dict):
        raise WorkspaceError(
            code=code,
            where=where,
            why="文件内容不是一个对象",
            fix="保留原文件并从备份恢复",
            details={"path": str(path)},
        )
    return payload


def _check_module_id(module_id: str) -> None:
    if is_id(module_id, "module"):
        return
    raise WorkspaceError(
        code="MODULE_NOT_FOUND",
        where="library.module",
        why=f"模块标识不合法：{module_id}",
        fix="使用列表返回的 module_id；不要拼接路径",
        details={"module_id": module_id},
    )


def _check_revision_id(revision_id: str) -> None:
    if is_id(revision_id, "revision"):
        return
    raise WorkspaceError(
        code="MODULE_REVISION_NOT_FOUND",
        where="library.revision",
        why=f"版本标识不合法：{revision_id}",
        fix="使用模块版本列表返回的 revision_id",
        details={"revision_id": revision_id},
    )


def _lock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import importlib

        fcntl = importlib.import_module("fcntl")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import importlib

        fcntl = importlib.import_module("fcntl")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
