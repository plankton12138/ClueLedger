"""本进程内的轮次占用登记（0.8 F2）。

装配接口与写作轮次分属两个路由，但都要回答同一个问题：这部作品现在有没有一轮在写。
用一个很薄的登记表共享状态，避免通过文件锁或全局单例互相猜。

只登记「已受理到收尾」这段区间：受理完成后再标忙，避免写初始记录失败时留下假占用；
收尾在 ``finally`` 释放，异常、停止与中断都走同一条路径。
"""

from __future__ import annotations

import threading
from pathlib import Path

__all__ = ["RunRegistry"]


class RunRegistry:
    """按作品根目录登记当前轮次；同一作品只允许一个写入轮次。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, str] = {}

    @staticmethod
    def key(root: Path) -> str:
        return str(Path(root).resolve())

    def mark(self, root: Path, turn_id: str) -> None:
        with self._lock:
            self._active[self.key(root)] = turn_id

    def release(self, root: Path, turn_id: str) -> None:
        """只释放自己登记的那一轮，避免迟到的收尾清掉后来者。"""
        key = self.key(root)
        with self._lock:
            if self._active.get(key) == turn_id:
                self._active.pop(key, None)

    def is_busy(self, root: Path) -> bool:
        with self._lock:
            return self.key(root) in self._active

    def turn_id(self, root: Path) -> str:
        with self._lock:
            return self._active.get(self.key(root), "")
