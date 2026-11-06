"""账号接口的可分类失败。

与作品错误分开：认证失败要覆盖 401、403、429、503，而作品错误的尾缀规则
只适合文件与状态冲突。对外正文统一为 ``[错误码] 位置：现象。建议：…``。
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["AuthError"]


class AuthError(Exception):
    """账号与认证的稳定错误码；``status_code`` 由抛出点明确给出。"""

    def __init__(
        self,
        *,
        code: str,
        where: str,
        why: str,
        fix: str,
        status_code: int = 400,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.where = where
        self.why = why
        self.fix = fix
        self.status_code = status_code
        self.details: dict[str, object] = dict(details or {})
        super().__init__(self.message)

    @property
    def message(self) -> str:
        return f"[{self.code}] {self.where}：{self.why}。建议：{self.fix}"

    def __str__(self) -> str:
        return self.message
