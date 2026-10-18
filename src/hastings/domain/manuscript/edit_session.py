"""手工修改副本（V03-2.4 / 计划 §4.1、M04）。

副本是**独立持久工作数据**，不是正文版本：

- ``EditSession``：一个作品同时最多一个未结束副本（项目级唯一约束），
  固定正式基础 ``base_commit_id``，结束方式只有「完成」或「放弃」。
- ``DraftChapter``：按访问章节惰性建立，记录该章开始修改时的正式基础
  （``base_chapter_base``）、当前文本、保存序号与最后一次请求标识。

保存序号只增不减；同一 ``request_id`` 的重试返回原序号（不重复自增），
不同请求携带旧序号则显式冲突——迟到响应不能覆盖新文本（M04 第 2 条）。

两者都以 JSON 形式存仓库辅助表（V03-0.5 端口）：副本与正文共用存储，
但**不移动正式分支**；正式上下文默认不读副本。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "DraftChapter",
    "EditSession",
    "SESSION_ABANDONED",
    "SESSION_CLOSED",
    "SESSION_OPEN",
]

SESSION_OPEN = "open"
SESSION_CLOSED = "closed"
SESSION_ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class EditSession:
    """未结束或已归档的修改副本。

    ``chapters`` 是**本轮章节清单**（V03-5.1）：作者声明或首次保存时进入，
    顺序即声明顺序。它有两个用途：界面显示「本轮改了哪几章」；完成前核对
    清单内每章都已落草稿（flush）——声明了却没保存的章不允许带着未保存
    状态完成。清单不等于草稿集合：有机流式修改不事先声明，首次保存即入列。
    """

    session_id: str
    project_id: str
    base_commit_id: str | None
    status: str
    started_at: str
    ended_at: str | None = None
    closed_operation_id: str | None = None
    chapters: tuple[str, ...] = ()

    @property
    def is_open(self) -> bool:
        return self.status == SESSION_OPEN

    def to_wire(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "base_commit_id": self.base_commit_id,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "closed_operation_id": self.closed_operation_id,
            "chapters": list(self.chapters),
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> EditSession:
        return cls(
            session_id=str(data["session_id"]),
            project_id=str(data["project_id"]),
            base_commit_id=(str(data["base_commit_id"]) if data.get("base_commit_id") else None),
            status=str(data.get("status", SESSION_OPEN)),
            started_at=str(data["started_at"]),
            ended_at=str(data["ended_at"]) if data.get("ended_at") else None,
            closed_operation_id=(
                str(data["closed_operation_id"]) if data.get("closed_operation_id") else None
            ),
            # 旧会话没有清单字段：按空列表读取，有机流式修改不受影响。
            chapters=tuple(str(c) for c in data.get("chapters") or ()),
        )


@dataclass(frozen=True, slots=True)
class DraftChapter:
    """副本章节：实际文本 + 保存序号 + 开始修改时的正式基础。"""

    session_id: str
    chapter_id: str
    base_chapter_base: str
    text: str
    content_hash: str
    save_seq: int
    updated_at: str
    request_id: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "chapter_id": self.chapter_id,
            "base_chapter_base": self.base_chapter_base,
            "text": self.text,
            "content_hash": self.content_hash,
            "save_seq": self.save_seq,
            "updated_at": self.updated_at,
            "request_id": self.request_id,
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> DraftChapter:
        return cls(
            session_id=str(data["session_id"]),
            chapter_id=str(data["chapter_id"]),
            base_chapter_base=str(data.get("base_chapter_base", "")),
            text=str(data.get("text", "")),
            content_hash=str(data.get("content_hash", "")),
            save_seq=int(data.get("save_seq", 0)),
            updated_at=str(data.get("updated_at", "")),
            request_id=str(data.get("request_id", "")),
        )
