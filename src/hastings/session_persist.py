"""会话持久化桥（P4 T1，计划 D1/D2）——tau 现成 JSONL 存储的订阅式接线。

设计要点：
- **持久化即事件流的又一个消费者**：与 CLI 渲染器、前端 WS 桥同源 fan-out，
  挂在 ``message_end``（消息定稿）上——流式中间态不落盘，abort 后 harness
  的 repair 消息同样是 message_end，天然被记录；
- **零内核改动**：JsonlSessionStorage / MessageEntry 是 tau_agent 公开组件，
  线性 append 不触碰 session/tree 分支语义（计划 §0 核实）；
- **坏文件泄压阀**（D2）：SessionJsonlError → 改名备份 ``session.jsonl.bad-<ts>``，
  返回中文 warning，以空会话继续——不阻塞、不静默。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hastings.bible.store import StoryWorkspace
from tau_agent.events import AgentEvent, MessageEndEvent
from tau_agent.harness import AgentHarness
from tau_agent.messages import AgentMessage
from tau_agent.session import JsonlSessionStorage, MessageEntry, SessionStorage
from tau_agent.session.jsonl import SessionJsonlError

__all__ = [
    "SessionLoad",
    "attach_session_storage",
    "load_session_messages",
    "quarantine_session_file",
    "restore_and_attach",
    "session_file",
]


@dataclass(frozen=True)
class SessionLoad:
    """启动加载结果：恢复出的消息 + 可选的中文告警（坏文件备份说明）。"""

    messages: tuple[AgentMessage, ...]
    warning: str | None = None


def quarantine_session_file(session_path: Path) -> Path | None:
    """把损坏的会话文件改名备份，返回备份路径；文件不存在则不动（noop）。"""
    if not session_path.exists():
        return None
    backup = session_path.with_name(f"{session_path.name}.bad-{session_path.stat().st_mtime_ns}")
    session_path.rename(backup)
    return backup


async def load_session_messages(session_path: Path) -> SessionLoad:
    """从 JSONL 会话文件恢复消息历史；坏文件备份后以空会话继续（D2）。"""
    storage = JsonlSessionStorage(session_path)
    try:
        entries = await storage.read_all()
    except SessionJsonlError as exc:
        backup = quarantine_session_file(session_path)
        backup_name = backup.name if backup is not None else session_path.name
        return SessionLoad(
            messages=(),
            warning=(
                f"会话文件损坏，已备份为 {backup_name}（原数据未删除）。"
                f"本次以空会话启动。损坏原因：{exc}"
            ),
        )
    messages = tuple(entry.message for entry in entries if isinstance(entry, MessageEntry))
    return SessionLoad(messages=messages)


def attach_session_storage(harness: AgentHarness, storage: SessionStorage) -> Callable[[], None]:
    """订阅 harness 事件流，把定稿消息 append 进 storage；返回退订函数。"""

    async def _persist(event: AgentEvent) -> None:
        if isinstance(event, MessageEndEvent):
            await storage.append(MessageEntry(message=event.message))

    return harness.subscribe(_persist)


def session_file(workspace: StoryWorkspace) -> Path:
    """故事会话文件：``stories/<slug>/session.jsonl``——与 Bible、章节同目录（D1）。"""
    return workspace.story_dir / "session.jsonl"


async def restore_and_attach(
    workspace: StoryWorkspace,
    harness: AgentHarness,
    *,
    on_warning: Callable[[str], None] | None = None,
) -> None:
    """一次性接线（CLI 与 server 共用，P4 T2）：恢复历史消息并挂上持久化。

    坏文件走 D2 泄压阀：备份 + on_warning 中文告警 + 空会话继续——attach 不受影响，
    本轮写作照常落盘。
    """
    result = await load_session_messages(session_file(workspace))
    if result.warning is not None and on_warning is not None:
        on_warning(result.warning)
    if result.messages:
        harness.replace_messages(result.messages)
    attach_session_storage(harness, JsonlSessionStorage(session_file(workspace)))
