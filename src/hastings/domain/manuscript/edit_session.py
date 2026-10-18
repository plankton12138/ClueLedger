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

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from hastings.domain.manuscript.diff import split_paragraphs
from hastings.domain.repository.serialization import content_hash

__all__ = [
    "DraftChapter",
    "EditSession",
    "EditSnapshot",
    "SESSION_ABANDONED",
    "SESSION_CLOSED",
    "SESSION_OPEN",
    "SOURCE_AGENT",
    "SOURCE_HUMAN",
    "SOURCE_UNKNOWN",
    "merge_saved_sources",
    "normalize_sources",
    "source_runs",
]

SESSION_OPEN = "open"
SESSION_CLOSED = "closed"
SESSION_ABANDONED = "abandoned"

#: 段落来源（V03-5.3）：人工编辑 / 模型生成 / 无法判断（合并后保守标记）。
SOURCE_HUMAN = "human"
SOURCE_AGENT = "agent"
SOURCE_UNKNOWN = "unknown"
SOURCE_VALUES = frozenset({SOURCE_HUMAN, SOURCE_AGENT, SOURCE_UNKNOWN})


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
    """副本章节：实际文本 + 保存序号 + 开始修改时的正式基础。

    ``base_chapter_base`` 是章基础（内容指纹），``base_commit_id`` 是它对应的
    提交——三方合并要按提交取**作者读到的那一版原文**做 base，只有指纹不够
    （V03-5.2）。旧草稿没有该字段，服务层回落到会话打开时的提交。

    ``paragraph_sources`` 与 ``split_paragraphs(text)`` 逐段对齐（V03-5.3）：
    ``human`` 是作者输入，``agent`` 来自模型候选，``unknown`` 是无法判断。
    旧草稿没有来源字段：非空正文按人工编辑读取（这些文字确实是人写的）；
    长度对不上时按 ``unknown`` 保守处理，绝不把生成文字默认算成人工。
    """

    session_id: str
    chapter_id: str
    base_chapter_base: str
    text: str
    content_hash: str
    save_seq: int
    updated_at: str
    request_id: str
    base_commit_id: str = ""
    paragraph_sources: tuple[str, ...] = ()

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
            "base_commit_id": self.base_commit_id,
            "paragraph_sources": list(normalize_sources(self.text, self.paragraph_sources)),
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> DraftChapter:
        text = str(data.get("text", ""))
        return cls(
            session_id=str(data["session_id"]),
            chapter_id=str(data["chapter_id"]),
            base_chapter_base=str(data.get("base_chapter_base", "")),
            text=text,
            content_hash=str(data.get("content_hash", "")),
            save_seq=int(data.get("save_seq", 0)),
            updated_at=str(data.get("updated_at", "")),
            request_id=str(data.get("request_id", "")),
            base_commit_id=str(data.get("base_commit_id", "")),
            paragraph_sources=normalize_sources(text, data.get("paragraph_sources")),
        )


