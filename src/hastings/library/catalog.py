"""库的基础索引（0.8 B）。

索引按**精确模块版本**生成：条目 ID、标题、别名、可筛选字段与字符数，全部由程序从已发布
的条目原文提取，不调用模型，也不生成同义摘要。短条目本身就是查询结果。

缓存可以丢：文件损坏、版本清单变化或指纹对不上时按磁盘重建；已发布条目的内容与清单
不符则按内容损坏报错，不能在同一 ``revision_id`` 下悄悄接受新内容。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hastings.library.records import parse_entry
from hastings.library.storage import LibraryStorage, atomic_write_text
from hastings.workspace.errors import WorkspaceError

__all__ = ["CACHE_SCHEMA", "CatalogEntry", "CatalogRevision", "LibraryCatalog"]

CACHE_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """索引里的一条：只够定位与筛选，原文仍要按需读取。"""

    entry_id: str
    kind: str
    title: str
    aliases: tuple[str, ...]
    fields: dict[str, Any]
    chars: int
    digest: str

    def view(self) -> dict[str, object]:
        return {
            "entry_id": self.entry_id,
            "kind": self.kind,
            "title": self.title,
            "aliases": list(self.aliases),
            "fields": dict(self.fields),
            "chars": self.chars,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class CatalogRevision:
    """一个已发布版本的目录：只含结构字段，不含任何作品状态。"""

    library_id: str
    module_id: str
    revision_id: str
    module_kind: str
    module_name: str
    entries: tuple[CatalogEntry, ...]

    def view(self) -> dict[str, object]:
        return {
            "library_id": self.library_id,
            "module_id": self.module_id,
            "revision_id": self.revision_id,
            "kind": self.module_kind,
            "name": self.module_name,
            "entries": [item.view() for item in self.entries],
        }


class LibraryCatalog:
    """按版本缓存的结构索引；缓存文件不存在或损坏时重建。"""

    def __init__(self, cache_file: Path | None) -> None:
        self.cache_file = Path(cache_file) if cache_file is not None else None

    def revision(
        self,
        storage: LibraryStorage,
        *,
        library_id: str,
        module_id: str,
        revision_id: str,
        module_kind: str,
        module_name: str,
    ) -> CatalogRevision:
        manifest_path = storage.revision_dir(module_id, revision_id) / "manifest.json"
        manifest_digest = _file_digest(manifest_path)
        cached = self._read_cache().get((library_id, module_id, revision_id))
        if cached is not None and cached[0] == manifest_digest:
            return CatalogRevision(
                library_id=library_id,
                module_id=module_id,
                revision_id=revision_id,
                module_kind=module_kind,
                module_name=module_name,
                entries=tuple(_entry_from(row) for row in cached[1]),
            )
        result = self._build(storage, library_id, module_id, revision_id, module_kind, module_name)
        self._write_cache(library_id, module_id, revision_id, manifest_digest, result.entries)
        return result

    def rebuild(self, storage: LibraryStorage, *, library_id: str) -> int:
        """按磁盘重建整个库的索引，返回已建立的版本数；坏缓存直接覆盖。"""
        self._clear_cache()
        count = 0
        for module_id in storage.module_ids():
            try:
                module = storage.read_module(module_id)
            except WorkspaceError:
                continue
            head = module.get("head")
            if not isinstance(head, str):
                continue
            self.revision(
                storage,
                library_id=library_id,
                module_id=module_id,
                revision_id=head,
                module_kind=str(module.get("kind") or ""),
                module_name=str(module.get("name") or ""),
            )
            count += 1
        return count

    # -- 内部 ---------------------------------------------------------------

    def _build(
        self,
        storage: LibraryStorage,
        library_id: str,
        module_id: str,
        revision_id: str,
        module_kind: str,
        module_name: str,
    ) -> CatalogRevision:
        manifest = storage.read_manifest(module_id, revision_id)
        entries: list[CatalogEntry] = []
        for row in manifest.get("entries") or []:
            if not isinstance(row, dict):
                raise _content_error(module_id, revision_id, "版本清单里的条目不是对象")
            entry_id = str(row.get("entry_id") or "")
            expected = str(row.get("digest") or "")
            text, digest = storage.read_entry(module_id, revision_id, entry_id)
            if digest != expected:
                raise _content_error(
                    module_id,
                    revision_id,
                    f"条目 {entry_id} 的内容与版本清单不符",
                )
            draft = parse_entry(text, module_kind=module_kind)
            if draft.entry_id != entry_id:
                raise _content_error(
                    module_id, revision_id, f"条目 {entry_id} 的 id 与文件名不一致"
                )
            entries.append(
                CatalogEntry(
                    entry_id=draft.entry_id,
                    kind=draft.kind,
                    title=draft.title,
                    aliases=draft.aliases,
                    fields=dict(draft.fields),
                    chars=len(text),
                    digest=digest,
                )
            )
        return CatalogRevision(
            library_id=library_id,
            module_id=module_id,
            revision_id=revision_id,
            module_kind=module_kind,
            module_name=module_name,
            entries=tuple(entries),
        )

    def _read_cache(self) -> dict[tuple[str, str, str], tuple[str, list[dict[str, Any]]]]:
        if self.cache_file is None or not self.cache_file.is_file():
            return {}
        try:
            payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != CACHE_SCHEMA:
            return {}
        rows = payload.get("revisions")
        if not isinstance(rows, dict):
            return {}
        parsed: dict[tuple[str, str, str], tuple[str, list[dict[str, Any]]]] = {}
        for key, value in rows.items():
            parts = key.split("/")
            if len(parts) != 3 or not isinstance(value, dict):
                continue
            digest = value.get("manifest_digest")
            entries = value.get("entries")
            if not isinstance(digest, str) or not isinstance(entries, list):
                continue
            parsed[(parts[0], parts[1], parts[2])] = (digest, entries)
        return parsed

    def _write_cache(
        self,
        library_id: str,
        module_id: str,
        revision_id: str,
        manifest_digest: str,
        entries: tuple[CatalogEntry, ...],
    ) -> None:
        if self.cache_file is None:
            return
        rows = self._read_cache()
        rows[(library_id, module_id, revision_id)] = (
            manifest_digest,
            [item.view() for item in entries],
        )
        payload = {
            "schema_version": CACHE_SCHEMA,
            "revisions": {
                f"{key[0]}/{key[1]}/{key[2]}": {
                    "manifest_digest": value[0],
                    "entries": value[1],
                }
                for key, value in rows.items()
            },
        }
        try:
            atomic_write_text(
                self.cache_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            )
        except OSError:
            # 索引是可重建派生物：写不进去不影响读原文。
            return

    def _clear_cache(self) -> None:
        if self.cache_file is None:
            return
        try:
            self.cache_file.unlink(missing_ok=True)
        except OSError:
            return


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise WorkspaceError(
            code="MODULE_CONTENT_INVALID",
            where=f"library.manifest:{path.parent.name}",
            why=f"版本清单读不到：{exc.strerror or exc}",
            fix="从备份恢复这一版",
            details={"path": str(path)},
        ) from exc


def _entry_from(row: dict[str, Any]) -> CatalogEntry:
    return CatalogEntry(
        entry_id=str(row.get("entry_id") or ""),
        kind=str(row.get("kind") or ""),
        title=str(row.get("title") or ""),
        aliases=tuple(str(item) for item in row.get("aliases") or []),
        fields=dict(row.get("fields") or {}),
        chars=int(row.get("chars") or 0),
        digest=str(row.get("digest") or ""),
    )


def _content_error(module_id: str, revision_id: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="MODULE_CONTENT_INVALID",
        where=f"library.revision:{module_id}/{revision_id}",
        why=why,
        fix="从备份恢复这一版；不能在同一 revision 下接受新内容",
        details={"module_id": module_id, "revision_id": revision_id},
    )
