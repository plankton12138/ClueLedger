"""应用状态：最近目录与会话事件（0.4 M05 的状态部分）。

0.4 的作品内容只在作品目录里。应用自己需要记住的只有两类定位信息，都放在应用状态
目录，不复制正文，也不成为第二份当前稿：

- **最近目录**：同一目录重复打开只有一条记录，绑定 ID 由规范化路径算出；
- **会话与事件**：每个作品一组会话，每个会话一个 JSONL，按自增 ``seq`` 追加，
  供界面回读与断线补齐。事件是过程记录，不是作品内容；
- **界面状态**：最近正式会话与各会话草稿，单独一个 JSON，不属于聊天记录。

登记或事件文件损坏时按空处理——宁可让作者重新选择目录、重新开始对话，也不让启动失败。
会话日志是「这一轮是否被受理」的唯一依据：索引缺项可以从日志重建，反过来不行。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace

__all__ = [
    "DEFAULT_STATE_ROOT",
    "AcceptedTurn",
    "Conversation",
    "ConversationStore",
    "ConversationUi",
    "ConversationUiStore",
    "EventTail",
    "RecentWorkspaces",
    "WorkspaceBinding",
    "conversation_root",
    "default_state_root",
]

#: 应用状态目录：聊天、最近目录、运行事件都在这里；不含作品正文。
DEFAULT_STATE_ROOT = Path.home() / ".hastings"

_REGISTRY_FILE = "workspaces.json"
_REGISTRY_VERSION = 1
#: 最近目录保留条数；超出后丢弃最旧的登记，不删除磁盘上的作品。
_MAX_ENTRIES = 20

#: 新会话日志的格式版本与首条元信息：旧日志没有它，按旧登记继续读。
LOG_VERSION = 2
META_TYPE = "conversation_meta"
#: 会话列表索引与界面状态文件；两者都可以从日志重建或按需重建。
_INDEX_FILE = "conversations.json"
_UI_FILE = "conversation-ui.json"
#: 会话标题取首条提示的前若干字，够在列表里辨认即可。
_TITLE_LIMIT = 24


def default_state_root() -> Path:
    """状态目录默认值：用户主目录下的 ``.hastings``。

    位置由启动参数 ``--state-dir`` 决定（未给时用这里的默认值）。不读进程环境：
    同一台机器上换个终端启动，作品与对话仍在同一处。
    """
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


@dataclass(frozen=True, slots=True)
class AcceptedTurn:
    """一次已被受理的提示：正式会话从这一刻开始存在。"""

    conversation_id: str
    turn_id: str
    request_key: str
    last_seq: int
    started_at: str
    title: str


@dataclass(frozen=True, slots=True)
class ConversationUi:
    """一个作品的界面状态：上次看的正式会话与各会话草稿。"""

    recent_id: str | None
    drafts: dict[str, str]
    revision: str

    def to_dict(self) -> dict[str, object]:
        return {
            "recent_id": self.recent_id,
            "drafts": dict(self.drafts),
            "revision": self.revision,
        }


class ConversationStore:
    """一个作品的会话登记与事件日志；事件按 ``seq`` 追加，界面据此补齐。

    写入顺序固定为「先发布日志，再更新索引」：日志是受理成立的依据，索引只是加速列表。
    索引写失败不影响已经受理的提示，下次读取时按日志补齐（``_repair``）。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.registry_file = self.root / _INDEX_FILE
        # 重入锁：公开方法之间会互相调用（受理要查重、追加要 touch 索引）。
        self._lock = threading.RLock()
        self._seqs: dict[str, int] = {}

    # -- 会话 ---------------------------------------------------------------

    def conversations(self) -> tuple[Conversation, ...]:
        with self._lock:
            items = self._load()
            repaired = self._repair(items)
            if repaired is not items:
                # 索引缺项时尝试补写；补写失败仍然返回从日志读到的会话。
                with suppress(WorkspaceError):
                    self._save(repaired)
            return tuple(repaired)

    def get(self, conversation_id: str) -> Conversation:
        for item in self.conversations():
            if item.id == conversation_id:
                return item
        raise _unknown_conversation(conversation_id)

    def rename(self, conversation_id: str, title: str) -> Conversation:
        """作者改名；空白标题按原样保留，不覆盖成空字符串。"""
        clean = title.strip()
        if not clean:
            raise WorkspaceError(
                code="CONVERSATION_TITLE_INVALID",
                where="conversations.rename",
                why="会话标题不能为空",
                fix="写一个能辨认这段讨论的标题后再保存",
                details={"id": conversation_id},
            )
        with self._lock:
            items = self._load()
            updated: Conversation | None = None
            for item in items:
                if item.id == conversation_id:
                    updated = Conversation(
                        id=item.id,
                        title=clean,
                        created_at=item.created_at,
                        # 改名算一次活动：列表按最近活动排序，改名要能反映出来。
                        updated_at=_now(),
                    )
                    break
            if updated is None:
                raise _unknown_conversation(conversation_id)
            self._save([updated if item.id == conversation_id else item for item in items])
        return updated

    # -- 受理 ---------------------------------------------------------------

    def find_accepted(self, request_key: str) -> AcceptedTurn | None:
        """按请求键找工作区里已受理的那一轮；同键重试不能生成第二个会话。"""
        if not request_key:
            return None
        for conversation in self.conversations():
            for event in self.events(conversation.id):
                if event.get("type") != "turn_started":
                    continue
                if event.get("request_key") != request_key:
                    continue
                turn_id = event.get("turn_id")
                if not isinstance(turn_id, str):
                    continue
                return AcceptedTurn(
                    conversation_id=conversation.id,
                    turn_id=turn_id,
                    request_key=request_key,
                    last_seq=int(event["seq"]),
                    started_at=str(event.get("at", "")),
                    title=conversation.title,
                )
        return None

    def prompt_of(self, conversation_id: str, turn_id: str) -> str:
        for event in self.events(conversation_id):
            if event.get("type") == "turn_started" and event.get("turn_id") == turn_id:
                text = event.get("text")
                return text if isinstance(text, str) else ""
        return ""

    def create_with_first_turn(
        self,
        *,
        text: str,
        request_key: str,
        turn_id: str,
        facts: Mapping[str, object] | None = None,
        conversation_id: str | None = None,
    ) -> AcceptedTurn:
        """建会话与受理首轮合成一步：只留下「至少有一条已受理提示」的正式会话。"""
        prompt = _require_prompt(text)
        with self._lock:
            previous = self.find_accepted(request_key)
            if previous is not None:
                stored = self.prompt_of(previous.conversation_id, previous.turn_id)
                return _same_request(previous, prompt, stored)
            cid = conversation_id or _new_id("c")
            if self.events_file(cid).exists():
                raise WorkspaceError(
                    code="CONVERSATION_EXISTS_CONFLICT",
                    where="conversations.create",
                    why=f"会话 ID 已存在：{cid}",
                    fix="换一个会话 ID，或直接打开已有会话",
                    details={"id": cid},
                )
            now = _now()
            title = _title_from(prompt)
            meta = {
                "seq": 0,
                "at": now,
                "type": META_TYPE,
                "version": LOG_VERSION,
                "conversation_id": cid,
                "title": title,
                "created_at": now,
            }
            event = {
                "seq": 1,
                "at": now,
                "type": "turn_started",
                "turn_id": turn_id,
                "text": prompt,
                "request_key": request_key,
                **dict(facts or {}),
            }
            self._publish(cid, (meta, event))
            self._seqs[cid] = 1
            # 日志已经发布：这一轮确实被受理了，索引缺项由下次读取的重建补齐。
            with suppress(WorkspaceError):
                self._save([Conversation(cid, title, now, now), *self._load()])
            return AcceptedTurn(cid, turn_id, request_key, 1, now, title)

    def accept_turn(
        self,
        conversation_id: str,
        *,
        text: str,
        request_key: str,
        turn_id: str,
        facts: Mapping[str, object] | None = None,
    ) -> AcceptedTurn:
        """在已有会话里受理一轮；同一请求键重复提交返回原来那一轮。"""
        prompt = _require_prompt(text)
        with self._lock:
            conversation = self.get(conversation_id)
            previous = self.find_accepted(request_key)
            if previous is not None:
                if previous.conversation_id != conversation_id:
                    raise _request_key_conflict(request_key, previous.conversation_id)
                return _same_request(
                    previous, prompt, self.prompt_of(previous.conversation_id, previous.turn_id)
                )
            record = self.append(
                conversation_id,
                {
                    "type": "turn_started",
                    "turn_id": turn_id,
                    "text": prompt,
                    "request_key": request_key,
                    **dict(facts or {}),
                },
            )
            return AcceptedTurn(
                conversation_id=conversation_id,
                turn_id=turn_id,
                request_key=request_key,
                last_seq=int(record["seq"]),
                started_at=str(record["at"]),
                title=conversation.title,
            )

    # -- 事件 ---------------------------------------------------------------

    def append(self, conversation_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """追加一条事件并返回带 ``seq`` 与时间的完整记录。"""
        return self.append_batch(conversation_id, [event], update_activity=True)[0]

    def append_batch(
        self,
        conversation_id: str,
        events: list[dict[str, Any]],
        *,
        update_activity: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        """一次写入一批事件：分配连续 ``seq``，整批在同一把写锁内落地。

        流式增量一批可能只有几十个字符，逐条 ``open/write/close`` 既慢又会让读者看到
        顺序错乱的片段。这里一次写完再返回，调用方确认成功后才让 SSE 读到。

        ``update_activity`` 为假时不改写会话索引：增量不是「一次活动」，
        每批都重写 ``conversations.json`` 会把长回复变成大量无意义的小文件写入。
        """
        if not events:
            return ()
        path = self.events_file(conversation_id)
        with self._lock:
            seq = self._seqs.get(conversation_id)
            if seq is None:
                seq = _last_seq(path)
            now = _now()
            records: list[dict[str, Any]] = []
            for event in events:
                seq += 1
                records.append({"seq": seq, "at": now, **event})
            payload = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(payload)
            except OSError as exc:
                raise WorkspaceError(
                    code="STATE_WRITE_FAILED",
                    where="conversations.append",
                    why=f"无法写入会话事件：{exc.strerror or exc}",
                    fix="检查应用状态目录是否可写（--state-dir 指定的目录或 ~/.hastings）",
                ) from exc
            self._seqs[conversation_id] = seq
            if update_activity:
                self._touch(conversation_id)
            return tuple(records)

    def events(self, conversation_id: str, *, after: int = 0) -> tuple[dict[str, Any], ...]:
        """读取 ``seq > after`` 的事件；文件损坏时跳过无法解析的行。

        首条元信息 ``seq`` 为 0，因此不会出现在 ``after >= 0`` 的读取结果里：
        界面只看到轮次事件，元信息只服务于会话重建。
        """
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

    def unfinished_turns(self, conversation_id: str) -> list[str]:
        """有过 ``turn_started`` 却没有终态的轮次，按出现顺序。

        只看「最后一条是否 turn_started」会漏掉崩在中途的轮次：工具开始、工具结束、
        助理回复之后都可能直接断电。这里按轮次配对，重复调用不会新增第二次中断。
        """
        started: list[str] = []
        finished: set[str] = set()
        for event in self.events(conversation_id):
            turn_id = event.get("turn_id")
            if not isinstance(turn_id, str):
                continue
            kind = event.get("type")
            if kind == "turn_started":
                started.append(turn_id)
            elif kind == "run_finished":
                finished.add(turn_id)
        return [turn_id for turn_id in started if turn_id not in finished]

    def events_file(self, conversation_id: str) -> Path:
        return self.root / f"{conversation_id}.jsonl"

    def tail(self, conversation_id: str, *, after: int = 0) -> EventTail:
        """按游标追尾读取：先定位一次，之后每次只读新增的完整行。

        给事件流用。``events()`` 每次都从头读整份日志，长回复下增量一多就是平方级
        扫描；这里把偏移留在读者自己身上，重连的每个客户端各持一份，互不抢先。
        """
        return EventTail(self.events_file(conversation_id), after=after)

    def revision(self) -> str:
        """会话集合的版本：标题、活动时间与条数变化都会换一个值。"""
        payload = [
            [item.id, item.title, item.updated_at] for item in self.conversations()
        ]
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return digest[:12]

    # -- 内部 ---------------------------------------------------------------

    def _publish(self, conversation_id: str, records: tuple[dict[str, Any], ...]) -> None:
        """原子发布一份初始日志：整份写入临时文件后替换，不留半条会话。"""
        path = self.events_file(conversation_id)
        handle: int | None = None
        temp_name: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=f".{conversation_id}-", suffix=".jsonl"
            )
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                handle = None
                for record in records:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            os.replace(temp_name, path)
            temp_name = None
        except OSError as exc:
            raise WorkspaceError(
                code="STATE_WRITE_FAILED",
                where="conversations.create",
                why=f"无法写入会话日志：{exc.strerror or exc}",
                fix="检查应用状态目录是否可写（--state-dir 指定的目录或 ~/.hastings）",
            ) from exc
        finally:
            if handle is not None:
                os.close(handle)
            if temp_name is not None:
                with suppress(OSError):
                    os.unlink(temp_name)

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

    def _repair(self, items: list[Conversation]) -> list[Conversation]:
        """把日志里存在、索引里缺失的会话补回来；旧记录一条都不改。"""
        known = {item.id for item in items}
        orphans: list[Conversation] = []
        try:
            paths = sorted(self.root.glob("*.jsonl"))
        except OSError:
            return items
        for path in paths:
            conversation_id = path.stem
            if conversation_id in known:
                continue
            rebuilt = self._rebuild(path)
            if rebuilt is not None:
                orphans.append(rebuilt)
        return [*items, *orphans] if orphans else items

    def _rebuild(self, path: Path) -> Conversation | None:
        """从日志恢复一条会话：优先用首条元信息，旧日志退回首条提示。"""
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        created_at = ""
        title = ""
        first_at = ""
        last_at = ""
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            at = record.get("at")
            if isinstance(at, str) and at:
                last_at = at
                if not first_at:
                    first_at = at
            if record.get("type") == META_TYPE:
                meta_title = record.get("title")
                if isinstance(meta_title, str) and meta_title:
                    title = meta_title
                meta_created = record.get("created_at")
                if isinstance(meta_created, str) and meta_created:
                    created_at = meta_created
                continue
            if record.get("type") == "turn_started" and not title:
                text = record.get("text")
                if isinstance(text, str) and text.strip():
                    title = _title_from(text)
        if not title:
            return None
        return Conversation(
            id=path.stem,
            title=title,
            created_at=created_at or first_at or _now(),
            updated_at=last_at or created_at or _now(),
        )

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


class EventTail:
    """会话日志的增量读者：记住字节偏移，只返回上次之后写入的完整行。

    追加写入是「一批一次 write」，读者可能在写一半时检查文件；因此只消费到最后一个
    换行为止，未写完的半行留到下次。文件被替换或截断时偏移会超过文件长度，
    这时从头重新定位，而不是继续读一个不存在的区间。

    偏移属于这一个读者：几个窗口各订阅同一会话时，谁的游标也不会把别人的位置推走。
    """

    def __init__(self, path: Path, *, after: int = 0) -> None:
        self.path = Path(path)
        self._last_seq = max(0, after)
        self._offset = 0
        self._positioned = False

    def read(self) -> tuple[dict[str, Any], ...]:
        """读取新增的完整事件；没有新内容时返回空。"""
        if not self._positioned:
            self._position()
        try:
            size = self.path.stat().st_size
        except OSError:
            return ()
        if size < self._offset:
            # 截断或替换：旧偏移不再对应原来的内容，重新从头定位。
            self._offset = 0
        if size == self._offset:
            return ()
        try:
            with self.path.open("rb") as stream:
                stream.seek(self._offset)
                data = stream.read()
        except OSError:
            return ()
        cut = data.rfind(b"\n")
        if cut < 0:
            return ()
        chunk = data[: cut + 1]
        self._offset += len(chunk)
        return self._parse(chunk)

    def _parse(self, chunk: bytes) -> tuple[dict[str, Any], ...]:
        records: list[dict[str, Any]] = []
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            seq = record.get("seq")
            if not isinstance(seq, int) or seq <= self._last_seq:
                continue
            self._last_seq = seq
            records.append(record)
        return tuple(records)

    def _position(self) -> None:
        """首次读取时定位到游标之后：只有这一次要扫全文件。"""
        self._positioned = True
        try:
            data = self.path.read_bytes()
        except OSError:
            return
        offset = 0
        start = 0
        while True:
            end = data.find(b"\n", start)
            if end < 0:
                break
            try:
                record = json.loads(data[start:end].decode("utf-8", errors="replace"))
            except ValueError:
                record = None
            if (
                isinstance(record, dict)
                and isinstance(record.get("seq"), int)
                and record["seq"] <= self._last_seq
            ):
                offset = end + 1
            start = end + 1
        self._offset = offset


class ConversationUiStore:
    """界面状态：上次查看的正式会话 + 各会话草稿。

    草稿不是已发送的提示，不进入模型上下文；这里只负责「切回来还在」。写入按版本核对，
    避免一个过期的请求把别的会话草稿整份覆盖掉。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.file = self.root / _UI_FILE
        self._lock = threading.RLock()

    def read(self) -> ConversationUi:
        with self._lock:
            return self._read()

    def update(
        self,
        *,
        expected_revision: str = "",
        set_recent: bool = False,
        recent_id: str | None = None,
        set_draft: bool = False,
        draft_target: str | None = None,
        draft_text: str | None = None,
    ) -> ConversationUi:
        with self._lock:
            current = self._read()
            if expected_revision and expected_revision != current.revision:
                raise WorkspaceError(
                    code="CONVERSATION_UI_CONFLICT",
                    where="conversation-ui.update",
                    why="界面状态已被其他窗口改动",
                    fix="刷新对话后按当前内容重新编辑",
                    details={"revision": current.revision},
                )
            recent = current.recent_id
            if set_recent:
                recent = recent_id if recent_id else None
            drafts = dict(current.drafts)
            if set_draft and draft_target:
                text = (draft_text or "").strip()
                if text:
                    drafts[draft_target] = text
                else:
                    drafts.pop(draft_target, None)
            self._save(recent, drafts)
            return self._read()

    def _read(self) -> ConversationUi:
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return ConversationUi(recent_id=None, drafts={}, revision="0")
        data = raw if isinstance(raw, dict) else {}
        recent = data.get("recent_id")
        drafts_raw = data.get("drafts")
        drafts: dict[str, str] = {}
        if isinstance(drafts_raw, dict):
            for key, value in drafts_raw.items():
                if isinstance(key, str) and isinstance(value, str) and value.strip():
                    drafts[key] = value
        return ConversationUi(
            recent_id=recent if isinstance(recent, str) and recent else None,
            drafts=drafts,
            revision=_file_revision(self.file),
        )

    def _save(self, recent_id: str | None, drafts: dict[str, str]) -> None:
        _write_json_atomic(
            self.file,
            {"version": 1, "recent_id": recent_id, "drafts": drafts},
            where="conversation-ui.save",
            why_prefix="无法保存界面状态",
        )


def _require_prompt(text: str) -> str:
    prompt = text.strip()
    if not prompt:
        raise WorkspaceError(
            code="PROMPT_EMPTY",
            where="conversations.create",
            why="提示词是空的",
            fix="写下要让模型做什么，再发送",
        )
    return prompt


def _same_request(previous: AcceptedTurn, prompt: str, stored: str) -> AcceptedTurn:
    """同键同内容返回原来那一轮；同键不同内容拒绝，不能悄悄换掉已受理的要求。"""
    if stored == prompt:
        return previous
    raise _request_key_conflict(previous.request_key, previous.conversation_id)


def _request_key_conflict(request_key: str, conversation_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="REQUEST_KEY_CONFLICT",
        where="conversations.accept",
        why=f"这个请求标识已经用在另一条要求上：{request_key}",
        fix="要发新的要求就换一个请求标识；要重试上一次，请保持文字不变",
        details={"request_key": request_key, "conversation": conversation_id},
    )


def _title_from(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _TITLE_LIMIT:
        return collapsed
    return collapsed[:_TITLE_LIMIT] + "…"


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
            fix="检查应用状态目录是否可写（--state-dir 指定的目录或 ~/.hastings）",
        ) from exc
    finally:
        if handle is not None:
            os.close(handle)
        if temp_name is not None:
            with suppress(OSError):
                os.unlink(temp_name)


def _file_revision(path: Path) -> str:
    """文件内容散列作为版本：没写过时是 ``0``，写完就换值。"""
    try:
        data = path.read_bytes()
    except (FileNotFoundError, OSError):
        return "0"
    return hashlib.sha256(data).hexdigest()[:12]


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
