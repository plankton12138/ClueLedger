"""1.0 S7：切作品、改名、删除与重启的边界（LC01／LC02）。

这些用例只走真实文件、账本与事件流；模型要么是脚本化的，要么被禁止调用。
迟到响应、重启后自动重跑与断网失败都按「不自动花钱」判定。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.clue_designs import create_clue_identity, save_clue_inference
from hastings.workspace.clue_reviews import (
    ClueReviewDraft,
    ClueReviewFinding,
    clue_review_report,
    freeze_clue_review,
    publish_clue_review,
)
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.files import Workspace
from hastings.workspace.materials import MaterialLedger, SourceRef
from hastings.workspace.review import ReviewEvidence
from hastings.workspace.service import ProviderFactory
from hastings.workspace.state import RecentWorkspaces, conversation_root
from tau_agent.messages import ToolCall

FIRST = "正文/0001 遗书.md"
SECOND = "正文/0002 照片.md"
QUOTE = "林岚说她从未见过这封信。"


def _no_model() -> object:
    raise AssertionError("这个动作不应该调用模型")


def _snapshot(provider: Any) -> ModelSnapshot:
    return ModelSnapshot(
        connection_id="test", display_name="测试连接", model="fake-model", provider=provider
    )


def _wait_finished(client: TestClient, workspace_id: str, conversation_id: str) -> list[dict]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        events = client.get(
            f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
        ).json()["events"]
        if any(event["type"] == "run_finished" for event in events):
            return events
        time.sleep(0.05)
    raise AssertionError("轮次没有结束")


def _book(root: Path) -> Workspace:
    workspace = Workspace.create(root)
    workspace.write(FIRST, "# 遗书\n\n信封背面有字。\n", expected_digest=None)
    workspace.write(
        SECOND, f"# 照片\n\n照片证明林岚看过背面。\n\n{QUOTE}\n", expected_digest=None
    )
    return workspace


def _seed(workspace: Workspace, store: ClueLedgerStore) -> tuple[dict[str, Any], str]:
    """建一条线索、一个推理目标与一份已发布报告；返回目标与报告标识。"""
    clue_id = str(
        create_clue_identity(
            store,
            title="公开的照片",
            request_key="seed-clue",
            expected_revision=store.read().revision,
        )["clue_id"]
    )
    view = workspace.read(SECOND)
    linked = (
        save_clue_inference(
            store,
            clue_ids=[clue_id],
            goal="林岚提前接触过遗书原件",
            intent="deduce",
            premises=[],
            alternatives=[],
            design_link_ids=[],
            request_key="seed-inference",
            expected_revision=store.read().revision,
            created_by="author",
        )
    )
    scope = freeze_clue_review(
        workspace,
        store,
        inference_id=str(linked["inference_id"]),
        inference_revision=str(linked["inference_revision"]),
        clue_ids=[clue_id],
        as_of_chapter=2,
        chapter_paths=[SECOND],
    )
    ledger = MaterialLedger()
    ledger.record_read(
        SourceRef(SECOND, view.digest), length=len(view.text), start=0, end=len(view.text)
    )
    start = view.text.index(QUOTE)
    report = publish_clue_review(
        scope,
        ClueReviewDraft(
            status="findings",
            findings=(
                ClueReviewFinding(
                    kind="counter_evidence",
                    certainty="supported",
                    description="林岚否认见过信，但照片显示她看过背面。",
                    suggestion="补一句她当时是否记得这封信。",
                    evidence=(
                        ReviewEvidence(
                            path=SECOND,
                            digest=view.digest,
                            start=start,
                            end=start + len(QUOTE),
                            quote=QUOTE,
                        ),
                    ),
                ),
            ),
            uncovered=(),
        ),
        requirement="核对这组线索。",
        workspace=workspace,
        ledger=ledger,
        store=store,
        turn_id="seed-turn",
    )
    return (
        {
            "inference_id": str(linked["inference_id"]),
            "inference_revision": str(linked["inference_revision"]),
            "clue_id": clue_id,
        },
        report.report_id,
    )


@pytest.fixture
def registry(tmp_path: Path) -> RecentWorkspaces:
    return RecentWorkspaces(tmp_path / "state")


def test_rename_keeps_clue_lineage_and_report(registry: RecentWorkspaces, tmp_path: Path) -> None:
    client = TestClient(
        create_workspace_app(workspaces=registry, provider_factory=_no_model)  # type: ignore[arg-type]
    )
    root = tmp_path / "书架" / "旧名"
    root.parent.mkdir()
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    workspace_id = str(created["id"])
    workspace = _book(root)
    store = ClueLedgerStore(conversation_root(registry.root, workspace_id))
    inference, report_id = _seed(workspace, store)
    lineage = store.read().ledger
    assert lineage is not None
    before = lineage.workspace_lineage

    renamed = client.patch(f"/api/file-workspaces/{workspace_id}", json={"name": "新名"})
    assert renamed.status_code == 200, renamed.text
    new_id = str(renamed.json()["workspace"]["id"])
    assert new_id != workspace_id
    assert not conversation_root(registry.root, workspace_id).exists()

    moved = ClueLedgerStore(conversation_root(registry.root, new_id)).read()
    assert moved.ledger is not None
    # 改名只换登记与目录，不换线索身份。
    assert moved.ledger.workspace_lineage == before
    report = clue_review_report(
        Workspace(Path(str(renamed.json()["workspace"]["path"]))),
        ClueLedgerStore(conversation_root(registry.root, new_id)),
        report_id,
    )
    assert report.freshness == "valid"
    assert report.findings[0]["evidence"][0]["quote"] == QUOTE
    assert client.get(f"/api/file-workspaces/{workspace_id}/clue-ledger").status_code == 404


def test_delete_removes_clue_state_and_read_does_not_recreate(
    registry: RecentWorkspaces, tmp_path: Path
) -> None:
    client = TestClient(
        create_workspace_app(workspaces=registry, provider_factory=_no_model)  # type: ignore[arg-type]
    )
    root = tmp_path / "雪山旅馆"
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    workspace_id = str(created["id"])
    workspace = _book(root)
    store = ClueLedgerStore(conversation_root(registry.root, workspace_id))
    _seed(workspace, store)

    removed = client.request(
        "DELETE", f"/api/file-workspaces/{workspace_id}", json={"confirmed_path": str(root)}
    )
    assert removed.status_code == 200, removed.text
    assert not store.root.exists()

    # 删除之后任何读取都不能把状态目录重新建出来，更不能写回空账本。
    gone = ClueLedgerStore(store.root).read()
    assert gone.ledger is None
    assert not store.file.exists()
    assert not store.root.exists()
    assert client.get(f"/api/file-workspaces/{workspace_id}/conversations").status_code == 404


def test_rename_and_delete_reject_running_turn(
    registry: RecentWorkspaces, tmp_path: Path
) -> None:
    client = TestClient(
        create_workspace_app(workspaces=registry, provider_factory=_no_model)  # type: ignore[arg-type]
    )
    root = tmp_path / "雪山旅馆"
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    workspace_id = str(created["id"])
    _book(root)
    client.app.state.run_registry.mark(root, "t1")  # type: ignore[attr-defined]

    renamed = client.patch(f"/api/file-workspaces/{workspace_id}", json={"name": "新名"})
    assert renamed.status_code == 409
    assert renamed.json()["error"]["code"] == "WORKSPACE_BUSY_CONFLICT"
    removed = client.request(
        "DELETE", f"/api/file-workspaces/{workspace_id}", json={"confirmed_path": str(root)}
    )
    assert removed.status_code == 409
    assert root.exists()
    assert client.get(f"/api/file-workspaces/{workspace_id}/files").status_code == 200


@pytest.fixture
def clients(registry: RecentWorkspaces) -> Iterator[Callable[[ProviderFactory], TestClient]]:
    opened: list[TestClient] = []

    def build(factory: ProviderFactory) -> TestClient:
        client = TestClient(
            create_workspace_app(workspaces=registry, provider_factory=factory)
        )
        client.__enter__()
        opened.append(client)
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


def test_restart_keeps_report_and_does_not_rerun(
    registry: RecentWorkspaces, tmp_path: Path, clients: Callable[[ProviderFactory], TestClient]
) -> None:
    from tests.workspace.test_chat import ScriptedProvider, _text_turn

    root = tmp_path / "遗书"
    provider = ScriptedProvider([_text_turn("先登记线索。")])
    client = clients(lambda: _snapshot(provider))
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    workspace_id = str(created["id"])
    workspace = _book(root)
    store = ClueLedgerStore(conversation_root(registry.root, workspace_id))
    _, report_id = _seed(workspace, store)

    conversation = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "先记一下。", "request_key": "restart-1"},
    )
    assert conversation.status_code == 202, conversation.text
    conversation_id = str(conversation.json()["conversation_id"])
    _wait_finished(client, workspace_id, conversation_id)
    assert provider.calls == 1

    # 关掉这个应用实例，用同一份状态重新开一个；模型被禁止调用。
    client.__exit__(None, None, None)
    restarted = clients(_no_model)  # type: ignore[arg-type]
    events = restarted.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
    ).json()["events"]
    runs = [event for event in events if event["type"] == "run_finished"]
    assert len(runs) == 1
    read = restarted.get(f"/api/file-workspaces/{workspace_id}/clue-reviews/{report_id}")
    assert read.status_code == 200, read.text
    assert read.json()["freshness"] == "valid"
    assert read.json()["findings"][0]["evidence"][0]["quote"] == QUOTE


def test_provider_failure_keeps_published_report_and_does_not_retry(
    registry: RecentWorkspaces, tmp_path: Path, clients: Callable[[ProviderFactory], TestClient]
) -> None:
    class BrokenProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream_response(self, **_kwargs: Any) -> Any:
            self.calls += 1
            raise ConnectionError("连接被重置")

    root = tmp_path / "遗书"
    provider = BrokenProvider()
    client = clients(lambda: _snapshot(provider))
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    workspace_id = str(created["id"])
    workspace = _book(root)
    store = ClueLedgerStore(conversation_root(registry.root, workspace_id))
    _, report_id = _seed(workspace, store)

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "继续写第二章。", "request_key": "offline-1"},
    )
    assert response.status_code == 202, response.text
    events = _wait_finished(client, workspace_id, response.json()["conversation_id"])
    assert events[-1]["status"] == "failed"
    # 断网不是重试的理由：同一轮不自动再调一次模型。
    assert provider.calls == 1
    read = client.get(f"/api/file-workspaces/{workspace_id}/clue-reviews/{report_id}")
    assert read.status_code == 200
    assert read.json()["freshness"] == "valid"


def test_stop_does_not_publish_or_retry(
    registry: RecentWorkspaces, tmp_path: Path, clients: Callable[[ProviderFactory], TestClient]
) -> None:
    from tests.workspace.test_chat import ScriptedProvider, _tool_turn

    root = tmp_path / "遗书"
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(id="v1", name="save_review_report", arguments={"target_paths": [SECOND]})
            )
        ],
        hang_from=0,
    )
    client = clients(lambda: _snapshot(provider))
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    workspace_id = str(created["id"])
    workspace = _book(root)
    store = ClueLedgerStore(conversation_root(registry.root, workspace_id))
    inference, report_id = _seed(workspace, store)
    before = store.read().ledger
    assert before is not None and len(before.reviews) == 1

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "核对这组线索。",
            "request_key": "stop-review",
            "purpose": "review",
            "review_paths": [SECOND],
            "context": {"state_chapter": 2, "selected_path": SECOND},
            "clue_review": {
                "inference_id": inference["inference_id"],
                "inference_revision": inference["inference_revision"],
                "clue_ids": [inference["clue_id"]],
                "as_of_chapter": 2,
            },
        },
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    stopped = client.post(
        f"/api/operations/{accepted['operation_id']}/pause", json={"request_key": "pause-1"}
    )
    assert stopped.status_code == 200
    events = _wait_finished(client, workspace_id, response.json()["conversation_id"])
    assert events[-1]["status"] == "stopped"
    # 停止之后不再补发请求，也不伪造报告：调用数停在停下那一刻，不再增长。
    calls_at_stop = provider.calls
    time.sleep(0.3)
    assert provider.calls == calls_at_stop
    after = store.read().ledger
    assert after is not None
    assert list(after.reviews) == [report_id]
    assert clue_review_report(workspace, store, report_id).freshness == "valid"
