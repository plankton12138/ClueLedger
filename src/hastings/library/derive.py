"""用当前组合派生新作品（0.8 D）。

派生只复制**装配**：新作品固定同一批模块版本（可替换其中几项），但没有正文、没有灵感、
没有会话与观察记录。作者从空白开始写，库里的内容仍只有一份。

中断恢复靠目标目录里的派生标记：同一请求键重试会继续未完成的装配写入，不会在磁盘上
留下第二份半成品；目标目录已经存在且不是这次请求建的，直接冲突。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hastings.library.modules import ModuleLibrary
from hastings.library.storage import atomic_write_text, now_iso
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import Assembly, AssemblyStore, Binding, ModuleRef

__all__ = ["DERIVE_MARKER", "DeriveResult", "derive_workspace"]

DERIVE_MARKER = ".clueledger/derive.json"
DERIVE_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class DeriveResult:
    root: Path
    assembly: dict[str, object]
    recovered: bool


def derive_workspace(
    library: ModuleLibrary,
    *,
    source: Workspace,
    source_workspace_id: str,
    target: Path,
    replacements: Mapping[str, str] | None = None,
    expected_digest: str | None,
    request_key: str,
) -> DeriveResult:
    """从 ``source`` 的装配派生一个空正文作品；同一请求键可安全重试。"""
    key = _check_key(request_key)
    resolved = Path(target).expanduser().resolve()
    if resolved == Path(source.root).resolve() or Path(source.root).resolve() in resolved.parents:
        raise WorkspaceError(
            code="DERIVE_TARGET_CONFLICT",
            where="derive.target",
            why="新作品不能建在当前作品里面",
            fix="换一个与当前作品并列的目录名",
            details={"target": str(resolved)},
        )

    marker_path = resolved / DERIVE_MARKER
    if resolved.exists():
        marker = _read_marker(marker_path, resolved)
        if marker is None or marker.get("request_key") != key:
            raise WorkspaceError(
                code="DERIVE_TARGET_CONFLICT",
                where="derive.target",
                why=f"目标目录已经存在：{resolved}",
                fix="换一个名称；派生不会覆盖已有目录",
                details={"target": str(resolved)},
            )
        # 同一次请求的重试：装配没写完就补上，写完就直接返回。
        assembly = marker.get("assembly")
        if not isinstance(assembly, dict):
            raise WorkspaceError(
                code="DERIVE_MARKER_INVALID",
                where="derive.recover",
                why="派生标记里没有装配内容",
                fix="删除这个空目录后重新派生；不要手工改标记",
                details={"path": str(marker_path)},
            )
        recovered = not (resolved / ".clueledger/assembly.json").is_file()
        if recovered:
            _write_assembly(resolved, assembly, key=f"{key}:assembly")
        return DeriveResult(root=resolved, assembly=_assembly_view(resolved), recovered=recovered)

    source_store = AssemblyStore(Path(source.root))
    current = source_store.read()
    if source_store.digest() != expected_digest:
        raise _stale(expected_digest, source_store.digest())
    if library.library_id is None or current.bindings == ():
        raise WorkspaceError(
            code="DERIVE_TARGET_CONFLICT",
            where="derive.source",
            why="当前作品还没有装任何模块，派生出来也只是空作品",
            fix="先从模块库装入至少一份内容，或用「新建作品」",
        )

    bindings = _with_replacements(library, current, replacements or {})
    assembly = {
        "schema_version": current.schema_version,
        "library_id": current.library_id,
        "bindings": [binding.view() for binding in bindings],
        "links": [list(link) for link in current.links],
    }
    try:
        Workspace.create(resolved)
        atomic_write_text(
            marker_path,
            json.dumps(
                {
                    "schema_version": DERIVE_SCHEMA,
                    "request_key": key,
                    "source_workspace_id": source_workspace_id,
                    "created_at": now_iso(),
                    "assembly": assembly,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        _write_assembly(resolved, assembly, key=f"{key}:assembly")
    except OSError as exc:
        with suppress(OSError):
            if resolved.is_dir() and not (resolved / ".clueledger/assembly.json").is_file():
                _remove_empty(resolved)
        raise WorkspaceError(
            code="LIBRARY_WRITE_FAILED",
            where="derive.target",
            why=f"无法创建派生作品：{exc.strerror or exc}",
            fix="检查父目录权限与磁盘空间；重试同一请求会继续未完成的写入",
            details={"target": str(resolved)},
        ) from exc
    return DeriveResult(root=resolved, assembly=_assembly_view(resolved), recovered=False)


def _with_replacements(
    library: ModuleLibrary, current: Assembly, replacements: Mapping[str, str]
) -> list[Binding]:
    bound = {item.module.module_id: item for item in current.bindings}
    for module_id, revision_id in replacements.items():
        if module_id not in bound:
            raise WorkspaceError(
                code="DERIVE_REPLACEMENT_INVALID",
                where="derive.replacements",
                why=f"当前作品没有装入模块 {module_id}",
                fix="只替换已装入的模块；需要新增模块请在派生后再装入",
                details={"module_id": module_id},
            )
        if not library.storage.revision_exists(module_id, revision_id):
            raise WorkspaceError(
                code="MODULE_REVISION_NOT_FOUND",
                where="derive.replacements",
                why=f"模块 {module_id} 没有版本 {revision_id}",
                fix="刷新版本列表；派生不会用最新版顶替指定版本",
                details={"module_id": module_id, "revision_id": revision_id},
            )
    result: list[Binding] = []
    for item in current.bindings:
        revision = replacements.get(item.module.module_id, item.module.revision_id)
        result.append(
            Binding(
                id=item.id,
                module=ModuleRef(
                    library_id=item.module.library_id,
                    module_id=item.module.module_id,
                    revision_id=revision,
                ),
                label=item.label,
            )
        )
    return result


def _write_assembly(root: Path, assembly: Mapping[str, Any], *, key: str) -> None:
    bindings = [
        Binding(
            id=str(item.get("id") or ""),
            module=ModuleRef(
                library_id=str((item.get("module") or {}).get("library_id") or ""),
                module_id=str((item.get("module") or {}).get("module_id") or ""),
                revision_id=str((item.get("module") or {}).get("revision_id") or ""),
            ),
            label=str(item.get("label") or ""),
        )
        for item in assembly.get("bindings") or []
        if isinstance(item, dict)
    ]
    links = tuple(
        (str(link[0]), str(link[1]))
        for link in assembly.get("links") or []
        if isinstance(link, (list, tuple)) and len(link) == 2
    )
    AssemblyStore(root).replace(
        library_id=str(assembly.get("library_id") or "") or None,
        bindings=bindings,
        links=links,
        expected_digest=None,
        request_key=key,
    )


def _assembly_view(root: Path) -> dict[str, object]:
    store = AssemblyStore(root)
    assembly = store.read()
    return {
        **assembly.view(),
        "digest": store.digest(),
    }


def _read_marker(path: Path, root: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError(
            code="DERIVE_MARKER_INVALID",
            where="derive.recover",
            why=f"派生标记无法解析：{exc}",
            fix="删除这个空目录后重新派生；不要手工改标记",
            details={"path": str(path), "root": str(root)},
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != DERIVE_SCHEMA:
        raise WorkspaceError(
            code="DERIVE_MARKER_INVALID",
            where="derive.recover",
            why="派生标记的结构版本不受支持",
            fix="删除这个空目录后重新派生",
            details={"path": str(path)},
        )
    return payload


def _stale(expected: str | None, current: str | None) -> WorkspaceError:
    return WorkspaceError(
        code="ASSEMBLY_STALE_CONFLICT",
        where="derive.source",
        why="当前作品的装配在读取之后被改过",
        fix="刷新装配后重新派生；不要用旧页面上的组合新建作品",
        details={"expected": expected, "current": current},
    )


def _check_key(key: str) -> str:
    text = str(key or "").strip()
    if not 8 <= len(text) <= 120:
        raise WorkspaceError(
            code="REQUEST_KEY_INVALID",
            where="derive.request",
            why="请求键长度需要在 8–120 字符之间",
            fix="由调用方为这次派生生成一个稳定且不重复的键",
        )
    return text


def _remove_empty(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    root.rmdir()
