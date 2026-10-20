"""应用状态：最近目录与会话事件（0.4 M05 的状态部分）。

0.4 的作品内容只在作品目录里。应用自己需要记住的只有两类定位信息，都放在应用状态
目录，不复制正文，也不成为第二份当前稿：

- **最近目录**：同一目录重复打开只有一条记录，绑定 ID 由规范化路径算出；
- **会话与事件**：每个作品一组会话，每个会话一个 JSONL，按自增 ``seq`` 追加，
  供界面回读与断线补齐。事件是过程记录，不是作品内容。

登记或事件文件损坏时按空处理——宁可让作者重新选择目录、重新开始对话，也不让启动失败。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace

__all__ = [
    "DEFAULT_STATE_ROOT",
    "Conversation",
    "ConversationStore",
    "RecentWorkspaces",
    "WorkspaceBinding",
    "default_state_root",
]

#: 应用状态目录：聊天、最近目录、运行事件都在这里；不含作品正文。
DEFAULT_STATE_ROOT = Path.home() / ".hastings"

_STATE_ENV = "HASTINGS_STATE_DIR"
_REGISTRY_FILE = "workspaces.json"
_REGISTRY_VERSION = 1
#: 最近目录保留条数；超出后丢弃最旧的登记，不删除磁盘上的作品。
_MAX_ENTRIES = 20


def default_state_root() -> Path:
    """状态目录：``HASTINGS_STATE_DIR`` 优先，否则用户主目录下的 ``.hastings``。"""
    override = os.environ.get(_STATE_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_STATE_ROOT


@dataclass(frozen=True, slots=True)
class WorkspaceBinding:
    """界面与接口使用的本地绑定：ID 只用于定位，不写入作品内容。"""

    id: str
    name: str
    path: str
    opened_at: str

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "name": self.name, "path": self.path, "opened_at": self.opened_at}

    @property
    def exists(self) -> bool:
        return Path(self.path).is_dir()


class RecentWorkspaces:
    """最近目录登记：JSON 单文件，读写都加锁，写入用临时文件替换。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root is not None else default_state_root()
        self.registry_file = self.root / _REGISTRY_FILE
        self._lock = threading.Lock()

    # -- 查询 ---------------------------------------------------------------

    def recent(self) -> tuple[WorkspaceBinding, ...]:
        with self._lock:
            return tuple(self._load())

    def get(self, workspace_id: str) -> WorkspaceBinding:
        for binding in self.recent():
            if binding.id == workspace_id:
                return binding
        raise _unknown_workspace(workspace_id)

    def workspace(self, workspace_id: str) -> Workspace:
        """按绑定 ID 打开作品；目录已被移动或删除时给出可理解的失败。"""
        binding = self.get(workspace_id)
        try:
            return Workspace.open(Path(binding.path))
        except WorkspaceError as exc:
            raise WorkspaceError(
                code="WORKSPACE_NOT_FOUND",
                where="workspace.resolve",
                why=f"已登记的目录当前不可用：{binding.path}（{exc.why}）",
                fix="确认目录仍在原位置，或在顶栏重新打开目录",
                details={"id": workspace_id, "path": binding.path},
            ) from exc

    # -- 登记 ---------------------------------------------------------------

    def bind(self, root: Path) -> WorkspaceBinding:
        """登记一个已存在的目录并放到最前；同一目录不会产生第二条记录。"""
        resolved = Path(root).expanduser().resolve()
        if not resolved.exists():
            raise WorkspaceError(
                code="WORKSPACE_NOT_FOUND",
                where="workspace.bind",
                why=f"目录不存在：{resolved}",
                fix="确认路径拼写，或先用「新建」创建作品目录",
            )
        if not resolved.is_dir():
            raise WorkspaceError(
                code="WORKSPACE_NOT_DIRECTORY",
                where="workspace.bind",
                why=f"路径不是目录：{resolved}",
                fix="作品目录必须是一个文件夹",
            )
        binding = WorkspaceBinding(
            id=binding_id(resolved),
            name=resolved.name or str(resolved),
            path=str(resolved),
            opened_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        with self._lock:
            entries = [item for item in self._load() if item.id != binding.id]
            self._save([binding, *entries][:_MAX_ENTRIES])
        return binding

    # -- 内部 ---------------------------------------------------------------

    def _load(self) -> list[WorkspaceBinding]:
        try:
            raw = json.loads(self.registry_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            # 文件缺失、不可读或内容损坏：按空登记处理，不阻断打开目录。
            return []
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        bindings: list[WorkspaceBinding] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            workspace_id = item.get("id")
            path = item.get("path")
            name = item.get("name")
            opened_at = item.get("opened_at")
            if not isinstance(workspace_id, str) or not workspace_id:
                continue
            if not isinstance(path, str) or not path:
                continue
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(opened_at, str) or not opened_at:
                continue
            bindings.append(
                WorkspaceBinding(id=workspace_id, name=name, path=path, opened_at=opened_at)
            )
        return bindings

    def _save(self, bindings: list[WorkspaceBinding]) -> None:
        _write_json_atomic(
            self.registry_file,
            {
                "version": _REGISTRY_VERSION,
                "items": [binding.to_dict() for binding in bindings],
            },
            where="workspace.registry",
            why_prefix="无法保存最近目录登记",
        )


# -- 会话与事件 -------------------------------------------------------------


def conversation_root(state_root: Path, workspace_id: str) -> Path:
    """一个作品在应用状态目录里的位置：按绑定 ID 分开，互不干扰。"""
    return Path(state_root) / "workspaces" / workspace_id


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ConversationStore:
    """一个作品的会话登记与事件日志；事件按 ``seq`` 追加，界面据此补齐。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.registry_file = self.root / "conversations.json"
        self._lock = threading.Lock()
        self._seqs: dict[str, int] = {}

    # -- 会话 ---------------------------------------------------------------

    def conversations(self) -> tuple[Conversation, ...]:
        with self._lock:
            return tuple(self._load())

    def create(self, title: str = "", *, conversation_id: str | None = None) -> Conversation:
        conversation = Conversation(
            id=conversation_id or _new_id("c"),
            title=title.strip() or "新的对话",
            created_at=_now(),
            updated_at=_now(),
        )
        with self._lock:
            items = self._load()
            if any(item.id == conversation.id for item in items):
                raise WorkspaceError(
                    code="CONVERSATION_EXISTS_CONFLICT",
                    where="conversations.create",
                    why=f"会话 ID 已存在：{conversation.id}",
                    fix="换一个会话 ID，或直接打开已有会话",
                    details={"id": conversation.id},
                )
            self._save([conversation, *items])
        return conversation

    def get(self, conversation_id: str) -> Conversation:
        for item in self.conversations():
            if item.id == conversation_id:
                return item
        raise _unknown_conversation(conversation_id)

    def rename(self, conversation_id: str, title: str) -> Conversation:
        """按首条消息补一个标题；已有自定义标题不覆盖。"""
        with self._lock:
            items = self._load()
            updated: Conversation | None = None
            for item in items:
                if item.id == conversation_id:
                    updated = Conversation(
                        id=item.id,
                        title=title.strip() or item.title,
                        created_at=item.created_at,
                        updated_at=_now(),
                    )
                    break
            if updated is None:
                raise _unknown_conversation(conversation_id)
            self._save([updated if item.id == conversation_id else item for item in items])
        return updated

    # -- 事件 ---------------------------------------------------------------

    def append(self, conversation_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """追加一条事件并返回带 ``seq`` 与时间的完整记录。"""
        path = self.events_file(conversation_id)
        with self._lock:
            seq = self._seqs.get(conversation_id)
            if seq is None:
                seq = _last_seq(path)
            seq += 1
            record = {"seq": seq, "at": _now(), **event}
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError as exc:
                raise WorkspaceError(
                    code="STATE_WRITE_FAILED",
                    where="conversations.append",
                    why=f"无法写入会话事件：{exc.strerror or exc}",
                    fix="检查应用状态目录是否可写（HASTINGS_STATE_DIR 或 ~/.hastings）",
                ) from exc
            self._seqs[conversation_id] = seq
            self._touch(conversation_id)
            return record

    def events(self, conversation_id: str, *, after: int = 0) -> tuple[dict[str, Any], ...]:
        """读取 ``seq > after`` 的事件；文件损坏时跳过无法解析的行。"""
        path = self.events_file(conversation_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return ()
        records: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if (
                isinstance(record, dict)
                and isinstance(record.get("seq"), int)
                and record["seq"] > after
            ):
                records.append(record)
        return tuple(records)

    def last_event(self, conversation_id: str) -> dict[str, Any] | None:
        events = self.events(conversation_id)
        return events[-1] if events else None

    def events_file(self, conversation_id: str) -> Path:
        return self.root / f"{conversation_id}.jsonl"

    # -- 内部 ---------------------------------------------------------------

    def _load(self) -> list[Conversation]:
        try:
            raw = json.loads(self.registry_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return []
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        conversations: list[Conversation] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            conversation_id = item.get("id")
            title = item.get("title")
            created_at = item.get("created_at")
            updated_at = item.get("updated_at")
            if not isinstance(conversation_id, str) or not conversation_id:
                continue
            if not isinstance(title, str) or not title:
                continue
            if not isinstance(created_at, str) or not created_at:
                continue
            if not isinstance(updated_at, str) or not updated_at:
                continue
            conversations.append(
                Conversation(
                    id=conversation_id,
                    title=title,
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
        return conversations

    def _save(self, items: list[Conversation]) -> None:
        _write_json_atomic(
            self.registry_file,
            {"version": 1, "items": [item.to_dict() for item in items]},
            where="conversations.registry",
            why_prefix="无法保存会话登记",
        )

    def _touch(self, conversation_id: str) -> None:
        items = self._load()
        if not any(item.id == conversation_id for item in items):
            return
        self._save(
            [
                Conversation(
                    id=item.id,
                    title=item.title,
                    created_at=item.created_at,
                    updated_at=_now() if item.id == conversation_id else item.updated_at,
                )
                for item in items
            ]
        )


def binding_id(path: Path) -> str:
    """路径散列作为绑定 ID：规范化大小写，同一目录稳定得到同一 ID。"""
    key = os.path.normcase(str(path))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _write_json_atomic(path: Path, payload: object, *, where: str, why_prefix: str) -> None:
    handle: int | None = None
    temp_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.stem}-", suffix=".json"
        )
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            handle = None
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, path)
        temp_name = None
    except OSError as exc:
        raise WorkspaceError(
            code="STATE_WRITE_FAILED",
            where=where,
            why=f"{why_prefix}：{exc.strerror or exc}",
            fix="检查应用状态目录是否可写（HASTINGS_STATE_DIR 或 ~/.hastings）",
        ) from exc
    finally:
        if handle is not None:
            os.close(handle)
        if temp_name is not None:
            with suppress(OSError):
                os.unlink(temp_name)


def _last_seq(path: Path) -> int:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return 0
    for line in reversed(raw.splitlines()):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            seq = record.get("seq")
            if isinstance(seq, int):
                return seq
    return 0


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{os.urandom(6).hex()}"


def _unknown_conversation(conversation_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="CONVERSATION_NOT_FOUND",
        where="conversations.get",
        why=f"没有这个会话：{conversation_id}",
        fix="刷新会话列表后重新选择",
        details={"id": conversation_id},
    )


def _unknown_workspace(workspace_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="WORKSPACE_NOT_FOUND",
        where="workspace.resolve",
        why=f"没有这个目录绑定：{workspace_id}",
        fix="在顶栏重新打开目录；绑定只记录最近打开过的本地路径",
        details={"id": workspace_id},
    )
