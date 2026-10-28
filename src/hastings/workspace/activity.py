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
from dataclasses import dataclass
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


SCHEMA_VERSION = 3


def reverted_commits(root: Path) -> frozenset[str]:
    """已经明确撤销的提交标识。

    只读原始操作日志，不依赖查询索引：撤销是 ``撤销版本`` 操作里记下的目标提交。
    日志缺失或损坏时返回空集——宁可多显示一条「该位置已变更」，也不把没撤销的
    写入说成已撤回。
    """
    path = Path(root) / "activity" / "operations.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return frozenset()
    found: set[str] = set()
    for line in lines:
        # 先做一次廉价过滤：绝大多数事件既没有提交标识，也不是撤销操作。
        if "target_commit" not in line or "撤销版本" not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "operation_finished":
            continue
        if event.get("operation") != "撤销版本" or event.get("status") != "completed":
            continue
        commit = event.get("target_commit")
        if isinstance(commit, str) and commit:
            found.add(commit)
    return frozenset(found)


#: 工具名 → 作者读得懂的动词。未列出的工具保留原名，不猜用途。
_TOOL_VERBS = {
    "read_file": "读取",
    "write_file": "写入",
    "edit_file": "修改",
    "move_file": "移动",
    "delete_file": "删除",
    "list_files": "列出文件",
    "search_text": "查找",
    "read_state": "查看状态",
    "set_task_scope": "登记修改范围",
    "extend_task_scope": "扩展修改范围",
    "request_review": "请求检查",
    "register_references": "登记参考",
    "git_log": "查看历史",
    "git_diff": "查看差异",
    "publish_material_result": "提交整理结果",
    "read_idea": "读取灵感",
    "search_ideas": "查找灵感",
    "save_idea": "保存灵感",
    "ask_author": "询问作者",
    "link_clue_design": "关联计划",
    "save_clue_inference": "保存推理目标",
}
_FILE_VERBS = {
    "create": "新建文件",
    "update": "写入文件",
    "delete": "删除文件",
    "move": "移动文件",
}
_STAGE_LABELS = {"write": "写作", "review": "检查", "index": "索引", "materials": "资料整理"}


@dataclass(frozen=True, slots=True)
class ActivityText:
    """一条事件在界面上的说法：动作、阶段、动词、对象、结果。

    同一 ``pair_key`` 的开始与结束事件在界面合成一行；``summary`` 仍按旧格式保留，
    供导出与旧客户端读取。
    """

    category: str
    status: str
    action: str
    phase: str
    title: str
    subject: str
    note: str
    pair_key: str
    summary: str


def _squash(value: Any, limit: int = 60) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _size(value: Any) -> str:
    size = _count(value)
    if not size:
        return ""
    return f"{size} 字节" if size < 1024 else f"{size / 1024:.1f} KB"


