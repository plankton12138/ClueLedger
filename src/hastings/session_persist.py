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

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from hastings.bible.store import StoryWorkspace
from hastings.session_store import active_session_id, active_session_path, touch_session
from tau_agent.events import AgentEvent, MessageEndEvent
from tau_agent.harness import AgentHarness
from tau_agent.messages import AgentMessage
from tau_agent.session import JsonlSessionStorage, MessageEntry, SessionStorage
from tau_agent.session.jsonl import SessionJsonlError

__all__ = [
    "SessionLoad",
    "archive_session_file",
    "attach_session_index",
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


def archive_session_file(session_path: Path) -> Path | None:
    """会话文件复制为时间戳副本后删除原文件（P9 D4 reset 专用）。

    语义：归档保留（磁盘可找回），原会话随后经 registry.rebuild 重装配为空会话。
    先复制后删除——复制失败原文件不动；同名归档（同秒内多次 reset）追加序号。
    文件不存在（会话从未落盘）→ noop，调用方照常重装配。
    """
    if not session_path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = session_path.with_name(f"session-{stamp}.jsonl")
    counter = 1
    while target.exists():
        target = session_path.with_name(f"session-{stamp}-{counter}.jsonl")
        counter += 1
    shutil.copy2(session_path, target)
    session_path.unlink()
    return target


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
    """订阅 harness 事件流，把定稿消息 append 进 storage；返回退订函数。

    泄压阀（R3-⑥）：磁盘/锁故障（OSError）只告警不传播——持久化是事件流的
    消费者，不能反过来杀死写作流；故障显式上屏（stderr），会话继续。
    """

    async def _persist(event: AgentEvent) -> None:
        if isinstance(event, MessageEndEvent):
            try:
                await storage.append(MessageEntry(message=event.message))
            except OSError as exc:
                print(
                    "[session] 会话落盘失败，写作继续但历史不完整。"
                    f"为什么错：{exc}。"
                    "怎样算对：检查磁盘空间/文件占用后重启服务恢复。",
                    file=sys.stderr,
                )

    return harness.subscribe(_persist)


def session_file(workspace: StoryWorkspace) -> Path:
    """当前**活跃会话**的文件路径（P10 D13 后语义变更）。

    指向 ``stories/<slug>/sessions/<active_id>.jsonl``；解析会触发旧单会话格式
    的迁移与索引初始化（见 :mod:`hastings.session_store`）。REST/CLI/压缩导出
    都经此解析，切换活跃会话即换文件。
    """
    return active_session_path(workspace)


def attach_session_index(
    harness: AgentHarness,
    workspace: StoryWorkspace,
    session_id: str,
) -> Callable[[], None]:
    """订阅 message_end，把活跃会话的 last_active / message_count 记进索引。

    索引刷新是观测性的账：IO 故障只告警不传播（持久化只是事件流的消费者，
    不能反杀写作流）。返回退订函数。
    """

    async def _touch(event: AgentEvent) -> None:
        if isinstance(event, MessageEndEvent):
            try:
                touch_session(workspace, session_id)
            except OSError as exc:
                print(
                    "[sessions] 会话索引刷新失败，写作继续但列表信息可能滞后。"
                    f"为什么错：{exc}。"
                    "怎样算对：检查磁盘空间/文件占用；会话本身已正常落盘。",
                    file=sys.stderr,
                )

    return harness.subscribe(_touch)


async def restore_and_attach(
    workspace: StoryWorkspace,
    harness: AgentHarness,
    *,
    on_warning: Callable[[str], None] | None = None,
) -> Callable[[], None]:
    """一次性接线（CLI 与 server 共用，P4 T2）：恢复活跃会话消息并挂上持久化。

    恢复的是**当前活跃会话**（session_store 解析，含旧格式迁移）；切换会话后
    重新调用即换文件换历史。坏文件走 D2 泄压阀：备份 + on_warning 中文告警 +
    空会话继续——attach 不受影响，本轮写作照常落盘。

    返回退订函数（存储订阅 + 索引订阅一并解除）：CLI 切换会话时先退订旧的再接线新的。
    """
    path = session_file(workspace)
    session_id = active_session_id(workspace)
    result = await load_session_messages(path)
    if result.warning is not None and on_warning is not None:
        on_warning(result.warning)
    # 无条件替换（含空会话）：切换会话时旧历史必须清掉，不能残留上一会话的消息
    harness.replace_messages(result.messages)
    unsubscribe_storage = attach_session_storage(harness, JsonlSessionStorage(path))
    unsubscribe_touch = attach_session_index(harness, workspace, session_id)

    def unsubscribe() -> None:
        unsubscribe_storage()
        unsubscribe_touch()

    return unsubscribe
