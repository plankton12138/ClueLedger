"""v1.0.7 P5：三状态读模型、提醒暂缓与「创作差异不阻止保存」。

这里的用例只判断应用能确定的事实：关联是否可定位、构思或正文是否变化、作者是否暂缓过
同一份提醒。语义是否真的冲突不在这里判断。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.files import Workspace
from hastings.workspace.state import RecentWorkspaces, conversation_root
from hastings.workspace.story_models import (
    AddNode,
    BindProse,
    ProseBinding,
    StoryChangeSet,
    StoryNode,
)
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore
from hastings.workspace.story_sync import contract_digest


class _NoModel:
    def stream_response(self, **_: Any) -> Any:
        raise AssertionError("三状态读模型不应调用模型")


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    book = RecentWorkspaces(tmp_path / "state")
    app = create_workspace_app(
        workspaces=book,
        provider_factory=lambda: ModelSnapshot(
            connection_id="test", display_name="测试连接", model="fake", provider=_NoModel()
        ),
    )
    with TestClient(app) as opened:
        yield opened


def open_workspace(client: TestClient, tmp_path: Path) -> str:
    root = tmp_path / "book"
    response = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": not root.exists()}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def write_chapter(tmp_path: Path, text: str) -> tuple[str, str, str]:
    path = "正文/0001 雨夜.md"
    target = tmp_path / "book" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    view = Workspace(tmp_path / "book").read(path)
    return path, view.digest, view.text


def store_for(tmp_path: Path, workspace_id: str) -> StoryStore:
    return StoryStore(
        conversation_root(tmp_path / "state", workspace_id), Workspace(tmp_path / "book")
    )


def seed(
    tmp_path: Path,
    workspace_id: str,
    *,
    node: dict[str, Any],
    quote: str,
    path: str = "正文/0001 雨夜.md",
) -> StoryStore:
    store = store_for(tmp_path, workspace_id)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(
            id="seed-node",
            base_revision="empty",
            operations=[AddNode(node=StoryNode.model_validate(node))],
        ),
        request_key="seed-node",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    view = Workspace(tmp_path / "book").read(path)
    start = view.text.find(quote)
    assert start >= 0
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(
            id="seed-binding",
            base_revision=variant.revision,
            operations=[
                BindProse(
                    binding=ProseBinding(
                        id="b1",
                        node_id=str(node["id"]),
                        document_id="doc_" + hashlib.sha256(path.encode()).hexdigest()[:24],
                        path=path,
                        digest=view.digest,
                        span_digest=hashlib.sha256(quote.encode()).hexdigest(),
                        start=start,
                        end=start + len(quote),
                        quote=quote,
                        contract_digest=contract_digest(variant, variant.nodes[str(node["id"])]),
                    )
                )
            ],
        ),
        request_key="seed-binding",
    )
    return store


def attention(client: TestClient, workspace_id: str, **params: Any) -> dict[str, Any]:
    response = client.get(
        f"/api/file-workspaces/{workspace_id}/story/variants/{MAIN_VARIANT_ID}/sync",
        params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


def node_row(body: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(row for row in body["nodes"] if row["node_id"] == node_id)


def update_action(client: TestClient, workspace_id: str, store: StoryStore, action: str) -> None:
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json={
            "variant_id": MAIN_VARIANT_ID,
            "expected_revision": store.require_variant(MAIN_VARIANT_ID).revision,
            "request_key": action,
            "operations": [
                {
                    "op": "update_node",
                    "node_id": "n1",
                    "node": {"id": "n1", "title": "发现异样", "action": action},
                }
            ],
        },
    )
    assert response.status_code == 201, response.text


def acknowledge(client: TestClient, workspace_id: str, store: StoryStore, fingerprint: str) -> None:
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json={
            "variant_id": MAIN_VARIANT_ID,
            "expected_revision": store.require_variant(MAIN_VARIANT_ID).revision,
            "request_key": f"defer-{fingerprint[:8]}",
            "operations": [
                {
                    "op": "acknowledge_attention",
                    "node_id": "n1",
                    "fingerprint": fingerprint,
                }
            ],
        },
    )
    assert response.status_code == 201, response.text


def test_attention_view_reports_ready_draft_and_attention(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    store = seed(
        tmp_path,
        workspace_id,
        node={"id": "n1", "title": "发现异样", "action": "木板没有响"},
        quote="有人经过时木板没有响",
    )
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(
            id="second-node",
            base_revision=store.require_variant(MAIN_VARIANT_ID).revision,
            operations=[AddNode(node=StoryNode(id="n2", title="未写节点", action="还没写"))],
        ),
        request_key="second-node",
    )

    body = attention(client, workspace_id)

    assert node_row(body, "n1")["status"] == "ready"
    assert node_row(body, "n1")["reasons"] == []
    assert node_row(body, "n2")["status"] == "draft"
    assert body["counts"]["ready"] == 1
    assert body["counts"]["draft"] == 1
    assert node_row(body, "n1")["reminder_visible"] is True
    assert node_row(body, "n1")["fingerprint"]


def test_plan_change_saves_and_leaves_an_alert(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    store = seed(
        tmp_path,
        workspace_id,
        node={"id": "n1", "title": "发现异样", "action": "木板没有响"},
        quote="有人经过时木板没有响",
    )
    before = node_row(attention(client, workspace_id), "n1")

    update_action(client, workspace_id, store, "侦探确认有人走过")

    after = node_row(attention(client, workspace_id), "n1")
    assert after["status"] == "attention"
    assert "plan_changed" in after["reasons"]
    assert after["reminder_visible"] is True
    assert after["fingerprint"] != before["fingerprint"]


def test_acknowledging_keeps_attention_but_hides_the_reminder(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    store = seed(
        tmp_path,
        workspace_id,
        node={"id": "n1", "title": "发现异样", "action": "木板没有响"},
        quote="有人经过时木板没有响",
    )
    update_action(client, workspace_id, store, "侦探确认有人走过")
    current = node_row(attention(client, workspace_id), "n1")
    acknowledge(client, workspace_id, store, current["fingerprint"])

    deferred = attention(client, workspace_id)
    row = node_row(deferred, "n1")
    assert row["status"] == "attention"
    assert row["reminder_visible"] is False
    assert deferred["counts"]["deferred"] == 1


def test_title_change_keeps_the_deferred_reminder_hidden(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    store = seed(
        tmp_path,
        workspace_id,
        node={"id": "n1", "title": "发现异样", "action": "木板没有响"},
        quote="有人经过时木板没有响",
    )
    update_action(client, workspace_id, store, "侦探确认有人走过")
    current = node_row(attention(client, workspace_id), "n1")
    acknowledge(client, workspace_id, store, current["fingerprint"])
    renamed = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json={
            "variant_id": MAIN_VARIANT_ID,
            "expected_revision": store.require_variant(MAIN_VARIANT_ID).revision,
            "request_key": "rename",
            "operations": [
                {
                    "op": "update_node",
                    "node_id": "n1",
                    "node": {"id": "n1", "title": "改了标题", "action": "侦探确认有人走过"},
                }
            ],
        },
    )
    assert renamed.status_code == 201, renamed.text

    row = node_row(attention(client, workspace_id), "n1")
    assert row["status"] == "attention"
    assert row["reminder_visible"] is False


def test_relevant_change_brings_the_reminder_back(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    store = seed(
        tmp_path,
        workspace_id,
        node={"id": "n1", "title": "发现异样", "action": "木板没有响"},
        quote="有人经过时木板没有响",
    )
    update_action(client, workspace_id, store, "侦探确认有人走过")
    current = node_row(attention(client, workspace_id), "n1")
    acknowledge(client, workspace_id, store, current["fingerprint"])

    target = tmp_path / "book" / "正文/0001 雨夜.md"
    target.write_text("# 第一章\n\n有人经过时地板没有响。\n", encoding="utf-8")

    row = node_row(attention(client, workspace_id), "n1")
    assert row["status"] == "attention"
    assert "anchor_missing" in row["reasons"]
    assert row["reminder_visible"] is True


def test_stale_prose_patch_is_still_rejected(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, _ = write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    store = seed(
        tmp_path,
        workspace_id,
        node={"id": "n1", "title": "发现异样", "action": "木板没有响"},
        quote="有人经过时木板没有响",
    )
    rejected = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json={
            "variant_id": MAIN_VARIANT_ID,
            "expected_revision": store.require_variant(MAIN_VARIANT_ID).revision,
            "request_key": "stale-patch",
            "prose_patches": [
                {
                    "path": path,
                    "base_digest": "0" * 64,
                    "start": 0,
                    "end": 2,
                    "replacement": "夜",
                    "expected": "# ",
                }
            ],
        },
    )
    assert rejected.status_code != 201, rejected.text


def test_attention_view_can_be_scoped_to_one_chapter(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, _ = write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    store = seed(
        tmp_path,
        workspace_id,
        node={"id": "n1", "title": "发现异样", "action": "木板没有响"},
        quote="有人经过时木板没有响",
    )
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(
            id="other-node",
            base_revision=store.require_variant(MAIN_VARIANT_ID).revision,
            operations=[AddNode(node=StoryNode(id="n2", title="别的章", action="还没写"))],
        ),
        request_key="other-node",
    )

    scoped = attention(client, workspace_id, chapter_path=path)

    assert [row["node_id"] for row in scoped["nodes"]] == ["n1"]


def test_opening_a_discussion_with_a_stale_fingerprint_is_rejected(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, _ = write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    store = seed(
        tmp_path,
        workspace_id,
        node={"id": "n1", "title": "发现异样", "action": "木板没有响"},
        quote="有人经过时木板没有响",
    )
    update_action(client, workspace_id, store, "侦探确认有人走过")
    variant = store.require_variant(MAIN_VARIANT_ID)
    rejected = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "请核对这条提醒",
            "request_key": "reconcile-1",
            "purpose": "review",
            "review_paths": [path],
            "context": {
                "selected_path": path,
                "reference_paths": [path],
                "story_reconcile": {
                    "variant_id": MAIN_VARIANT_ID,
                    "variant_revision": variant.revision,
                    "node_id": "n1",
                    "binding_ids": ["b1"],
                    "warning_fingerprint": "0" * 64,
                },
            },
        },
    )
    assert rejected.status_code >= 400, rejected.text
    assert rejected.json()["error"]["code"] == "STORY_ATTENTION_STALE"
