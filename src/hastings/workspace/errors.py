"""文件工作区的失败分类（0.4 M01）。

0.4 不再使用 0.3 的领域错误层：这里的错误只回答「目录或文件这一层发生了什么」，
不携带版本、候选、账本等旧概念。消息统一写成「位置与现象 + 建议」，
因为调用方是模型工具和界面，二者都不该解析自然语言来判断处理方式。

状态码由错误码尾缀决定：``*_NOT_FOUND`` → 404，``*_CONFLICT`` → 409，
``*_FAILED`` → 500（磁盘/权限故障，不是作者输入写错），其余 → 400。
这样新增错误码不需要同时改映射表，也不会把「文件被别人改过」和「路径写错」混成同一类。
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["WorkspaceError", "status_for_code"]


class WorkspaceError(Exception):
    """文件工作区的可分类失败：稳定错误码 + 可读消息 + 结构化详情。"""

    def __init__(
        self,
        *,
        code: str,
        where: str,
        why: str,
        fix: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.where = where
        self.why = why
        self.fix = fix
        self.details: dict[str, object] = dict(details or {})
        super().__init__(self.message)

    @property
    def message(self) -> str:
        return f"[{self.code}] {self.where}：{self.why}。建议：{self.fix}"

    def __str__(self) -> str:
        return self.message


def status_for_code(code: str) -> int:
    """错误码 → HTTP 状态；未知码按「输入写错」处理（400）。"""
    if code.endswith("_NOT_FOUND"):
        return 404
    if code.endswith("_CONFLICT"):
        return 409
    if code.endswith("_FAILED"):
        return 500
    return 400