def _duration(value: Any) -> str:
    ms = _count(value)
    if not ms:
        return ""
    if ms < 1000:
        return f"{ms} 毫秒"
    if ms < 60_000:
        return f"{ms / 1000:.1f} 秒"
    minutes, seconds = divmod(round(ms / 1000), 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def _join(*parts: str) -> str:
    return " · ".join(part for part in parts if part)


def _domain_label(domain: str) -> str:
    if not domain:
        return "资料"
    from hastings.workspace.story_materials import DOMAIN_LABEL

    return DOMAIN_LABEL.get(domain, "资料")


def _status(kind: str, event: Mapping[str, Any]) -> str:
    status = str(event.get("status", ""))
    if kind == "error" or event.get("ok") is False or status == "failed":
        return "failed"
    if kind.endswith("started"):
        return "started"
    return status or "completed"


def classify(event: dict[str, Any], event_id: str = "") -> ActivityText:
    """把内部事件翻译成作者能读的一行动作；不改事件本身，也不推断未记录的结果。"""
    kind = str(event.get("type", "unknown"))
    status = _status(kind, event)
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

    action, title, subject, note = "other", kind, "", ""
    phase = status
    pair = f"event:{event_id or event.get('seq', '')}"

    if kind == "turn_started":
        scope = event.get("material_scope")
        scope = scope if isinstance(scope, Mapping) else {}
        if str(event.get("origin") or "writing") == "materials":
            action, title = "materials", "开始整理"
            label = _domain_label(str(scope.get("domain") or ""))
            chapter = scope.get("as_of_chapter")
            subject = f"{label} · 第 {chapter} 章" if _count(chapter) else label
        else:
            action, title = "conversation", "收到要求"
            subject = _squash(event.get("text"), 42)
        note = str(event.get("model") or "")
        pair = f"turn:{event.get('turn_id', '')}"
    elif kind == "run_finished":
        action, title = "conversation", "本轮结束"
        changed = event.get("changed_paths")
        changed = changed if isinstance(changed, (list, tuple)) else []
        recording = str(event.get("recording") or "")
        if changed:
            note = f"{len(changed)} 个文件" + (" · 已提交" if recording == "committed" else "")
        elif recording == "no_change":
            note = "没有文件变化"
        subject = str(event.get("commit") or "")[:7]
        pair = f"turn:{event.get('turn_id', '')}"
    elif kind in ("assistant_started", "assistant_finished"):
        action, title = "model", "模型回复"
        pair = f"model:{event.get('parent_operation_id') or event_id}"
    elif kind == "assistant_message":
        action, title = "conversation", "公开回复"
        text = str(event.get("text") or "")
        subject = f"{len(text):,} 字" if text else ""
    elif kind == "model_request_started":
        action, title = "model", "请求模型"
        subject = str(event.get("model") or "")
        note = _STAGE_LABELS.get(str(event.get("stage") or ""), "")
        pair = f"model:{event.get('operation_id') or event_id}"
    elif kind == "model_request_finished":
        action, title = "model", "模型返回"
        subject = str(event.get("model") or "")
        usage = event.get("usage")
        output = usage.get("output") if isinstance(usage, Mapping) else None
        note = _join(
            f"输出 {output:,} tokens" if _count(output) else "",
            _duration(event.get("duration_ms")),
        )
        pair = f"model:{event.get('operation_id') or event_id}"
    elif kind in ("tool_started", "tool_finished"):
        tool = str(event.get("tool") or "")
        args = event.get("args")
        args = args if isinstance(args, Mapping) else {}
        action = "tool"
        title = _TOOL_VERBS.get(tool, tool or "调用工具")
        subject = str(args.get("path") or args.get("query") or "")
        if kind == "tool_finished":
            chars = _count(event.get("result_chars"))
            note = (
                _squash(event.get("summary"), 80)
                if event.get("ok") is False
                else _join(f"{chars:,} 字" if chars else "", _duration(event.get("duration_ms")))
            )
        pair = f"tool:{event.get('tool_call_id') or event_id}"
    elif kind == "file_changed":
        change = str(event.get("action") or "update")
        action, title = "file", _FILE_VERBS.get(change, "修改文件")
        subject = str(event.get("path") or "")
        note = _size(event.get("size"))
        pair = f"tool:{event['tool_call_id']}" if event.get("tool_call_id") else f"event:{event_id}"
    elif kind in ("version_baseline", "version_finished"):
        action = "version"
        title = "建立版本基线" if kind == "version_baseline" else "记录版本"
        subject = str(event.get("commit") or event.get("baseline_commit") or "")[:7]
        if kind == "version_finished":
            changed = event.get("changed_paths")
            changed = changed if isinstance(changed, (list, tuple)) else []
            note = _join(
                f"{len(changed)} 个文件" if changed else "", _duration(event.get("duration_ms"))
            )
        pair = f"version:{event.get('parent_operation_id') or event.get('turn_id') or event_id}"
    elif kind in ("task_scope_set", "task_scope_extended"):
        action = "scope"
        title = "登记修改范围" if kind == "task_scope_set" else "扩展修改范围"
        subject = _squash(event.get("summary"), 46)
        grants = event.get("grants")
        note = f"{len(grants)} 项" if isinstance(grants, (list, tuple)) and grants else ""
    elif kind == "state_injected":
        action, title = "context", "装配上下文"
        subject = str(event.get("profile") or "")
        budget = event.get("budget")
        if isinstance(budget, Mapping):
            note = f"{_count(budget.get('used')):,} / {_count(budget.get('limit')):,} 字"
    elif kind == "material_read":
        action, title = "context", "读取材料"
        subject = str(event.get("path") or "")
        chars = _count(event.get("total_chars"))
        note = f"{chars:,} 字" if chars else ""
    elif kind == "context_prepared":
        action, title = "context", "准备参考"
        paths = event.get("reference_paths") or event.get("paths")
        paths = paths if isinstance(paths, (list, tuple)) else []
        note = f"{len(paths)} 个文件" if paths else ""
    elif kind == "reference_registered":
        action, title = "context", "登记参考"
        subject = str(event.get("path") or "")
    elif kind.startswith("state_"):
        action, title = "state", "发布状态"
        subject = str(event.get("domain") or event.get("path") or "")
    elif kind.startswith(("index_", "chapter_index_")):
        action, title = "state", "发布章节索引"
        subject = str(event.get("path") or "")
    elif kind.startswith("review_"):
        action = "state"
        title = "请求检查" if kind == "review_requested" else "检查结束"
    elif kind in ("operation_started", "operation_finished"):
        action = "operation"
        title = str(event.get("operation") or "作品操作")
        subject = _squash(event.get("filename") or event.get("summary") or "", 40)
        if kind == "operation_finished":
            note = _join(
                _duration(event.get("duration_ms")),
                _squash(event.get("message"), 60) if status == "failed" else "",
            )
        pair = f"operation:{event.get('operation_id') or event_id}"
    elif kind == "run_phase":
        action, title = "other", "执行阶段"
        note = {"model": "模型思考", "tool": "工具执行"}.get(str(event.get("phase") or ""), "")
    elif kind == "material_target_published":
        action, title = "materials", "发布整理结果"
        subject = _join(_domain_label(str(event.get("domain") or "")), str(event.get("path") or ""))
        outcome = {"found": "有结果", "empty": "无发现", "insufficient": "信息不足"}.get(
            str(event.get("outcome") or ""), ""
        )
        published = _count(event.get("published"))
        note = _join(outcome, f"提交 {published} 条" if published else "")
    elif kind == "material_task_finished":
        action, title = "materials", "整理任务结束"
        subject = _domain_label(str(event.get("domain") or ""))
        done, total = _count(event.get("done")), _count(event.get("total"))
        if total:
            note = f"完成 {done} / {total}"
            phase = "completed" if done >= total else "partial"
    elif kind == "stop_requested":
        action, title = "conversation", "请求停止"
    elif kind == "author_input_requested":
        action, title = "conversation", "等待作者回答"
        subject = _squash(event.get("question"), 60)
    elif kind == "author_replied":
        action, title = "conversation", "作者已回答"
        subject = _squash(event.get("answer"), 60)
    elif kind == "error":
        action, title = "error", "错误"
        subject = str(event.get("code") or "")
        note = _squash(event.get("message"), 80)
    elif kind == "conversation_meta":
        action, title = "other", "会话建立"
        subject = _squash(event.get("title"), 40)

    summary = _join(title, subject, note)[:180]
    return ActivityText(category, status, action, phase, title, subject, note, pair, summary)


def describe(event: dict[str, Any]) -> tuple[str, str, str]:
    """旧接口：只取类别、状态与摘要。"""
    text = classify(event)
    return text.category, text.status, text.summary


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
               category TEXT, status TEXT, action TEXT, phase TEXT, title TEXT,
               subject TEXT, note TEXT, pair_key TEXT, summary TEXT, search TEXT,
               payload TEXT);
            CREATE INDEX IF NOT EXISTS events_scope ON events(conversation_id, turn_id, id);
        """)
        db.execute("INSERT OR IGNORE INTO meta VALUES ('generation', ?)", (uuid.uuid4().hex,))
        row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if row is None or row[0] != str(SCHEMA_VERSION):
            # 索引只用于查询，可整体重建；原始日志不动，游标代次改变让客户端重取。
            db.executescript("""
                DROP TABLE IF EXISTS events;
                DROP TABLE IF EXISTS sources;
                CREATE TABLE events
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, source TEXT,
                   seq INTEGER, at TEXT, conversation_id TEXT, turn_id TEXT, type TEXT,
                   category TEXT, status TEXT, action TEXT, phase TEXT, title TEXT,
                   subject TEXT, note TEXT, pair_key TEXT, summary TEXT, search TEXT,
                   payload TEXT);
                CREATE TABLE sources
                  (name TEXT PRIMARY KEY, offset INTEGER, inode TEXT, modified INTEGER);
                CREATE INDEX events_scope ON events(conversation_id, turn_id, id);
            """)
            db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
            db.execute("UPDATE meta SET value=? WHERE key='generation'", (uuid.uuid4().hex,))
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
                    text = classify(event, str(event_id))
                    payload = json.dumps(event, ensure_ascii=False)
                    db.execute(
                        """INSERT INTO pending
                        (event_id,source,seq,at,conversation_id,turn_id,type,category,status,
                         action,phase,title,subject,note,pair_key,summary,search,payload)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            event_id,
                            name,
                            event.get("seq", offset),
                            at,
                            conversation,
                            event.get("turn_id", ""),
                            event.get("type", "unknown"),
                            text.category,
                            text.status,
                            text.action,
                            text.phase,
                            text.title,
                            text.subject,
                            text.note,
                            text.pair_key,
                            text.summary,
                            payload.lower(),
                            payload,
                        ),
                    )
            db.execute(
                "INSERT OR REPLACE INTO sources VALUES (?,?,?,?)",
                (name, offset, inode, path.stat().st_mtime_ns),
            )
        db.execute("""INSERT OR IGNORE INTO events
            (event_id,source,seq,at,conversation_id,turn_id,type,category,status,
             action,phase,title,subject,note,pair_key,summary,search,payload)
            SELECT event_id,source,seq,at,conversation_id,turn_id,type,category,status,
                   action,phase,title,subject,note,pair_key,summary,search,payload
            FROM pending ORDER BY at,source,seq""")
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
                        scope = payload.get("material_scope")
                        scope = scope if isinstance(scope, Mapping) else {}
                        origin = str(payload.get("origin") or "writing")
                        run = {
                            "turn_id": latest_run["turn_id"],
                            "started_at": latest_run["at"],
                            "finished_at": terminal["at"] if terminal else None,
                            "model": payload.get("model"),
                            "status": json.loads(terminal["payload"])["status"]
                            if terminal
                            else "running",
                            "origin": origin,
                            "label": _domain_label(str(scope.get("domain") or ""))
                            if origin == "materials"
                            else "写作",
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
                                "action",
                                "phase",
                                "title",
                                "subject",
                                "note",
                                "pair_key",
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
