"""V2 统一结果类别与错误信封（Phase 11 T11.5）。

设计来源：DESIGN.md P8「失败必须可分类」与各模块文档的跨模块共同约束
（稳定错误码 + 可读消息）。旧 V1 异常不迁移到这里；新错误按需定义子类。

消息是喂给模型与用户的 API（四透镜①）：where 指出位置，why 说明现象与原因，
fix 给出建议。LEGAL_EMPTY 永不作为异常抛出——合法空结果是正常返回值，
走异常会把「没改动」污名化成故障（四透镜③）。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

__all__ = [
    "EnvironmentFailure",
    "HastingsError",
    "ResultCategory",
]


class ResultCategory(StrEnum):
    """统一结果类别（DESIGN.md P8）：三者不得共用同一错误路径。"""

    VIOLATION = "violation"  # 违规：命令违反版本、授权或领域不变量，应纠正输入
    LEGAL_EMPTY = "legal_empty"  # 合法空结果：正常结束并解释，不是异常
    ENVIRONMENT = "environment"  # 环境故障：保留任务状态，允许恢复


class HastingsError(Exception):
    """V2 统一错误信封：稳定错误码 + 结果类别 + 可读消息 + 结构化详情。

    ``code`` 是对外稳定契约（如 ``REPO_HEAD_CONFLICT``），调用方按 code 分支，
    不得解析 message 文本；``details`` 保留给日志/前端的结构化数据（四透镜④）。
    """

    def __init__(
        self,
        *,
        code: str,
        category: ResultCategory,
        where: str,
        why: str,
        fix: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.category = category
        self.where = where
        self.why = why
        self.fix = fix
        self.details: Mapping[str, object] = dict(details or {})
        super().__init__(self.message)

    @property
    def message(self) -> str:
        """合成可读消息：位置与现象，再加一句建议。"""
        return f"[{self.code}] {self.where}：{self.why}。建议：{self.fix}"

    def __str__(self) -> str:
        return self.message


class EnvironmentFailure(HastingsError):
    """环境故障：保留状态、可恢复重试，不归因于输入（ResultCategory.ENVIRONMENT）。"""

    def __init__(
        self,
        *,
        code: str,
        where: str,
        why: str,
        fix: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            category=ResultCategory.ENVIRONMENT,
            where=where,
            why=why,
            fix=fix,
            details=details,
        )
