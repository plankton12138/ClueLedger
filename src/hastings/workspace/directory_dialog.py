"""系统文件夹选择对话框（V04-8）。

产品里不让作者手抄目录路径：路径由系统对话框选出来，应用只接收结果。手抄路径
要求作者知道绝对路径、记得分隔符、还得自己先建目录，这些都不该是写作工具的事。

对话框在**运行应用的这台机器**上弹出——服务与应用同机是本地单用户工具的前提。
这条前提不成立时（远程部署、无桌面会话）不能假装成功：直接以「不可用」失败，
并给出 ``--workspace`` 的替代路径。

取消不是错误：返回 ``None``，界面保持原样。同一时刻只允许一个对话框，第二个请求
以冲突返回，避免作者在几个窗口之间迷路。
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path
from typing import Any

__all__ = ["DirectoryPickerError", "pick_directory"]


class DirectoryPickerError(RuntimeError):
    """对话框打不开或已有窗口在等操作；``code`` 决定 HTTP 状态与前端分支。"""

    def __init__(self, *, code: str, why: str, fix: str) -> None:
        self.code = code
        self.why = why
        self.fix = fix
        super().__init__(f"[{code}] {why}。建议：{fix}")


#: 系统对话框是单例资源：同一进程同时开两个，作者会分不清哪个属于哪个窗口。
_PICKER_LOCK = threading.Lock()


def pick_directory(*, title: str = "选择作品位置", initial: Path | None = None) -> Path | None:
    """弹出系统文件夹选择框；返回选中目录，取消返回 ``None``。"""
    if not _PICKER_LOCK.acquire(blocking=False):
        raise DirectoryPickerError(
            code="DIRECTORY_PICKER_BUSY_CONFLICT",
            why="已经有一个选择窗口开着，等你选完或取消",
            fix="先在系统窗口里选好或取消，再点一次",
        )
    try:
        return _ask_directory(title=title, initial=initial)
    finally:
        _PICKER_LOCK.release()


def _ask_directory(*, title: str, initial: Path | None) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # 无 Tk 组件的 Python 发行版
        raise DirectoryPickerError(
            code="DIRECTORY_PICKER_FAILED",
            why=f"这个 Python 没有图形界面组件，打不开文件夹选择窗口（{type(exc).__name__}）",
            fix="用 --workspace <目录> 启动应用直接打开作品，或换用带 Tk 的 Python 运行",
        ) from exc

    root: Any = None
    try:
        root = tk.Tk()
        root.withdraw()
        # 浏览器窗口在前台时，对话框也要能被看到。
        root.attributes("-topmost", True)
        root.update_idletasks()
        chosen = filedialog.askdirectory(
            title=title,
            initialdir=str(initial) if initial is not None else "",
            mustexist=True,
            parent=root,
        )
    except Exception as exc:  # 无桌面会话、显示服务不可用
        raise DirectoryPickerError(
            code="DIRECTORY_PICKER_FAILED",
            why=f"打不开文件夹选择窗口：{exc}",
            fix="确认应用运行在有桌面会话的机器上；无桌面会话时用 --workspace <目录> 启动",
        ) from exc
    finally:
        if root is not None:
            with contextlib.suppress(Exception):  # 窗口已被系统关闭
                root.destroy()
    return Path(chosen) if chosen else None
