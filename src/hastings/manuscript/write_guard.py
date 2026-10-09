"""人机互斥锁（P5 T3，计划 D4）：作者草稿锁注册表。

核心不变量：正文同一时刻只经由一只手变更——作者有未保存草稿时 agent
不得写该章（工具层守门，chapter_tools）；agent 写作流进行中作者不得
保存（HTTP 侧守门，rest.py 的 is_running 检查）。双侧共用本模块的
注册表状态，注册表实例挂在 HastingsSession 上。

纯内存态：进程重启自然清空。单用户假设下不做锁超时抢占、不做跨进程
锁（已知限制，计划 D4）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EditMark:
    """一章正文的作者编辑锁：holder 与加锁时刻。"""

    chapter_id: str
    holder: str
    ts: float


class EditMarkRegistry:
    """chapter_id → EditMark 的内存注册表（单会话一份，挂在 session 上）。"""

    def __init__(self) -> None:
        self._marks: dict[str, EditMark] = {}

    def acquire(
        self,
        chapter_id: str,
        *,
        holder: str = "user",
        now: Callable[[], float] = time.time,
    ) -> EditMark:
        """加锁；重复加锁覆盖旧 mark（单用户无竞态，最后一次编辑为准）。"""
        mark = EditMark(chapter_id=chapter_id, holder=holder, ts=now())
        self._marks[chapter_id] = mark
        return mark

    def release(self, chapter_id: str) -> bool:
        """释放锁；返回是否原本持锁（幂等，未持锁释放无害）。"""
        return self._marks.pop(chapter_id, None) is not None

    def mark_of(self, chapter_id: str) -> EditMark | None:
        return self._marks.get(chapter_id)


__all__ = ["EditMark", "EditMarkRegistry"]
