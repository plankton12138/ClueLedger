"""旧作品资料的显式导入（0.8 B）。

旧作品里的线索、真相与人物小传仍按原路径保存。导入只做一件事：把文件内容作为条目
发布到全局库，再把精确版本绑定到当前作品。原文留在原处，不移动、不改写；重复导入
同一内容复用同一模块与版本，内容变化时发布新版本并更新绑定。

导入清单记录在 ``.clueledger/library-imports.json``，用于两件事：幂等判断，以及让上下文
装配不要同时注入原文件与模块副本（避免同一资料读两遍）。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from hastings.library.bindings import AssemblyService
from hastings.library.modules import ModuleLibrary
from hastings.library.records import EntryDraft
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import AssemblyStore, Binding, ModuleRef

__all__ = [
    "IMPORT_PATH",
    "ImportedSource",
    "import_legacy_materials",
    "imported_paths",
    "read_import_info",
]

IMPORT_PATH = ".clueledger/library-imports.json"
IMPORT_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class ImportedSource:
    path: str
    module_kind: str
    entry_kind: str
    entry_id: str
    title: str
    name: str
    text: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def imported_paths(workspace: Workspace) -> frozenset[str]:
    """已导入的原文件路径；清单损坏时按未导入处理，不影响写作。"""
    try:
        payload = json.loads((Path(workspace.root) / IMPORT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    rows = payload.get("imports") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        return frozenset()
    return frozenset(str(path) for path in rows)


def read_import_info(workspace: Workspace) -> dict[str, object]:
    """读取导入清单：给界面显示已导入项与下一次写入的 CAS 期望值。"""
    store = _ImportStore(Path(workspace.root))
    rows = store.read()
    return {
        "digest": store.digest(),
        "items": [
            {"path": path, **{key: value for key, value in row.items()}}
            for path, row in sorted(rows.items())
        ],
    }


def import_legacy_materials(
    library: ModuleLibrary,
    workspace: Workspace,
    *,
    expected_digest: str | None,
    request_key: str,
) -> dict[str, object]:
    """导入旧资料并绑定；返回每个来源的动作与最终装配。"""
    sources = _legacy_sources(workspace)
    store = _ImportStore(Path(workspace.root))
    current = store.read()
    if store.digest() != expected_digest:
        raise WorkspaceError(
            code="LIBRARY_IMPORT_STALE_CONFLICT",
            where="library.import",
            why="导入清单在读取之后被改过",
            fix="刷新作品后重做导入；不要覆盖另一次导入的结果",
            details={"expected": expected_digest, "current": store.digest()},
        )

    assembly_digest = AssemblyStore(Path(workspace.root)).digest()
    if not sources:
        return {
            "items": [],
            "assembly": AssemblyService(library, workspace).view(),
            "digest": store.digest(),
        }
    manifest: dict[str, object] = dict(current)
    items: list[dict[str, object]] = []
    changes: list[tuple[str, str]] = []
    workspace_key = hashlib.sha256(str(Path(workspace.root)).encode("utf-8")).hexdigest()[:16]
    for source in sources:
        existing = current.get(source.path)
        module_id, revision_id, action = _publish_source(
            library,
            source,
            existing if isinstance(existing, dict) else None,
            workspace_key=workspace_key,
        )
        manifest[source.path] = {
            "digest": source.digest,
            "module_id": module_id,
            "revision_id": revision_id,
            "kind": source.module_kind,
            "name": source.name,
        }
        changes.append((module_id, revision_id))
        items.append(
            {
                "path": source.path,
                "module_id": module_id,
                "revision_id": revision_id,
                "kind": source.module_kind,
                "name": source.name,
                "action": action,
            }
        )

    store.write(manifest, expected_digest=expected_digest)
    assembly = _bind_changes(
        library,
        workspace,
        changes,
        expected_digest=assembly_digest,
        request_key=f"import-assembly:{hashlib.sha256(request_key.encode()).hexdigest()[:16]}",
    )
    return {
        "items": items,
        "assembly": assembly,
        "digest": store.digest(),
    }


def _legacy_sources(workspace: Workspace) -> tuple[ImportedSource, ...]:
    rows: list[ImportedSource] = []
    for path, module_kind, entry_kind, entry_id, title, name in (
        ("设定集/线索.md", "clues", "clue_plan", "legacy-clues", "线索", "线索（导入）"),
        ("设定集/真相.md", "truth", "truth", "legacy-truth", "真相", "真相（导入）"),
    ):
        text = _read_optional(workspace, path)
        if text:
            rows.append(ImportedSource(path, module_kind, entry_kind, entry_id, title, name, text))
    for entry in workspace.catalog().characters:
        text = _read_optional(workspace, entry.path)
        if not text:
            continue
        suffix = hashlib.sha256(entry.path.encode("utf-8")).hexdigest()[:12]
        rows.append(
            ImportedSource(
                path=entry.path,
                module_kind="characters",
                entry_kind="character",
                entry_id=f"legacy-char-{suffix}",
                title=entry.display_title or entry.title or entry.path,
                name=f"{entry.display_title or entry.title or '人物'}（导入）",
                text=text,
            )
        )
    return tuple(rows)


def _read_optional(workspace: Workspace, path: str) -> str:
    try:
        return workspace.read(path).text.strip()
    except WorkspaceError:
        return ""


def _publish_source(
    library: ModuleLibrary,
    source: ImportedSource,
    existing: Mapping[str, object] | None,
    *,
    workspace_key: str,
) -> tuple[str, str, str]:
    key = (
        f"import:{workspace_key}:"
        f"{hashlib.sha256(source.path.encode()).hexdigest()[:12]}:{source.digest[:12]}"
    )
    draft = EntryDraft(
        entry_id=source.entry_id,
        kind=source.entry_kind,
        title=source.title,
        body=source.text,
    )
    if existing is not None and existing.get("digest") == source.digest:
        module_id = str(existing.get("module_id") or "")
        revision_id = str(existing.get("revision_id") or "")
        if module_id and revision_id and _revision_exists(library, module_id, revision_id):
            return module_id, revision_id, "unchanged"

    module_id = str((existing or {}).get("module_id") or "")
    head = _module_head(library, module_id) if module_id else None
    if module_id and head:
        try:
            published = library.publish(
                module_id=module_id,
                base_revision=head,
                expected_head=head,
                entries=[draft],
                request_key=f"{key}:publish",
            )
        except WorkspaceError:
            published = None
        if published is not None:
            return module_id, published.revision_id, "published"

    created = library.create_module(
        kind=source.module_kind,
        name=source.name,
        entries=[draft],
        request_key=f"{key}:create",
    )
    module = created.get("module")
    revision = created.get("revision")
    module_row = module if isinstance(module, Mapping) else {}
    revision_row = revision if isinstance(revision, Mapping) else {}
    return (
        str(module_row.get("module_id") or ""),
        str(revision_row.get("revision_id") or ""),
        "created",
    )


def _revision_exists(library: ModuleLibrary, module_id: str, revision_id: str) -> bool:
    return library.storage.revision_exists(module_id, revision_id)


def _module_head(library: ModuleLibrary, module_id: str) -> str | None:
    try:
        return library.module(module_id).head
    except WorkspaceError:
        return None


def _bind_changes(
    library: ModuleLibrary,
    workspace: Workspace,
    changes: list[tuple[str, str]],
    *,
    expected_digest: str | None,
    request_key: str,
) -> dict[str, object]:
    if not changes:
        return AssemblyService(library, workspace).view()
    store = AssemblyStore(Path(workspace.root))
    assembly = store.read()
    bindings = list(assembly.bindings)
    for module_id, revision_id in changes:
        if not module_id or not revision_id:
            continue
        found = next(
            (item for item in bindings if item.module.module_id == module_id),
            None,
        )
        module = ModuleRef(
            library_id=library.library_id or "",
            module_id=module_id,
            revision_id=revision_id,
        )
        if found is None:
            bindings.append(Binding(id=_new_binding_id(), module=module, label=""))
        else:
            bindings[bindings.index(found)] = Binding(id=found.id, module=module, label=found.label)
    return AssemblyService(library, workspace).replace(
        bindings=bindings,
        links=assembly.links,
        expected_digest=expected_digest,
        request_key=request_key,
    )


def _new_binding_id() -> str:
    from hastings.library.ids import new_id

    return new_id("binding")


class _ImportStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / IMPORT_PATH

    def read(self) -> dict[str, dict[str, object]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise WorkspaceError(
                code="LIBRARY_IMPORT_INVALID",
                where="library.import",
                why=f"导入清单无法解析：{exc}",
                fix="保留原文件并从备份恢复；不要把损坏的清单当成空清单",
                details={"path": str(self.path)},
            ) from exc
        rows = payload.get("imports") if isinstance(payload, dict) else None
        if not isinstance(rows, dict) or payload.get("schema_version") != IMPORT_SCHEMA:
            raise WorkspaceError(
                code="LIBRARY_IMPORT_INVALID",
                where="library.import",
                why="导入清单格式不受支持",
                fix="用当前版本重新导入；旧格式不会被猜测解析",
                details={"path": str(self.path)},
            )
        return {str(key): dict(value) for key, value in rows.items() if isinstance(value, dict)}

    def digest(self) -> str | None:
        try:
            return hashlib.sha256(self.path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise WorkspaceError(
                code="LIBRARY_IMPORT_INVALID",
                where="library.import",
                why=f"读不到导入清单：{exc.strerror or exc}",
                fix="检查作品目录权限；文件损坏时从备份恢复",
                details={"path": str(self.path)},
            ) from exc

    def write(self, rows: Mapping[str, object], *, expected_digest: str | None) -> None:
        current = self.digest()
        if current != expected_digest:
            raise WorkspaceError(
                code="LIBRARY_IMPORT_STALE_CONFLICT",
                where="library.import",
                why="导入清单在读取之后被改过",
                fix="刷新作品后重做导入",
                details={"expected": expected_digest, "current": current},
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": IMPORT_SCHEMA, "imports": dict(rows)}
        handle, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".imports.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        except OSError as exc:
            with suppress(OSError):
                os.unlink(temp)
            raise WorkspaceError(
                code="LIBRARY_IMPORT_WRITE_FAILED",
                where="library.import",
                why=f"无法写入导入清单：{exc.strerror or exc}",
                fix="检查作品目录权限与磁盘空间后重试",
            ) from exc
