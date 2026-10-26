"""作品默认存放位置（V04-7）。

作品与共享模块默认归入作者「文档」目录下的 ClueLedger 总目录。
应用源码目录、临时目录都不该用来放作品——作品是长期资产，位置要稳定。

位置由服务端解析：浏览器不知道本机的主目录，跑在同一台机器上的服务知道。
Windows 用系统已知文件夹（会跟随 OneDrive 之类的重定向），其他平台用
``~/Documents``；两者都取不到就退回用户主目录——宁可先放在一个一定存在的
地方，也不要让「新建作品」因为文档目录不存在直接失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["default_data_root", "default_workspace_root", "documents_dir"]


def documents_dir() -> Path | None:
    """系统的文档目录；不存在或取不到时返回 ``None``。"""
    if sys.platform == "win32":
        resolved = _windows_documents()
        if resolved is not None:
            return resolved
    candidate = Path.home() / "Documents"
    return candidate if candidate.is_dir() else None


def _windows_documents() -> Path | None:
    """按系统已知文件夹取文档目录（重定向到 OneDrive 时也能取对）。"""
    import ctypes

    buffer = ctypes.create_unicode_buffer(260)
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer)
    if result != 0 or not buffer.value:
        return None
    path = Path(buffer.value)
    return path if path.is_dir() else None


def default_data_root() -> Path:
    """用户内容总目录；取不到系统文档目录时使用主目录下的 ClueLedger。"""
    return (documents_dir() or Path.home()) / "ClueLedger"


def default_workspace_root() -> Path:
    """新作品的默认父目录；实际新建时才创建，不因查询位置写入磁盘。"""
    return default_data_root() / "作品"