@dataclass(frozen=True, slots=True)
class EditSnapshot:
    """修改稿的不可变引用（V03-5.3）：冻结某个 ``save_seq`` 的选区与文本。

    快照是任务输入，不是副本的另一种可写形态：创建后副本继续前进，快照
    里的文本、哈希、段落来源与基础版本都不再变化。``selection`` 为空表示
    整章；``context_version`` 是任务快照身份要求的锚点（V03-0.2），不随
    save_seq 变化。
    """

    snapshot_id: str
    project_id: str
    session_id: str
    chapter_id: str
    save_seq: int
    text: str
    text_hash: str
    paragraph_sources: tuple[str, ...]
    base_chapter_base: str
    base_commit_id: str
    selection_start: int | None = None
    selection_end: int | None = None
    context_version: int = 1
    created_at: str = ""

    def to_wire(self) -> dict[str, Any]:
        selection = None
        if self.selection_start is not None and self.selection_end is not None:
            selection = {
                "start_paragraph": self.selection_start,
                "end_paragraph": self.selection_end,
            }
        return {
            "snapshot_id": self.snapshot_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "chapter_id": self.chapter_id,
            "save_seq": self.save_seq,
            "text": self.text,
            "text_hash": self.text_hash,
            "selection": selection,
            "paragraph_sources": list(self.paragraph_sources),
            "base_chapter_base": self.base_chapter_base,
            "base_commit_id": self.base_commit_id,
            "context_version": self.context_version,
            "manuscript_ref": {
                "kind": "task_snapshot",
                "id": self.snapshot_id,
                "context_version": self.context_version,
            },
            "created_at": self.created_at,
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> EditSnapshot:
        selection = data.get("selection") or {}
        start = selection.get("start_paragraph")
        end = selection.get("end_paragraph")
        return cls(
            snapshot_id=str(data["snapshot_id"]),
            project_id=str(data["project_id"]),
            session_id=str(data["session_id"]),
            chapter_id=str(data["chapter_id"]),
            save_seq=int(data.get("save_seq", 0)),
            text=str(data.get("text", "")),
            text_hash=str(data.get("text_hash", "")),
            paragraph_sources=tuple(str(s) for s in data.get("paragraph_sources") or ()),
            base_chapter_base=str(data.get("base_chapter_base", "")),
            base_commit_id=str(data.get("base_commit_id", "")),
            selection_start=int(start) if start is not None else None,
            selection_end=int(end) if end is not None else None,
            context_version=int(data.get("context_version", 1)),
            created_at=str(data.get("created_at", "")),
        )


def normalize_sources(text: str, sources: Sequence[Any] | None) -> tuple[str, ...]:
    """把来源列表对齐到文本段落数；缺失/错位按保守方向归一。

    ``None`` 表示旧草稿没有来源字段（这些文字确实是人写的）；显式给了列表
    却与段落数对不上，说明来源已不可信，整章标 ``unknown``，不能默认成人工。
    """
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return ()
    if sources is None:
        return (SOURCE_HUMAN,) * len(paragraphs)
    values = tuple(str(value) for value in sources)
    if len(values) != len(paragraphs):
        return (SOURCE_UNKNOWN,) * len(paragraphs)
    return tuple(value if value in SOURCE_VALUES else SOURCE_UNKNOWN for value in values)


def merge_saved_sources(
    old_text: str,
    old_sources: Sequence[Any] | None,
    new_text: str,
) -> tuple[str, ...]:
    """保存全文时保留未改段落的来源（V03-5.3）。

    段落文本没变就沿用原来源；改过、插入或删除的段落是作者这次输入的结果，
    标为 ``human``。这样模型生成的段落只有在作者一个字都没动时才继续算
    ``agent``——一旦被改写，就不再是「生成文字」。
    """
    old_paragraphs = split_paragraphs(old_text)
    new_paragraphs = split_paragraphs(new_text)
    old = normalize_sources(old_text, old_sources)
    merged = [SOURCE_HUMAN] * len(new_paragraphs)
    matcher = SequenceMatcher(None, old_paragraphs, new_paragraphs, autojunk=False)
    for tag, i1, i2, j1, _j2 in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset in range(i2 - i1):
            merged[j1 + offset] = old[i1 + offset]
    return tuple(merged)


def source_runs(sources: Sequence[str]) -> list[dict[str, Any]]:
    """把逐段来源压缩成连续区间，写入操作记录 refs（版本历史可读）。"""
    runs: list[dict[str, Any]] = []
    for index, source in enumerate(sources):
        if runs and runs[-1]["source"] == source and runs[-1]["end"] == index:
            runs[-1]["end"] = index + 1
            continue
        runs.append({"start": index, "end": index + 1, "source": source})
    return runs


def snapshot_id_for(
    *,
    project_id: str,
    session_id: str,
    chapter_id: str,
    save_seq: int,
    start: int | None,
    end: int | None,
    text_hash: str,
) -> str:
    """确定性快照 ID：同一个冻结点重复请求得到同一个引用，不会重复落盘。"""
    return (
        "tks_"
        + content_hash(
            {
                "kind": "edit_snapshot",
                "project_id": project_id,
                "session_id": session_id,
                "chapter_id": chapter_id,
                "save_seq": save_seq,
                "selection": [start, end],
                "text_hash": text_hash,
            }
        )[:16]
    )


def snapshot_key_for(
    *,
    project_id: str,
    session_id: str,
    chapter_id: str,
    save_seq: int,
    start: int | None,
    end: int | None,
) -> str:
    """冻结点索引键（不含文本哈希）：副本前进后仍能找到已冻结的同一引用。"""
    return (
        "snapkey_"
        + content_hash(
            {
                "kind": "edit_snapshot_key",
                "project_id": project_id,
                "session_id": session_id,
                "chapter_id": chapter_id,
                "save_seq": save_seq,
                "selection": [start, end],
            }
        )[:16]
    )
