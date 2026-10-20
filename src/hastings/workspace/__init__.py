"""0.4 文件工作区：作品 = 一个 Markdown 目录 + Git 历史。

包内包含文件规则（``files``）、Git 历史（``git``）、一轮写入的记录协调（``service``）、
最近目录登记（``state``）与接口（``api``）。不引入段落对象、作品版本库或候选采用；
内容以磁盘为准，历史以真实 Git 为准。
"""

from __future__ import annotations

__all__ = ["api", "errors", "files", "git", "service", "state"]
