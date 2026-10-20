"""操作记录领域模型（V03-2.1，计划 §4.1 / §3.3）。

``OperationRecord`` 是正文发布时的**权威业务记录**，按版本化 Artifact 建模
（kind = ``operation_record``），继承仓库的历史、并发与导出能力；
``OperationReceipt`` 落仓库辅助表并与提交同事务（V03-0.5 的 aux 端口），
覆盖无变化与操作成功两种回执。

固定讨论（计划 §4.2）：没有会话时**确定性分配**本作品「创作记录」的会话 ID，
默认讨论唯一——同一作品的所有操作都投影到同一会话，投影消费者按
operation_id 幂等创建讨论引用，不投递到作者当时正在打开的会话。

状态转换（§4.3）：``preparing → committed / no_change / blocked / failed``；
committed 只有一个正式结果（after commit 由携带记录的那个 Commit 充当，
不在记录内容里冗余存储）；no_change 不伪造 commit。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.errors import violation
from hastings.domain.repository.serialization import content_hash

__all__ = [
    "OPERATION_EVENT_COMMITTED",
    "OPERATION_RECORD_KIND",
    "OPERATION_RECEIPT_BUCKET",
    "OperationReceipt",
    "OperationRecord",
    "OperationStatus",
    "creation_log_conversation_id",
    "operation_reference_message_id",
]

#: 仓库 Outbox 事件类型（计划 §6：operation_id、commit、变更章、固定会话 ID；
#: 与提交同事务进入 Outbox，投影失败不回滚正文）。
OPERATION_EVENT_COMMITTED = "manuscript.operation.committed"

#: OperationRecord 作为版本化 Artifact 的 kind。
OPERATION_RECORD_KIND = "operation_record"

#: 回执在仓库辅助表中的 bucket（key = operation_id）。
OPERATION_RECEIPT_BUCKET = "operation_receipts"

_AUTHOR_ID = "author"


class OperationStatus(StrEnum):
    """操作状态（§4.3）：committed 只有一个正式结果；no_change 不伪造 commit。"""

    PREPARING = "preparing"
    COMMITTED = "committed"
    NO_CHANGE = "no_change"
    BLOCKED = "blocked"
    FAILED = "failed"


def _require_prefixed(value: str, prefix: str, where: str, what: str) -> str:
    if not value or value.strip() != value or not value.startswith(f"{prefix}_"):
        raise violation(
            ec.MANUSCRIPT_INVALID_OPERATION,
            where,
            f"{what} {value!r} 必须是 {prefix}_ 前缀的稳定 ID（创建后不可变）",
            f"使用生成器产出的 {prefix}_<后缀> 形式",
        )
    return value


@dataclass(frozen=True, slots=True)
class OperationRecord:
    """一次正文操作的权威记录（版本化 Artifact 的 content 形状）。

    ``after_commit_id`` 不冗余存储：携带本记录的那个 Commit 就是操作结果
    所在版本，由查询侧解析（见 OperationService.get_operation）。
    """

    operation_id: str
    request_fingerprint: str
    kind: str
    project_id: str
    conversation_id: str
    source: str = _AUTHOR_ID
    before_commit_id: str | None = None
    changed_chapter_ids: tuple[str, ...] = ()
    status: OperationStatus = OperationStatus.PREPARING
    refs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        where = f"operation_record({self.operation_id})"
        _require_prefixed(self.operation_id, "op", where, "操作 ID")
        if not self.request_fingerprint:
            raise violation(
                ec.MANUSCRIPT_MISSING_REQUIRED_FIELD,
                f"{where}.request_fingerprint",
                "请求指纹为空",
                "对提交内容计算稳定哈希后写入",
            )
        if not self.kind:
            raise violation(
                ec.MANUSCRIPT_MISSING_REQUIRED_FIELD,
                f"{where}.kind",
                "操作类型为空",
                "使用 chapter.save / chapter.import 等稳定类型名",
            )
        _require_prefixed(self.project_id, "prj", f"{where}.project_id", "作品 ID")
        _require_prefixed(self.conversation_id, "conv", f"{where}.conversation_id", "固定讨论 ID")
        if self.status is OperationStatus.PREPARING and self.refs:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"{where}.refs",
                "preparing 状态不允许携带附加审计信息（终态才固化）",
                "终态时再写 refs",
            )

    # -- wire 往返（Artifact content 即 wire 形状，与前端契约同一形状） ------

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation_id": self.operation_id,
            "request_fingerprint": self.request_fingerprint,
            "kind": self.kind,
            "project_id": self.project_id,
            "conversation_id": self.conversation_id,
            "source": self.source,
            "changed_chapter_ids": list(self.changed_chapter_ids),
            "status": self.status.value,
        }
        if self.before_commit_id is not None:
            payload["before_commit_id"] = self.before_commit_id
        if self.refs:
            payload["refs"] = self.refs
        return payload

    @classmethod
    def from_wire(cls, payload: Any) -> OperationRecord:
        if not isinstance(payload, dict):
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                "operation_record.from_wire",
                f"记录负载不是对象：{type(payload).__name__}",
                "传入 to_wire 产出的对象",
            )
        required_fields = (
            "operation_id",
            "request_fingerprint",
            "kind",
            "project_id",
            "conversation_id",
        )
        missing = [f for f in required_fields if f not in payload]
        if missing:
            raise violation(
                ec.MANUSCRIPT_MISSING_REQUIRED_FIELD,
                "operation_record.from_wire",
                f"记录缺少必填字段 {missing}",
                "补齐后重试",
            )
        return cls(
            operation_id=str(payload["operation_id"]),
            request_fingerprint=str(payload["request_fingerprint"]),
            kind=str(payload["kind"]),
            project_id=str(payload["project_id"]),
            conversation_id=str(payload["conversation_id"]),
            source=str(payload.get("source", _AUTHOR_ID)),
            before_commit_id=payload.get("before_commit_id"),
            changed_chapter_ids=tuple(payload.get("changed_chapter_ids", ())),
            status=OperationStatus(payload.get("status", "preparing")),
            refs=dict(payload.get("refs") or {}),
        )


@dataclass(frozen=True, slots=True)
class OperationReceipt:
    """辅助表回执（与提交同事务写入）：幂等重放与结果定位的最小凭据。

    只含提交前已知的信息（指纹、归属、固定讨论）——committed/no_change 的
    区分由「记录 Artifact 是否存在」在查询侧裁决，避免把提交后才能确定的
    状态写进指纹。
    """

    operation_id: str
    request_fingerprint: str
    kind: str
    project_id: str
    conversation_id: str
    source: str = _AUTHOR_ID
    before_commit_id: str | None = None

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation_id": self.operation_id,
            "request_fingerprint": self.request_fingerprint,
            "kind": self.kind,
            "project_id": self.project_id,
            "conversation_id": self.conversation_id,
            "source": self.source,
        }
        if self.before_commit_id is not None:
            payload["before_commit_id"] = self.before_commit_id
        return payload

    @classmethod
    def from_wire(cls, payload: Any) -> OperationReceipt:
        if not isinstance(payload, dict):
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                "operation_receipt.from_wire",
                f"回执负载不是对象：{type(payload).__name__}",
                "传入 to_wire 产出的对象",
            )
        required_fields = (
            "operation_id",
            "request_fingerprint",
            "kind",
            "project_id",
            "conversation_id",
        )
        missing = [f for f in required_fields if f not in payload]
        if missing:
            raise violation(
                ec.MANUSCRIPT_MISSING_REQUIRED_FIELD,
                "operation_receipt.from_wire",
                f"回执缺少必填字段 {missing}",
                "回执由 OperationService 写入，不接受手工构造",
            )
        return cls(
            operation_id=str(payload["operation_id"]),
            request_fingerprint=str(payload["request_fingerprint"]),
            kind=str(payload["kind"]),
            project_id=str(payload["project_id"]),
            conversation_id=str(payload["conversation_id"]),
            source=str(payload.get("source", _AUTHOR_ID)),
            before_commit_id=payload.get("before_commit_id"),
        )


def creation_log_conversation_id(project_id: str) -> str:
    """本作品「创作记录」的确定性会话 ID（默认讨论唯一，计划 §4.2）。

    同一作品永远得到同一 ID：投影重试、重启、多入口都落回同一讨论，
    不依赖预约记录，也不依赖创建顺序。
    """
    _require_prefixed(project_id, "prj", "creation_log_conversation_id", "作品 ID")
    return f"conv_{content_hash(['creation-log', project_id])[:16]}"


def operation_reference_message_id(operation_id: str) -> str:
    """操作在固定讨论中引用消息的确定性 ID（append_message 同 ID 幂等）。"""
    _require_prefixed(operation_id, "op", "operation_reference_message_id", "操作 ID")
    return f"msg_{content_hash(['operation-ref', operation_id])[:16]}"
