"""V2 统一结果类别与错误信封（Phase 11 T11.5）。

设计来源：DESIGN.md P8「失败必须可分类」与各模块文档的跨模块共同约束
（稳定错误码 + 三要素消息）。旧 V1 异常不迁移到这里；新错误按需定义子类。

三要素是喂给模型与用户的 API（四透镜①）：where=哪里错，why=为什么错，
fix=怎样算对。LEGAL_EMPTY 永不作为异常抛出——合法空结果是正常返回值，
走异常会把「没改动」污名化成故障（四透镜③）。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

__all__ = [
    "EnvironmentFailure",
    "HastingsError",
    "ResultCategory",
    "ViolationError",
    "V03_ERROR_CODES",
]

#: 0.3 目标错误码集中登记（计划 §4.4、执行指导 §3.1）：每新增一个错误码
#: 在此登记，并同次补三类测试——领域抛出、API 映射 HTTP、前端按 code 分支。
V03_ERROR_CODES: tuple[str, ...] = (
    "EDIT_SAVE_CONFLICT",
    "MANUSCRIPT_BASE_CONFLICT",
    "EDIT_SESSION_BLOCKS_PUBLISH",
    "SOURCE_NEEDS_RECHECK",
    "IMPORT_PREVIEW_STALE",
    "IMPORT_DUPLICATE_CONTENT",
    "EXECUTION_NOT_AUTHORIZED",
    "TASK_BUDGET_EXHAUSTED",
    "UNDO_DEPENDENCY_CONFLICT",
    "EDIT_SESSION_UNFLUSHED",
    "EDIT_SESSION_MERGE_CONFLICT",
    "OPERATION_KEY_REUSED",
    "AGREEMENT_TOPIC_CONFLICT",
    "AGREEMENT_REVISION_CONFLICT",
    "AGREEMENT_NOT_FOUND",
    "BACKGROUND_SCOPE_INVALID",
    "BACKGROUND_BUDGET_INVALID",
    "BACKGROUND_BUDGET_TOO_SMALL",
    "SUMMARY_CHAPTER_NOT_FOUND",
    "SUMMARY_NOT_AVAILABLE",
    "SUMMARY_EMPTY_RESULT",
    "SUMMARY_JOB_NOT_FOUND",
    "SUMMARY_JOB_CANCELLED",
    "SUGGESTION_CHAPTER_NOT_FOUND",
    "SUGGESTION_NOT_AVAILABLE",
    "DISCLOSURE_NEEDS_AUTHORIZATION",
    "CONVERSATION_NOT_FOUND",
)


class ResultCategory(StrEnum):
    """统一结果类别（DESIGN.md P8）：三者不得共用同一错误路径。"""

    VIOLATION = "violation"  # 违规：命令违反版本、授权或领域不变量，应纠正输入
    LEGAL_EMPTY = "legal_empty"  # 合法空结果：正常结束并解释，不是异常
    ENVIRONMENT = "environment"  # 环境故障：保留任务状态，允许恢复


class HastingsError(Exception):
    """V2 统一错误信封：稳定错误码 + 结果类别 + 三要素消息 + 结构化详情。

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
        """三要素合成消息：哪里错、为什么错、怎样算对。"""
        return f"[{self.code}] 在 {self.where}：{self.why}。怎样算对：{self.fix}"

    def __str__(self) -> str:
        return self.message


class ViolationError(HastingsError):
    """违规：应纠正输入后重试（ResultCategory.VIOLATION）。"""

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
            category=ResultCategory.VIOLATION,
            where=where,
            why=why,
            fix=fix,
            details=details,
        )


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
