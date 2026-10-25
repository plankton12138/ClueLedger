"""灵感本的条目与修订（0.8 C）。

一条灵感一个 JSON 文件，放在作品目录的 ``灵感本/`` 下。一次操作在同一文件内原子替换：
头指针、修订数组与使用定位一起落盘，避免「正文存了、历史没存」的分裂。

几条边界：

- **修订只追加**：编辑、删除、恢复都产生新修订；恢复历史不移动指针、不删除后续修订。
- **冲突不覆盖**：编辑要求 ``expected_digest`` 与 ``expected_revision``；别人先写过就拒绝，
  由作者决定保留哪一版。
- **请求键幂等**：同键同内容返回上次修订，同键不同内容直接拒绝；网络重试不改两次。
- **损坏文件保护**：主文件无法解析时报可修复错误并保留原字节，绝不写入空历史。
- **灵感不进作品 Git**：``灵感本/`` 不在受管路径内，普通文件工具与搜索也读不到。

这里不判断内容好不好，也不把灵感自动变成正式设定。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path

__all__ = [
    "IDEAS_DIR",
    "IdeaStore",
    "IdeaUseDraft",
    "MAX_BODY_CHARS",
    "MAX_TITLE_CHARS",
]

IDEAS_DIR = "灵感本"
IDEA_SCHEMA = 1
MAX_BODY_CHARS = 20_000
MAX_TITLE_CHARS = 200
MAX_TAGS = 20
MAX_TAG_CHARS = 40
MAX_REVISIONS_PER_IDEA = 5_000
MAX_REQUEST_KEYS = 100
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
#: 列表摘要的字符上限；超过时由 ``excerpt_truncated`` 标记，进入编辑前必须读完整修订。
EXCERPT_CHARS = 120

IdeaOperation = Literal["create", "edit", "delete", "restore"]
IdeaUseKind = Literal["author_link", "applied"]

#: 类里有名为 ``list`` 的方法，注解需要绕开类作用域里的同名成员。
PathList = list[Path]
RowList = list[dict[str, object]]

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_IDEA_ID_LEN = 16


@dataclass(frozen=True, slots=True)
class IdeaUseDraft:
    """一次使用定位：作者明确关联，或本轮确实写入并核对通过的落实位置。"""

    idea_revision_id: str
    kind: IdeaUseKind
    target_record_id: str = ""
    target_path: str = ""
    target_digest: str = ""
    quote: str = ""
    start: int | None = None
    end: int | None = None
    turn_id: str = ""


class IdeaStore:
    """一个作品的灵感本；每个条目独立加锁、独立原子替换。"""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.root = workspace.root / IDEAS_DIR

    # -- 写入 ---------------------------------------------------------------

    def create(
        self,
        *,
        title: str = "",
        body: str,
        tags: Sequence[str] = (),
        origin: Mapping[str, object] | None = None,
        request_key: str,
    ) -> dict[str, object]:
        key = _check_key(request_key)
        checked_title, checked_body, checked_tags = _checked_content(title, body, tags)
        request = _request_digest(
            {
                "op": "create",
                "title": checked_title,
                "body": checked_body,
                "tags": list(checked_tags),
            }
        )
        existing = self._find_request(key, request)
        if existing is not None:
            return existing
        idea_id = _new_id("idea")
        revision_id = _new_id("irev")
        now = _now()
        document: dict[str, Any] = {
            "schema_version": IDEA_SCHEMA,
            "idea_id": idea_id,
            "head_revision_id": revision_id,
            "created_at": now,
            "updated_at": now,
            "revisions": [
                {
                    "id": revision_id,
                    "parent": None,
                    "created_at": now,
                    "operation": "create",
                    "restored_from": None,
                    "title": checked_title,
                    "body": checked_body,
                    "tags": list(checked_tags),
                    "deleted": False,
                }
            ],
            "origin": dict(origin) if origin is not None else None,
            "uses": [],
            "requests": {key: {"request": request, "revision_id": revision_id}},
        }
        self._write(idea_id, document)
        return _idea_view(document, _revision(document, revision_id))

    def update(
        self,
        idea_id: str,
        *,
        expected_digest: str,
        expected_revision: str,
        title: str | None = None,
        body: str | None = None,
        tags: Sequence[str] | None = None,
        request_key: str,
    ) -> dict[str, object]:
        key = _check_key(request_key)
        with self._lock(idea_id):
            document = self._read(idea_id)
            head = _revision(document, str(document["head_revision_id"]))
            next_title, next_body, next_tags = _checked_content(
                head["title"] if title is None else title,
                head["body"] if body is None else body,
                head["tags"] if tags is None else tags,
            )
            request = _request_digest(
                {"op": "edit", "title": next_title, "body": next_body, "tags": list(next_tags)}
            )
            replay = self._replay(document, key, request)
            if replay is not None:
                return _idea_view(document, _revision(document, replay))
            if head["deleted"]:
                raise WorkspaceError(
                    code="IDEA_DELETED_CONFLICT",
                    where=f"ideas.update:{idea_id}",
                    why="这条灵感已删除，不能直接编辑",
                    fix="先从最近删除里恢复它，再修改",
                    details={"idea_id": idea_id},
                )
            self._check_current(idea_id, document, expected_digest, expected_revision)
            if (
                not bool(head["deleted"])
                and str(head["title"]) == next_title
                and str(head["body"]) == next_body
                and [str(item) for item in head["tags"]] == list(next_tags)
            ):
                # 内容没有变化：不追加空修订，但把这次请求记在头上，重试仍然幂等。
                document["requests"][key] = {"request": request, "revision_id": head["id"]}
                document["requests"] = _trim(document["requests"])
                self._write(idea_id, document)
                return _idea_view(document, head)
            return self._append(
                idea_id,
                document,
                operation="edit",
                title=next_title,
                body=next_body,
                tags=next_tags,
                deleted=False,
                request_key=key,
                request=request,
            )

    def trash(
        self,
        idea_id: str,
        *,
        expected_digest: str,
        expected_revision: str,
        request_key: str,
    ) -> dict[str, object]:
        key = _check_key(request_key)
        with self._lock(idea_id):
            document = self._read(idea_id)
            request = _request_digest({"op": "delete", "head": str(document["head_revision_id"])})
            replay = self._replay(document, key, request)
            if replay is not None:
                return _idea_view(document, _revision(document, replay))
            self._check_current(idea_id, document, expected_digest, expected_revision)
            head = _revision(document, str(document["head_revision_id"]))
            if head["deleted"]:
                return _idea_view(document, head)
            return self._append(
                idea_id,
                document,
                operation="delete",
                title=str(head["title"]),
                body=str(head["body"]),
                tags=tuple(str(item) for item in head["tags"]),
                deleted=True,
                request_key=key,
                request=request,
            )

    def restore(
        self,
        idea_id: str,
        *,
        target_revision: str,
        expected_digest: str,
        request_key: str,
    ) -> dict[str, object]:
        """以指定历史修订的内容新建一条 head；目标修订是删除时也恢复删除状态。"""
        key = _check_key(request_key)
        with self._lock(idea_id):
            document = self._read(idea_id)
            target = next(
                (item for item in document["revisions"] if item["id"] == target_revision), None
            )
            if target is None:
                raise WorkspaceError(
                    code="IDEA_REVISION_NOT_FOUND",
                    where=f"ideas.restore:{idea_id}",
                    why=f"没有这个修订：{target_revision}",
                    fix="从修订历史里选一条存在的修订；不会自动回退到当前版本",
                    details={"idea_id": idea_id, "revision_id": target_revision},
                )
            request = _request_digest({"op": "restore", "target": target_revision})
            replay = self._replay(document, key, request)
            if replay is not None:
                return _idea_view(document, _revision(document, replay))
            self._check_digest(idea_id, expected_digest)
            return self._append(
                idea_id,
                document,
                operation="restore",
                title=str(target["title"]),
                body=str(target["body"]),
                tags=tuple(str(item) for item in target["tags"]),
                deleted=bool(target["deleted"]),
                request_key=key,
                request=request,
                restored_from=target_revision,
            )

    def add_use(
        self,
        idea_id: str,
        *,
        expected_digest: str,
        use: IdeaUseDraft,
        request_key: str,
    ) -> dict[str, object]:
        """记录一次使用定位；``applied`` 必须能对上当前正文的指纹与区间。"""
        key = _check_key(request_key)
        with self._lock(idea_id):
            document = self._read(idea_id)
            _revision(document, use.idea_revision_id)
            checked = self._checked_use(use)
            request = _request_digest({"op": "link", "use": _use_payload(checked)})
            replay = self._replay(document, key, request)
            if replay is not None:
                return _idea_view(document, _revision(document, replay))
            self._check_digest(idea_id, expected_digest)
            uses = list(document.get("uses") or [])
            if not any(_same_use(item, checked) for item in uses):
                uses.append(_use_payload(checked))
            document["uses"] = uses
            document["updated_at"] = _now()
            head = _revision(document, str(document["head_revision_id"]))
            document["requests"][key] = {"request": request, "revision_id": head["id"]}
            document["requests"] = _trim(document["requests"])
            self._write(idea_id, document)
            return _idea_view(document, head)

    # -- 读取 ---------------------------------------------------------------

    def get(self, idea_id: str) -> dict[str, object]:
        document = self._read(idea_id)
        return _idea_view(document, _revision(document, str(document["head_revision_id"])))

    def list(
        self,
        *,
        q: str = "",
        deleted: bool = False,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        order: Literal["created", "updated"] = "updated",
    ) -> dict[str, object]:
        """列表只给摘要；默认不含已删除条目，搜索不返回未选中的灵感给 agent。

        ``order`` 默认按更新时间，兼容旧客户端；新界面显式按创建时间，避免恢复历史
        把条目挪到最前。排序方式与查询一起进入游标身份。
        """
        size = _limit(limit)
        sort_by = "created" if order == "created" else "updated"
        needle = q.strip().lower()
        rows: RowList = []
        invalid = 0
        for path in self._idea_files():
            try:
                document = self._read(path.stem)
            except WorkspaceError:
                invalid += 1
                continue
            head = _revision(document, str(document["head_revision_id"]))
            if bool(head["deleted"]) != deleted:
                continue
            if needle and not _matches(head, needle):
                continue
            rows.append(_idea_summary(document, head))
        query = f"ideas:{needle}:{1 if deleted else 0}:{sort_by}:{len(rows)}"
        offset = _offset(cursor, query)
        field = "created_at" if sort_by == "created" else "updated_at"
        rows.sort(key=lambda item: (str(item[field]), str(item["idea_id"])), reverse=True)
        page = rows[offset : offset + size]
        next_offset = offset + size
        return {
            "items": page,
            "total": len(rows),
            "invalid": invalid,
            "next_cursor": _cursor(query, next_offset) if next_offset < len(rows) else None,
        }

    def revisions(
        self, idea_id: str, *, cursor: str | None = None, limit: int = DEFAULT_PAGE_SIZE
    ) -> dict[str, object]:
        """修订列表只给元信息；正文按修订单独取，不一次返回全部历史。"""
        size = _limit(limit)
        document = self._read(idea_id)
        rows = [
            {
                "id": item["id"],
                "parent": item["parent"],
                "created_at": item["created_at"],
                "operation": item["operation"],
                "restored_from": item["restored_from"],
                "deleted": item["deleted"],
                "title": item["title"],
                "chars": len(str(item["body"])),
            }
            for item in reversed(document["revisions"])
        ]
        query = f"revisions:{idea_id}:{document['head_revision_id']}:{len(rows)}"
        offset = _offset(cursor, query)
        page = rows[offset : offset + size]
        next_offset = offset + size
        return {
            "items": page,
            "total": len(rows),
            "next_cursor": _cursor(query, next_offset) if next_offset < len(rows) else None,
        }

    def revision(self, idea_id: str, revision_id: str) -> dict[str, object]:
        document = self._read(idea_id)
        item = _revision(document, revision_id)
        return {
            "idea_id": idea_id,
            "revision_id": item["id"],
            "parent": item["parent"],
            "created_at": item["created_at"],
            "operation": item["operation"],
            "restored_from": item["restored_from"],
            "title": item["title"],
            "body": item["body"],
            "tags": list(item["tags"]),
            "deleted": item["deleted"],
        }

    def resolve_reference(self, idea_id: str, revision_id: str) -> dict[str, object]:
        """受理引用时在同一把锁里核对条目与修订：条目已删除或修订不可读都拒绝。

        分别读取当前条目和历史修订会留下竞争窗口：刚检查完就被删除，本轮仍会带上
        一条已经不存在的原文。这里一次读盘、一次判断，返回值就是本轮冻结的原文。
        """
        with self._lock(idea_id):
            document = self._read(idea_id)
            head = _revision(document, str(document["head_revision_id"]))
            try:
                selected = _revision(document, revision_id)
            except WorkspaceError as exc:
                raise WorkspaceError(
                    code="IDEA_REFERENCE_UNAVAILABLE",
                    where=f"ideas.reference:{idea_id}",
                    why=f"灵感修订不存在：{idea_id}@{revision_id}",
                    fix="刷新灵感本后重新选择当前修订",
                    details={"idea_id": idea_id, "revision_id": revision_id},
                ) from exc
            if bool(head["deleted"]):
                raise WorkspaceError(
                    code="IDEA_REFERENCE_UNAVAILABLE",
                    where=f"ideas.reference:{idea_id}",
                    why=f"这条灵感已删除：{idea_id}",
                    fix="从最近删除恢复，或改选其他灵感",
                    details={"idea_id": idea_id, "revision_id": revision_id},
                )
            if bool(selected["deleted"]):
                raise WorkspaceError(
                    code="IDEA_REFERENCE_UNAVAILABLE",
                    where=f"ideas.reference:{idea_id}",
                    why=f"所选修订是删除修订：{idea_id}@{revision_id}",
                    fix="改选这条灵感未删除的修订",
                    details={"idea_id": idea_id, "revision_id": revision_id},
                )
            return _idea_view(document, selected)

    # -- 内部：存储 ---------------------------------------------------------

    def _idea_files(self) -> PathList:
        if not self.root.is_dir():
            return []
        return sorted(
            path
            for path in self.root.glob("*.json")
            if path.is_file() and _valid_id(path.stem, "idea")
        )

    def _path(self, idea_id: str) -> Path:
        if not _valid_id(idea_id, "idea"):
            raise WorkspaceError(
                code="IDEA_NOT_FOUND",
                where="ideas.read",
                why=f"灵感标识不合法：{idea_id}",
                fix="使用列表返回的 idea_id；不要拼接路径",
                details={"idea_id": idea_id},
            )
        return self.root / f"{idea_id}.json"

    def _read(self, idea_id: str) -> dict[str, Any]:
        path = self._path(idea_id)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise WorkspaceError(
                code="IDEA_NOT_FOUND",
                where=f"ideas.read:{idea_id}",
                why="这条灵感不存在，或已经不在这个作品里",
                fix="刷新灵感列表；已删除的条目在最近删除里",
                details={"idea_id": idea_id},
            ) from exc
        except OSError as exc:
            raise WorkspaceError(
                code="IDEA_STORE_INVALID",
                where=f"ideas.read:{idea_id}",
                why=f"读不到灵感文件：{exc.strerror or exc}",
                fix="检查作品目录权限；文件损坏时从备份恢复",
                details={"path": str(path)},
            ) from exc
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError(
                code="IDEA_STORE_INVALID",
                where=f"ideas.read:{idea_id}",
                why=f"灵感文件无法解析：{exc}",
                fix="保留原文件并修复内容，或从备份恢复；不要用空内容覆盖历史",
                details={"path": str(path)},
            ) from exc
        if not isinstance(document, dict) or document.get("schema_version") != IDEA_SCHEMA:
            raise WorkspaceError(
                code="IDEA_STORE_INVALID",
                where=f"ideas.read:{idea_id}",
                why="灵感文件的结构版本不受支持",
                fix="保留原文件并从备份恢复；不要把它当成空条目",
                details={"path": str(path)},
            )
        if not isinstance(document.get("revisions"), list) or not document["revisions"]:
            raise WorkspaceError(
                code="IDEA_STORE_INVALID",
                where=f"ideas.read:{idea_id}",
                why="灵感文件没有修订历史",
                fix="从备份恢复这个条目；空历史不能当作有效条目",
                details={"path": str(path)},
            )
        if str(document.get("idea_id") or "") != idea_id:
            raise WorkspaceError(
                code="IDEA_STORE_INVALID",
                where=f"ideas.read:{idea_id}",
                why="文件内容与条目标识不一致",
                fix="不要把一条灵感复制成另一个标识；需要复制请新建条目",
                details={"path": str(path)},
            )
        head_id = str(document.get("head_revision_id") or "")
        if not any(item.get("id") == head_id for item in document["revisions"]):
            raise WorkspaceError(
                code="IDEA_STORE_INVALID",
                where=f"ideas.read:{idea_id}",
                why="头指针指向的修订不存在",
                fix="从备份恢复；不要手工删改修订数组",
                details={"path": str(path)},
            )
        document.setdefault("requests", {})
        return document

    def _write(self, idea_id: str, document: Mapping[str, Any]) -> None:
        path = self._path(idea_id)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            handle, temp = tempfile.mkstemp(dir=self.root, prefix=f".{idea_id}.", suffix=".tmp")
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(dict(document), ensure_ascii=False, indent=2) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, path)
            except BaseException:
                with suppress(OSError):
                    os.unlink(temp)
                raise
        except OSError as exc:
            raise WorkspaceError(
                code="IDEA_STORE_INVALID",
                where=f"ideas.write:{idea_id}",
                why=f"无法写入灵感：{exc.strerror or exc}",
                fix="检查作品目录权限与磁盘空间后重试",
                details={"path": str(path)},
            ) from exc

    @contextmanager
    def _lock(self, idea_id: str) -> Iterator[None]:
        self._path(idea_id)
        key = str((self.root / f".{idea_id}.lock").resolve())
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(key, threading.Lock())
        with lock:
            self.root.mkdir(parents=True, exist_ok=True)
            handle = open(self.root / f".{idea_id}.lock", "a+b")  # noqa: SIM115
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

    # -- 内部：校验 ---------------------------------------------------------

    def _append(
        self,
        idea_id: str,
        document: dict[str, Any],
        *,
        operation: IdeaOperation,
        title: str,
        body: str,
        tags: Sequence[str],
        deleted: bool,
        request_key: str,
        request: str,
        restored_from: str | None = None,
    ) -> dict[str, object]:
        revisions = document["revisions"]
        if len(revisions) >= MAX_REVISIONS_PER_IDEA:
            raise WorkspaceError(
                code="IDEA_CONTENT_TOO_LARGE",
                where=f"ideas.update:{idea_id}",
                why=f"修订数量已达上限 {MAX_REVISIONS_PER_IDEA}",
                fix="把长期不用的条目拆成新条目；历史不会被自动清理",
                details={"idea_id": idea_id, "revisions": len(revisions)},
            )
        now = _now()
        revision_id = _new_id("irev")
        revisions.append(
            {
                "id": revision_id,
                "parent": document["head_revision_id"],
                "created_at": now,
                "operation": operation,
                "restored_from": restored_from,
                "title": title,
                "body": body,
                "tags": list(tags),
                "deleted": deleted,
            }
        )
        document["head_revision_id"] = revision_id
        document["updated_at"] = now
        document["requests"][request_key] = {"request": request, "revision_id": revision_id}
        document["requests"] = _trim(document["requests"])
        self._write(idea_id, document)
        return _idea_view(document, _revision(document, revision_id))

    def _replay(self, document: Mapping[str, Any], key: str, request: str) -> str | None:
        """同键同请求返回上次修订；同键不同请求直接拒绝，不悄悄改成另一件事。"""
        row = (document.get("requests") or {}).get(key)
        if not isinstance(row, dict):
            return None
        if row.get("request") != request:
            raise WorkspaceError(
                code="REQUEST_KEY_CONFLICT",
                where="ideas.request",
                why=f"请求键 {key} 之前用于另一次修改",
                fix="为这次修改生成新的请求键；不要复用已经用过的键",
                details={"request_key": key},
            )
        revision_id = str(row.get("revision_id") or "")
        if revision_id and any(item["id"] == revision_id for item in document["revisions"]):
            return revision_id
        return None

    def _find_request(self, key: str, request: str) -> dict[str, object] | None:
        """新建请求的重放：同键同内容返回已有条目，不同内容拒绝。"""
        for path in self._idea_files():
            try:
                document = self._read(path.stem)
            except WorkspaceError:
                continue
            row = (document.get("requests") or {}).get(key)
            if not isinstance(row, dict):
                continue
            if row.get("request") != request:
                raise WorkspaceError(
                    code="REQUEST_KEY_CONFLICT",
                    where="ideas.request",
                    why=f"请求键 {key} 之前用于另一次修改",
                    fix="为这次修改生成新的请求键；不要复用已经用过的键",
                    details={"request_key": key},
                )
            revision_id = str(row.get("revision_id") or "")
            for item in document["revisions"]:
                if item["id"] == revision_id:
                    return _idea_view(document, item)
        return None

    def _check_current(
        self,
        idea_id: str,
        document: Mapping[str, Any],
        expected_digest: str,
        expected_revision: str,
    ) -> None:
        self._check_digest(idea_id, expected_digest)
        if expected_revision != str(document.get("head_revision_id") or ""):
            raise WorkspaceError(
                code="IDEA_STALE_CONFLICT",
                where=f"ideas.update:{idea_id}",
                why="页面上的修订不是当前最新修订",
                fix="保留本地输入并重新读取当前版本；系统不会自动覆盖新内容",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": document.get("head_revision_id"),
                },
            )

    def _check_digest(self, idea_id: str, expected_digest: str) -> None:
        current = _digest_of(self._path(idea_id))
        if expected_digest != current:
            raise WorkspaceError(
                code="IDEA_STALE_CONFLICT",
                where=f"ideas.update:{idea_id}",
                why="灵感文件在读取之后被改过",
                fix="保留本地输入并重新读取当前版本；不要用旧页面覆盖新内容",
                details={"expected_digest": expected_digest, "current_digest": current},
            )

    def _checked_use(self, use: IdeaUseDraft) -> IdeaUseDraft:
        if use.kind == "author_link":
            return use
        if not use.target_path:
            raise WorkspaceError(
                code="IDEA_USE_UNVERIFIED",
                where="ideas.link",
                why="标为已落实时必须给出目标文件与区间",
                fix="给出本轮实际写入的正文路径、指纹、区间与引文；只作参考时用 author_link",
            )
        if not is_managed_path(use.target_path):
            raise WorkspaceError(
                code="IDEA_USE_UNVERIFIED",
                where="ideas.link",
                why=f"目标不是作品受管文件：{use.target_path}",
                fix="只登记正文或设定集里的路径；灵感本身不是落实目标",
                details={"path": use.target_path},
            )
        view = self.workspace.read(use.target_path)
        if use.target_digest and use.target_digest != view.digest:
            raise WorkspaceError(
                code="IDEA_USE_UNVERIFIED",
                where="ideas.link",
                why="目标文件在本轮读取之后被改过",
                fix="重新读取目标并核对区间；不要用旧指纹登记落实",
                details={"path": use.target_path},
            )
        start = use.start or 0
        end = len(view.text) if use.end is None else use.end
        if not 0 <= start < end <= len(view.text):
            raise WorkspaceError(
                code="IDEA_USE_UNVERIFIED",
                where="ideas.link",
                why="区间超出目标原文",
                fix="按 read_file 返回的字符位置重新取区间",
                details={"path": use.target_path, "start": start, "end": end},
            )
        if use.quote and view.text[start:end] != use.quote:
            raise WorkspaceError(
                code="IDEA_USE_UNVERIFIED",
                where="ideas.link",
                why="引文与目标区间不一致",
                fix="引文要与区间完全对应；只作参考时用 author_link",
                details={"path": use.target_path, "start": start, "end": end},
            )
        return IdeaUseDraft(
            idea_revision_id=use.idea_revision_id,
            kind=use.kind,
            target_record_id=use.target_record_id,
            target_path=use.target_path,
            target_digest=view.digest,
            quote=use.quote,
            start=start,
            end=end,
            turn_id=use.turn_id,
        )


# -- 数据形状 ---------------------------------------------------------------


def _idea_view(document: Mapping[str, Any], revision: Mapping[str, Any]) -> dict[str, object]:
    return {
        "idea_id": str(document.get("idea_id") or ""),
        "head_revision_id": str(document.get("head_revision_id") or ""),
        "revision_id": str(revision.get("id") or ""),
        "parent": revision.get("parent"),
        "created_at": str(document.get("created_at") or ""),
        "updated_at": str(document.get("updated_at") or ""),
        "title": str(revision.get("title") or ""),
        "body": str(revision.get("body") or ""),
        "tags": list(revision.get("tags") or []),
        "deleted": bool(revision.get("deleted")),
        "operation": str(revision.get("operation") or ""),
        "restored_from": revision.get("restored_from"),
        "origin": document.get("origin"),
        "uses": list(document.get("uses") or []),
        "digest": _digest_of_document(document),
    }


def _idea_summary(document: Mapping[str, Any], revision: Mapping[str, Any]) -> dict[str, object]:
    body = str(revision.get("body") or "")
    excerpt = body[:EXCERPT_CHARS]
    return {
        "idea_id": str(document.get("idea_id") or ""),
        "head_revision_id": str(document.get("head_revision_id") or ""),
        "title": str(revision.get("title") or ""),
        "excerpt": excerpt,
        "excerpt_truncated": len(body) > len(excerpt),
        "body_chars": len(body),
        "tags": list(revision.get("tags") or []),
        "deleted": bool(revision.get("deleted")),
        "created_at": str(document.get("created_at") or ""),
        "updated_at": str(document.get("updated_at") or ""),
    }


def _revision(document: Mapping[str, Any], revision_id: str) -> dict[str, Any]:
    for item in document.get("revisions") or []:
        if isinstance(item, dict) and item.get("id") == revision_id:
            return item
    raise WorkspaceError(
        code="IDEA_REVISION_NOT_FOUND",
        where="ideas.revision",
        why=f"没有这个修订：{revision_id}",
        fix="从修订历史里选一条存在的修订",
        details={"revision_id": revision_id},
    )


def _use_payload(use: IdeaUseDraft) -> dict[str, object]:
    return {
        "id": _new_id("use"),
        "idea_revision_id": use.idea_revision_id,
        "kind": use.kind,
        "target_record_id": use.target_record_id,
        "target_path": use.target_path,
        "target_digest": use.target_digest,
        "quote": use.quote,
        "start": use.start,
        "end": use.end,
        "turn_id": use.turn_id,
        "at": _now(),
    }


def _same_use(left: Mapping[str, Any], right: IdeaUseDraft) -> bool:
    return (
        left.get("idea_revision_id") == right.idea_revision_id
        and left.get("kind") == right.kind
        and left.get("target_path") == right.target_path
        and left.get("target_digest") == right.target_digest
        and left.get("start") == right.start
        and left.get("end") == right.end
    )


def _checked_content(
    title: object, body: object, tags: Sequence[object]
) -> tuple[str, str, tuple[str, ...]]:
    """标题与标签是旧字段，按原样裁剪；正文只做空白判定，保存原文。"""
    checked_title = str(title or "").strip()
    if len(checked_title) > MAX_TITLE_CHARS:
        raise WorkspaceError(
            code="IDEA_CONTENT_TOO_LARGE",
            where="ideas.content",
            why=f"标题最长 {MAX_TITLE_CHARS} 字符，当前 {len(checked_title)}",
            fix="缩短标题，把说明写进正文",
        )
    raw_body = str(body or "")
    if not raw_body.strip():
        raise WorkspaceError(
            code="IDEA_CONTENT_INVALID",
            where="ideas.content",
            why="灵感正文不能为空",
            fix="写一句想记下来的内容；只言片语也可以",
        )
    if len(raw_body) > MAX_BODY_CHARS:
        raise WorkspaceError(
            code="IDEA_CONTENT_TOO_LARGE",
            where="ideas.content",
            why=f"灵感正文最长 {MAX_BODY_CHARS} 字符，当前 {len(raw_body)}",
            fix="拆成几条灵感分别记录；不会截断保存",
            details={"chars": len(raw_body), "limit": MAX_BODY_CHARS},
        )
    checked_body = raw_body
    checked_tags: list[str] = []
    for tag in tags:
        text = str(tag).strip()
        if not text:
            continue
        if len(text) > MAX_TAG_CHARS:
            raise WorkspaceError(
                code="IDEA_CONTENT_TOO_LARGE",
                where="ideas.content",
                why=f"标签过长：{text[:20]}…",
                fix="每个标签不超过 40 字符",
            )
        if text not in checked_tags:
            checked_tags.append(text)
    if len(checked_tags) > MAX_TAGS:
        raise WorkspaceError(
            code="IDEA_CONTENT_TOO_LARGE",
            where="ideas.content",
            why=f"标签最多 {MAX_TAGS} 个，当前 {len(checked_tags)}",
            fix="只保留检索会用到的标签",
        )
    return checked_title, checked_body, tuple(checked_tags)


def _matches(revision: Mapping[str, Any], needle: str) -> bool:
    haystack = [
        str(revision.get("title") or ""),
        str(revision.get("body") or ""),
        *(str(tag) for tag in revision.get("tags") or []),
    ]
    return any(needle in item.lower() for item in haystack)


def _new_id(prefix: str) -> str:
    import secrets

    return f"{prefix}_{secrets.token_hex(_IDEA_ID_LEN // 2)}"


def _valid_id(value: str, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(f"{prefix}_"):
        return False
    tail = value[len(prefix) + 1 :]
    return len(tail) == _IDEA_ID_LEN and all(char in "0123456789abcdef" for char in tail)


def _digest_of(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise WorkspaceError(
            code="IDEA_STORE_INVALID",
            where="ideas.digest",
            why=f"读不到灵感文件：{exc.strerror or exc}",
            fix="检查作品目录权限；文件损坏时从备份恢复",
            details={"path": str(path)},
        ) from exc


def _digest_of_document(document: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(document), ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _request_digest(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _trim(requests: Mapping[str, Any]) -> dict[str, Any]:
    if len(requests) <= MAX_REQUEST_KEYS:
        return dict(requests)
    keep = list(requests)[-MAX_REQUEST_KEYS:]
    return {key: requests[key] for key in keep}


def _check_key(key: str) -> str:
    text = str(key or "").strip()
    if not 8 <= len(text) <= 120:
        raise WorkspaceError(
            code="REQUEST_KEY_INVALID",
            where="ideas.request",
            why="请求键长度需要在 8–120 字符之间",
            fix="由调用方为每次修改生成一个稳定且不重复的键",
        )
    return text


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


def _limit(value: int) -> int:
    return max(1, min(int(value), MAX_PAGE_SIZE))


def _cursor(query: str, offset: int) -> str:
    import base64

    raw = f"{offset}:{hashlib.sha256(query.encode('utf-8')).hexdigest()[:12]}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _offset(cursor: str | None, query: str) -> int:
    if not cursor:
        return 0
    import base64

    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        offset_text, digest = raw.split(":", 1)
        offset = int(offset_text)
    except (ValueError, UnicodeDecodeError):
        offset, digest = -1, ""
    if offset < 0 or digest != hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]:
        raise WorkspaceError(
            code="QUERY_CURSOR_STALE_CONFLICT",
            where="ideas.query",
            why="查询条件或条目数量已经变化，旧游标不能继续使用",
            fix="从第一页重新查询",
        )
    return offset
