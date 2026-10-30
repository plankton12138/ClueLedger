"""1.0.1 P1：构思结构的契约、提交、隔离与恢复，不调用模型。

这些用例对应验收表 D01—D07 中属于结构层的部分：空白构思、两类无环、方案隔离、
幂等、崩溃恢复、外部改动与撤销。语义是否成立（线索是否真的成立）不在这一层判断。
"""

from __future__ import annotations

import json
import multiprocessing
import threading
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.operations import Operation, OperationStore
from hastings.workspace.story_changes import apply_change_set, diff_variant
from hastings.workspace.story_models import (
    AddClue,
    AddNode,
    Clue,
    ClueResolution,
    ClueUse,
    Connect,
    EventTimeRelation,
    ProsePatch,
    ReadDependency,
    RemoveClue,
    RemoveNode,
    SetEventTime,
    StoryChangeSet,
    StoryEdge,
    StoryNode,
    StoryVariant,
    UpdateNode,
)
from hastings.workspace.story_store import (
    EMPTY_REVISION,
    MAIN_VARIANT_ID,
    StoryStore,
)

_counter = {"value": 0}


def change_id() -> str:
    _counter["value"] += 1
    return f"change_{_counter['value']}"


@pytest.fixture
def store(tmp_path: Path) -> StoryStore:
    return StoryStore(tmp_path / "state" / "workspaces" / "ws1")


def node(node_id: str, **kwargs: Any) -> StoryNode:
    payload: dict[str, Any] = {
        "id": node_id,
        "title": kwargs.pop("title", f"节点 {node_id}"),
        "action": kwargs.pop("action", "发生了某件事"),
    }
    payload.update(kwargs)
    return StoryNode(**payload)


def clue(clue_id: str, **kwargs: Any) -> Clue:
    payload: dict[str, Any] = {"id": clue_id, "title": kwargs.pop("title", f"线索 {clue_id}")}
    payload.update(kwargs)
    return Clue(**payload)


def change(operations: list[Any], base_revision: str, **kwargs: Any) -> StoryChangeSet:
    payload: dict[str, Any] = {
        "id": kwargs.pop("id", change_id()),
        "base_revision": base_revision,
        "operations": operations,
    }
    payload.update(kwargs)
    return StoryChangeSet(**payload)


def commit(store: StoryStore, operations: list[Any], *, key: str) -> Any:
    current = store.read()
    revision = current.variant(MAIN_VARIANT_ID)
    base = revision.revision if revision is not None else EMPTY_REVISION
    return store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change(operations, base),
        request_key=key,
    )


