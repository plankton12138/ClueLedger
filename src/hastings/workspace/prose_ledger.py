"""正文台账（1.2 事实层）：每章实际入档修订的事务层记录。

台账回答且只回答一个问题：**哪几章真的有正文**。由事务层在三元提交成功时
自动追加、整体撤销时自动回退；模型只读不可写、不进入 ``STRUCTURE.md``——
这是系统的「git status」，为确定性对账提供不依赖自由格式解析的事实基础。

2026-11-03 最小试验确立：被动注入事实模型不对账（0/3 顺假前提跑），
对账必须由装配层确定性执行，因此对账逻辑放在本模块（纯函数、离线可测）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from hastings.workspace.atomic_io import atomic_write_json, locked

__all__ = [
    "ProseLedger",
    "ReconcileDecision",
    "empty_ledger",
    "earliest_missing",
    "load_ledger",
    "record_chapter",
    "reconcile",
    "remove_chapter",
    "save_ledger",
]

#: 台账落盘文件名（作品目录内，与 ``STRUCTURE.md`` 同级）。
LEDGER_FILENAME = "prose-ledger.json"

#: 台账数据结构版本。字段变更时递增，旧文件按迁移规则处理。
LEDGER_SCHEMA = 1


@dataclass(frozen=True)
class ProseLedger:
    """章号 → 该章最近一次入档的修订号。缺失即「尚无正文」。"""

    chapters: dict[int, str]

    def to_json(self) -> dict[str, object]:
        return {
            "schema": LEDGER_SCHEMA,
            "chapters": {
                str(chapter): revision for chapter, revision in sorted(self.chapters.items())
            },
        }

    @classmethod
    def from_json(cls, value: object) -> ProseLedger:
        if not isinstance(value, dict):
            return empty_ledger()
        if value.get("schema") != LEDGER_SCHEMA:
            return empty_ledger()
        raw = value.get("chapters")
        if not isinstance(raw, dict):
            return empty_ledger()
        chapters: dict[int, str] = {}
        for key, revision in raw.items():
            try:
                chapters[int(key)] = str(revision)
            except (TypeError, ValueError):
                continue
        return cls(chapters=chapters)


def empty_ledger() -> ProseLedger:
    """起步前（或结构文件缺失时）的空台账。"""
    return ProseLedger(chapters={})


def record_chapter(ledger: ProseLedger, chapter: int, revision: str) -> ProseLedger:
    """三元提交成功后登记一章；重复入档按覆盖处理（修订号取最新）。"""
    return replace(ledger, chapters={**ledger.chapters, chapter: revision})


def remove_chapter(ledger: ProseLedger, chapter: int) -> ProseLedger:
    """整体撤销时回退一章的登记。"""
    if chapter not in ledger.chapters:
        return ledger
    chapters = {key: value for key, value in ledger.chapters.items() if key != chapter}
    return ProseLedger(chapters=chapters)


def earliest_missing(ledger: ProseLedger, up_to: int) -> int | None:
    """``1..up_to`` 中最早没有正文登记的章号；全部都有则 ``None``。"""
    for chapter in range(1, up_to + 1):
        if chapter not in ledger.chapters:
            return chapter
    return None


@dataclass(frozen=True)
class ReconcileDecision:
    """确定性对账结论。

    ``action="write"``：目标章可写。作者明确跳过时 ``skipped`` 记录被跳过的
    缺章，供装配回执展示（不被蒙在鼓里）。
    ``action="redirect"``：目标章之前存在缺章，改派 ``target_to``（最早缺章）。
    """

    action: Literal["write", "redirect"]
    target: int
    target_to: int
    skipped: tuple[int, ...]
    reason: str


def reconcile(
    ledger: ProseLedger,
    target_chapter: int,
    *,
    allow_skip: bool = False,
) -> ReconcileDecision:
    """任务目标章与台账对账（第一道防线，纯函数）。

    目标章号来自任务元数据（系统发起写作任务时已知），不从自由格式解析。
    目标章本身已入档不拦——修改既有章是合法路径。目标章之前存在缺章时，
    默认改派最早缺章；``allow_skip=True`` 表示作者明确说跳着写，放行并记录。
    """
    missing = [chapter for chapter in range(1, target_chapter) if chapter not in ledger.chapters]
    if not missing:
        return ReconcileDecision(
            action="write",
            target=target_chapter,
            target_to=target_chapter,
            skipped=(),
            reason=f"第 1—{target_chapter - 1} 章均有正文登记，第 {target_chapter} 章可写",
        )
    if allow_skip:
        return ReconcileDecision(
            action="write",
            target=target_chapter,
            target_to=target_chapter,
            skipped=tuple(missing),
            reason=f"作者明确跳过缺章 {'、'.join(str(c) for c in missing)}，放行并记录",
        )
    earliest = missing[0]
    return ReconcileDecision(
        action="redirect",
        target=target_chapter,
        target_to=earliest,
        skipped=(),
        reason=(
            f"第 {earliest} 章尚无正文登记，第 {target_chapter} 章不予起草，改派第 {earliest} 章"
        ),
    )


def load_ledger(path: Path) -> ProseLedger:
    """读台账；文件缺失或损坏按空台账处理（事实层宁可从零重建，不带病运行）。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_ledger()
    return ProseLedger.from_json(raw)


def save_ledger(path: Path, ledger: ProseLedger) -> None:
    """原子落盘；与正文入档同事务调用（成功路径），跨进程锁防并发写。

    锁用专用 ``.lock`` 文件而非数据文件本身——Windows 上替换被打开的文件
    会拒绝访问（与 ``creative_candidates`` / ``operations`` 同一惯例）。
    """
    with locked(path.with_name(path.name + ".lock")):
        atomic_write_json(path, ledger.to_json())
