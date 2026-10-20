"""0.3 业务层幂等与请求指纹（V03-0.2 冻结）。

规则对齐 ``CommitService._request_fingerprint`` 的取舍（application/services/
commit_service.py）：指纹只含「写什么」，**排除并发执行凭据**——它们是提交
时刻的乐观并发条件，同一逻辑操作的重放天然可能携带前进后的凭据（如 restore
重放时基础版本已变成上次 restore 的结果）。凭据进指纹会把合法重放误判为
冲突（计划 §3.2、§6.1）。

回执保存与业务提交的事务原子性在 V03-0.5 落实（执行指导 §3.3）；本模块
只冻结指纹与错误契约，不含存储。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hastings.application.errors import ViolationError
from hastings.domain.repository.serialization import content_hash

__all__ = [
    "CONCURRENCY_CREDENTIAL_FIELDS",
    "operation_key_reused",
    "request_fingerprint",
]

#: 并发凭据字段：进请求不带指纹（排除集）。新增写入口时把同类字段登记于此，
#: 避免「合法重放被误判冲突」逐案复发。
CONCURRENCY_CREDENTIAL_FIELDS: frozenset[str] = frozenset(
    {
        "expected_head",
        "expected_chapter_base",
        "expected_save_seq",
        "expected_save_sequences",
        "expected_revision",
        "expected_group_revision",
        "expected_preview_revision",
        "old_revision_id",
        "required_leases",
        "conflict_resolutions",
    }
)


def request_fingerprint(request: Mapping[str, Any]) -> str:
    """业务层请求指纹：规范化 JSON 的稳定哈希，排除并发凭据字段。

    参与：一切描述「写什么」的字段（目标、内容、类型、范围、来源）。
    排除：``CONCURRENCY_CREDENTIAL_FIELDS`` 中的凭据字段——它们表达
    「基于哪个版本写」，不表达「写什么」；重放时天然可能变化。

    同键同指纹 → 返回原回执（幂等重放）；同键不同指纹 →
    ``OPERATION_KEY_REUSED`` 显式冲突，不静默返回旧结果。
    """
    content = {k: v for k, v in request.items() if k not in CONCURRENCY_CREDENTIAL_FIELDS}
    return content_hash(content)


def operation_key_reused(
    operation_id: str,
    *,
    where: str,
    original: Mapping[str, Any] | None = None,
) -> ViolationError:
    """同一操作键携带了不同内容（计划 §4.4）：返回原标识与冲突原因。"""
    return ViolationError(
        code="OPERATION_KEY_REUSED",
        where=where,
        why=f"操作键 {operation_id!r} 已被不同内容的请求使用",
        fix="新意图使用新的 operation_id；原操作回执见 details.original",
        details={"operation_id": operation_id, "original": dict(original or {})},
    )
