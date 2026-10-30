"""1.0.1 P5：构思结构的读取、提交、差异与撤销接口，不调用模型。

界面要的每个动作都对应一个接口；这里验证接口返回的是状态文件里的真实内容，并且
确定性写入的修订与幂等规则成立。
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
    """接口用例不该走到模型；真被调用就说明接线错了。"""

    def stream_response(self, **_: Any) -> Any:
        raise AssertionError("结构接口不应调用模型")


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


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/story/variants"),
        ("POST", "/story/adoptions"),
        ("GET", "/story/comparison?base=main&candidate=other"),
        ("GET", "/story/candidates"),
        ("GET", "/story/candidates/candidate_old"),
        ("GET", "/story/variants/main/prose?path=chapter.md"),
        ("POST", "/reader-previews"),
    ],
)
def test_removed_story_features_have_no_public_route(
    client: TestClient,
    tmp_path: Path,
    method: str,
    path: str,
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    response = client.request(method, f"/api/file-workspaces/{workspace_id}{path}")
    assert response.status_code in (404, 405), response.text


def test_conversation_cannot_start_removed_reader_mode(
    client: TestClient,
    tmp_path: Path,
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "试读",
            "request_key": "removed-reader",
            "context": {"story": {"purpose": "reader_preview"}},
        },
    )
    assert response.status_code == 422, response.text


def seed(tmp_path: Path, workspace_id: str) -> None:
    store = StoryStore(conversation_root(tmp_path / "state", workspace_id))
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(
            id="seed_api",
            base_revision="empty",
            operations=[
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
                {
                    "op": "connect",
                    "edge": {"id": "e1", "from_id": "n1", "to_id": "n2", "kind": "next"},
                },
            ],
        ),
        request_key="seed-api",
    )


def revision_of(client: TestClient, workspace_id: str) -> str:
    body = client.get(f"/api/file-workspaces/{workspace_id}/story/variants").json()
    return str(body["variants"][0]["revision"])


def test_empty_structure_is_an_empty_list_not_an_error(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)

    response = client.get(f"/api/file-workspaces/{workspace_id}/story/variants")

    assert response.status_code == 200
    assert response.json() == {"revision": "", "primary_variant_id": "", "variants": []}


def test_changes_with_common_request_key_prefix_keep_both_receipts(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    seed(tmp_path, workspace_id)
    url = f"/api/file-workspaces/{workspace_id}/story/changes"
    prefix = "a" * 24
    first = client.post(
        url,
        json={
            "variant_id": MAIN_VARIANT_ID,
            "expected_revision": revision_of(client, workspace_id),
            "request_key": prefix + "1",
            "operations": [
                {"op": "add_node", "node": {"id": "n3", "title": "三", "action": "行动"}}
            ],
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        url,
        json={
            "variant_id": MAIN_VARIANT_ID,
            "expected_revision": first.json()["revision"],
            "request_key": prefix + "2",
            "operations": [
                {"op": "add_node", "node": {"id": "n4", "title": "四", "action": "行动"}}
            ],
        },
    )
    assert second.status_code == 201, second.text
    assert first.json()["change_id"] != second.json()["change_id"]
    store = StoryStore(conversation_root(tmp_path / "state", workspace_id))
    document = store.read().document
    assert document is not None
    assert first.json()["change_id"] in document.changes
    assert second.json()["change_id"] in document.changes


def test_variants_graph_and_node_detail(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    seed(tmp_path, workspace_id)

    variants = client.get(f"/api/file-workspaces/{workspace_id}/story/variants").json()
    assert variants["primary_variant_id"] == MAIN_VARIANT_ID
    summary = variants["variants"][0]
    assert summary["primary"] is True
    assert summary["counts"]["nodes"] == 2
    assert summary["counts"]["edges"] == 1

    graph = client.get(
        f"/api/file-workspaces/{workspace_id}/story/variants/{MAIN_VARIANT_ID}"
    ).json()
    assert [node["id"] for node in graph["nodes"]] == ["n1", "n2"]
    assert graph["edges"][0]["from_id"] == "n1"
    assert graph["collapsed"] == []

    detail = client.get(
        f"/api/file-workspaces/{workspace_id}/story/nodes/n1",
        params={"variant_id": MAIN_VARIANT_ID},
    ).json()
    assert detail["node"]["title"] == "抵达"
    assert detail["clue_uses"][0]["author_truth"] == "管家搬过尸体"
    assert [edge["id"] for edge in detail["outgoing"]] == ["e1"]
    assert detail["incoming"] == []


def test_change_commit_is_idempotent_and_revision_checked(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    seed(tmp_path, workspace_id)
    revision = revision_of(client, workspace_id)
    body = {
        "variant_id": MAIN_VARIANT_ID,
        "expected_revision": revision,
        "request_key": "ui-change-1",
        "operations": [
            {"op": "add_node", "node": {"id": "n3", "title": "雪停", "action": "雪停了"}}
        ],
    }

    first = client.post(f"/api/file-workspaces/{workspace_id}/story/changes", json=body)
    assert first.status_code == 201, first.text
    receipt = first.json()
    assert receipt["revision"] != revision
    assert "node:n3" in receipt["changed"]

    # 同一个键与同一份负载只应用一次。
    again = client.post(f"/api/file-workspaces/{workspace_id}/story/changes", json=body)
    assert again.status_code == 201
    assert again.json()["revision"] == receipt["revision"]

    # 用旧修订再改一次：冲突，不写入。
    stale = dict(body, request_key="ui-change-2")
    conflict = client.post(f"/api/file-workspaces/{workspace_id}/story/changes", json=stale)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "STORY_REVISION_CONFLICT"


def test_missing_variant_and_node_are_reported(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    seed(tmp_path, workspace_id)

    missing = client.get(f"/api/file-workspaces/{workspace_id}/story/variants/variant_missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "STORY_VARIANT_NOT_FOUND"

    node = client.get(
        f"/api/file-workspaces/{workspace_id}/story/nodes/n99",
        params={"variant_id": MAIN_VARIANT_ID},
    )
    assert node.status_code == 400
    assert node.json()["error"]["code"] == "STORY_CONTEXT_INCOMPLETE"
