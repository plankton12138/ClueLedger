"""灵感的使用记录（0.8 I5）。

回答一个问题：这条灵感在哪次讨论里被带上、哪次展开过、有没有真的写进正文。

三条依据分开：

- **引用与展开**：以会话日志里的受理事件为准（``turn_started`` 冻结的
  ``idea_revisions`` 与 ``purpose``）。它们是事实，不是模型措辞。
- **写入**：以灵感文件里已核对的使用关联（``kind=applied``）为准，并且每次读取都
  重新核对当前正文的指纹与区间——路径还在不代表引文还在；已经明确撤销的那次提交
  单列为「已撤回」，不再算作仍然生效的写入。
- **关联**：作者手动登记的条目只显示为「关联」，不冒充已写入。
- **会话**：讨论被删掉时保留当时那条记录并标为不可用，不悄悄隐藏。

索引只保存「哪个会话的第几条事件提到了哪条灵感的哪一版」，不复制灵感正文；
索引丢了可以从事件日志重建，重建按批推进，没扫完时明确告诉调用方「还不完整」。
正文已经写好、关联没记上的那一次留一条待补记录，下次读取时按原文再核对，
对不上就始终不声称「已写入」。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Literal

from hastings.workspace.activity import reverted_commits
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.ideas import IdeaStore, IdeaUseDraft
from hastings.workspace.state import ConversationStore

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "IdeaActivityIndex",
    "MAX_PAGE_SIZE",
    "SCAN_BATCH",
]

INDEX_SCHEMA = 1
#: 一次请求最多回放多少条事件：索引按批重建，不把整个日志读进一次响应。
SCAN_BATCH = 400
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50

ActivityKind = Literal["referenced", "expanded", "applied", "linked"]

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class IdeaActivityIndex:
    """一个作品的使用记录索引；按会话日志增量回放，写入时整文件原子替换。"""

    def __init__(self, workspace: Workspace, state_root: Path) -> None:
        self.workspace = workspace
        self.state_root = state_root
        self.store = ConversationStore(state_root)
        self.path = state_root / "idea-activity.json"

    def page(
        self,
        idea_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, object]:
        size = _limit(limit)
        with self._lock():
            document = self._read()
            unfinished = self._scan(document)
            # 上次正文写好了、关联没记上的条目：每次读取时再核对一次。
            self._retry_deferred(document)
            items = self._items(document, idea_id)
            query = f"activity:{idea_id}:{len(items)}:{_fingerprint(items)}"
            offset = _offset(cursor, query)
            page = items[offset : offset + size]
            next_offset = offset + size
            return {
                "items": page,
                "total": len(items),
                # 还没扫完时不给游标：这一页本身还在长，翻页只会错位。
                "complete": not unfinished,
                "next_cursor": (_cursor(query, next_offset) if next_offset < len(items) else None)
                if not unfinished
                else None,
            }

    # -- 索引读写 -----------------------------------------------------------

    def defer(
        self,
        idea_id: str,
        *,
        revision_id: str,
        target_path: str,
        quote: str,
        commit: str,
        turn_id: str,
    ) -> None:
        """正文已经改好、关联没记上时留一条待补记录。

        不重跑模型、不改正文：下次读取使用记录时按当时的原文再核对一次区间，
        对得上就补成「已写入」，对不上就放弃补记，始终不声称采用。
        """
        marker = f"{turn_id}:{idea_id}:{target_path}"
        with self._lock():
            document = self._read()
            rows = [
                dict(row)
                for row in document.get("pending") or []
                if isinstance(row, dict) and row.get("id") != marker
            ]
            rows.append(
                {
                    "id": marker,
                    "idea_id": idea_id,
                    "revision_id": revision_id,
                    "target_path": target_path,
                    "quote": quote,
                    "commit": commit,
                    "turn_id": turn_id,
                }
            )
            document["pending"] = rows
            self._write(document)

    def _retry_deferred(self, document: dict[str, Any]) -> None:
        """补记待办：仍能核对上的补成已写入，明确对不上的丢弃并说明原因。"""
        rows = [row for row in document.get("pending") or [] if isinstance(row, dict)]
        if not rows:
            return
        store = IdeaStore(self.workspace)
        keep: list[dict[str, object]] = []
        changed = False
        for row in rows:
            idea_id = str(row.get("idea_id") or "")
            revision_id = str(row.get("revision_id") or "")
            path = str(row.get("target_path") or "")
            quote = str(row.get("quote") or "")
            turn_id = str(row.get("turn_id") or "")
            if not idea_id or not revision_id or not path or not quote:
                changed = True
                continue
            try:
                view = self.workspace.read(path)
            except WorkspaceError:
                # 目标不在了：这次关联永远核对不上，丢掉待办，不制造已写入。
                changed = True
                continue
            start = view.text.find(quote)
            if start < 0:
                changed = True
                continue
            try:
                current = store.get(idea_id)
            except WorkspaceError:
                # 灵感已经读不到：这条待补没有归属，直接丢掉。
                changed = True
                continue
            try:
                store.add_use(
                    idea_id,
                    expected_digest=str(current["digest"]),
                    use=IdeaUseDraft(
                        idea_revision_id=revision_id,
                        kind="applied",
                        target_path=path,
                        target_digest=view.digest,
                        quote=quote,
                        start=start,
                        end=start + len(quote),
                        turn_id=turn_id,
                        commit=str(row.get("commit") or ""),
                    ),
                    # 与写入时同一个键：之前其实写成功过就直接重放，不另算一次。
                    request_key=f"{turn_id}:idea-apply:{idea_id}:{path}",
                )
            except WorkspaceError:
                # 仍然记不上（锁、冲突、权限）：留着下次再试。
                keep.append(row)
                continue
            changed = True
        if changed:
            document["pending"] = keep
            self._write(document)

    def _read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"schema": INDEX_SCHEMA, "cursors": {}, "items": [], "pending": []}
        except OSError as exc:
            raise WorkspaceError(
                code="IDEA_ACTIVITY_UNAVAILABLE",
                where="ideas.activity",
                why=f"读不到使用记录索引：{exc.strerror or exc}",
                fix="删除该索引后会按会话日志重建",
                details={"path": str(self.path)},
            ) from exc
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            # 索引是派生物：坏了就重建，不能因此让灵感不可用。
            return {"schema": INDEX_SCHEMA, "cursors": {}, "items": [], "pending": []}
        if not isinstance(document, dict) or document.get("schema") != INDEX_SCHEMA:
            return {"schema": INDEX_SCHEMA, "cursors": {}, "items": [], "pending": []}
        document.setdefault("cursors", {})
        document.setdefault("items", [])
        document.setdefault("pending", [])
        return document

    def _write(self, document: Mapping[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temp = tempfile.mkstemp(
                dir=self.path.parent, prefix=".idea-activity.", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(dict(document), ensure_ascii=False, indent=2) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, self.path)
            except BaseException:
                with suppress(OSError):
                    os.unlink(temp)
                raise
        except OSError as exc:
            raise WorkspaceError(
                code="IDEA_ACTIVITY_UNAVAILABLE",
                where="ideas.activity",
                why=f"写入使用记录索引失败：{exc.strerror or exc}",
                fix="检查应用状态目录权限；索引可以重建，不影响灵感本身",
                details={"path": str(self.path)},
            ) from exc

    @contextmanager
    def _lock(self) -> Iterator[None]:
        key = str(self.path.resolve())
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(key, threading.Lock())
        with lock:
            yield

    # -- 回放 ---------------------------------------------------------------

    def _scan(self, document: dict[str, Any]) -> bool:
        """回放未扫过的事件；返回 True 表示还有没扫完的部分。

        批次用完时先落盘再返回：游标只留在内存里的话，下次仍然从同一处重读，
        索引就永远停在原地。慢一点可以接受，卡住不行。
        """
        budget = SCAN_BATCH
        cursors: dict[str, int] = dict(document.get("cursors") or {})
        seen = {str(item.get("id")) for item in document.get("items") or []}
        items: list[dict[str, object]] = list(document.get("items") or [])
        changed = False
        for conversation in self.store.conversations():
            if budget <= 0:
                break
            after = int(cursors.get(conversation.id, 0))
            events = self.store.events(conversation.id, after=after)
            for event in events:
                if budget <= 0:
                    break
                budget -= 1
                cursors[conversation.id] = int(event.get("seq") or 0)
                changed = True
                if event.get("type") != "turn_started":
                    continue
                context = event.get("context")
                revisions = context.get("idea_revisions") if isinstance(context, dict) else None
                if not isinstance(revisions, list):
                    continue
                purpose = str(event.get("purpose") or "chat")
                kind: ActivityKind = "expanded" if purpose == "expand_idea" else "referenced"
                for revision in revisions:
                    if not isinstance(revision, dict):
                        continue
                    idea_id = str(revision.get("idea_id") or "")
                    revision_id = str(revision.get("revision_id") or "")
                    if not idea_id or not revision_id:
                        continue
                    item_id = f"{conversation.id}:{event.get('seq')}:{idea_id}:{kind}"
                    if item_id in seen:
                        continue
                    seen.add(item_id)
                    items.append(
                        {
                            "id": item_id,
                            "kind": kind,
                            "idea_id": idea_id,
                            "revision_id": revision_id,
                            "conversation_id": conversation.id,
                            "conversation_title": conversation.title,
                            "turn_id": str(event.get("turn_id") or ""),
                            "at": str(event.get("at") or ""),
                        }
                    )
        if changed:
            document["cursors"] = cursors
            document["items"] = items
            self._write(document)
        return budget <= 0

    # -- 组装 ---------------------------------------------------------------

    def _items(self, document: Mapping[str, Any], idea_id: str) -> list[dict[str, object]]:
        # 会话标题与可用性在读取时补：改名能立刻反映，会话没了也不删掉当时的记录。
        known = {conversation.id: conversation.title for conversation in self.store.conversations()}
        rows: list[dict[str, object]] = []
        for item in document.get("items") or []:
            if not isinstance(item, dict) or item.get("idea_id") != idea_id:
                continue
            row = dict(item)
            conversation_id = str(row.get("conversation_id") or "")
            row["conversation_available"] = conversation_id in known
            if conversation_id in known:
                row["conversation_title"] = known[conversation_id]
            rows.append(row)
        rows.extend(self._use_items(idea_id))
        rows.sort(
            key=lambda item: (str(item.get("at") or ""), str(item.get("id") or "")), reverse=True
        )
        return rows

    def _use_items(self, idea_id: str) -> list[dict[str, object]]:
        """已核对的使用关联；每次读取都按当前正文重新判断是否仍然有效。"""
        try:
            view = IdeaStore(self.workspace).get(idea_id)
        except WorkspaceError:
            return []
        reverted = reverted_commits(self.state_root)
        rows: list[dict[str, object]] = []
        uses = view.get("uses")
        for use in uses if isinstance(uses, list) else []:
            if not isinstance(use, dict):
                continue
            applied = str(use.get("kind") or "") == "applied"
            row: dict[str, object] = {
                "id": f"use:{use.get('id')}",
                "kind": "applied" if applied else "linked",
                "idea_id": idea_id,
                "revision_id": str(use.get("idea_revision_id") or ""),
                "turn_id": str(use.get("turn_id") or ""),
                "target_path": str(use.get("target_path") or ""),
                "at": str(use.get("at") or ""),
            }
            if applied:
                row.update(
                    {
                        "target_digest": str(use.get("target_digest") or ""),
                        "commit": str(use.get("commit") or ""),
                        "start": use.get("start"),
                        "end": use.get("end"),
                        "quote": str(use.get("quote") or ""),
                        "validity": self._validity(use, reverted),
                    }
                )
            rows.append(row)
        return rows

    def _validity(self, use: Mapping[str, Any], reverted: frozenset[str]) -> str:
        """current 需要指纹与区间同时对上；路径还在但内容变了就是 changed。

        ``reverted`` 优先：撤销过的那次写入即便正文又回到相同内容，也不能算作
        仍然生效的「已写入」，否则作者会以为这条灵感还在正文里。
        """
        commit = str(use.get("commit") or "")
        if commit and commit in reverted:
            return "reverted"
        path = str(use.get("target_path") or "")
        if not path:
            return "changed"
        try:
            view = self.workspace.read(path)
        except WorkspaceError:
            return "changed"
        if str(use.get("target_digest") or "") != view.digest:
            return "changed"
        start = use.get("start")
        end = use.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            return "changed"
        if start < 0 or end > len(view.text) or start >= end:
            return "changed"
        if view.text[start:end] != str(use.get("quote") or ""):
            return "changed"
        return "current"


def _limit(value: int) -> int:
    return max(1, min(int(value), MAX_PAGE_SIZE))


def _fingerprint(items: list[dict[str, object]]) -> str:
    raw = "|".join(str(item.get("id") or "") for item in items)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


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
            where="ideas.activity",
            why="使用记录在翻页期间发生变化，旧游标不能继续使用",
            fix="从第一页重新读取",
        )
    return offset
