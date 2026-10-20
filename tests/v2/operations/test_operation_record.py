"""OperationRecord 领域模型契约（V03-2.1）。

钉死：ID 前缀与校验、wire 往返、状态枚举、固定讨论 ID 确定性（计划 §4.1/§4.2）。
"""

from __future__ import annotations

import pytest

from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.errors import ManuscriptViolation
from hastings.domain.manuscript.operation_record import (
    OPERATION_EVENT_COMMITTED,
    OPERATION_RECEIPT_BUCKET,
    OPERATION_RECORD_KIND,
    OperationReceipt,
    OperationRecord,
    OperationStatus,
    creation_log_conversation_id,
    operation_reference_message_id,
)


def _record(**over: object) -> OperationRecord:
    base: dict = {
        "operation_id": "op_abc123",
        "request_fingerprint": "fp-1",
        "kind": "chapter.save",
        "project_id": "prj_x",
        "conversation_id": "conv_createlog",
        "changed_chapter_ids": ("ch_1",),
        "status": OperationStatus.COMMITTED,
    }
    base.update(over)
    return OperationRecord(**base)


def test_wire_round_trip_preserves_all_fields() -> None:
    record = _record(before_commit_id="cmt_1", refs={"origin": "manual_edit"})
    wire = record.to_wire()
    assert OperationRecord.from_wire(wire) == record
    # 必备字段齐全（前端契约同一形状）
    assert wire["status"] == "committed"
    assert wire["changed_chapter_ids"] == ["ch_1"]


@pytest.mark.parametrize(
    "over",
    [
        {"operation_id": "wrong_1"},
        {"operation_id": " op_1"},
        {"operation_id": "op_1 "},
        {"project_id": "notaprj"},
        {"conversation_id": "msg_x"},
        {"request_fingerprint": ""},
        {"kind": ""},
    ],
)
def test_invalid_records_rejected(over: dict) -> None:
    with pytest.raises(ManuscriptViolation) as excinfo:
        _record(**over)
    assert excinfo.value.code in (
        ec.MANUSCRIPT_INVALID_OPERATION,
        ec.MANUSCRIPT_MISSING_REQUIRED_FIELD,
    )


def test_receipt_round_trip_and_missing_fields() -> None:
    receipt = OperationReceipt(
        operation_id="op_1",
        request_fingerprint="fp",
        kind="chapter.save",
        project_id="prj_x",
        conversation_id="conv_c",
        before_commit_id="cmt_0",
    )
    assert OperationReceipt.from_wire(receipt.to_wire()) == receipt
    with pytest.raises(ManuscriptViolation):
        OperationReceipt.from_wire({"operation_id": "op_1"})


def test_creation_log_conversation_id_is_deterministic_and_unique_per_project() -> None:
    a1 = creation_log_conversation_id("prj_a")
    a2 = creation_log_conversation_id("prj_a")
    b = creation_log_conversation_id("prj_b")
    assert a1 == a2  # 确定性：重试/重启/多入口同一讨论
    assert a1 != b  # 作品之间隔离
    assert a1.startswith("conv_")


def test_reference_message_id_is_deterministic_per_operation() -> None:
    assert operation_reference_message_id("op_x") == operation_reference_message_id("op_x")
    assert operation_reference_message_id("op_x") != operation_reference_message_id("op_y")
    assert operation_reference_message_id("op_x").startswith("msg_")


def test_module_constants_are_frozen() -> None:
    """常量即契约：Outbox 事件类型 / Artifact kind / aux bucket 不可漂移。"""
    assert OPERATION_EVENT_COMMITTED == "manuscript.operation.committed"
    assert OPERATION_RECORD_KIND == "operation_record"
    assert OPERATION_RECEIPT_BUCKET == "operation_receipts"
    assert {s.value for s in OperationStatus} == {
        "preparing",
        "committed",
        "no_change",
        "blocked",
        "failed",
    }
