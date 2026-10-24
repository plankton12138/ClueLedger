"""作品装配：绑定哪些模块版本（0.8 B）。

作品不复制模块原文，只在 ``.clueledger/assembly.json`` 里记录精确版本：

```text
作品/正文/*.md
作品/.clueledger/assembly.json
```

这份文件属于作者内容：由装配操作按 CAS 改写，不进应用缓存，也不在设定集目录里，
因此普通文件工具不会把它当作可编辑的正文。装、卸、升级都只改这一份文件，库里的原文
与其他作品不受影响。

这里只负责「存什么、怎么原子替换」，不校验模块是否真的存在——那需要库服务，留给
装配接口与前端一起接。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hastings.library.ids import is_id
from hastings.workspace.errors import WorkspaceError

__all__ = [
    "ASSEMBLY_PATH",
    "Assembly",
    "AssemblyStore",
    "Binding",
    "ModuleRef",
]

ASSEMBLY_PATH = ".clueledger/assembly.json"
ASSEMBLY_SCHEMA = 1
MAX_BINDINGS = 200
MAX_REQUEST_KEYS = 50

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True, slots=True)
class ModuleRef:
    library_id: str
    module_id: str
    revision_id: str

    def view(self) -> dict[str, str]:
        return {
            "library_id": self.library_id,
            "module_id": self.module_id,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True, slots=True)
class Binding:
    id: str
    module: ModuleRef
    label: str = ""

    def view(self) -> dict[str, object]:
        return {"id": self.id, "module": self.module.view(), "label": self.label}


@dataclass(frozen=True, slots=True)
class Assembly:
    """一个作品的装配清单；没有绑定就是空清单，不是错误。"""

    schema_version: int = ASSEMBLY_SCHEMA
    library_id: str | None = None
    bindings: tuple[Binding, ...] = ()
    links: tuple[tuple[str, str], ...] = field(default=())

    def view(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "library_id": self.library_id,
            "bindings": [binding.view() for binding in self.bindings],
            "links": [list(link) for link in self.links],
        }

    def binding(self, binding_id: str) -> Binding | None:
        return next((item for item in self.bindings if item.id == binding_id), None)


class AssemblyStore:
    """按作品读写装配清单；写入带 CAS 与请求键，重试不产生第二份结果。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / ASSEMBLY_PATH

    def read(self) -> Assembly:
        payload = self._read_document()
        if payload is None:
            return Assembly()
        return _assembly_from(payload, where=str(self.path))

    def digest(self) -> str | None:
        """当前文件的字节指纹；没有装配文件时为 None（首次写入的 CAS 期望值）。"""
        try:
            return hashlib.sha256(self.path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise WorkspaceError(
                code="ASSEMBLY_WRITE_FAILED",
                where="workspace.assembly",
                why=f"读不到装配清单：{exc.strerror or exc}",
                fix="检查作品目录权限；文件损坏时从备份恢复",
                details={"path": str(self.path)},
            ) from exc

    def replace(
        self,
        *,
        library_id: str | None,
        bindings: Sequence[Binding],
        links: Sequence[tuple[str, str]] = (),
        expected_digest: str | None,
        request_key: str,
    ) -> Assembly:
        """整份替换装配；指纹不符说明别人改过，直接冲突，不做三方合并。"""
        key = _check_key(request_key)
        checked = _checked_bindings(library_id, bindings)
        assembly = Assembly(library_id=library_id, bindings=checked, links=tuple(links))
        with self._lock():
            current = self._read_document()
            replay = _replay(current, key, assembly)
            if replay is not None:
                return _assembly_from(replay, where=str(self.path))
            if self.digest() != expected_digest:
                raise WorkspaceError(
                    code="ASSEMBLY_STALE_CONFLICT",
                    where="workspace.assembly",
                    why="装配清单在读取之后被改过",
                    fix="刷新作品后重做这次装卸；不要覆盖别人的绑定",
                    details={"expected": expected_digest, "current": self.digest()},
                )
            payload = assembly.view()
            payload["requests"] = _with_request(current, key, assembly)
            self._write(payload)
            return assembly

    # -- 内部 ---------------------------------------------------------------

    def _read_document(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError(
                code="ASSEMBLY_INVALID",
                where="workspace.assembly",
                why=f"装配清单无法解析：{exc}",
                fix="保留原文件并从备份恢复；不要把无法解析的文件当成空装配",
                details={"path": str(self.path)},
            ) from exc
        if not isinstance(payload, dict):
            raise WorkspaceError(
                code="ASSEMBLY_INVALID",
                where="workspace.assembly",
                why="装配清单不是一个对象",
                fix="保留原文件并从备份恢复",
                details={"path": str(self.path)},
            )
        return payload

    def _write(self, payload: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".assembly.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        except OSError as exc:
            with suppress(OSError):
                os.unlink(temp)
            raise WorkspaceError(
                code="ASSEMBLY_WRITE_FAILED",
                where="workspace.assembly",
                why=f"无法写入装配清单：{exc.strerror or exc}",
                fix="检查作品目录权限与磁盘空间后重试",
                details={"path": str(self.path)},
            ) from exc

    @contextmanager
    def _lock(self) -> Any:
        lock_path = self.root / ".clueledger" / ".assembly.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        key = str(lock_path.resolve())
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(key, threading.Lock())
        with lock:
            yield


def _check_key(key: str) -> str:
    text = str(key or "").strip()
    if not 8 <= len(text) <= 120:
        raise WorkspaceError(
            code="REQUEST_KEY_INVALID",
            where="workspace.assembly",
            why="请求键长度需要在 8–120 字符之间",
            fix="由调用方为每次装卸生成一个稳定且不重复的键",
        )
    return text


def _checked_bindings(library_id: str | None, bindings: Sequence[Binding]) -> tuple[Binding, ...]:
    if len(bindings) > MAX_BINDINGS:
        raise WorkspaceError(
            code="ASSEMBLY_INVALID",
            where="workspace.assembly",
            why=f"一个作品最多绑定 {MAX_BINDINGS} 份模块，当前 {len(bindings)}",
            fix="卸下不再使用的模块；未使用的预设不需要全部装入",
            details={"bindings": len(bindings)},
        )
    seen_ids: set[str] = set()
    seen_modules: dict[str, str] = {}
    checked: list[Binding] = []
    for binding in bindings:
        if not binding.id or binding.id in seen_ids:
            raise WorkspaceError(
                code="ASSEMBLY_INVALID",
                where="workspace.assembly",
                why=f"绑定标识重复或为空：{binding.id or '（空）'}",
                fix="每次装入使用新的绑定标识",
                details={"binding_id": binding.id},
            )
        seen_ids.add(binding.id)
        ref = binding.module
        if not (
            is_id(ref.library_id, "library")
            and is_id(ref.module_id, "module")
            and is_id(ref.revision_id, "revision")
        ):
            raise WorkspaceError(
                code="ASSEMBLY_INVALID",
                where="workspace.assembly",
                why=f"模块引用不完整：{ref.view()}",
                fix="按库、模块、版本三层标识绑定；不要只写模块名",
                details=ref.view(),
            )
        if library_id is not None and ref.library_id != library_id:
            raise WorkspaceError(
                code="ASSEMBLY_INVALID",
                where="workspace.assembly",
                why="同一份装配清单里出现了两个库的模块",
                fix="一个作品只绑定当前活动库的版本",
                details={"library_id": library_id, "found": ref.library_id},
            )
        if ref.module_id in seen_modules:
            raise WorkspaceError(
                code="ASSEMBLY_BINDING_CONFLICT",
                where="workspace.assembly",
                why="同一个模块同时绑定了两个版本",
                fix="一个实例在同一作品里只能有一个版本；需要并行安排时另存变体",
                details={"module_id": ref.module_id},
            )
        seen_modules[ref.module_id] = ref.revision_id
        checked.append(binding)
    return tuple(checked)


def _assembly_from(payload: Mapping[str, Any], *, where: str) -> Assembly:
    if payload.get("schema_version") != ASSEMBLY_SCHEMA:
        raise WorkspaceError(
            code="ASSEMBLY_INVALID",
            where="workspace.assembly",
            why=f"不支持的装配格式版本：{payload.get('schema_version')}",
            fix="用当前版本重新保存装配；旧格式不会被猜测解析",
            details={"path": where},
        )
    bindings: list[Binding] = []
    for row in payload.get("bindings") or []:
        if not isinstance(row, dict):
            raise WorkspaceError(
                code="ASSEMBLY_INVALID",
                where="workspace.assembly",
                why="绑定项不是对象",
                fix="保留原文件并从备份恢复",
                details={"path": where},
            )
        module = row.get("module") or {}
        if not isinstance(module, dict):
            raise WorkspaceError(
                code="ASSEMBLY_INVALID",
                where="workspace.assembly",
                why="绑定项缺少模块引用",
                fix="保留原文件并从备份恢复",
                details={"path": where},
            )
        bindings.append(
            Binding(
                id=str(row.get("id") or ""),
                module=ModuleRef(
                    library_id=str(module.get("library_id") or ""),
                    module_id=str(module.get("module_id") or ""),
                    revision_id=str(module.get("revision_id") or ""),
                ),
                label=str(row.get("label") or ""),
            )
        )
    links = tuple(
        (str(item[0]), str(item[1]))
        for item in payload.get("links") or []
        if isinstance(item, (list, tuple)) and len(item) == 2
    )
    return Assembly(
        library_id=payload.get("library_id")
        if isinstance(payload.get("library_id"), str)
        else None,
        bindings=tuple(bindings),
        links=links,
    )


def _replay(
    document: Mapping[str, Any] | None, key: str, assembly: Assembly
) -> dict[str, Any] | None:
    if document is None:
        return None
    requests = document.get("requests")
    if not isinstance(requests, dict):
        return None
    row = requests.get(key)
    if row is None:
        return None
    if not isinstance(row, dict) or row.get("request") != _request_digest(assembly):
        raise WorkspaceError(
            code="REQUEST_KEY_CONFLICT",
            where="workspace.assembly",
            why=f"请求键 {key} 之前用于另一次装配操作",
            fix="为这次装卸生成新的请求键",
            details={"request_key": key},
        )
    stored = row.get("result")
    return dict(stored) if isinstance(stored, dict) else None


def _with_request(
    document: Mapping[str, Any] | None, key: str, assembly: Assembly
) -> dict[str, Any]:
    requests: dict[str, Any] = {}
    if document is not None and isinstance(document.get("requests"), dict):
        requests = {
            str(name): value
            for name, value in document["requests"].items()
            if isinstance(value, dict)
        }
    requests[key] = {"request": _request_digest(assembly), "result": assembly.view()}
    if len(requests) > MAX_REQUEST_KEYS:
        keep = list(requests)[-MAX_REQUEST_KEYS:]
        requests = {name: requests[name] for name in keep}
    return requests


def _request_digest(assembly: Assembly) -> str:
    raw = json.dumps(assembly.view(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
