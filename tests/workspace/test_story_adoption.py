"""1.0.1 P5：备选的整案与片段采用。

采用是确定性的结构复制：选中范围、必需前提、冲突与覆盖都要能说清楚，不静默改写目标。
需要模型按依赖调整正文的 ``assist`` 在这一版明确拒绝。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.state import RecentWorkspaces, conversation_root
from hastings.workspace.story_models import StoryChangeSet
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore


class _NoModel:
    def stream_response(self, **_: Any) -> Any:
        raise AssertionError("采用接口不应调用模型")


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


def change(operations: list[dict[str, Any]], *, key: str, base: str) -> StoryChangeSet:
    return StoryChangeSet(
        id=f"seed_{key}", base_revision=base, operations=operations  # type: ignore[arg-type]
    )


NODES: list[dict[str, Any]] = [
    {
        "op": "add_clue",
        "clue": {
            "id": "gloves",
            "title": "手套",
            "author_truth": "管家搬过尸体",
            "resolution": {"intent": "explain", "node_ids": ["n2"]},
        },
    },
    {
        "op": "add_node",
        "node": {
            "id": "n1",
            "title": "抵达",
            "action": "侦探看到手套",
            "thread_ids": ["main"],
            "clue_uses": [
                {
                    "clue_id": "gloves",
                    "presentation": "手套还在滴水",
                    "mode": "narration",
                }
            ],
        },
    },
    {"op": "add_node", "node": {"id": "n2", "title": "对质", "action": "他问管家"}},
    {"op": "connect", "edge": {"id": "e1", "from_id": "n1", "to_id": "n2", "kind": "next"}},
    {"op": "add_thread", "thread": {"id": "main", "title": "主线"}},
]


def seed_both(tmp_path: Path, workspace_id: str) -> tuple[StoryStore, str]:
    """主方案有两个节点；备选从主方案复制后改了一处、再加一个节点。"""
    store = StoryStore(conversation_root(tmp_path / "state", workspace_id))
    main = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change(NODES, key="main", base="empty"),
        request_key="seed-main",
    )
    receipt = store.create_variant(
        name="备选",
        source_variant_id=MAIN_VARIANT_ID,
        source_revision=main.revision,
        request_key="alt",
    )
    alt_id = str(receipt.result["variant_id"])
    alt = store.read().variant(alt_id)
    assert alt is not None
    store.commit_change(
        variant_id=alt_id,
        change=change(
            [
                {
                    "op": "update_node",
                    "node_id": "n2",
                    "node": {
                        **alt.nodes["n2"].model_dump(mode="json"),
                        "action": "他把手套放在桌上",
                    },
                },
                {"op": "add_node", "node": {"id": "n3", "title": "雪停", "action": "雪停了"}},
                {
                    "op": "connect",
                    "edge": {"id": "e2", "from_id": "n2", "to_id": "n3", "kind": "next"},
                },
            ],
            key="alt-edit",
            base=alt.revision,
        ),
        request_key="alt-edit",
    )
    return store, alt_id


def revisions(client: TestClient, workspace_id: str) -> dict[str, str]:
    body = client.get(f"/api/file-workspaces/{workspace_id}/story/variants").json()
    return {str(item["id"]): str(item["revision"]) for item in body["variants"]}


def test_fragment_adoption_copies_prerequisites_and_leaves_other_nodes(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store, alt_id = seed_both(tmp_path, workspace_id)
    current = revisions(client, workspace_id)

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/story/adoptions",
        json={
            "source_variant_id": alt_id,
            "source_revision": current[alt_id],
            "target_variant_id": MAIN_VARIANT_ID,
            "target_revision": current[MAIN_VARIANT_ID],
            "node_ids": ["n3"],
            "mode": "exact",
            "request_key": "adopt-n3",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan"]["copied"]["nodes"] == ["n3"]
    # 没选 n1/n2：它们的改动不会跟过来。
    assert "n2" not in body["plan"]["copied"]["nodes"]
    assert body["receipt"]["revision"]
    merged = store.read().variant(MAIN_VARIANT_ID)
    assert merged is not None
    assert "n3" in merged.nodes
    assert merged.nodes["n2"].action == "他问管家"


def test_fragment_adoption_pulls_in_clue_and_thread(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store = StoryStore(conversation_root(tmp_path / "state", workspace_id))
    main = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change(NODES[:3] + NODES[4:], key="main", base="empty"),
        request_key="seed-main",
    )
    receipt = store.create_variant(
        name="备选",
        source_variant_id=MAIN_VARIANT_ID,
        source_revision=main.revision,
        request_key="alt",
    )
    alt_id = str(receipt.result["variant_id"])
    alt = store.read().variant(alt_id)
    assert alt is not None
    # 备选里新增一条只有它才有的线索与一个用到它的节点：采用这个节点必须把线索带过来。
    store.commit_change(
        variant_id=alt_id,
        change=change(
            [
                {
                    "op": "add_clue",
                    "clue": {
                        "id": "letter",
                        "title": "遗书",
                        "author_truth": "遗书是管家伪造的",
                    },
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n9",
                        "title": "读遗书",
                        "action": "侦探读到遗书",
                        "clue_uses": [
                            {
                                "clue_id": "letter",
                                "presentation": "遗书上的字迹很新",
                                "mode": "narration",
                            }
                        ],
                    },
                },
            ],
            key="alt-use",
            base=alt.revision,
        ),
        request_key="alt-use",
    )
    main_variant = store.read().variant(MAIN_VARIANT_ID)
    assert main_variant is not None and "letter" not in main_variant.clues
    current = revisions(client, workspace_id)

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/story/adoptions",
        json={
            "source_variant_id": alt_id,
            "source_revision": current[alt_id],
            "target_variant_id": MAIN_VARIANT_ID,
            "target_revision": current[MAIN_VARIANT_ID],
            "node_ids": ["n9"],
            "request_key": "adopt-n9",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan"]["copied"]["nodes"] == ["n9"]
    assert body["plan"]["copied"]["clues"] == ["letter"]
    assert body["plan"]["pulled_in"] == ["clue:letter"]
    merged = store.read().variant(MAIN_VARIANT_ID)
    assert merged is not None
    assert "letter" in merged.clues
    assert merged.nodes["n9"].clue_uses[0].presentation == "遗书上的字迹很新"


def test_conflicting_object_is_reported_instead_of_overwritten(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store, alt_id = seed_both(tmp_path, workspace_id)
    current = revisions(client, workspace_id)

    body = {
        "source_variant_id": alt_id,
        "source_revision": current[alt_id],
        "target_variant_id": MAIN_VARIANT_ID,
        "target_revision": current[MAIN_VARIANT_ID],
        "request_key": "adopt-conflict",
    }
    conflict = client.post(f"/api/file-workspaces/{workspace_id}/story/adoptions", json=body)

    assert conflict.status_code == 409
    error = conflict.json()["error"]
    assert error["code"] == "STORY_ADOPTION_CONFLICT"
    assert {"kind": "node", "id": "n2",
            "reason": "目标方案里已有同 ID 但内容不同的对象"} in error["details"]["conflicts"]
    # 冲突时不写任何东西。
    unchanged = store.read().variant(MAIN_VARIANT_ID)
    assert unchanged is not None and unchanged.nodes["n2"].action == "他问管家"

    replaced = client.post(
        f"/api/file-workspaces/{workspace_id}/story/adoptions",
        json={**body, "replace": True, "request_key": "adopt-replace"},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["plan"]["copied"]["nodes"] == ["n2", "n3"]
    merged = store.read().variant(MAIN_VARIANT_ID)
    assert merged is not None and merged.nodes["n2"].action == "他把手套放在桌上"


def test_whole_variant_adoption_and_idempotent_retry(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store, alt_id = seed_both(tmp_path, workspace_id)
    current = revisions(client, workspace_id)
    body = {
        "source_variant_id": alt_id,
        "source_revision": current[alt_id],
        "target_variant_id": MAIN_VARIANT_ID,
        "target_revision": current[MAIN_VARIANT_ID],
        "node_ids": [],
        "replace": True,
        "request_key": "adopt-all",
    }

    first = client.post(f"/api/file-workspaces/{workspace_id}/story/adoptions", json=body)
    assert first.status_code == 200, first.text
    revision = first.json()["receipt"]["revision"]

    again = client.post(f"/api/file-workspaces/{workspace_id}/story/adoptions", json=body)
    assert again.status_code == 200
    assert again.json()["receipt"]["revision"] == revision
    merged = store.read().variant(MAIN_VARIANT_ID)
    assert merged is not None and set(merged.nodes) == {"n1", "n2", "n3"}


def test_stale_revision_and_assist_are_rejected(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    store, alt_id = seed_both(tmp_path, workspace_id)
    current = revisions(client, workspace_id)
    base = {
        "source_variant_id": alt_id,
        "source_revision": current[alt_id],
        "target_variant_id": MAIN_VARIANT_ID,
        "target_revision": current[MAIN_VARIANT_ID],
        "request_key": "adopt-stale",
    }

    stale = client.post(
        f"/api/file-workspaces/{workspace_id}/story/adoptions",
        json={**base, "source_revision": "rev_old"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STORY_REVISION_CONFLICT"

    assist = client.post(
        f"/api/file-workspaces/{workspace_id}/story/adoptions",
        json={**base, "mode": "assist", "request_key": "adopt-assist"},
    )
    assert assist.status_code == 400
    assert assist.json()["error"]["code"] == "STORY_ADOPTION_UNAVAILABLE"
    # 明确拒绝时不写任何结构。
    before = store.read().variant(MAIN_VARIANT_ID)
    assert before is not None and set(before.nodes) == {"n1", "n2"}
