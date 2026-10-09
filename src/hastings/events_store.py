"""事件持久化（P6 T4，计划 D4）——events.jsonl：可观测性的账。

**职责分离宣言**（两本账，读者不同）：
- ``session.jsonl``（session_persist.py）喂 harness 的**会话记忆**——模型视角的账，
  只收 ``message_end`` 定稿；
- ``events.jsonl``（本模块）喂**可观测性**——工程师/作者视角的账，收全量事件帧
  （agent_start / turn_* / message_start|end / tool_execution_* / agent_end / notice），
  服务重启后事件流页据此回放历史。

落盘格式（每行一条 JSON，append-only）：

    {"ts": "2026-10-09T12:00:00+00:00", "seq": 1, "type": "agent_start", "data": {...}}

``seq`` 进程内单调自增（重启时从文件末行续起），是分页与前端合并去重的游标；
``ts`` 为 UTC ISO8601，与本地化展示解耦；``data`` 是事件帧原文
（``model_dump(mode="json", by_alias=True)``，与 WS 帧同一口径）。

泄压阀（沿用 P4 落盘故障教训）：写盘 OSError → stderr 三要素告警，
事件流不中断——持久化只是事件流的又一个消费者，不能反过来杀死写作流。
读路径宽容：单行损坏跳过，不因一行坏数据作废整份历史。

刻意裁剪（记录于构建笔记）：``message_update`` 不入库——它是流式中间态
（逐 token delta），全量落盘会淹没事件流且体积失控，与 session.jsonl
「只落定稿」同一哲学。
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, cast

from hastings.bible.store import StoryWorkspace
from tau_agent.events import AgentEvent
from tau_agent.types import JSONValue

__all__ = [
    "CATEGORIES",
    "EventCategory",
    "EventPage",
    "EventRecord",
    "EventStore",
    "categorize",
    "events_file",
]

EventCategory = Literal["message", "tool", "error"]

# 前端筛选 Tab 的取值域，同时是 REST 查询参数的合法集。
CATEGORIES: Final[tuple[EventCategory, ...]] = ("message", "tool", "error")


def categorize(event_type: str, data: Mapping[str, JSONValue]) -> str:
    """事件帧 → 分类（纯函数，REST 过滤与前端映射同规则）。

    - ``isError`` 为真 → 错误（工具失败帧强制归错误，压过其类型前缀）；
    - ``message_*`` → 消息；
    - ``tool_execution_*`` → 工具；
    - 其余（agent_*/turn_*/notice…）→ other（前端「全部」Tab 覆盖）。
    """
    if data.get("isError") is True:
        return "error"
    if event_type.startswith("message"):
        return "message"
    if event_type.startswith("tool_execution"):
        return "tool"
    return "other"


@dataclass(frozen=True, slots=True)
class EventRecord:
    """一条持久化事件：ts（UTC ISO8601） + seq（游标） + type + data（帧原文）。"""

    ts: str
    seq: int
    type: str
    data: dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class EventPage:
    """一页倒序事件（新 → 旧）、分页信号与整账统计（P6 D5 运行概览数据源）。

    ``total``/``counts`` 描述**整份文件**（不受分页与 category 过滤影响）——
    概览与分类 Tab 计数展示的是账本本身，不是当前页。
    """

    records: tuple[EventRecord, ...]
    has_more: bool
    total: int
    counts: dict[str, int]


def events_file(workspace: StoryWorkspace) -> Path:
    """故事事件流文件：``stories/<slug>/events.jsonl``——与 Bible、章节同目录（D1）。"""
    return workspace.story_dir / "events.jsonl"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _scan_last_seq(path: Path) -> int:
    """重启续号：读文件末条有效记录的 seq；缺文件/坏行回落到 0。"""
    if not path.exists():
        return 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            return int(json.loads(stripped)["seq"])
        except (ValueError, KeyError, TypeError):
            continue
    return 0


class EventStore:
    """append-only 事件账本：写侧 seq 自增 + 锁串行；读侧倒序分页 + 分类过滤。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._seq = _scan_last_seq(path)

    @property
    def path(self) -> Path:
        return self._path

    async def append(self, event_type: str, data: Mapping[str, JSONValue]) -> None:
        """追加一条事件（seq 在锁内自增，保证单调）；坏盘只告警不中断。"""
        async with self._lock:
            self._seq += 1
            record: dict[str, JSONValue] = {
                "ts": _now_iso(),
                "seq": self._seq,
                "type": event_type,
                "data": dict(data),
            }
            try:
                self._write_line(record)
            except OSError as exc:
                print(
                    "[events] 事件流落盘失败，写作继续但事件历史不完整。"
                    f"为什么错：{exc}。"
                    "怎样算对：检查磁盘空间/文件占用后重启服务恢复记录。",
                    file=sys.stderr,
                )

    async def append_event(self, event: AgentEvent) -> None:
        """AgentEvent → 落盘（帧原文与 WS 同形：``model_dump(mode="json", by_alias=True)``）。"""
        data = cast("dict[str, JSONValue]", event.model_dump(mode="json", by_alias=True))
        await self.append(event.type, data)

    def _write_line(self, record: Mapping[str, JSONValue]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    async def read(
        self,
        *,
        limit: int = 200,
        before_seq: int | None = None,
        category: str | None = None,
    ) -> EventPage:
        """倒序分页读取：seq < before_seq 的帧、按 category 过滤、取最新 limit 条。

        统计（total/counts）总是对整份文件计算——读路径本就全量扫一遍，
        概览计数顺带产出，不引入第二份存储。
        """
        records = await asyncio.to_thread(self._read_all)
        counts: dict[str, int] = {}
        for record in records:
            key = categorize(record.type, record.data)
            counts[key] = counts.get(key, 0) + 1
        selected = [
            record
            for record in records
            if (before_seq is None or record.seq < before_seq)
            and (category is None or categorize(record.type, record.data) == category)
        ]
        selected.sort(key=lambda record: record.seq, reverse=True)
        return EventPage(
            records=tuple(selected[:limit]),
            has_more=len(selected) > limit,
            total=len(records),
            counts=counts,
        )

    def _read_all(self) -> list[EventRecord]:
        if not self._path.exists():
            return []
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return []
        records: list[EventRecord] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw: object = json.loads(stripped)
                if not isinstance(raw, dict):
                    continue
                records.append(
                    EventRecord(
                        ts=str(raw["ts"]),
                        seq=int(raw["seq"]),
                        type=str(raw["type"]),
                        data=cast("dict[str, JSONValue]", raw.get("data") or {}),
                    )
                )
            except (ValueError, KeyError, TypeError):
                continue  # 单行损坏跳过（读路径宽容）
        return records
