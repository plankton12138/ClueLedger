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


def seed_identity(tmp_path: Path, workspace_id: str) -> None:
    from uuid import uuid4

    from hastings.workspace.clue_models import ClueIdentity
    from hastings.workspace.clue_store import ClueLedgerStore

    ledger = ClueLedgerStore(conversation_root(tmp_path / "state", workspace_id))

    def apply(store: Any) -> dict[str, str]:
        store.clues["gloves"] = ClueIdentity(
            clue_id="gloves",
            title="手套",
            clue_revision=f"rev_{uuid4().hex}",
            created_at="2026-10-30T00:00:00+08:00",
            created_by="author",
        )
        return {"clue_id": "gloves"}

    ledger.mutate(
        expected_revision=ledger.read().revision,
        request_key="seed-identity",
        kind="test_identity",
        payload={},
        apply=apply,
    )


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
                    "op": "upsert_clue_plan",
                    "clue_id": "gloves",
                    "plan": {
                        "author_truth": "管家搬过尸体",
                        "resolution_intent": "explain",
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
                {
                    "op": "add_node",
                    "node": {
                        "id": "n2",
                        "title": "对质",
                        "action": "他问管家",
                        "clue_uses": [
                            {
                                "clue_id": "gloves",
                                "roles": ["resolve"],
                                "presentation": "他说出手套的来历",
                                "mode": "narration",
                            }
                        ],
                    },
                },
                {
                    "op": "connect",
                    "edge": {"id": "e1", "from_id": "n1", "to_id": "n2", "kind": "next"},
                },
            ],
        ),
        request_key="seed-api",
    )
    seed_identity(tmp_path, workspace_id)


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
    assert conflict.status_code == 409, conflict.text
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


def test_story_scope_clue_reads_plan_and_uses(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    seed(tmp_path, workspace_id)

    listing = client.get(
        f"/api/file-workspaces/{workspace_id}/clues",
        params={"scope": "story"},
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["title"] == "手套"
    assert item["plan"]["author_truth"] == "管家搬过尸体"
    assert item["scope"] == "story"

    related = client.get(
        f"/api/file-workspaces/{workspace_id}/clues",
        params={"scope": "story", "node_id": "n1"},
    )
    assert related.status_code == 200
    assert [row["clue_id"] for row in related.json()["items"]] == ["gloves"]

    detail = client.get(
        f"/api/file-workspaces/{workspace_id}/clues/gloves",
        params={"scope": "story"},
    )
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["plan"]["resolution_intent"] == "explain"
    assert [use["node_id"] for use in detail_body["uses"]] == ["n1", "n2"]
    assert detail_body["uses"][1]["roles"] == ["resolve"]

    invalid = client.get(
        f"/api/file-workspaces/{workspace_id}/clues",
        params={"scope": "story", "node_id": "missing"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "STORY_CONTEXT_INCOMPLETE"


def test_create_identity_is_joint_and_undo_restores_ledger(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    revision = "empty"
    created = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json={
            "variant_id": MAIN_VARIANT_ID,
            "expected_revision": revision,
            "request_key": "identity-create",
            "operations": [
                {"op": "create_clue_identity", "temp_id": "board", "title": "没有响的木板"},
                {"op": "upsert_clue_plan", "clue_id": "board",
                 "plan": {"author_truth": None, "resolution_intent": "undecided"}},
                {"op": "add_node", "node": {
                    "id": "observe", "title": "发现异样", "action": "木板没有响",
                    "clue_uses": [{"clue_id": "board", "presentation": "木板没有响",
                                   "mode": "narration"}],
                }},
            ],
        },
    )
    assert created.status_code == 201, created.text
    receipt = created.json()
    stable = receipt["id_map"]["board"]
    assert stable.startswith("clue_")
    assert receipt["ledger_revision"]

    listing = client.get(
        f"/api/file-workspaces/{workspace_id}/clues", params={"scope": "story"}
    )
    assert [row["title"] for row in listing.json()["items"]] == ["没有响的木板"]
    store = StoryStore(conversation_root(tmp_path / "state", workspace_id))
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert stable in variant.clue_plans
    assert variant.nodes["observe"].clue_uses[0].clue_id == stable

    undid = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes/{receipt['change_id']}/undo",
        params={"request_key": "identity-undo"},
    )
    assert undid.status_code == 201, undid.text
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert "observe" not in variant.nodes
    assert stable not in variant.clue_plans
    listing = client.get(
        f"/api/file-workspaces/{workspace_id}/clues", params={"scope": "story"}
    )
    assert listing.json()["items"] == []
