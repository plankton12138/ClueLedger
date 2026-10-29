"""1.0.1 P2：统一操作的存储、代次与控制收据，不调用模型。

这一层回答的是「谁还能写、重复点击会不会再来一次、重启之后算什么状态」。
模型是否真的产出了内容不在这一层判断。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.operations import (
    Operation,
    OperationStore,
    operation_status_for_run,
)


def make(operation_id: str = "op_1", **kwargs: object) -> Operation:
    payload: dict[str, object] = {
        "id": operation_id,
        "owner": "workspace:ws1",
        "workspace_id": "ws1",
        "conversation_id": "c1",
        "turn_id": "t1",
        "purpose": "chat",
    }
    payload.update(kwargs)
    return Operation(**payload)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> OperationStore:
    return OperationStore(tmp_path / "state")


def test_operation_roundtrips_through_disk(store: OperationStore) -> None:
    operation = make()
    operation.instruction("把结尾改得更克制", "author", request_key="k1")
    store.create(operation)

    loaded = store.read("op_1")

    assert loaded.status == "accepted"
    assert loaded.generation == 1
    assert loaded.instruction_log[0]["text"] == "把结尾改得更克制"
    assert loaded.created_at and loaded.updated_at


def test_same_id_is_refused_instead_of_overwritten(store: OperationStore) -> None:
    store.create(make())

    with pytest.raises(WorkspaceError) as excinfo:
        store.create(make())

    assert excinfo.value.code == "OPERATION_REQUEST_CONFLICT"
    assert store.read("op_1").status == "accepted"


def test_output_belongs_to_the_current_generation_only() -> None:
    operation = make(status="running", generation=2)

    assert operation.accepts_output(2) is True
    assert operation.accepts_output(1) is False

    operation.status = "paused"
    assert operation.accepts_output(2) is False


def test_control_receipt_is_stored_per_request_key() -> None:
    operation = make(status="running")

    first = operation.record_control("k1", "pause", generation=1)
    again = operation.control("k1")

    assert again == first
    assert operation.control("k2") is None


def test_view_hides_request_keys_and_reports_capabilities() -> None:
    operation = make(status="running")
    operation.record_control("k1", "pause", generation=1)
    view = operation.as_view()

    assert "controls" not in view
    assert "instruction_log" not in view
    assert view["capabilities"] == {"pause": True, "continue": False, "interject": True}

    operation.status = "paused"
    paused = operation.as_view()
    assert paused["capabilities"] == {"pause": False, "continue": True, "interject": False}


def test_recovery_turns_unfinished_operations_into_needs_attention(store: OperationStore) -> None:
    store.create(make("op_running", status="running", generation=3))
    store.create(make("op_done", status="completed", generation=1))

    changed = store.recover()

    assert [item.id for item in changed] == ["op_running"]
    recovered = store.read("op_running")
    assert recovered.status == "needs_attention"
    assert recovered.generation == 4
    assert recovered.resumable is False
    assert recovered.checkpoints[-1]["status"] == "interrupted"
    # 已完成的操作保持原状，也不再补一次中断记录。
    assert store.read("op_done").status == "completed"
    assert store.recover() == ()


def test_corrupt_operation_is_reported_not_reset(store: OperationStore) -> None:
    store.create(make())
    path = store.path("op_1")
    path.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(WorkspaceError) as excinfo:
        store.read("op_1")

    assert excinfo.value.code == "OPERATION_STATE_FAILED"
    assert path.read_text(encoding="utf-8") == "{ this is not json"


def test_unknown_or_invalid_id_is_not_found(store: OperationStore) -> None:
    with pytest.raises(WorkspaceError) as excinfo:
        store.read("op_missing")
    assert excinfo.value.code == "OPERATION_NOT_FOUND"

    with pytest.raises(WorkspaceError) as invalid:
        store.read("../escape")
    assert invalid.value.code == "OPERATION_NOT_FOUND"


def test_run_status_mapping_never_claims_completion_by_default() -> None:
    assert operation_status_for_run("completed") == "completed"
    assert operation_status_for_run("needs_input") == "awaiting_answer"
    assert operation_status_for_run("partial") == "needs_attention"
    assert operation_status_for_run("stopped") == "paused"
    assert operation_status_for_run("failed") == "failed"
    assert operation_status_for_run("something-new") == "needs_attention"


def test_saved_file_keeps_generation_and_receipts(store: OperationStore) -> None:
    operation = make(status="running")
    store.create(operation)
    operation.generation = 2
    operation.status = "paused"
    operation.record_control("k1", "interject", generation=2)
    operation.committed_receipts.append({"kind": "git", "commit": "abc"})
    store.save(operation)

    raw = json.loads(store.path("op_1").read_text(encoding="utf-8"))

    assert raw["generation"] == 2
    assert raw["controls"]["k1"]["kind"] == "interject"
    assert raw["committed_receipts"] == [{"kind": "git", "commit": "abc"}]
    assert store.read("op_1").status == "paused"
