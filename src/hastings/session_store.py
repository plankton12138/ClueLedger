"""多会话存储与索引（P10 T14，D13）：平铺会话 + ``sessions.json`` 索引。

布局（每工作区）：

- ``stories/<slug>/sessions/<session_id>.jsonl`` —— 每会话一条消息流
  （仍是 tau 现成 ``JsonlSessionStorage`` 的线性 append，零内核改动）；
- ``stories/<slug>/sessions.json`` —— 索引：``active_id`` + 会话元数据列表。

迁移（泄压阀，不强制用户迁移）：``sessions.json`` 缺失即视为旧单会话格式——
首次访问把现存 ``stories/<slug>/session.jsonl`` 归位为 ``sessions/<id>.jsonl``
并建索引；无旧文件则新建一个空首会话。

本模块是**纯函数 + dataclass** 的存储层，不 import fastapi / harness：REST、CLI、
server 工厂都经它解析「当前活跃会话文件」。索引损坏时改名备份并从磁盘重建，
不静默丢会话（四透镜④：可观测性是契约的一部分）。
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from hastings.bible.store import StoryWorkspace

__all__ = [
    "SessionIndex",
    "SessionMeta",
    "SessionNotFoundError",
    "activate_session",
    "active_session_id",
    "active_session_path",
    "create_session",
    "delete_session",
    "legacy_session_file",
    "list_sessions",
    "load_index",
    "rename_session",
    "session_index_file",
    "session_path",
    "sessions_dir",
    "touch_session",
]

# 会话 id 形态：session-<YYYYMMDD>-<HHMMSS>-<4位hex>。带 hex 后缀与压缩归档名
# （session-<stamp>.jsonl）区分，避免磁盘重建时把归档误认成会话。
_ID_RE = re.compile(r"^session-\d{8}-\d{6}-[0-9a-f]{4}$")


class SessionNotFoundError(ValueError):
    """会话 id 不存在（REST 转 404，CLI 提示可用 id）。"""


@dataclass(frozen=True)
class SessionMeta:
    """一条会话的元数据（索引记录）。"""

    id: str
    title: str
    created_at: str
    last_active: str
    message_count: int


@dataclass(frozen=True)
class SessionIndex:
    """工作区会话索引：活跃会话 id + 全部会话元数据（平铺，无分支/树）。"""

    active_id: str
    sessions: tuple[SessionMeta, ...]


def sessions_dir(workspace: StoryWorkspace) -> Path:
    return workspace.story_dir / "sessions"


def session_index_file(workspace: StoryWorkspace) -> Path:
    return workspace.story_dir / "sessions.json"


def legacy_session_file(workspace: StoryWorkspace) -> Path:
    """旧单会话格式的文件路径（迁移源；D13 之前的工作区）。"""
    return workspace.story_dir / "session.jsonl"


def session_path(workspace: StoryWorkspace, session_id: str) -> Path:
    return sessions_dir(workspace) / f"{session_id}.jsonl"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _default_title(ordinal: int) -> str:
    return f"会话 {ordinal + 1}"


def _new_id(existing: set[str]) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for _ in range(1000):
        candidate = f"session-{stamp}-{uuid.uuid4().hex[:4]}"
        if candidate not in existing:
            return candidate
    raise RuntimeError("无法生成唯一会话 id")  # pragma: no cover — 概率不可达


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _write_index(workspace: StoryWorkspace, index: SessionIndex) -> None:
    """原子写索引（写临时文件后 replace）——避免半截 JSON 破坏索引。"""
    path = session_index_file(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "active_id": index.active_id,
        "sessions": [asdict(meta) for meta in index.sessions],
    }
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _parse_index(text: str) -> SessionIndex:
    data = json.loads(text)
    sessions = tuple(
        SessionMeta(
            id=str(item["id"]),
            title=str(item["title"]),
            created_at=str(item["created_at"]),
            last_active=str(item["last_active"]),
            message_count=int(item["message_count"]),
        )
        for item in data["sessions"]
    )
    if not sessions:
        raise ValueError("索引中没有会话")
    active = str(data["active_id"])
    if active not in {meta.id for meta in sessions}:
        active = sessions[0].id
    return SessionIndex(active_id=active, sessions=sessions)


def _scan_session_files(workspace: StoryWorkspace) -> tuple[SessionMeta, ...]:
    """从磁盘上的会话文件重建元数据（索引损坏时的兜底，标题回落默认名）。"""
    directory = sessions_dir(workspace)
    if not directory.exists():
        return ()
    metas: list[SessionMeta] = []
    for path in sorted(directory.glob("*.jsonl")):
        if not _ID_RE.fullmatch(path.stem):
            continue
        try:
            stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
        except OSError:  # pragma: no cover — 文件在扫描间隙消失
            continue
        metas.append(SessionMeta(path.stem, path.stem, stamp, stamp, _count_lines(path)))
    return tuple(metas)


def _init_or_migrate(workspace: StoryWorkspace) -> SessionIndex:
    """索引缺失/损坏时的初始化：优先归位旧单会话文件，否则从磁盘重建，再次新建。"""
    sessions_dir(workspace).mkdir(parents=True, exist_ok=True)
    existing = _scan_session_files(workspace)
    if existing:
        active = max(existing, key=lambda meta: meta.last_active).id
        index = SessionIndex(active_id=active, sessions=existing)
    else:
        legacy = legacy_session_file(workspace)
        if legacy.exists():
            session_id = _new_id(set())
            target = session_path(workspace, session_id)
            legacy.rename(target)
            try:
                stamp = datetime.fromtimestamp(target.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
            except OSError:  # pragma: no cover
                stamp = _now()
            meta = SessionMeta(session_id, "会话 1", stamp, stamp, _count_lines(target))
        else:
            session_id = _new_id(set())
            now = _now()
            meta = SessionMeta(session_id, _default_title(0), now, now, 0)
        index = SessionIndex(active_id=meta.id, sessions=(meta,))
    _write_index(workspace, index)
    return index


def load_index(workspace: StoryWorkspace) -> SessionIndex:
    """读取索引；缺失则迁移/初始化，损坏则备份后从磁盘重建（泄压阀）。"""
    index_file = session_index_file(workspace)
    if index_file.exists():
        try:
            return _parse_index(index_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            backup = index_file.with_name(f"{index_file.name}.bad-{index_file.stat().st_mtime_ns}")
            try:
                index_file.rename(backup)
            except OSError:  # pragma: no cover — 备份失败则原文件保留待手工处理
                backup = index_file
            print(
                f"[sessions] 会话索引损坏，已备份为 {backup.name} 并从磁盘重建。"
                f"为什么错：{exc}。"
                "怎样算对：无需干预；若会话缺失，检查 stories/<slug>/sessions/ 目录。",
                file=sys.stderr,
            )
    return _init_or_migrate(workspace)


def list_sessions(workspace: StoryWorkspace) -> list[SessionMeta]:
    return list(load_index(workspace).sessions)


def active_session_id(workspace: StoryWorkspace) -> str:
    return load_index(workspace).active_id


def active_session_path(workspace: StoryWorkspace) -> Path:
    """当前活跃会话的文件路径（REST/CLI/工厂/压缩共用同一解析点）。"""
    return session_path(workspace, active_session_id(workspace))


def _find(index: SessionIndex, session_id: str) -> SessionMeta:
    for meta in index.sessions:
        if meta.id == session_id:
            return meta
    raise SessionNotFoundError(
        f"会话不存在：{session_id}。"
        "为什么错：该 id 不在本工作区的会话索引中。"
        "怎样算对：用 GET /api/{slug}/sessions 列出可用会话 id 后重试。"
    )


def create_session(workspace: StoryWorkspace, title: str | None = None) -> SessionMeta:
    """新建空会话并设为活跃（旧会话保留在列表，可回看）。"""
    index = load_index(workspace)
    session_id = _new_id({meta.id for meta in index.sessions})
    now = _now()
    meta = SessionMeta(session_id, title or _default_title(len(index.sessions)), now, now, 0)
    _write_index(
        workspace,
        SessionIndex(active_id=session_id, sessions=index.sessions + (meta,)),
    )
    return meta


def activate_session(workspace: StoryWorkspace, session_id: str) -> SessionMeta:
    """切换活跃会话并刷新其 last_active。"""
    index = load_index(workspace)
    target = _find(index, session_id)
    now = _now()
    updated = tuple(
        replace(meta, last_active=now) if meta.id == session_id else meta for meta in index.sessions
    )
    _write_index(workspace, SessionIndex(active_id=session_id, sessions=updated))
    return replace(target, last_active=now)


def rename_session(workspace: StoryWorkspace, session_id: str, title: str) -> SessionMeta:
    index = load_index(workspace)
    target = _find(index, session_id)
    updated = tuple(
        replace(meta, title=title) if meta.id == session_id else meta for meta in index.sessions
    )
    _write_index(workspace, SessionIndex(active_id=index.active_id, sessions=updated))
    return replace(target, title=title)


def delete_session(workspace: StoryWorkspace, session_id: str) -> str:
    """删除会话（含文件）；返回新的活跃 id。

    删除活跃会话 → 切到最近活跃的剩余会话；无剩余 → 新建一个空会话（不静默留空工作区）。
    """
    index = load_index(workspace)
    _find(index, session_id)
    remaining = tuple(meta for meta in index.sessions if meta.id != session_id)
    try:
        path = session_path(workspace, session_id)
        if path.exists():
            path.unlink()
    except OSError as exc:
        print(
            f"[sessions] 会话文件删除失败：{exc}。"
            "为什么错：磁盘/权限故障。"
            "怎样算对：索引已更新，检查 sessions 目录后手工清理残留文件。",
            file=sys.stderr,
        )
    if not remaining:
        session_id_new = _new_id({session_id})
        now = _now()
        meta = SessionMeta(session_id_new, _default_title(0), now, now, 0)
        remaining = (meta,)
        active = session_id_new
    elif index.active_id == session_id:
        active = max(remaining, key=lambda meta: meta.last_active).id
    else:
        active = index.active_id
    _write_index(workspace, SessionIndex(active_id=active, sessions=remaining))
    return active


def touch_session(workspace: StoryWorkspace, session_id: str) -> None:
    """一条消息定稿后刷新该会话的 last_active 与 message_count（列表新鲜度）。"""
    index = load_index(workspace)
    now = _now()
    updated = tuple(
        replace(meta, message_count=meta.message_count + 1, last_active=now)
        if meta.id == session_id
        else meta
        for meta in index.sessions
    )
    _write_index(workspace, SessionIndex(active_id=index.active_id, sessions=updated))