def test_blank_structure_needs_no_prose_and_allows_undecided_truth(store: StoryStore) -> None:
    receipt = commit(
        store,
        [
            AddNode(node=node("n1")),
            AddClue(clue=clue("c1", author_truth=None, resolution=ClueResolution())),
        ],
        key="first",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert variant.revision == receipt.revision
    assert variant.nodes["n1"].title == "节点 n1"
    assert variant.clues["c1"].author_truth is None
    assert variant.clues["c1"].resolution.intent == "undecided"
    assert store.read().document is not None
    assert store.read().document.changes[receipt.change_id].operations


def test_next_cycle_is_rejected_while_reframe_may_point_backwards(store: StoryStore) -> None:
    commit(store, [AddNode(node=node("a")), AddNode(node=node("b"))], key="nodes")
    commit(
        store,
        [Connect(edge=StoryEdge(id="e1", from_id="a", to_id="b", kind="next"))],
        key="forward",
    )
    with pytest.raises(WorkspaceError) as caught:
        commit(
            store,
            [Connect(edge=StoryEdge(id="e2", from_id="b", to_id="a", kind="next"))],
            key="cycle",
        )
    assert caught.value.code == "STORY_GRAPH_INVALID"
    assert "推进顺序存在循环" in caught.value.why

    commit(
        store,
        [Connect(edge=StoryEdge(id="e3", from_id="b", to_id="a", kind="reframe"))],
        key="reframe",
    )
    assert store.require_variant(MAIN_VARIANT_ID).edges["e3"].kind == "reframe"


def test_event_time_cycle_is_checked_separately_from_disclosure_order(store: StoryStore) -> None:
    commit(store, [AddNode(node=node("a")), AddNode(node=node("b"))], key="nodes")
    commit(
        store,
        [SetEventTime(relation=EventTimeRelation(before_node_id="a", after_node_id="b"))],
        key="time-forward",
    )
    with pytest.raises(WorkspaceError) as caught:
        commit(
            store,
            [SetEventTime(relation=EventTimeRelation(before_node_id="b", after_node_id="a"))],
            key="time-cycle",
        )
    assert caught.value.code == "STORY_GRAPH_INVALID"
    assert "实际事件时间存在循环" in caught.value.why


def test_alternative_keeps_primary_structure_untouched(store: StoryStore) -> None:
    commit(store, [AddNode(node=node("n1"))], key="main-node")
    main_before = store.require_variant(MAIN_VARIANT_ID)

    receipt = store.create_variant(
        name="备选：换凶手",
        source_variant_id=MAIN_VARIANT_ID,
        source_revision=main_before.revision,
        request_key="fork",
    )
    alternative_id = receipt.variant_id
    alternative = store.require_variant(alternative_id)
    assert alternative.base_variant_id == MAIN_VARIANT_ID
    assert alternative.base_revision == main_before.revision
    assert set(alternative.nodes) == {"n1"}
    # 不共享可变对象：备选的节点是另一份实例，改它不会改到主方案。
    assert alternative.nodes["n1"] is not main_before.nodes["n1"]
    # 不共享可变对象：备选的节点是另一份实例，改它不会改到主方案。
    assert alternative.nodes["n1"] is not main_before.nodes["n1"]

    store.commit_change(
        variant_id=alternative_id,
        change=change(
            [AddNode(node=node("n2", title="只出现在备选"))], alternative.revision
        ),
        request_key="alt-node",
    )
    main_after = store.require_variant(MAIN_VARIANT_ID)
    assert main_after.revision == main_before.revision
    assert set(main_after.nodes) == {"n1"}
    assert store.read().document is not None
    assert store.read().document.primary_variant_id == MAIN_VARIANT_ID


def test_same_request_key_is_applied_once(store: StoryStore) -> None:
    payload = change([AddNode(node=node("n1"))], EMPTY_REVISION)
    first = store.commit_change(variant_id=MAIN_VARIANT_ID, change=payload, request_key="same")
    second = store.commit_change(variant_id=MAIN_VARIANT_ID, change=payload, request_key="same")
    assert second.change_id == first.change_id
    assert second.revision == first.revision
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert len(variant.nodes) == 1
    document = store.read().document
    assert document is not None
    assert len(document.changes) == 1


def test_same_request_key_with_different_payload_conflicts(store: StoryStore) -> None:
    payload = change([AddNode(node=node("n1"))], EMPTY_REVISION)
    store.commit_change(variant_id=MAIN_VARIANT_ID, change=payload, request_key="key-1")
    other = change([AddNode(node=node("n2"))], EMPTY_REVISION)
    with pytest.raises(WorkspaceError) as caught:
        store.commit_change(variant_id=MAIN_VARIANT_ID, change=other, request_key="key-1")
    assert caught.value.code == "STORY_REQUEST_CONFLICT"


def test_stale_base_revision_conflicts_without_writing(store: StoryStore) -> None:
    commit(store, [AddNode(node=node("n1"))], key="one")
    stale = store.require_variant(MAIN_VARIANT_ID).revision
    commit(store, [AddNode(node=node("n2"))], key="two")
    with pytest.raises(WorkspaceError) as caught:
        store.commit_change(
            variant_id=MAIN_VARIANT_ID,
            change=change([AddNode(node=node("n3"))], stale),
            request_key="three",
        )
    assert caught.value.code == "STORY_REVISION_CONFLICT"
    assert set(store.require_variant(MAIN_VARIANT_ID).nodes) == {"n1", "n2"}


def test_pending_without_decision_returns_to_original_state(
    store: StoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(store, [AddNode(node=node("n1"))], key="one")
    before = store.require_variant(MAIN_VARIANT_ID).revision

    def refuse(path: Path, data: bytes, **kwargs: Any) -> None:
        raise OSError("injected decision failure")

    monkeypatch.setattr("hastings.workspace.story_store.atomic_write_bytes", refuse)
    with pytest.raises(WorkspaceError) as caught:
        commit(store, [AddNode(node=node("n2"))], key="two")
    assert caught.value.code == "STORY_WRITE_FAILED"
    assert store.pending.exists()
    assert not store.decision.exists()

    monkeypatch.undo()
    assert store.require_variant(MAIN_VARIANT_ID).revision == before
    assert set(store.require_variant(MAIN_VARIANT_ID).nodes) == {"n1"}
    assert not store.pending.exists()


def test_decided_commit_is_completed_after_head_write_failure(
    store: StoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(store, [AddNode(node=node("n1"))], key="one")
    head = store.file
    real_write = __import__("hastings.workspace.story_store", fromlist=["x"]).atomic_write_json

    def fail_head(path: Path, value: object, **kwargs: Any) -> None:
        if path == head:
            raise OSError("injected head failure")
        real_write(path, value, **kwargs)

    monkeypatch.setattr("hastings.workspace.story_store.atomic_write_json", fail_head)
    with pytest.raises(WorkspaceError) as caught:
        commit(store, [AddNode(node=node("n2"))], key="two")
    assert caught.value.code == "STORY_WRITE_FAILED"
    assert store.decision.exists()

    monkeypatch.undo()
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert set(variant.nodes) == {"n1", "n2"}
    assert not store.decision.exists()
    document = store.read().document
    assert document is not None and document.changes
    assert (store.revisions / f"{document.revision}.json").exists()


def test_decided_variant_creation_recovers_after_head_write_failure(
    store: StoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(store, [AddNode(node=node("n1"))], key="one")
    base = store.require_variant(MAIN_VARIANT_ID).revision
    real_write = __import__("hastings.workspace.story_store", fromlist=["x"]).atomic_write_json

    def fail_head(path: Path, value: object, **kwargs: Any) -> None:
        if path == store.file:
            raise OSError("injected head failure")
        real_write(path, value, **kwargs)

    monkeypatch.setattr("hastings.workspace.story_store.atomic_write_json", fail_head)
    with pytest.raises(WorkspaceError):
        store.create_variant(
            name="备选", source_variant_id=MAIN_VARIANT_ID,
            source_revision=base, request_key="create-after-crash",
        )
    monkeypatch.undo()
    receipt = store.create_variant(
        name="备选", source_variant_id=MAIN_VARIANT_ID,
        source_revision=base, request_key="create-after-crash",
    )
    assert store.require_variant(str(receipt.result["variant_id"])).name == "备选"
    assert not store.decision.exists()


def test_decided_recovery_preserves_external_edit(
    store: StoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(store, [AddNode(node=node("n1"))], key="one")
    real_write = __import__("hastings.workspace.story_store", fromlist=["x"]).atomic_write_json

    def fail_head(path: Path, value: object, **kwargs: Any) -> None:
        if path == store.file:
            raise OSError("injected head failure")
        real_write(path, value, **kwargs)

    monkeypatch.setattr("hastings.workspace.story_store.atomic_write_json", fail_head)
    with pytest.raises(WorkspaceError):
        commit(store, [AddNode(node=node("n2"))], key="two")
    monkeypatch.undo()
    external = b"external edit that is not either committed version"
    store.file.write_bytes(external)
    with pytest.raises(WorkspaceError) as caught:
        store.read()
    assert caught.value.code == "STORY_WRITE_CONFLICT"
    assert store.file.read_bytes() == external
    assert store.pending.exists() and store.decision.exists()


def test_recovery_refuses_modified_pending_payload_and_digest(
    store: StoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    commit(store, [AddNode(node=node("n1"))], key="one")
    before = store.file.read_bytes()
    real_write = __import__("hastings.workspace.story_store", fromlist=["x"]).atomic_write_json

    def fail_head(path: Path, value: object, **kwargs: Any) -> None:
        if path == store.file:
            raise OSError("injected head failure")
        real_write(path, value, **kwargs)

    monkeypatch.setattr("hastings.workspace.story_store.atomic_write_json", fail_head)
    with pytest.raises(WorkspaceError):
        commit(store, [AddNode(node=node("n2"))], key="two")
    monkeypatch.undo()
    pending = json.loads(store.pending.read_text(encoding="utf-8"))
    pending["document"]["variants"]["main"]["nodes"]["n2"]["action"] = "外部修改"
    encoded = (json.dumps(pending["document"], ensure_ascii=False, indent=2) + "\n").encode()
    pending["target_digest"] = hashlib.sha256(encoded).hexdigest()
    store.pending.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(WorkspaceError) as caught:
        store.read()
    assert caught.value.code == "STORY_STATE_FAILED"
    assert store.file.read_bytes() == before
    assert store.pending.exists() and store.decision.exists()


def test_unimplemented_joint_change_does_not_report_success(store: StoryStore) -> None:
    commit(store, [AddNode(node=node("n1"))], key="one")
    before = store.file.read_bytes()
    revision = store.require_variant(MAIN_VARIANT_ID).revision
    for extra in (
        {"prose_patches": [ProsePatch(path="正文/a.md", base_digest="abc", start=0, end=1,
                                      expected="甲", replacement="乙")]},
        {"read_dependencies": [ReadDependency(kind="prose", id="正文/a.md", revision="abc")]},
    ):
        with pytest.raises(WorkspaceError) as caught:
            store.commit_change(
                variant_id=MAIN_VARIANT_ID,
                change=change([AddNode(node=node("n2"))], revision, **extra),
                request_key=f"rejected-{len(extra)}",
            )
        assert caught.value.code == "STORY_CHANGE_UNAVAILABLE"
        assert store.file.read_bytes() == before


def test_pause_and_structure_decision_share_a_lock(
    store: StoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(store, [AddNode(node=node("n1"))], key="one")
    revision = store.require_variant(MAIN_VARIANT_ID).revision
    operations = OperationStore(tmp_path / "operation-state")
    operations.create(Operation(
        id="op_test", owner="workspace:one", workspace_id="one", conversation_id="c",
        turn_id="t", purpose="chat", status="running",
    ))
    real_write = __import__("hastings.workspace.story_store", fromlist=["x"]).atomic_write_bytes
    at_decision = threading.Event()
    allow_decision = threading.Event()
    pause_finished = threading.Event()
    failures: list[BaseException] = []

    def wait_before_decision(path: Path, data: bytes, **kwargs: Any) -> None:
        if path == store.decision:
            at_decision.set()
            if not allow_decision.wait(5):
                raise TimeoutError("decision was not released")
        real_write(path, data, **kwargs)

    def submit() -> None:
        try:
            store.commit_change(
                variant_id=MAIN_VARIANT_ID,
                change=change([AddNode(node=node("n2"))], revision,
                              operation_id="op_test", generation=1),
                request_key="protected-commit",
                decision_guard=lambda: operations.eligible_generation("op_test", 1),
            )
        except BaseException as exc:
            failures.append(exc)

    def pause() -> None:
        try:
            operations.update("op_test", lambda operation: setattr(operation, "status", "paused"))
            pause_finished.set()
        except BaseException as exc:
            failures.append(exc)

    monkeypatch.setattr("hastings.workspace.story_store.atomic_write_bytes", wait_before_decision)
    writer = threading.Thread(target=submit)
    pauser = threading.Thread(target=pause)
    writer.start()
    try:
        assert at_decision.wait(5)
        pauser.start()
        assert not pause_finished.wait(0.1)
    finally:
        allow_decision.set()
        writer.join(5)
        if pauser.ident is not None:
            pauser.join(5)
    assert not failures
    assert pause_finished.is_set()
    assert operations.read("op_test").status == "paused"
    assert "n2" in store.require_variant(MAIN_VARIANT_ID).nodes


def test_pause_before_structure_decision_prevents_commit(store: StoryStore, tmp_path: Path) -> None:
    commit(store, [AddNode(node=node("n1"))], key="one")
    revision = store.require_variant(MAIN_VARIANT_ID).revision
    operations = OperationStore(tmp_path / "operation-state")
    operations.create(Operation(
        id="op_test", owner="workspace:one", workspace_id="one", conversation_id="c",
        turn_id="t", purpose="chat", status="paused",
    ))
    with pytest.raises(WorkspaceError) as caught:
        store.commit_change(
            variant_id=MAIN_VARIANT_ID,
            change=change([AddNode(node=node("n2"))], revision,
                          operation_id="op_test", generation=1),
            request_key="revoked-commit",
            decision_guard=lambda: operations.eligible_generation("op_test", 1),
        )
    assert caught.value.code == "OPERATION_PAUSED_CONFLICT"
    assert not store.decision.exists()
    assert set(store.require_variant(MAIN_VARIANT_ID).nodes) == {"n1"}


def test_snapshot_and_log_failure_is_repaired_on_next_read(
    store: StoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_snapshot(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected snapshot failure")

    monkeypatch.setattr(StoryStore, "_write_snapshot", fail_snapshot)
    with pytest.raises(WorkspaceError):
        commit(store, [AddNode(node=node("n1"))], key="one")
    monkeypatch.undo()
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert set(variant.nodes) == {"n1"}
    document = store.read().document
    assert document is not None
    assert (store.revisions / f"{document.revision}.json").exists()
    assert store.log.exists()
    assert len(store.log.read_text(encoding="utf-8").splitlines()) == 1


def test_undo_keeps_later_unrelated_edits(store: StoryStore) -> None:
    first = commit(store, [AddNode(node=node("n1"))], key="one")
    commit(store, [AddNode(node=node("n2"))], key="two")
    receipt = store.undo(change_id=first.change_id, request_key="undo-one")
    assert receipt.kind == "undo"
    assert set(store.require_variant(MAIN_VARIANT_ID).nodes) == {"n2"}
    assert store.read().document is not None
    assert store.read().document.changes[first.change_id].undone_by is not None


def test_undo_conflicts_when_touched_object_changed_later(store: StoryStore) -> None:
    first = commit(store, [AddNode(node=node("n1"))], key="one")
    commit(
        store,
        [UpdateNode(node_id="n1", node=node("n1", title="后来改过"))],
        key="two",
    )
    with pytest.raises(WorkspaceError) as caught:
        store.undo(change_id=first.change_id, request_key="undo-one")
    assert caught.value.code == "STORY_CHANGE_CONFLICT"
    assert "node:n1" in caught.value.details["targets"]


def test_undo_is_rejected_twice(store: StoryStore) -> None:
    first = commit(store, [AddNode(node=node("n1"))], key="one")
    store.undo(change_id=first.change_id, request_key="undo-one")
    with pytest.raises(WorkspaceError) as caught:
        store.undo(change_id=first.change_id, request_key="undo-two")
    assert caught.value.code == "STORY_CHANGE_CONFLICT"


def test_removing_node_cascades_relations_but_refuses_authored_references(
    store: StoryStore,
) -> None:
    commit(
        store,
        [
            AddNode(node=node("a")),
            AddNode(node=node("b")),
            Connect(edge=StoryEdge(id="e1", from_id="a", to_id="b", kind="next")),
            AddClue(
                clue=clue(
                    "c1",
                    resolution=ClueResolution(intent="explain", node_ids=["a"]),
                )
            ),
        ],
        key="base",
    )
    with pytest.raises(WorkspaceError) as caught:
        commit(store, [RemoveNode(node_id="a")], key="remove-with-clue")
    assert caught.value.code == "STORY_GRAPH_INVALID"
    assert "线索回收指向不存在的节点" in caught.value.why

    commit(
        store,
        [RemoveClue(clue_id="c1"), RemoveNode(node_id="a")],
        key="remove-together",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert set(variant.nodes) == {"b"}
    assert not variant.edges


def test_corrupt_structure_is_reported_instead_of_reset(store: StoryStore) -> None:
    commit(store, [AddNode(node=node("n1"))], key="one")
    store.file.write_text("{ broken", encoding="utf-8")
    with pytest.raises(WorkspaceError) as caught:
        store.read()
    assert caught.value.code == "STORY_STATE_FAILED"
    assert store.file.read_text(encoding="utf-8") == "{ broken"


def test_receipts_are_not_reused_across_variants(store: StoryStore) -> None:
    commit(store, [AddNode(node=node("n1"))], key="shared")
    main = store.require_variant(MAIN_VARIANT_ID)
    receipt = store.create_variant(
        name="备选",
        source_variant_id=MAIN_VARIANT_ID,
        source_revision=main.revision,
        request_key="fork",
    )
    with pytest.raises(WorkspaceError) as caught:
        store.commit_change(
            variant_id=receipt.variant_id,
            change=change([AddNode(node=node("n1"))], main.revision),
            request_key="shared",
        )
    assert caught.value.code == "STORY_REQUEST_CONFLICT"


def test_apply_is_pure_and_diff_round_trips() -> None:
    variant = StoryVariant(id="main", name="主方案", revision="rev_1")
    updated = apply_change_set(variant, change([AddNode(node=node("n1"))], variant.revision))
    assert not variant.nodes
    assert set(updated.nodes) == {"n1"}
    inverse = diff_variant(updated, variant)
    restored = apply_change_set(updated, change(inverse, updated.revision))
    assert not restored.nodes
    assert restored.clues == variant.clues
    assert restored.edges == variant.edges


def test_clue_use_requires_existing_clue(store: StoryStore) -> None:
    with pytest.raises(WorkspaceError) as caught:
        commit(
            store,
            [AddNode(node=node("n1", clue_uses=[ClueUse(clue_id="missing", presentation="出现")]))],
            key="bad-use",
        )
    assert caught.value.code == "STORY_GRAPH_INVALID"
    assert "节点引用了不存在的线索" in caught.value.why


def test_change_set_rejects_unknown_operation() -> None:
    with pytest.raises(ValueError):
        StoryChangeSet.model_validate(
            {
                "id": "c1",
                "base_revision": "rev_1",
                "operations": [{"op": "rewrite_everything"}],
            }
        )


def test_read_is_side_effect_free_before_first_write(store: StoryStore) -> None:
    assert store.read().initialized is False
    assert store.read().revision == EMPTY_REVISION
    assert not store.dir.exists()


def test_json_file_is_human_readable(store: StoryStore) -> None:
    commit(store, [AddNode(node=node("n1", title="雨夜的电话"))], key="one")
    raw = json.loads(store.file.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["variants"][MAIN_VARIANT_ID]["nodes"]["n1"]["title"] == "雨夜的电话"
    # 中文不转义、缩进固定：出问题时可以直接看文件，不用先解码。
    assert "雨夜的电话" in store.file.read_text(encoding="utf-8")


def _compete(root: str, revision: str, barrier: Any, queue: Any, number: int) -> None:
    """子进程：同一基线各提交一个节点，只有一个应该成功。"""
    store = StoryStore(Path(root))
    barrier.wait(timeout=30)
    payload = change([AddNode(node=node(f"n{number}"))], revision, id=f"change_{number}")
    try:
        store.commit_change(
            variant_id=MAIN_VARIANT_ID, change=payload, request_key=f"process-{number}"
        )
        queue.put("committed")
    except WorkspaceError as exc:
        queue.put(exc.code)


def test_two_processes_with_the_same_revision_have_exactly_one_commit(
    store: StoryStore,
) -> None:
    commit(store, [AddNode(node=node("base"))], key="base")
    revision = store.require_variant(MAIN_VARIANT_ID).revision
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [
        context.Process(target=_compete, args=(str(store.root), revision, barrier, queue, i))
        for i in (1, 2)
    ]
    try:
        for process in processes:
            process.start()
        results = [queue.get(timeout=60), queue.get(timeout=60)]
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
        assert sorted(results) == ["STORY_REVISION_CONFLICT", "committed"]
        nodes = set(store.require_variant(MAIN_VARIANT_ID).nodes)
        assert "base" in nodes
        assert len(nodes) == 2
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
