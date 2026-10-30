"""1.0 S7：切作品、改名、删除与重启的边界（LC01／LC02）。

这些用例只走真实文件、账本与事件流；模型要么是脚本化的，要么被禁止调用。
迟到响应、重启后自动重跑与断网失败都按「不自动花钱」判定。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clues import create_clue_identity
from hastings.workspace.files import Workspace
from hastings.workspace.state import RecentWorkspaces, conversation_root

FIRST = "正文/0001 遗书.md"
SECOND = "正文/0002 照片.md"


def _no_model() -> object:
    raise AssertionError("这个动作不应该调用模型")


def _book(root: Path) -> Workspace:
    workspace = Workspace.create(root)
    workspace.write(FIRST, "# 遗书\n\n信封背面有字。\n", expected_digest=None)
    workspace.write(SECOND, "# 照片\n\n照片证明林岚看过背面。\n", expected_digest=None)
    return workspace


def _seed(workspace: Workspace, store: ClueLedgerStore) -> str:
    """建一条线索；返回线索标识。"""
    return str(
        create_clue_identity(
            store,
            title="公开的照片",
            request_key="seed-clue",
            expected_revision=store.read().revision,
        )["clue_id"]
    )


@pytest.fixture
def registry(tmp_path: Path) -> RecentWorkspaces:
    return RecentWorkspaces(tmp_path / "state")


def test_rename_keeps_clue_lineage(registry: RecentWorkspaces, tmp_path: Path) -> None:
    client = TestClient(
        create_workspace_app(workspaces=registry, provider_factory=_no_model)  # type: ignore[arg-type]
    )
    root = tmp_path / "书架" / "旧名"
    root.parent.mkdir()
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    workspace_id = str(created["id"])
    workspace = _book(root)
    store = ClueLedgerStore(conversation_root(registry.root, workspace_id))
    _seed(workspace, store)
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
