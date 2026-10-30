"""1.0 S6：对话引用线索；正文依据与作者计划分开，版本变化按冲突处理。"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.clue_context import (
    ClueReferenceInput,
    clue_references_section,
    freeze_clue_references,
)
from hastings.workspace.clue_models import ClueCoverage, ClueEntry
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clues import create_clue_identity
from hastings.workspace.compaction import CompactionRecord, SummaryItem, summary_text
from hastings.workspace.context import materials_section
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.materials import ContextRequest, freeze_context
from hastings.workspace.service import ProviderFactory
from hastings.workspace.state import RecentWorkspaces, conversation_root

FIRST = "正文/0001 遗书.md"
SECOND = "正文/0002 照片.md"
PLAN = "设定集/线索.md"
QUOTE = "照片证明她看过背面。"
SECOND_TEXT = f"# 照片\n\n林岚说她没看过信封。\n\n{QUOTE}\n"


@pytest.fixture
def book(tmp_path: Path) -> tuple[Workspace, ClueLedgerStore, dict[str, Any]]:
    workspace = Workspace.create(tmp_path / "书")
    workspace.write(FIRST, "# 遗书\n\n信封背面有字。\n", expected_digest=None)
    workspace.write(SECOND, SECOND_TEXT, expected_digest=None)
    workspace.write(PLAN, "# 线索\n\n原计划：第二章让照片出现。\n", expected_digest=None)
    store = ClueLedgerStore(tmp_path / "state")
    clue_id = str(
        create_clue_identity(
            store,
            title="照片里的时间",
            request_key="ref-create",
            expected_revision=store.read().revision,
        )["clue_id"]
    )
    view = workspace.read(SECOND)
    start = view.text.index(QUOTE)
    proof = ObservedEvidence(SECOND, view.digest, start, start + len(QUOTE), QUOTE)
    store.publish(
        (
            ClueEntry(
                "照片里的时间",
                2,
                None,
                "open",
                "作者推测照片拍于当晚",
                proof,
                source_chapter=2,
                clue_id=clue_id,
                mode="testimony",
            ),
        ),
        source_chapter=2,
        expected_revision=store.read().revision,
        request_key="ref-publish",
        coverage=ClueCoverage(
            path=SECOND,
            digest=view.digest,
            chapter=2,
            outcome="found",
            task_id="ref",
            at="2026-10-28T10:00:00+08:00",
        ),
    )
    ledger = store.read().ledger
    assert ledger is not None
    return workspace, store, {
        "clue_id": clue_id,
        "clue_revision": ledger.clues[clue_id].clue_revision,
        "lineage": ledger.workspace_lineage,
    }


def _reference(book: tuple[Workspace, ClueLedgerStore, dict[str, Any]], **overrides: Any):
    _, _, state = book
    values: dict[str, Any] = {
        "workspace_lineage": state["lineage"],
        "clue_id": state["clue_id"],
        "clue_revision": state["clue_revision"],
        "browse_as_of_chapter": 2,
    }
    values.update(overrides)
    return ClueReferenceInput(**values)


def _code(info: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return info.value.code


def test_reference_freezes_reader_quotes(book):
    workspace, store, state = book
    (snapshot,) = freeze_clue_references(
        workspace, store.root, (_reference(book),), writer_cutoff=3
    )
    assert snapshot.clue_title == "照片里的时间"
    assert snapshot.reader_cutoff == 2
    assert [row["quote"] for row in snapshot.reader] == [QUOTE]
    assert snapshot.reader[0]["usable"] is True
    assert snapshot.reader[0]["mode"] == "testimony"

    section = clue_references_section((snapshot,))
    assert "读者已见" in section and QUOTE in section
    assert "引用不授予任何写入权限" in section


def test_reference_clips_browse_chapter_to_writer_cutoff(book):
    workspace, store, _ = book
    reference = _reference(book, browse_as_of_chapter=3)
    (snapshot,) = freeze_clue_references(workspace, store.root, (reference,), writer_cutoff=2)
    assert snapshot.browse_as_of_chapter == 3
    assert snapshot.reader_cutoff == 2
    section = clue_references_section((snapshot,))
    assert "读者已见" in section


def test_reference_rejects_other_workspace_and_stale_revision(book):
    workspace, store, _ = book
    with pytest.raises(WorkspaceError) as denied:
        freeze_clue_references(
            workspace,
            store.root,
            (_reference(book, workspace_lineage="ws_other"),),
            writer_cutoff=2,
        )
    assert _code(denied) == "CLUE_ACCESS_DENIED"

    with pytest.raises(WorkspaceError) as stale:
        freeze_clue_references(
            workspace,
            store.root,
            (_reference(book, clue_revision="rev_old"),),
            writer_cutoff=2,
        )
    assert _code(stale) == "CLUE_REFERENCE_STALE_CONFLICT"

    with pytest.raises(WorkspaceError) as missing:
        freeze_clue_references(
            workspace,
            store.root,
            (_reference(book, clue_id="clue_missing"),),
            writer_cutoff=2,
        )
    assert _code(missing) == "CLUE_NOT_FOUND"

    deduped = freeze_clue_references(
        workspace, store.root, (_reference(book), _reference(book)), writer_cutoff=2
    )
    assert len(deduped) == 1


def test_context_snapshot_carries_reference_identity_and_renders_section(book):
    workspace, store, _ = book
    request = ContextRequest(
        selected_path=SECOND,
        state_chapter=2,
        reference_paths=(FIRST,),
        clue_references=(_reference(book),),
    )
    frozen = freeze_context(workspace, request, state_root=store.root)
    identity = frozen.identity()
    assert identity["clue_references"][0]["clue_id"] == request.clue_references[0].clue_id
    assert identity["clue_references"][0]["browse_as_of_chapter"] == 2
    assert frozen.to_dict()["clues"][0]["reader"][0]["quote"] == QUOTE
    section = materials_section(frozen, ())
    assert "本轮线索引用" in section
    assert QUOTE in section


def test_compaction_record_keeps_reference_identity(book):
    record = CompactionRecord(
        compaction_id="cmp_1",
        kind="session",
        source_head_seq=4,
        covered_through_seq=4,
        first_kept_turn_id="t2",
        source_digest="digest",
        previous_id="",
        summary="讨论照片时间",
        items=(
            SummaryItem(
                item_id="i1", kind="goal", text="确认照片时间", source_event_ids=("t1",)
            ),
        ),
        clue_references=(
            {
                "clue_id": "clue_1",
                "clue_revision": "rev_1",
                "browse_as_of_chapter": 2,
                "inference_id": "inference_1",
                "inference_revision": "rev_2",
                "report_id": "cr_1",
            },
        ),
    )
    restored = CompactionRecord.from_dict(record.as_dict())
    assert restored is not None
    assert restored.clue_references[0]["clue_revision"] == "rev_1"
    text = summary_text(restored.summary, restored.items, restored.clue_references)
    assert "`clue_1`@rev_1" in text
    assert "第 2 章截止" in text
    assert "不是原文依据" in text


def test_compaction_keeps_references_from_covered_turns_only():
    from hastings.workspace.service import _covered_clue_references

    reference = {
        "workspace_lineage": "ws",
        "clue_id": "clue_1",
        "clue_revision": "rev_1",
        "browse_as_of_chapter": 2,
        "inference_id": "inference_1",
        "inference_revision": "rev_2",
    }
    events = [
        {"type": "turn_started", "turn_id": "t1", "context": {"clue_references": [reference]}},
        {"type": "turn_started", "turn_id": "t2", "context": {"clue_references": [reference]}},
        {"type": "turn_started", "turn_id": "t3", "context": {}},
    ]
    rows = _covered_clue_references(events, frozenset({"t1", "t3"}))
    assert rows == (
        {
            "clue_id": "clue_1",
            "clue_revision": "rev_1",
            "browse_as_of_chapter": 2,
            "inference_id": "inference_1",
            "inference_revision": "rev_2",
        },
    )
    assert _covered_clue_references(events, frozenset()) == ()


def _client(tmp_path: Path) -> tuple[TestClient, str, Path]:
    registry = RecentWorkspaces(tmp_path / "app-state")

    def no_model() -> object:
        raise AssertionError("引用校验不应调用模型")

    client = TestClient(
        create_workspace_app(workspaces=registry, provider_factory=no_model)  # type: ignore[arg-type]
    )
    created = client.post(
        "/api/file-workspaces", json={"path": str(tmp_path / "书"), "create": False}
    )
    assert created.status_code == 200, created.text
    workspace_id = str(created.json()["id"])
    return client, workspace_id, conversation_root(tmp_path / "app-state", workspace_id)


def test_http_reference_validation_and_draft_round_trip(tmp_path: Path, book):
    workspace, store, state = book
    client, workspace_id, root = _client(tmp_path)
    # 接口读取的是应用状态目录，把真实账本放在那里再引用。
    store = ClueLedgerStore(root)
    _seed(root, workspace)
    ledger = store.read().ledger
    assert ledger is not None
    clue_id = next(iter(ledger.clues))
    revision = ledger.clues[clue_id].clue_revision
    base = f"/api/file-workspaces/{workspace_id}"
    body = {
        "text": "看看这条线索。",
        "request_key": "ref-http",
        "context": {
            "clue_references": [
                {
                    "workspace_lineage": ledger.workspace_lineage,
                    "clue_id": clue_id,
                    "clue_revision": revision,
                    "browse_as_of_chapter": 2,
                }
            ]
        },
    }
    stale_ref = {**body["context"]["clue_references"][0], "clue_revision": "rev_old"}
    stale = client.post(
        f"{base}/conversations",
        json={**body, "context": {"clue_references": [stale_ref]}},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CLUE_REFERENCE_STALE_CONFLICT"

    draft = client.patch(
        f"{base}/conversation-ui",
        json={
            "set_draft_context": True,
            "draft_target": "new",
            "draft_clue_references": [
                {
                    "workspace_lineage": ledger.workspace_lineage,
                    "clue_id": clue_id,
                    "clue_revision": revision,
                    "browse_as_of_chapter": 2,
                    "title": "照片里的时间",
                }
            ],
        },
    )
    assert draft.status_code == 200, draft.text
    reference = draft.json()["draft_contexts"]["new"]["clue_references"][0]
    assert reference["clue_id"] == clue_id
    assert reference["title"] == "照片里的时间"
    read = client.get(f"{base}/conversation-ui").json()
    assert read["draft_contexts"]["new"]["clue_references"][0]["browse_as_of_chapter"] == 2


def _seed(root: Path, workspace: Workspace) -> None:
    """在应用状态目录里建立与作品一致的线索账本。"""
    store = ClueLedgerStore(root)
    clue_id = str(
        create_clue_identity(
            store,
            title="照片里的时间",
            request_key="http-create",
            expected_revision=store.read().revision,
        )["clue_id"]
    )
    view = workspace.read(SECOND)
    start = view.text.index(QUOTE)
    proof = ObservedEvidence(SECOND, view.digest, start, start + len(QUOTE), QUOTE)
    store.publish(
        (
            ClueEntry(
                "照片里的时间",
                2,
                None,
                "open",
                "",
                proof,
                source_chapter=2,
                clue_id=clue_id,
                mode="narration",
            ),
        ),
        source_chapter=2,
        expected_revision=store.read().revision,
        request_key="http-publish",
        coverage=ClueCoverage(
            path=SECOND,
            digest=view.digest,
            chapter=2,
            outcome="found",
            task_id="http",
            at="2026-10-28T10:00:00+08:00",
        ),
    )


def _snapshot(provider: Any) -> ModelSnapshot:
    return ModelSnapshot(
        connection_id="test", display_name="测试连接", model="fake-model", provider=provider
    )


@pytest.fixture
def live_client(tmp_path: Path) -> Iterator[Callable[[ProviderFactory], TestClient]]:
    opened: list[TestClient] = []

    def build(factory: ProviderFactory) -> TestClient:
        client = TestClient(
            create_workspace_app(
                workspaces=RecentWorkspaces(tmp_path / "live-state"), provider_factory=factory
            )
        )
        client.__enter__()
        opened.append(client)
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


def _wait_finished(client: TestClient, workspace_id: str, conversation_id: str) -> list[dict]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        events = client.get(
            f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
        ).json()["events"]
        if any(event["type"] == "run_finished" for event in events):
            return events
        time.sleep(0.05)
    raise AssertionError("引用轮次没有结束")


def test_turn_with_reference_sends_reader_section(
    tmp_path: Path, live_client: Callable[[ProviderFactory], TestClient]
) -> None:
    from tests.workspace.test_chat import ScriptedProvider, _text_turn

    workspace = Workspace.create(tmp_path / "书")
    workspace.write(FIRST, "# 遗书\n\n信封背面有字。\n", expected_digest=None)
    workspace.write(SECOND, SECOND_TEXT, expected_digest=None)
    workspace.write(PLAN, "# 线索\n\n原计划：第二章让照片出现。\n", expected_digest=None)
    registry = RecentWorkspaces(tmp_path / "live-state")
    provider = ScriptedProvider([_text_turn("看到了。")])
    client = live_client(lambda: _snapshot(provider))
    created = client.post(
        "/api/file-workspaces", json={"path": str(workspace.root), "create": False}
    )
    workspace_id = str(created.json()["id"])
    root = conversation_root(registry.root, workspace_id)
    _seed(root, workspace)
    ledger = ClueLedgerStore(root).read().ledger
    assert ledger is not None
    clue_id = next(iter(ledger.clues))
    reference = {
        "workspace_lineage": ledger.workspace_lineage,
        "clue_id": clue_id,
        "clue_revision": ledger.clues[clue_id].clue_revision,
        "browse_as_of_chapter": 2,
    }
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "看看这条线索。",
            "request_key": "ref-turn",
            "context": {"state_chapter": 2, "clue_references": [reference]},
        },
    )
    assert response.status_code == 202, response.text
    conversation_id = response.json()["conversation_id"]
    _wait_finished(client, workspace_id, conversation_id)
    system = provider.requests[0]["system"]
    assert "本轮线索引用" in system
    assert QUOTE in system
    assert "引用不授予任何写入权限" in system
    events = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
    ).json()["events"]
    started = next(event for event in events if event["type"] == "turn_started")
    assert started["context"]["clue_references"][0]["clue_id"] == clue_id

    changed = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "看看这条线索。",
            "request_key": "ref-turn",
            "context": {
                "state_chapter": 2,
                "clue_references": [{**reference, "clue_revision": "rev_other"}],
            },
        },
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "REQUEST_KEY_CONFLICT"
