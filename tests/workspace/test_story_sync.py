"""1.0.6 P1：构思契约与正文绑定的只读同步视图。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.clue_models import ClueIdentity
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.files import Workspace
from hastings.workspace.state import RecentWorkspaces, conversation_root
from hastings.workspace.story_candidate import StoryCandidate
from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_models import BindProse, StoryChangeSet
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore
from hastings.workspace.story_sync import contract_digest


class _NoModel:
    def stream_response(self, **_: Any) -> Any:
        raise AssertionError("同步视图不应调用模型")


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


def seed_identity(tmp_path: Path, workspace_id: str) -> None:
    ledger = ClueLedgerStore(conversation_root(tmp_path / "state", workspace_id))

    def apply(store: Any) -> dict[str, str]:
        store.clues["board"] = ClueIdentity(
            clue_id="board",
            title="没有响的木板",
            clue_revision="rev_board",
            created_at="2026-11-01T00:00:00+08:00",
            created_by="author",
        )
        return {"clue_id": "board"}

    ledger.mutate(
        expected_revision=ledger.read().revision,
        request_key="seed-sync-identity",
        kind="test_identity",
        payload={},
        apply=apply,
    )


def seed_story(tmp_path: Path, workspace_id: str) -> StoryStore:
    seed_identity(tmp_path, workspace_id)
    store = StoryStore(conversation_root(tmp_path / "state", workspace_id))
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(
            id="seed_sync",
            base_revision="empty",
            operations=[
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "board",
                    "plan": {"intention": "让人怀疑有人熟悉走廊"},
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n1",
                        "title": "发现异样",
                        "action": "侦探发现木板没有响",
                        "change": "开始怀疑走廊有人经过",
                        "clue_uses": [
                            {
                                "clue_id": "board",
                                "roles": ["plant"],
                                "presentation": "有人经过时木板没有响",
                                "mode": "action",
                            }
                        ],
                    },
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n2",
                        "title": "核对证词",
                        "action": "侦探比较两个证人的时间",
                    },
                },
                {
                    "op": "connect",
                    "edge": {
                        "id": "e1",
                        "from_id": "n1",
                        "to_id": "n2",
                        "kind": "next",
                    },
                },
            ],
        ),
        request_key="seed-sync-story",
    )
    return store


def write_chapter(tmp_path: Path, text: str) -> tuple[str, str, str]:
    path = "正文/0001 雨夜.md"
    target = tmp_path / "book" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    view = Workspace(tmp_path / "book").read(path)
    return path, view.digest, view.text


def bind_first_node(
    store: StoryStore,
    *,
    path: str,
    digest: str,
    text: str,
    quote: str = "有人经过时木板没有响",
) -> None:
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None
    node = variant.nodes["n1"]
    start = text.find(quote)
    assert start >= 0
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(
            id="bind_sync",
            base_revision=variant.revision,
            operations=[
                {
                    "op": "bind_prose",
                    "binding": {
                        "id": "b1",
                        "node_id": "n1",
                        "document_id": "doc_1",
                        "path": path,
                        "digest": digest,
                        "start": start,
                        "end": start + len(quote),
                        "quote": quote,
                        "contract_digest": contract_digest(variant, node),
                    },
                }
            ],
        ),
        request_key="bind-sync-prose",
    )


def sync(client: TestClient, workspace_id: str, **params: str) -> dict[str, Any]:
    response = client.get(
        f"/api/file-workspaces/{workspace_id}/story/variants/{MAIN_VARIANT_ID}/sync",
        params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_sync_view_reports_outline_only_and_unbound_prose(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    seed_story(tmp_path, workspace_id)
    path, _, _ = write_chapter(tmp_path, "# 第一章\n\n没有人经过。\n")

    body = sync(client, workspace_id)

    assert body["variant_id"] == MAIN_VARIANT_ID
    assert body["counts"]["outline_only"] == 2
    assert body["counts"]["unbound_prose"] == 1
    assert {node["state"] for node in body["nodes"]} == {"outline_only"}
    chapter = next(row for row in body["chapters"] if row["path"] == path)
    assert chapter["state"] == "unbound"
    assert [row["kind"] for row in body["conflicts"]] == ["unbound_prose"]


def test_sync_view_reports_aligned_binding_and_outline_change(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store = seed_story(tmp_path, workspace_id)
    path, digest, text = write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    bind_first_node(store, path=path, digest=digest, text=text)

    aligned = sync(client, workspace_id)
    node = next(row for row in aligned["nodes"] if row["node_id"] == "n1")
    assert node["state"] == "aligned"
    assert node["bound_quote"] == "有人经过时木板没有响"
    chapter = next(row for row in aligned["chapters"] if row["path"] == path)
    assert chapter["state"] == "aligned"
    assert chapter["checked_digest"] == digest
    assert aligned["counts"]["aligned"] == 1
    assert aligned["counts"]["outline_only"] == 1

    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None
    node_model = variant.nodes["n1"].model_copy(
        update={"action": "侦探发现木板没有响，并确认有人走过"}
    )
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(
            id="change_action",
            base_revision=variant.revision,
            operations=[
                {"op": "update_node", "node_id": "n1", "node": node_model.model_dump(mode="json")}
            ],
        ),
        request_key="change-action-sync",
    )

    changed = sync(client, workspace_id)
    node = next(row for row in changed["nodes"] if row["node_id"] == "n1")
    assert node["state"] == "outline_ahead"
    assert changed["counts"]["outline_ahead"] == 1
    assert changed["conflicts"][0]["kind"] == "outline_ahead"
    assert changed["conflicts"][0]["options"] == [
        "follow_outline",
        "follow_prose",
        "defer",
    ]


def test_sync_view_reports_prose_change_and_chapter_filter(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store = seed_story(tmp_path, workspace_id)
    path, digest, text = write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    bind_first_node(store, path=path, digest=digest, text=text)

    write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。后来，门外传来脚步声。\n")

    body = sync(client, workspace_id)
    node = next(row for row in body["nodes"] if row["node_id"] == "n1")
    assert node["state"] == "prose_ahead"
    assert body["counts"]["prose_ahead"] == 1

    filtered = sync(client, workspace_id, chapter_path=path)
    assert [row["node_id"] for row in filtered["nodes"]] == ["n1"]
    assert [row["path"] for row in filtered["chapters"]] == [path]
    assert [row["kind"] for row in filtered["conflicts"]] == ["prose_ahead"]


def action_change_body(store: StoryStore, action: str, request_key: str) -> dict[str, Any]:
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None
    node = variant.nodes["n1"].model_copy(update={"action": action})
    return {
        "variant_id": MAIN_VARIANT_ID,
        "expected_revision": variant.revision,
        "request_key": request_key,
        "operations": [
            {"op": "update_node", "node_id": "n1", "node": node.model_dump(mode="json")}
        ],
    }


def test_structure_change_requires_sync_decision_and_follow_prose_resolves(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store = seed_story(tmp_path, workspace_id)
    path, digest, text = write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    bind_first_node(store, path=path, digest=digest, text=text)

    url = f"/api/file-workspaces/{workspace_id}/story/changes"
    blocked = client.post(url, json=action_change_body(store, "侦探确认有人走过", "sync-gate-1"))
    assert blocked.status_code == 400, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "STORY_SYNC_CONFIRM_REQUIRED"
    details = error["details"]
    assert details["conflicts"][0]["binding_id"] == "b1"
    digest = details["conflict_digest"]

    body = action_change_body(store, "侦探确认有人走过", "sync-gate-2")
    body["sync"] = {
        "conflict_digest": digest,
        "choice": "follow_prose",
        "note": "作者选择以正文为准",
    }
    accepted = client.post(url, json=body)
    assert accepted.status_code == 201, accepted.text

    body = sync(client, workspace_id)
    node = next(row for row in body["nodes"] if row["node_id"] == "n1")
    assert node["state"] == "aligned"
    assert body["counts"]["outline_ahead"] == 0
    assert body["conflicts"] == []


def test_stale_sync_decision_is_rejected(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store = seed_story(tmp_path, workspace_id)
    path, digest, text = write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    bind_first_node(store, path=path, digest=digest, text=text)

    body = action_change_body(store, "侦探确认有人走过", "sync-stale")
    body["sync"] = {
        "conflict_digest": "0" * 64,
        "choice": "follow_prose",
        "note": "过期确认",
    }
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json=body,
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "STORY_SYNC_CONFLICT_STALE"


def test_defer_marks_binding_waived(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store = seed_story(tmp_path, workspace_id)
    path, digest, text = write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    bind_first_node(store, path=path, digest=digest, text=text)

    url = f"/api/file-workspaces/{workspace_id}/story/changes"
    blocked = client.post(url, json=action_change_body(store, "侦探确认有人走过", "sync-defer-1"))
    details = blocked.json()["error"]["details"]
    body = action_change_body(store, "侦探确认有人走过", "sync-defer-2")
    body["sync"] = {
        "conflict_digest": details["conflict_digest"],
        "choice": "defer",
        "note": "作者选择暂不处理",
    }
    accepted = client.post(url, json=body)
    assert accepted.status_code == 201, accepted.text

    view = sync(client, workspace_id)
    node = next(row for row in view["nodes"] if row["node_id"] == "n1")
    assert node["state"] == "waived"
    assert view["counts"]["waived"] == 1
    assert view["conflicts"] == []


def test_title_only_change_does_not_require_sync(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store = seed_story(tmp_path, workspace_id)
    path, digest, text = write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    bind_first_node(store, path=path, digest=digest, text=text)

    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None
    node = variant.nodes["n1"].model_copy(update={"title": "改了标题"})
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json={
            "variant_id": MAIN_VARIANT_ID,
            "expected_revision": variant.revision,
            "request_key": "sync-title-only",
            "operations": [
                {"op": "update_node", "node_id": "n1", "node": node.model_dump(mode="json")}
            ],
        },
    )
    assert response.status_code == 201, response.text
    node = next(row for row in sync(client, workspace_id)["nodes"] if row["node_id"] == "n1")
    assert node["state"] == "aligned"


def test_candidate_structure_requires_sync_and_accepts_follow_prose(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store = seed_story(tmp_path, workspace_id)
    path, digest, text = write_chapter(tmp_path, "# 第一章\n\n有人经过时木板没有响。\n")
    bind_first_node(store, path=path, digest=digest, text=text)
    variant = store.require_variant(MAIN_VARIANT_ID)
    node = variant.nodes["n1"].model_copy(update={"action": "侦探确认有人走过"})
    candidate = StoryCandidate(
        Workspace(tmp_path / "book"),
        store.root,
        StoryContextSnapshot(
            variant_id=MAIN_VARIANT_ID,
            revision=variant.revision,
            purpose="write",
            node_ids=("n1",),
        ),
        "op_sync",
        1,
        lambda: None,
        lambda kind, payload: None,
        "把发现异样的行动改成侦探确认有人走过",
    )
    operations = [{"op": "update_node", "node_id": "n1", "node": node.model_dump(mode="json")}]

    with pytest.raises(Exception) as exc:
        candidate.propose_structure(operations)
    error = exc.value
    assert getattr(error, "code", "") == "STORY_SYNC_CONFIRM_REQUIRED"
    conflict_digest = error.details["conflict_digest"]  # type: ignore[attr-defined]

    result = candidate.propose_structure(
        operations,
        {
            "conflict_digest": conflict_digest,
            "choice": "follow_prose",
            "note": "作者选择以正文为准",
        },
    )
    assert result["candidate_id"] == candidate.id
    assert candidate.sync is not None
    assert candidate.sync.choice == "follow_prose"
    assert any(isinstance(op, BindProse) for op in candidate.operations)


def test_contract_digest_ignores_display_fields_and_tracks_content(
    tmp_path: Path, client: TestClient
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store = seed_story(tmp_path, workspace_id)
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None
    node = variant.nodes["n1"]
    baseline = contract_digest(variant, node)

    renamed = variant.model_copy(
        update={
            "nodes": {
                **variant.nodes,
                "n1": node.model_copy(update={"title": "改了标题"}),
            }
        }
    )
    assert contract_digest(renamed, renamed.nodes["n1"]) == baseline

    changed = variant.model_copy(
        update={
            "nodes": {
                **variant.nodes,
                "n1": node.model_copy(update={"action": "侦探确认木板是被人踩住的"}),
            }
        }
    )
    assert contract_digest(changed, changed.nodes["n1"]) != baseline
