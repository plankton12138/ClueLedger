"""全局模块库：实例、不可变版本与发布（0.8 B）。

一个库里有多个模块实例，同一种类型可以有很多份；每个实例的内容以**不可变版本**保存，
作品只固定引用某个版本。这里的操作都不调用模型，也不读写任何作品文件。

四条边界：

- **版本不可改写**：发布先写 staging 再整目录移入，同名版本已存在时只核对不改写。
- **head 用 CAS 推进**：调用方带上看到的 head；别人先发布了就返回冲突，不自动合并，
  作者可以选择基于最新版重做或另存变体。
- **请求键幂等**：同键同请求返回上次结果，同键不同请求直接拒绝；中断后按请求键恢复
  已写好但还没登记的版本。
- **库只放作者内容**：作品里的生死、回收、披露状态不写进库；条目字段由预设类型限定。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hastings.library.catalog import CatalogRevision, LibraryCatalog
from hastings.library.ids import new_id, source_path
from hastings.library.presets import spec
from hastings.library.records import (
    EntryDraft,
    entry_digest,
    entry_markdown,
    parse_entry,
    validate_entries,
)
from hastings.library.storage import LibraryIdentity, LibraryStorage, now_iso
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.materials import READ_MAX_CHARS, MaterialLedger, SourceRef

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_NAME_CHARS",
    "MAX_PAGE_SIZE",
    "ModuleInfo",
    "ModuleLibrary",
    "RevisionInfo",
]

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
MAX_NAME_CHARS = 80
#: 每个模块保留的幂等键数量；超过后丢最早的，不让库状态无限增长。
MAX_REQUEST_KEYS = 100
MODULE_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class ModuleInfo:
    module_id: str
    kind: str
    name: str
    head: str | None
    deleted: bool
    created_at: str
    updated_at: str
    forked_from: dict[str, str] | None = None

    def view(self) -> dict[str, object]:
        return {
            "module_id": self.module_id,
            "kind": self.kind,
            "name": self.name,
            "head": self.head,
            "deleted": self.deleted,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "forked_from": dict(self.forked_from) if self.forked_from else None,
        }


@dataclass(frozen=True, slots=True)
class RevisionInfo:
    module_id: str
    revision_id: str
    parent: str | None
    created_at: str
    entry_ids: tuple[str, ...]

    def view(self) -> dict[str, object]:
        return {
            "module_id": self.module_id,
            "revision_id": self.revision_id,
            "parent": self.parent,
            "created_at": self.created_at,
            "entry_ids": list(self.entry_ids),
        }


class ModuleLibrary:
    """一个本地库的门面；写操作按模块串行，读操作直接读磁盘。"""

    def __init__(self, root: Path, *, cache_file: Path | None = None) -> None:
        self.storage = LibraryStorage(root)
        self.catalog = LibraryCatalog(cache_file)

    @property
    def root(self) -> Path:
        return self.storage.root

    @property
    def identity(self) -> LibraryIdentity | None:
        return self.storage.existing()

    @property
    def library_id(self) -> str | None:
        found = self.storage.existing()
        return found.library_id if found is not None else None

    # -- 新建与发布 ---------------------------------------------------------

    def create_module(
        self,
        *,
        kind: str,
        name: str,
        entries: Sequence[EntryDraft | Mapping[str, object]],
        request_key: str,
    ) -> dict[str, object]:
        """新建一个实例并发布第一版；类型与名称先校验，再落到磁盘。"""
        spec(kind)
        checked_name = _check_name(name)
        drafts = _drafts(kind, entries)
        request = _request_digest(
            {"op": "create", "kind": kind, "name": checked_name, "entries": _rows(drafts)}
        )
        key = _check_request_key(request_key)
        with self.storage.root_lock():
            identity = self.storage.create()
            document = self.storage.read_document() or {}
            requests = _requests(document.get("requests"))
            replay = _replay_row(requests, key, request)
            if replay is not None and replay.get("state") == "done":
                stored = replay.get("result")
                return dict(stored) if isinstance(stored, dict) else {}
            if replay is not None:
                # 上次建到一半：沿用同一个 module_id，把首版发完。
                stored = replay.get("result")
                module_id = str((stored or {}).get("module_id") or "")
            else:
                module_id = new_id("module")
                created = now_iso()
                module = {
                    "schema_version": MODULE_SCHEMA,
                    "library_id": identity.library_id,
                    "module_id": module_id,
                    "kind": kind,
                    "name": checked_name,
                    "head": None,
                    "deleted": False,
                    "created_at": created,
                    "updated_at": created,
                    "forked_from": None,
                    "pending": None,
                    "requests": {},
                }
                self.storage.write_module(module_id, module)
                requests[key] = {
                    "request": request,
                    "state": "pending",
                    "result": {"module_id": module_id, "revision": None},
                }
                document["requests"] = _trim(requests)
                self.storage.write_document(document)

            revision = self._first_revision(module_id, drafts, request_key=key)
            module = self.storage.read_module(module_id)
            result: dict[str, object] = {
                "module": _info(module).view(),
                "revision": revision.view(),
            }
            requests[key] = {"request": request, "state": "done", "result": result}
            document["requests"] = _trim(requests)
            self.storage.write_document(document)
            return result

    def publish(
        self,
        *,
        module_id: str,
        base_revision: str | None,
        expected_head: str | None,
        entries: Sequence[EntryDraft | Mapping[str, object]],
        request_key: str,
    ) -> RevisionInfo:
        """发布新版本：head 没按预期变化就冲突；发布不改任何作品的绑定。"""
        key = _check_request_key(request_key)
        with self.storage.lock(module_id):
            module = self.storage.read_module(module_id)
            _require_active(module)
            drafts = _drafts(str(module.get("kind") or ""), entries)
            request = _request_digest(
                {
                    "op": "publish",
                    "module_id": module_id,
                    "base": base_revision,
                    "entries": _rows(drafts),
                }
            )
            # 先看请求键：网络重试时 head 已经前进，重放要返回上次结果而不是冲突。
            replay = _replay(_requests(module.get("requests")), key, request)
            if replay is not None:
                revision_id = str(replay.get("revision_id") or "")
                if revision_id and self.storage.revision_exists(module_id, revision_id):
                    return self._revision_info(module_id, revision_id)
            _check_head(module, expected_head)
            _check_base(module, base_revision)
            revision = self._publish_unlocked(module, drafts, key=key, request=request)
            module["head"] = revision.revision_id
            module["updated_at"] = revision.created_at
            self.storage.write_module(module_id, module)
            return revision

    def save_workspace_revision(
        self,
        *,
        module_id: str,
        base_revision: str,
        entries: Sequence[EntryDraft | Mapping[str, object]],
        request_key: str,
    ) -> RevisionInfo:
        """保存作品自己的内容版本；库的 head 保持不变，其他作品也不跟随。

        仍使用不可变版本和幂等请求，绑定失败后可以用同一请求继续保存。
        base 可以是作品先前保存的版本，不要求它等于库的最新版本。
        """
        key = _check_request_key(request_key)
        with self.storage.lock(module_id):
            module = self.storage.read_module(module_id)
            self.revision(module_id=module_id, revision_id=base_revision)
            drafts = _drafts(str(module.get("kind") or ""), entries)
            request = _request_digest(
                {
                    "op": "workspace-revision",
                    "module_id": module_id,
                    "base": base_revision,
                    "entries": _rows(drafts),
                }
            )
            updated_at = module.get("updated_at")
            revision = self._publish_unlocked(
                module, drafts, key=key, request=request, parent_revision=base_revision
            )
            module["updated_at"] = updated_at
            self.storage.write_module(module_id, module)
            return revision

    def fork(
        self,
        *,
        module_id: str,
        base_revision: str | None,
        name: str,
        request_key: str,
    ) -> dict[str, object]:
        """基于某个版本另存为新实例；新实例有新 module_id，原实例不变。"""
        checked_name = _check_name(name)
        key = _check_request_key(request_key)
        with self.storage.lock(module_id):
            module = self.storage.read_module(module_id)
            _require_active(module)
            revision_id = base_revision or module.get("head")
            if not isinstance(revision_id, str):
                raise WorkspaceError(
                    code="MODULE_REVISION_NOT_FOUND",
                    where=f"library.fork:{module_id}",
                    why="这个模块还没有已发布的版本，不能另存变体",
                    fix="先发布一版，或直接从新建开始",
                    details={"module_id": module_id},
                )
            request = _request_digest(
                {"op": "fork", "module_id": module_id, "base": revision_id, "name": checked_name}
            )
            requests = _requests(module.get("requests"))
            replay = _replay(requests, key, request)
            if replay is not None:
                return replay

            texts = self._revision_texts(module_id, revision_id)
            new_module_id = new_id("module")
            created = now_iso()
            forked: dict[str, Any] = {
                "schema_version": MODULE_SCHEMA,
                "library_id": str(module.get("library_id") or self.library_id or ""),
                "module_id": new_module_id,
                "kind": str(module.get("kind") or ""),
                "name": checked_name,
                "head": None,
                "deleted": False,
                "created_at": created,
                "updated_at": created,
                "forked_from": {"module_id": module_id, "revision_id": revision_id},
                "pending": None,
                "requests": {},
            }
            self.storage.write_module(new_module_id, forked)
            drafts = tuple(
                parse_entry(text, module_kind=str(forked["kind"])) for text in texts.values()
            )
            revision = self._publish_unlocked(
                forked, drafts, key=f"{key}:fork", request=_request_digest({"texts": texts})
            )
            forked["head"] = revision.revision_id
            forked["updated_at"] = revision.created_at
            self.storage.write_module(new_module_id, forked)
            result: dict[str, object] = {
                "module": _info(forked).view(),
                "revision": revision.view(),
            }
            requests[key] = {"request": request, "result": result}
            module["requests"] = _trim(requests)
            self.storage.write_module(module_id, module)
            return result

    def trash(
        self, *, module_id: str, expected_head: str | None, request_key: str
    ) -> dict[str, object]:
        return self._set_deleted(
            module_id=module_id,
            expected_head=expected_head,
            request_key=request_key,
            deleted=True,
        )

    def restore(
        self, *, module_id: str, expected_head: str | None, request_key: str
    ) -> dict[str, object]:
        return self._set_deleted(
            module_id=module_id,
            expected_head=expected_head,
            request_key=request_key,
            deleted=False,
        )

    # -- 读取 ---------------------------------------------------------------

    def module(self, module_id: str) -> ModuleInfo:
        return _info(self.storage.read_module(module_id))

    def modules(
        self,
        *,
        kind: str | None = None,
        q: str = "",
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        include_deleted: bool = False,
        include_preview: bool = False,
    ) -> dict[str, object]:
        """分页列出实例；已删除的默认不出现，也不进入新装入候选。"""
        if kind is not None:
            spec(kind)
        size = _limit(limit)
        needle = q.strip().lower()
        query = f"modules:{kind or ''}:{1 if include_deleted else 0}:{needle}"
        offset = _offset(cursor, query)
        rows: list[dict[str, object]] = []
        for module_id in self.storage.module_ids():
            try:
                module = self.storage.read_module(module_id)
            except WorkspaceError:
                # 单个模块状态损坏不拖垮整个列表；它不会出现在候选中。
                continue
            info = _info(module)
            if info.deleted and not include_deleted:
                continue
            if kind is not None and info.kind != kind:
                continue
            if needle and not self._matches(info, needle):
                continue
            row = info.view()
            row["revision_count"] = len(self.storage.revision_ids(module_id))
            rows.append(row)
        rows.sort(key=lambda item: (str(item["updated_at"]), str(item["module_id"])), reverse=True)
        page = rows[offset : offset + size]
        if include_preview:
            for row in page:
                row["preview"] = self._preview(row)
        next_offset = offset + size
        return {
            "items": page,
            "total": len(rows),
            "next_cursor": _cursor(query, next_offset) if next_offset < len(rows) else None,
        }

    def revisions(
        self, *, module_id: str, cursor: str | None = None, limit: int = DEFAULT_PAGE_SIZE
    ) -> dict[str, object]:
        """版本列表只给元信息，按发布时间倒序；正文按版本单独取。"""
        size = _limit(limit)
        module = self.storage.read_module(module_id)
        query = f"revisions:{module_id}:{module.get('head') or ''}"
        offset = _offset(cursor, query)
        rows: list[dict[str, object]] = []
        for revision_id in self.storage.revision_ids(module_id):
            manifest = self.storage.read_manifest(module_id, revision_id)
            rows.append(
                {
                    "revision_id": revision_id,
                    "parent": manifest.get("parent"),
                    "created_at": manifest.get("created_at"),
                    "entry_ids": [
                        str(item.get("entry_id"))
                        for item in manifest.get("entries") or []
                        if isinstance(item, dict)
                    ],
                }
            )
        rows.sort(
            key=lambda item: (str(item["created_at"]), str(item["revision_id"])), reverse=True
        )
        page = rows[offset : offset + size]
        next_offset = offset + size
        return {
            "items": page,
            "total": len(rows),
            "next_cursor": _cursor(query, next_offset) if next_offset < len(rows) else None,
        }

    def revision(self, *, module_id: str, revision_id: str) -> CatalogRevision:
        module = self.storage.read_module(module_id)
        return self.catalog.revision(
            self.storage,
            library_id=str(module.get("library_id") or self.library_id or ""),
            module_id=module_id,
            revision_id=revision_id,
            module_kind=str(module.get("kind") or ""),
            module_name=str(module.get("name") or ""),
        )

    def read_entry(
        self,
        *,
        module_id: str,
        revision_id: str,
        entry_id: str,
        start: int = 0,
        end: int | None = None,
        ledger: MaterialLedger | None = None,
    ) -> dict[str, object]:
        """读取确定版本的一条原文；manifest 校验不算读过，只有返回的区间入账。"""
        module = self.storage.read_module(module_id)
        manifest = self.storage.read_manifest(module_id, revision_id)
        row = next(
            (
                item
                for item in manifest.get("entries") or []
                if isinstance(item, dict) and item.get("entry_id") == entry_id
            ),
            None,
        )
        if row is None:
            raise WorkspaceError(
                code="MODULE_RECORD_NOT_FOUND",
                where=f"library.record:{module_id}/{revision_id}",
                why=f"这一版没有条目 {entry_id}",
                fix="刷新版本目录；不要用同名条目替代已删除的条目",
                details={"module_id": module_id, "revision_id": revision_id, "entry_id": entry_id},
            )
        text, digest = self.storage.read_entry(module_id, revision_id, entry_id)
        if digest != str(row.get("digest") or ""):
            raise WorkspaceError(
                code="MODULE_CONTENT_INVALID",
                where=f"library.record:{module_id}/{revision_id}/{entry_id}",
                why="条目内容与版本清单不符",
                fix="从备份恢复这一版；不能在同一 revision 下接受新内容",
                details={"module_id": module_id, "revision_id": revision_id, "entry_id": entry_id},
            )
        length = len(text)
        begin = min(max(int(start), 0), length)
        finish = length if end is None else max(int(end), begin)
        finish = min(finish, begin + READ_MAX_CHARS, length)
        slice_text = text[begin:finish]
        if ledger is not None:
            ledger.record_read(
                SourceRef(
                    path=source_path(
                        str(module.get("library_id") or ""), module_id, revision_id, entry_id
                    ),
                    digest=digest,
                ),
                length=length,
                start=begin,
                end=finish,
            )
        draft = parse_entry(text, module_kind=str(module.get("kind") or ""))
        return {
            "entry_id": draft.entry_id,
            "kind": draft.kind,
            "title": draft.title,
            "aliases": list(draft.aliases),
            "fields": dict(draft.fields),
            "text": slice_text,
            "start": begin,
            "end": finish,
            "length": length,
            "digest": digest,
            "truncated": finish < length,
            "next_cursor": finish if finish < length else None,
        }

    # -- 内部：发布 ---------------------------------------------------------

    def _first_revision(
        self, module_id: str, drafts: tuple[EntryDraft, ...], *, request_key: str
    ) -> RevisionInfo:
        with self.storage.lock(module_id):
            current = self.storage.read_module(module_id)
            if isinstance(current.get("head"), str):
                # 上一次已经发完，只是库根状态没来得及记：沿用已经登记的版本。
                recorded = _requests(current.get("requests")).get(request_key)
                revision_id = str((recorded or {}).get("revision_id") or current["head"])
                if not self.storage.revision_exists(module_id, revision_id):
                    revision_id = str(current["head"])
                return self._revision_info(module_id, revision_id)
            request = _request_digest(
                {"op": "first", "module_id": module_id, "entries": _rows(drafts)}
            )
            revision = self._publish_unlocked(current, drafts, key=request_key, request=request)
            current["head"] = revision.revision_id
            current["updated_at"] = revision.created_at
            self.storage.write_module(module_id, current)
            return revision

    def _publish_unlocked(
        self,
        module: dict[str, Any],
        drafts: tuple[EntryDraft, ...],
        *,
        key: str,
        request: str,
        parent_revision: str | None = None,
    ) -> RevisionInfo:
        """写入版本目录并登记请求结果；调用方负责持有模块锁与推进 head。"""
        module_id = str(module["module_id"])
        requests = _requests(module.get("requests"))
        replay = _replay(requests, key, request)
        if replay is not None:
            revision_id = str(replay.get("revision_id") or module.get("head") or "")
            if not revision_id:
                raise _content_error(module_id, "", "请求结果里没有版本标识")
            return self._revision_info(module_id, revision_id)

        pending = module.get("pending")
        resumable = (
            isinstance(pending, dict)
            and pending.get("key") == key
            and pending.get("request") == request
            and isinstance(pending.get("revision_id"), str)
            and self.storage.revision_exists(module_id, str(pending["revision_id"]))
        )
        if resumable:
            revision_id = str(pending["revision_id"])  # type: ignore[index]
            manifest = self.storage.read_manifest(module_id, revision_id)
            if manifest.get("request_digest") != request:
                raise _content_error(
                    module_id, revision_id, "中断恢复时发现同版本的内容与这次请求不一致"
                )
        else:
            revision_id = new_id("revision")
            module["pending"] = {"key": key, "request": request, "revision_id": revision_id}
            self.storage.write_module(module_id, module)
            created = now_iso()
            entries = {draft.entry_id: entry_markdown(draft) for draft in drafts}
            manifest = _manifest(
                module_id=module_id,
                revision_id=revision_id,
                parent=parent_revision or module.get("head"),
                created_at=created,
                request_key=key,
                request_digest=request,
                entries=entries,
            )
            self.storage.write_revision(module_id, revision_id, manifest, entries)

        revision = self._revision_info(module_id, revision_id)
        module["pending"] = None
        module["updated_at"] = revision.created_at
        requests[key] = {"request": request, "result": revision.view()}
        module["requests"] = _trim(requests)
        return revision

    def _revision_info(self, module_id: str, revision_id: str) -> RevisionInfo:
        manifest = self.storage.read_manifest(module_id, revision_id)
        return RevisionInfo(
            module_id=module_id,
            revision_id=revision_id,
            parent=manifest.get("parent") if isinstance(manifest.get("parent"), str) else None,
            created_at=str(manifest.get("created_at") or ""),
            entry_ids=tuple(
                str(item.get("entry_id"))
                for item in manifest.get("entries") or []
                if isinstance(item, dict)
            ),
        )

    # -- 内部：其他 ---------------------------------------------------------

    def _set_deleted(
        self,
        *,
        module_id: str,
        expected_head: str | None,
        request_key: str,
        deleted: bool,
    ) -> dict[str, object]:
        key = _check_request_key(request_key)
        with self.storage.lock(module_id):
            module = self.storage.read_module(module_id)
            request = _request_digest(
                {"op": "trash" if deleted else "restore", "module_id": module_id}
            )
            requests = _requests(module.get("requests"))
            replay = _replay(requests, key, request)
            if replay is not None:
                return replay
            _check_head(module, expected_head)
            module["deleted"] = deleted
            module["updated_at"] = now_iso()
            info = _info(module)
            requests[key] = {"request": request, "result": info.view()}
            module["requests"] = _trim(requests)
            self.storage.write_module(module_id, module)
            return info.view()

    def _revision_texts(self, module_id: str, revision_id: str) -> dict[str, str]:
        manifest = self.storage.read_manifest(module_id, revision_id)
        texts: dict[str, str] = {}
        for row in manifest.get("entries") or []:
            if not isinstance(row, dict):
                continue
            entry_id = str(row.get("entry_id") or "")
            text, digest = self.storage.read_entry(module_id, revision_id, entry_id)
            if digest != str(row.get("digest") or ""):
                raise _content_error(
                    module_id, revision_id, f"条目 {entry_id} 的内容与版本清单不符"
                )
            texts[entry_id] = text
        return texts

    def _preview(self, row: Mapping[str, object]) -> str:
        """只供作者界面预览；模型的库目录不隐含读取正文。"""
        if not row.get("head"):
            return ""
        try:
            revision = self.revision(module_id=str(row["module_id"]), revision_id=str(row["head"]))
            if revision.entries:
                entry = revision.entries[0]
                text, digest = self.storage.read_entry(
                    str(row["module_id"]), str(row["head"]), entry.entry_id
                )
                if digest == entry.digest:
                    body = parse_entry(text, module_kind=str(row["kind"])).body
                    plain = re.sub(r"(?m)^\s*#{1,6}\s+", "", body)
                    return " ".join(plain.split())[:120]
        except WorkspaceError:
            # 列表保留定位，内容损坏在打开模块时报告，不用旧缓存伪造预览。
            pass
        return ""

    def _matches(self, info: ModuleInfo, needle: str) -> bool:
        if needle in info.name.lower():
            return True
        if info.head is None:
            return False
        try:
            revision = self.revision(module_id=info.module_id, revision_id=info.head)
        except WorkspaceError:
            return False
        return any(
            needle in entry.title.lower() or any(needle in alias.lower() for alias in entry.aliases)
            for entry in revision.entries
        )


# -- 数据形状 ---------------------------------------------------------------


def _manifest(
    *,
    module_id: str,
    revision_id: str,
    parent: object,
    created_at: str,
    request_key: str,
    request_digest: str,
    entries: Mapping[str, str],
) -> dict[str, object]:
    return {
        "schema_version": MODULE_SCHEMA,
        "module_id": module_id,
        "revision_id": revision_id,
        "parent": parent if isinstance(parent, str) else None,
        "created_at": created_at,
        "request_key": request_key,
        "request_digest": request_digest,
        "entries": [
            {
                "entry_id": entry_id,
                "path": f"entries/{entry_id}.md",
                "digest": entry_digest(text),
                "chars": len(text),
            }
            for entry_id, text in sorted(entries.items())
        ],
    }


def _info(module: Mapping[str, Any]) -> ModuleInfo:
    forked = module.get("forked_from")
    return ModuleInfo(
        module_id=str(module.get("module_id") or ""),
        kind=str(module.get("kind") or ""),
        name=str(module.get("name") or ""),
        head=module.get("head") if isinstance(module.get("head"), str) else None,
        deleted=bool(module.get("deleted")),
        created_at=str(module.get("created_at") or ""),
        updated_at=str(module.get("updated_at") or ""),
        forked_from=dict(forked) if isinstance(forked, dict) else None,
    )


def _check_name(name: str) -> str:
    text = name.strip()
    if not text:
        raise WorkspaceError(
            code="MODULE_NAME_INVALID",
            where="library.module",
            why="模块名称不能为空",
            fix="给这份内容起一个便于辨认的名字，例如「主角团」",
        )
    if len(text) > MAX_NAME_CHARS:
        raise WorkspaceError(
            code="MODULE_NAME_INVALID",
            where="library.module",
            why=f"模块名称最长 {MAX_NAME_CHARS} 字符，当前 {len(text)}",
            fix="缩短名称；详细说明写进条目",
            details={"name": text[:MAX_NAME_CHARS]},
        )
    return text


def _check_request_key(key: str) -> str:
    text = str(key or "").strip()
    if not 8 <= len(text) <= 120:
        raise WorkspaceError(
            code="REQUEST_KEY_INVALID",
            where="library.request",
            why="请求键长度需要在 8–120 字符之间",
            fix="由调用方为每次操作生成一个稳定且不重复的键",
        )
    return text


def _check_head(module: Mapping[str, Any], expected_head: str | None) -> None:
    current = module.get("head")
    current_head = current if isinstance(current, str) else None
    if expected_head != current_head:
        raise WorkspaceError(
            code="MODULE_HEAD_STALE_CONFLICT",
            where=f"library.module:{module.get('module_id')}",
            why="模块已经发布了新版本，页面上的 head 不是最新的",
            fix="刷新模块后基于最新版重做，或另存为变体；系统不会自动合并",
            details={"expected": expected_head, "current": current_head},
        )


def _check_base(module: Mapping[str, Any], base_revision: str | None) -> None:
    current = module.get("head")
    current_head = current if isinstance(current, str) else None
    if base_revision != current_head:
        raise WorkspaceError(
            code="MODULE_HEAD_STALE_CONFLICT",
            where=f"library.module:{module.get('module_id')}",
            why="编辑所基于的版本不是当前 head",
            fix="基于最新版重做，或另存为变体；不要用旧版本的稿覆盖新版本",
            details={"base": base_revision, "current": current_head},
        )


def _require_active(module: Mapping[str, Any]) -> None:
    if module.get("deleted"):
        raise WorkspaceError(
            code="MODULE_TRASHED_CONFLICT",
            where=f"library.module:{module.get('module_id')}",
            why="这个模块已删除，不能继续发布或另存变体",
            fix="先在库中恢复它；已绑定的旧版本仍可读取",
            details={"module_id": module.get("module_id")},
        )


def _drafts(
    module_kind: str, entries: Sequence[EntryDraft | Mapping[str, object]]
) -> tuple[EntryDraft, ...]:
    preset = spec(module_kind)
    default_kind = preset.source_kinds[0]
    drafts: list[EntryDraft] = []
    for item in entries:
        if isinstance(item, EntryDraft):
            drafts.append(item)
            continue
        if not isinstance(item, Mapping):
            raise WorkspaceError(
                code="RECORD_HEADER_INVALID",
                where="library.records",
                why="条目必须是对象或 EntryDraft",
                fix="提交 entry_id、title、body 等字段组成的对象",
            )
        aliases_raw = item.get("aliases")
        fields_raw = item.get("fields")
        drafts.append(
            EntryDraft(
                entry_id=str(item.get("entry_id") or item.get("id") or ""),
                kind=str(item.get("kind") or default_kind),
                title=str(item.get("title") or ""),
                body=str(item.get("body") or ""),
                aliases=(
                    tuple(str(alias) for alias in aliases_raw)
                    if isinstance(aliases_raw, (list, tuple))
                    else ()
                ),
                fields=dict(fields_raw) if isinstance(fields_raw, Mapping) else {},
            )
        )
    return validate_entries(drafts, module_kind=module_kind)


def _rows(drafts: Sequence[EntryDraft]) -> list[str]:
    return [entry_markdown(draft) for draft in drafts]


def _request_digest(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _requests(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}
    return {}


def _replay_row(requests: Mapping[str, Any], key: str, request: str) -> dict[str, Any] | None:
    row = requests.get(key)
    if row is None:
        return None
    if not isinstance(row, dict) or row.get("request") != request:
        raise WorkspaceError(
            code="REQUEST_KEY_CONFLICT",
            where="library.request",
            why=f"请求键 {key} 之前用于另一个请求",
            fix="为这次操作生成新的请求键；不要复用已经用过的键",
            details={"request_key": key},
        )
    return row


def _replay(requests: Mapping[str, Any], key: str, request: str) -> dict[str, Any] | None:
    row = _replay_row(requests, key, request)
    if row is None:
        return None
    result = row.get("result")
    return dict(result) if isinstance(result, dict) else {}


def _trim(requests: dict[str, Any]) -> dict[str, Any]:
    if len(requests) <= MAX_REQUEST_KEYS:
        return requests
    keep = list(requests)[-MAX_REQUEST_KEYS:]
    return {key: requests[key] for key in keep}


def _limit(value: int) -> int:
    return max(1, min(int(value), MAX_PAGE_SIZE))


def _cursor(query: str, offset: int) -> str:
    raw = f"{offset}:{hashlib.sha256(query.encode('utf-8')).hexdigest()[:12]}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _offset(cursor: str | None, query: str) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        offset_text, digest = raw.split(":", 1)
        offset = int(offset_text)
    except (ValueError, UnicodeDecodeError):
        offset, digest = -1, ""
    if offset < 0 or digest != hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]:
        raise WorkspaceError(
            code="QUERY_CURSOR_STALE_CONFLICT",
            where="library.query",
            why="查询条件或库内容已经变化，旧游标不能继续使用",
            fix="从第一页重新查询；不要跳过已变化的条目",
        )
    return offset


def _content_error(module_id: str, revision_id: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="MODULE_CONTENT_INVALID",
        where=f"library.revision:{module_id}/{revision_id}",
        why=why,
        fix="从备份恢复这一版；不能在同一 revision 下接受新内容",
        details={"module_id": module_id, "revision_id": revision_id},
    )
