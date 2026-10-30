"""1.0.1 P5：构思结构的读取、提交、差异与撤销接口，不调用模型。

界面要的每个动作都对应一个接口；这里验证接口返回的是状态文件里的真实内容，并且
确定性写入的修订与幂等规则成立。
"""

from __future__ import annotations

import time
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


class _ScriptedModel:
    """试读用例的受控回复；不访问网络，只证明请求确实发出并收到结果。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def stream_response(self, **kwargs: Any) -> Any:
        from tau_agent.messages import AssistantMessage, TextContent
        from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent

        self.requests.append(
            {"system": kwargs.get("system") or "", "tools": list(kwargs.get("tools") or [])}
        )

        async def generator() -> Any:
            yield AssistantStartEvent(partial=AssistantMessage(content=[]))
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(
                    content=[TextContent(text="我猜管家有问题。")], stop_reason="stop"
                ),
            )

        return generator()


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


def test_empty_structure_is_an_empty_list_not_an_error(
    client: TestClient, tmp_path: Path
) -> None:
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
    first = client.post(url, json={
        "variant_id": MAIN_VARIANT_ID,
        "expected_revision": revision_of(client, workspace_id),
        "request_key": prefix + "1",
        "operations": [{"op": "add_node", "node": {"id": "n3", "title": "三", "action": "行动"}}],
    })
    assert first.status_code == 201, first.text
    second = client.post(url, json={
        "variant_id": MAIN_VARIANT_ID,
        "expected_revision": first.json()["revision"],
        "request_key": prefix + "2",
        "operations": [{"op": "add_node", "node": {"id": "n4", "title": "四", "action": "行动"}}],
    })
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


def test_variant_prose_route_reads_variant_copy(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    seed(tmp_path, workspace_id)
    workspace = RecentWorkspaces(tmp_path / "state").workspace(workspace_id)
    view = workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n甲😀乙。", expected_digest=None)
    primary = client.get(f"/api/file-workspaces/{workspace_id}/story/variants").json()
    source_revision = primary["variants"][0]["revision"]
    fork = client.post(
        f"/api/file-workspaces/{workspace_id}/story/variants",
        json={
            "name": "备选",
            "source_variant_id": MAIN_VARIANT_ID,
            "source_revision": source_revision,
            "request_key": "fork-api",
        },
    )
    assert fork.status_code == 201, fork.text
    variant_id = fork.json()["result"]["variant_id"]
    response = client.get(
        f"/api/file-workspaces/{workspace_id}/story/variants/{variant_id}/prose",
        params={"path": view.path},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["variant_id"] == variant_id
    # 创建备选时就复制了来源实际可见的正文，不再回退到主方案当前内容。
    assert body["inherited"] is False
    assert body["digest"] == view.digest
    assert body["text"] == "# 雨夜\n\n甲😀乙。"
    missing = client.get(
        f"/api/file-workspaces/{workspace_id}/story/variants/{variant_id}/prose",
        params={"path": "正文/没有这一章.md"},
    )
    assert missing.status_code == 404


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
    conflict = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes", json=stale
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "STORY_REVISION_CONFLICT"


def test_variant_creation_comparison_and_undo(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    seed(tmp_path, workspace_id)
    revision = revision_of(client, workspace_id)

    created = client.post(
        f"/api/file-workspaces/{workspace_id}/story/variants",
        json={
            "name": "备选",
            "source_variant_id": MAIN_VARIANT_ID,
            "source_revision": revision,
            "request_key": "alt-1",
        },
    )
    assert created.status_code == 201, created.text
    alt_id = created.json()["result"]["variant_id"]

    alt_revision = next(
        item["revision"]
        for item in client.get(
            f"/api/file-workspaces/{workspace_id}/story/variants"
        ).json()["variants"]
        if item["id"] == alt_id
    )
    client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json={
            "variant_id": alt_id,
            "expected_revision": alt_revision,
            "request_key": "alt-change",
            "operations": [
                {"op": "add_node", "node": {"id": "n9", "title": "另一条路", "action": "去车站"}}
            ],
        },
    )

    comparison = client.get(
        f"/api/file-workspaces/{workspace_id}/story/comparison",
        params={"base": MAIN_VARIANT_ID, "candidate": alt_id},
    ).json()
    assert comparison["added"]["nodes"] == ["n9"]
    assert comparison["removed"]["nodes"] == []
    # 主方案没有被备选影响。
    graph = client.get(
        f"/api/file-workspaces/{workspace_id}/story/variants/{MAIN_VARIANT_ID}"
    ).json()
    assert "n9" not in [node["id"] for node in graph["nodes"]]

    # 撤销刚才在备选上的改动：只恢复这一个节点。
    alt_revision = next(
        item["revision"]
        for item in client.get(
            f"/api/file-workspaces/{workspace_id}/story/variants"
        ).json()["variants"]
        if item["id"] == alt_id
    )
    change = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json={
            "variant_id": alt_id,
            "expected_revision": alt_revision,
            "request_key": "alt-change-2",
            "operations": [
                {"op": "add_node", "node": {"id": "n10", "title": "第三个", "action": "等车"}}
            ],
        },
    ).json()
    record = client.get(
        f"/api/file-workspaces/{workspace_id}/story/changes/{change['change_id']}"
    ).json()
    assert record["changed"]

    undone = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes/{change['change_id']}/undo",
        params={"request_key": "undo-1"},
    )
    assert undone.status_code == 201, undone.text
    after = client.get(
        f"/api/file-workspaces/{workspace_id}/story/variants/{alt_id}"
    ).json()
    assert "n10" not in [node["id"] for node in after["nodes"]]
    # 前一次改动没有被撤销波及。
    assert "n9" in [node["id"] for node in after["nodes"]]


def test_missing_variant_and_node_are_reported(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    seed(tmp_path, workspace_id)

    missing = client.get(
        f"/api/file-workspaces/{workspace_id}/story/variants/variant_missing"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "STORY_VARIANT_NOT_FOUND"

    node = client.get(
        f"/api/file-workspaces/{workspace_id}/story/nodes/n99",
        params={"variant_id": MAIN_VARIANT_ID},
    )
    assert node.status_code == 400
    assert node.json()["error"]["code"] == "STORY_CONTEXT_INCOMPLETE"


def test_reader_preview_route_keeps_its_own_conversation(tmp_path: Path) -> None:
    book = RecentWorkspaces(tmp_path / "state")
    provider = _ScriptedModel()
    app = create_workspace_app(
        workspaces=book,
        provider_factory=lambda: ModelSnapshot(
            connection_id="test", display_name="测试连接", model="fake", provider=provider
        ),
    )
    with TestClient(app) as client:
        workspace_id = open_workspace(client, tmp_path)
        seed(tmp_path, workspace_id)
        revision = revision_of(client, workspace_id)
        response = client.post(
            f"/api/file-workspaces/{workspace_id}/reader-previews",
            json={
                "request_key": "reader-api-1",
                "variant_id": MAIN_VARIANT_ID,
                "revision": revision,
                "cutoff": "n1",
            },
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["conversation_id"]
        assert body["operation_id"]
        # 试读会话不出现在写作对话列表里：作者讨论与读者请求各自独立。
        listed = client.get(f"/api/file-workspaces/{workspace_id}/conversations").json()
        assert [item["id"] for item in listed["items"]] == []
        events: list[dict[str, Any]] = []
        for _ in range(100):
            events = client.get(
                f"/api/file-workspaces/{workspace_id}/conversations/"
                f"{body['conversation_id']}/messages"
            ).json()["events"]
            if any(event["type"] == "run_finished" for event in events):
                break
            time.sleep(0.02)
        story_events = [event for event in events if event["type"] == "story_context"]
        assert story_events[0]["reader"] is True
        assert provider.requests
        assert provider.requests[0]["tools"] == []


def test_comparison_reports_event_time_differences(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    seed(tmp_path, workspace_id)
    revision = revision_of(client, workspace_id)
    created = client.post(
        f"/api/file-workspaces/{workspace_id}/story/variants",
        json={
            "name": "倒叙备选",
            "source_variant_id": MAIN_VARIANT_ID,
            "source_revision": revision,
            "request_key": "compare-times",
        },
    )
    assert created.status_code == 201, created.text
    alternative_id = created.json()["result"]["variant_id"]
    alternative_revision = next(
        item["revision"]
        for item in client.get(
            f"/api/file-workspaces/{workspace_id}/story/variants"
        ).json()["variants"]
        if item["id"] == alternative_id
    )
    submitted = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json={
            "variant_id": alternative_id,
            "expected_revision": alternative_revision,
            "request_key": "compare-times-change",
            "operations": [
                {
                    "op": "set_event_time",
                    "relation": {"before_node_id": "n2", "after_node_id": "n1"},
                }
            ],
        },
    )
    assert submitted.status_code == 201, submitted.text

    comparison = client.get(
        f"/api/file-workspaces/{workspace_id}/story/comparison",
        params={"base": MAIN_VARIANT_ID, "candidate": alternative_id},
    ).json()

    assert comparison["added"]["event_times"] == ["n2->n1"]
    assert comparison["removed"]["event_times"] == []
