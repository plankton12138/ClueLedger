"""作品活动查询：原始 JSONL 为依据，SQLite 仅为可重建的查询索引。"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.state import _repair_tail

_SECRET = re.compile(
    r"^(api[_-]?key|password|credentials?|secret|access[_-]?token|refresh[_-]?token|"
    r".*signature)$",
    re.I,
)
_TOKEN = re.compile(r"(?:Bearer|Basic)\s+[^\s\"']+|\bsk-[A-Za-z0-9_-]{12,}", re.I)
_NOISY = ("assistant_block_delta", "assistant_block_started", "assistant_block_finished")


def _timestamp(value: str) -> str:
    if not value:
        return ""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds")


def redact(value: Any) -> Any:
    """结构化凭据和供应商签名不落盘；不猜测普通作品文字是否为秘密。"""
    if isinstance(value, Mapping):
        return {
            str(k): "[已隐藏]" if _SECRET.search(str(k)) else redact(v)
            for k, v in value.items()
            if str(k) != "data" or not isinstance(v, bytes)
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _TOKEN.sub("[已隐藏]", value)
    return value


def _error(exc: Exception) -> WorkspaceError:
    return WorkspaceError(
        code="ACTIVITY_STORE_FAILED",
        where="activity",
        why=f"活动记录无法读写：{exc}",
        fix="检查应用状态目录权限与剩余磁盘空间；索引异常时可重建查询索引",
    )


def describe(event: dict[str, Any]) -> tuple[str, str, str]:
    kind = str(event.get("type", "unknown"))
    category = "task"
    if kind.startswith("model_") or kind.startswith("assistant_"):
        category = "model"
    elif kind.startswith("tool_"):
        category = "tool"
    elif kind in ("file_changed", "operation_started", "operation_finished"):
        category = "file"
    elif kind.startswith(("material_", "context_", "reference_", "task_scope", "state_injected")):
        category = "context"
    elif kind.startswith(("state_", "index_", "chapter_index_", "review_")):
        category = "state"
    elif kind.startswith("version_"):
        category = "version"
    status = str(event.get("status", ""))
    if kind == "error" or event.get("ok") is False or status == "failed":
        status = "failed"
    elif kind.endswith("started"):
        status = "started"
    elif not status:
        status = "completed"
    labels = {
        "turn_started": "任务受理",
        "run_finished": "本轮结束",
        "run_phase": "执行阶段",
        "model_request_started": "模型请求",
        "model_request_finished": "模型请求结束",
        "assistant_started": "回复开始",
        "assistant_finished": "回复结束",
        "assistant_message": "公开回复",
        "assistant_block_delta": "文字增量",
        "tool_started": "工具开始",
        "tool_finished": "工具结果",
        "material_read": "读取材料",
        "context_prepared": "准备参考",
        "reference_registered": "登记参考",
        "state_injected": "装配上下文",
        "task_scope_set": "登记修改范围",
        "task_scope_extended": "扩展修改范围",
        "file_changed": "文件变化",
        "state_published": "发布状态",
        "index_published": "发布章节索引",
        "review_requested": "请求检查",
        "review_finished": "检查结束",
        "version_finished": "版本记录",
        "operation_started": "用户操作开始",
        "operation_finished": "用户操作结束",
        "stop_requested": "请求停止",
        "error": "错误",
    }
    target = (
        event.get("path")
        or event.get("entity")
        or event.get("tool")
        or event.get("model")
        or event.get("operation")
        or event.get("status")
    )
    detail = event.get("summary") or event.get("message") or event.get("text") or target or ""
    summary = f"{labels.get(kind, kind)} · {' '.join(str(detail).split())[:180]}".rstrip(" ·")
    return category, status, summary


class ActivityStore:
    """一个作品一个实例；索引和原始操作日志在同一锁内串行更新。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / "activity"
        self._lock = threading.RLock()
        self._recovered = False

    def recover(self) -> None:
        """每个服务实例一次；上次进程未封口的用户操作只记结果未确认。"""
        with self._lock:
            if self._recovered:
                return
            self._recovered = True
            path = self.directory / "operations.jsonl"
            _repair_tail(path)
            pending: dict[str, dict[str, Any]] = {}
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    operation = str(event.get("operation_id", ""))
                    if event.get("type") == "operation_started":
                        pending[operation] = event
                    elif event.get("type") == "operation_finished":
                        pending.pop(operation, None)
            for operation, event in pending.items():
                self.append(
                    {
                        "type": "operation_finished",
                        "operation_id": operation,
                        "operation": event.get("operation"),
                        "status": "interrupted",
                        "message": "上次进程在操作期间结束，实际结果未确认",
                        "duration_ms": None,
                    }
                )

    def _connect(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.directory / "index.sqlite3", timeout=15)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources
              (name TEXT PRIMARY KEY, offset INTEGER, inode TEXT, modified INTEGER);
            CREATE TABLE IF NOT EXISTS events
              (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, source TEXT,
               seq INTEGER, at TEXT, conversation_id TEXT, turn_id TEXT, type TEXT,
               category TEXT, status TEXT, summary TEXT, search TEXT, payload TEXT);
            CREATE INDEX IF NOT EXISTS events_scope ON events(conversation_id, turn_id, id);
        """)
        db.execute("INSERT OR IGNORE INTO meta VALUES ('generation', ?)", (uuid.uuid4().hex,))
        db.commit()
        return db

    def append(self, event: dict[str, Any]) -> None:
        with self._lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                record = {
                    "version": 1,
                    "at": datetime.now(UTC).isoformat(),
                    "event_id": uuid.uuid4().hex,
                    **redact(event),
                }
                with (self.directory / "operations.jsonl").open("a", encoding="utf-8") as out:
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError as exc:
                raise _error(exc) from exc

    def settings(self) -> dict[str, Any]:
        with self._lock:
            path = self.directory / "settings.json"
            try:
                result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                if not isinstance(result, dict):
                    raise ValueError("活动设置必须是对象")
                days = result.get("retention_days", 30)
                if type(days) is not int or not 0 <= days <= 3650:
                    raise ValueError("详情保留天数必须为 0 至 3650 的整数")
                return {
                    "capture_inputs": result.get("capture_inputs") is True,
                    "retention_days": days,
                }
            except (OSError, ValueError) as exc:
                raise _error(exc) from exc

    def configure(self, *, capture_inputs: bool, retention_days: int = 30) -> dict[str, Any]:
        with self._lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                target = self.directory / "settings.json"
                temp = target.with_suffix(".tmp")
                temp.write_text(
                    json.dumps(
                        {"capture_inputs": capture_inputs, "retention_days": retention_days}
                    ),
                    encoding="utf-8",
                )
                temp.replace(target)
            except OSError as exc:
                raise _error(exc) from exc
            return self.settings()

    def attachment(self, value: Any) -> str:
        """先保存详情，调用方再发布引用；失败则不得发送后续模型请求。"""
        key = uuid.uuid4().hex
        with self._lock:
            try:
                folder = self.directory / "details"
                folder.mkdir(parents=True, exist_ok=True)
                retention = self.settings()["retention_days"]
                if retention:
                    threshold = time.time() - retention * 86400
                    for old in folder.glob("*.json"):
                        if old.stat().st_mtime < threshold:
                            old.unlink()
                (folder / f"{key}.json").write_text(
                    json.dumps(redact(value), ensure_ascii=False), encoding="utf-8"
                )
            except OSError as exc:
                raise _error(exc) from exc
        return key

    def detail_attachment(self, key: str) -> Any:
        if not re.fullmatch(r"[a-f0-9]{32}", key):
            raise WorkspaceError(
                code="ACTIVITY_DETAIL_NOT_FOUND",
                where="activity.detail",
                why="详情标识不存在",
                fix="从活动列表重新选择事件",
            )
        with self._lock:
            try:
                path = self.directory / "details" / f"{key}.json"
                days = self.settings()["retention_days"]
                if days and path.stat().st_mtime < time.time() - days * 86400:
                    path.unlink()
                    return {"available": False, "reason": "详情已超过保留时间"}
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {"available": False, "reason": "详情未保存或已清理"}
            except (OSError, ValueError) as exc:
                raise _error(exc) from exc

    def clear_details(self) -> int:
        with self._lock:
            count = 0
            try:
                for path in (self.directory / "details").glob("*.json"):
                    path.unlink()
                    count += 1
            except OSError as exc:
                raise _error(exc) from exc
            return count

    def rebuild(self) -> None:
        with self._lock:
            try:
                (self.directory / "index.sqlite3").unlink(missing_ok=True)
            except OSError as exc:
                raise _error(exc) from exc

    def _sync(self, db: sqlite3.Connection) -> None:
        # 本批新增记录先按发生时间排列；游标使用索引序号，不依赖时钟严格递增。
        db.execute("CREATE TEMP TABLE IF NOT EXISTS pending AS SELECT * FROM events WHERE 0")
        files = sorted(self.root.glob("*.jsonl"))
        operations = self.directory / "operations.jsonl"
        if operations.exists():
            files.append(operations)
        names = {str(p.relative_to(self.root)) for p in files}
        for row in db.execute("SELECT * FROM sources").fetchall():
            if row["name"] not in names:
                db.execute("DELETE FROM events WHERE source=?", (row["name"],))
                db.execute("DELETE FROM sources WHERE name=?", (row["name"],))
                db.execute("UPDATE meta SET value=? WHERE key='generation'", (uuid.uuid4().hex,))
        for path in files:
            name = str(path.relative_to(self.root))
            stat = path.stat()
            inode = str(stat.st_ino)
            previous = db.execute("SELECT * FROM sources WHERE name=?", (name,)).fetchone()
            offset = previous["offset"] if previous else 0
            if previous and (
                previous["inode"] != inode
                or stat.st_size < offset
                or (stat.st_size == offset and stat.st_mtime_ns != previous["modified"])
            ):
                db.execute("DELETE FROM events WHERE source=?", (name,))
                offset = 0
                db.execute("UPDATE meta SET value=? WHERE key='generation'", (uuid.uuid4().hex,))
            with path.open("rb") as stream:
                stream.seek(offset)
                while line := stream.readline():
                    if not line.endswith(b"\n"):
                        break  # 半行留给下一次读取。
                    offset = stream.tell()
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        event = {
                            "type": "error",
                            "code": "ACTIVITY_INVALID_LINE",
                            "message": "原始日志包含无法解析的完整行",
                            "source_offset": offset,
                        }
                    if not isinstance(event, dict) or event.get("seq") == 0:
                        continue
                    try:
                        at = _timestamp(str(event.get("at", "")))
                        if not isinstance(event.get("type"), str):
                            raise ValueError("缺少事件类型")
                    except (ValueError, TypeError) as exc:
                        event = {
                            "type": "error",
                            "code": "ACTIVITY_INVALID_EVENT",
                            "message": f"原始事件字段不可用：{exc}",
                            "source_offset": offset,
                            "invalid_event": event,
                        }
                        at = ""
                    event = redact(event)
                    conversation = path.stem if path.parent == self.root else ""
                    event_id = (
                        event.get("event_id")
                        or hashlib.sha256(
                            f"{name}:{event.get('seq', offset)}".encode()
                        ).hexdigest()[:32]
                    )
                    category, status, summary = describe(event)
                    payload = json.dumps(event, ensure_ascii=False)
                    db.execute(
                        """INSERT INTO pending
                        (event_id,source,seq,at,conversation_id,turn_id,type,category,status,
                         summary,search,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            event_id,
                            name,
                            event.get("seq", offset),
                            at,
                            conversation,
                            event.get("turn_id", ""),
                            event.get("type", "unknown"),
                            category,
                            status,
                            summary,
                            payload.lower(),
                            payload,
                        ),
                    )
            db.execute(
                "INSERT OR REPLACE INTO sources VALUES (?,?,?,?)",
                (name, offset, inode, path.stat().st_mtime_ns),
            )
        db.execute("""INSERT OR IGNORE INTO events
            (event_id,source,seq,at,conversation_id,turn_id,type,category,status,summary,search,payload)
            SELECT event_id,source,seq,at,conversation_id,turn_id,type,category,status,
                   summary,search,payload FROM pending ORDER BY at,source,seq""")
        db.execute("DELETE FROM pending")
        db.commit()

    def query(
        self,
        *,
        conversation_id: str = "",
        turn_id: str = "",
        category: str = "",
        status: str = "",
        q: str = "",
        since: str = "",
        until: str = "",
        after: str = "",
        before: int = 0,
        limit: int = 100,
        verbose: bool = False,
        details: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            try:
                db = self._connect()
                try:
                    self._sync(db)
                    generation = db.execute(
                        "SELECT value FROM meta WHERE key='generation'"
                    ).fetchone()[0]
                    latest = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
                    clauses = ["1=1"]
                    args: list[Any] = []
                    for key, value in (
                        ("conversation_id", conversation_id),
                        ("turn_id", turn_id),
                        ("category", category),
                        ("status", status),
                    ):
                        if value:
                            clauses.append(f"{key}=?")
                            args.append(value)
                    if q:
                        clauses.append("instr(search,?)>0")
                        args.append(q.lower())
                    if since:
                        clauses.append("at>=?")
                        args.append(_timestamp(since))
                    if until:
                        clauses.append("at<=?")
                        args.append(_timestamp(until))
                    if not verbose:
                        clauses.append("type NOT IN (?,?,?)")
                        args.extend(_NOISY)
                    where = " AND ".join(clauses)
                    stats = {
                        "events": db.execute(
                            f"SELECT COUNT(*) FROM events WHERE {where}", args
                        ).fetchone()[0]
                    }
                    for key, kind in (
                        ("models", "model_request_started"),
                        ("tools", "tool_started"),
                    ):
                        stats[key] = db.execute(
                            f"SELECT COUNT(*) FROM events WHERE {where} AND type=?", [*args, kind]
                        ).fetchone()[0]
                    stats["errors"] = db.execute(
                        f"SELECT COUNT(*) FROM events WHERE {where} AND status='failed'", args
                    ).fetchone()[0]
                    reset = bool(after and after.split(":")[0] != generation)
                    start = int(after.split(":")[-1]) if after and not reset else 0
                    if after and not reset:
                        clauses.append("id>?")
                        args.append(start)
                    if before:
                        clauses.append("id<?")
                        args.append(before)
                    ascending = bool(after and not reset)
                    order = "ASC" if ascending else "DESC"
                    rows = db.execute(
                        f"SELECT * FROM events WHERE {' AND '.join(clauses)} "
                        f"ORDER BY id {order} LIMIT ?",
                        [*args, limit + 1],
                    ).fetchall()
                    more = len(rows) > limit
                    rows = rows[:limit]
                    cursor = rows[-1]["id"] if ascending and more else latest
                    scope_sql, scope_args = "", []
                    if conversation_id:
                        scope_sql += " AND conversation_id=?"
                        scope_args.append(conversation_id)
                    if turn_id:
                        scope_sql += " AND turn_id=?"
                        scope_args.append(turn_id)
                    latest_run = db.execute(
                        "SELECT * FROM events WHERE type='turn_started'"
                        + scope_sql
                        + " ORDER BY id DESC LIMIT 1",
                        scope_args,
                    ).fetchone()
                    run = None
                    if latest_run:
                        terminal = db.execute(
                            "SELECT payload,at FROM events WHERE type='run_finished' "
                            "AND conversation_id=? AND turn_id=? ORDER BY id DESC LIMIT 1",
                            (latest_run["conversation_id"], latest_run["turn_id"]),
                        ).fetchone()
                        payload = json.loads(latest_run["payload"])
                        run = {
                            "turn_id": latest_run["turn_id"],
                            "started_at": latest_run["at"],
                            "finished_at": terminal["at"] if terminal else None,
                            "model": payload.get("model"),
                            "status": json.loads(terminal["payload"])["status"]
                            if terminal
                            else "running",
                        }
                    items = []
                    for row in rows:
                        item = {
                            k: row[k]
                            for k in (
                                "id",
                                "event_id",
                                "at",
                                "conversation_id",
                                "turn_id",
                                "type",
                                "category",
                                "status",
                                "summary",
                            )
                        }
                        payload = json.loads(row["payload"])
                        item["duration_ms"] = payload.get("duration_ms")
                        item["operation_id"] = payload.get("operation_id") or payload.get(
                            "tool_call_id"
                        )
                        if details:
                            item["detail"] = payload
                        items.append(item)
                    return {
                        "items": items,
                        "cursor": f"{generation}:{cursor}",
                        "reset": reset,
                        "has_more": more,
                        "next_before": rows[-1]["id"] if rows and more else None,
                        "stats": stats,
                        "run": run,
                    }
                finally:
                    db.close()
            except (OSError, sqlite3.Error, ValueError) as exc:
                raise _error(exc) from exc

    def detail(self, event_id: str) -> dict[str, Any]:
        with self._lock:
            db = self._connect()
            try:
                self._sync(db)
                row = db.execute(
                    "SELECT payload FROM events WHERE event_id=?", (event_id,)
                ).fetchone()
                if row:
                    return dict(json.loads(row[0]))
            finally:
                db.close()
        raise WorkspaceError(
            code="ACTIVITY_EVENT_NOT_FOUND",
            where="activity.detail",
            why="事件不存在或原始记录已删除",
            fix="刷新活动列表后重新选择事件",
        )

    def export(self, *, include_details: bool = False, **filters: Any) -> Iterator[str]:
        # 固定导出上界，运行中的新增事件留给下一次导出。
        page = self.query(limit=1, **filters)
        generation, upper = page["cursor"].split(":")
        cursor = f"{generation}:0"
        while True:
            page = self.query(after=cursor, limit=500, details=True, **filters)
            if page["reset"]:
                raise WorkspaceError(
                    code="ACTIVITY_EXPORT_CHANGED",
                    where="activity.export",
                    why="导出期间原始日志发生变化",
                    fix="重新导出当前记录",
                )
            for item in page["items"]:
                if item["id"] > int(upper):
                    return
                if include_details:
                    item["attachments"] = {
                        key: self.detail_attachment(item["detail"][key])
                        for key in ("input_detail", "result_detail")
                        if isinstance(item["detail"].get(key), str)
                    }
                yield json.dumps(item, ensure_ascii=False) + "\n"
            if not page["has_more"]:
                return
            cursor = page["cursor"]

    def operation(self, name: str, **details: Any) -> ActivityOperation:
        return ActivityOperation(self, name, details)


class ActivityOperation:
    def __init__(self, store: ActivityStore, name: str, details: dict[str, Any]) -> None:
        self.store, self.name, self.details = store, name, details
        self.id = uuid.uuid4().hex
        self.started = 0.0
        self.result: dict[str, Any] = {}

    def __enter__(self) -> ActivityOperation:
        self.store.append(
            {
                "type": "operation_started",
                "operation": self.name,
                "operation_id": self.id,
                **self.details,
            }
        )
        self.started = time.monotonic()
        return self

    def __exit__(self, kind: Any, error: Any, traceback: Any) -> None:
        self.store.append(
            {
                "type": "operation_finished",
                "operation": self.name,
                "operation_id": self.id,
                "status": "failed" if error else "completed",
                "duration_ms": int((time.monotonic() - self.started) * 1000),
                **self.details,
                **self.result,
                **({"message": str(error), "code": getattr(error, "code", None)} if error else {}),
            }
        )
