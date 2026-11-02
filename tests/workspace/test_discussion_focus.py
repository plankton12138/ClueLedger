"""v1.0.8 M2：讨论焦点的受理与复核。

焦点只用于定位材料：对象焦点合并进冻结方案或补一份线索引用，章节与选段焦点绑定
正文摘要；执行前版本变化整轮失败。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.clue_models import ClueIdentity
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.context import materials_section
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.materials import (
    ContextRequest,
    DiscussionFocus,
    freeze_context,
    verify_context,
)
from hastings.workspace.state import RecentWorkspaces, conversation_root
from hastings.workspace.story_models import StoryChangeSet
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore


class _NoModel:
    def stream_response(self, **_: Any) -> Any:
        raise AssertionError("焦点受理不应调用模型")


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


def write_chapter(tmp_path: Path, text: str) -> tuple[str, str]:
    path = "正文/0001 雨夜.md"
    target = tmp_path / "book" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return path, text


def seed(tmp_path: Path, workspace_id: str) -> tuple[Path, Workspace, str, str]:
    root = conversation_root(tmp_path / "state", workspace_id)
    workspace = Workspace(tmp_path / "book")
    ledger = ClueLedgerStore(root)

    def apply(store: Any) -> dict[str, str]:
        store.clues["board"] = ClueIdentity(
            clue_id="board",
            title="没有响的木板",
            clue_revision=f"rev_{uuid4().hex}",
            created_at="2026-11-02T00:00:00+08:00",
            created_by="author",
        )
        return {"clue_id": "board"}

    ledger.mutate(
        expected_revision=ledger.read().revision,
        request_key="seed-focus-clue",
        kind="test_identity",
        payload={},
        apply=apply,
    )
    store = StoryStore(root, workspace)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="focus-story",
        change=StoryChangeSet(
            id="focus-story",
            base_revision="empty",
            operations=[
                {"op": "add_node", "node": {"id": "n1", "title": "抵达", "action": "侦探到达"}},
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "board",
                    "plan": {"kind": "mislead", "intention": "让人以为走廊没有人"},
                },
            ],
        ),
    )
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None
    return root, workspace, variant.revision, ledger.read().ledger.clues["board"].clue_revision


def test_node_focus_merges_into_frozen_story(tmp_path: Path) -> None:
    workspace_id = "w_focus_node"
    path, _ = write_chapter(tmp_path, "# 第一章\n\n侦探到达。\n")
    root, workspace, revision, _ = seed(tmp_path, workspace_id)

    snapshot = freeze_context(
        workspace,
        ContextRequest(
            selected_path=path,
            discussion_focus=DiscussionFocus(
                kind="object",
                object_kind="node",
                object_id="n1",
                story_revision=revision,
            ),
        ),
        state_root=root,
    )

    assert snapshot.story is not None
    assert snapshot.story.node_ids == ("n1",)
    assert snapshot.story.purpose == "design"
    assert snapshot.discussion_focus is not None
    assert snapshot.discussion_focus.story_revision == revision
    assert snapshot.to_dict()["discussion_focus"]["object_id"] == "n1"
    materials = materials_section(snapshot, ())
    assert "本轮讨论对象：构思节点 `n1`" in materials


def test_clue_focus_adds_an_implicit_reference(tmp_path: Path) -> None:
    workspace_id = "w_focus_clue"
    path, _ = write_chapter(tmp_path, "# 第一章\n\n木板没有响。\n")
    root, workspace, revision, clue_revision = seed(tmp_path, workspace_id)

    snapshot = freeze_context(
        workspace,
        ContextRequest(
            selected_path=path,
            discussion_focus=DiscussionFocus(
                kind="object",
                object_kind="clue",
                object_id="board",
                clue_revision=clue_revision,
            ),
        ),
        state_root=root,
    )

    assert snapshot.story is not None
    assert snapshot.story.node_ids == ()
    assert [item.clue_id for item in snapshot.clues] == ["board"]
    assert snapshot.discussion_focus is not None
    assert snapshot.discussion_focus.clue_revision == clue_revision


def test_clue_focus_works_without_a_story_document(tmp_path: Path) -> None:
    workspace_id = "w_focus_clue_only"
    path, _ = write_chapter(tmp_path, "# 第一章\n\n钟停在23:00。\n")
    root = conversation_root(tmp_path / "state", workspace_id)
    ledger = ClueLedgerStore(root)

    def apply(store: Any) -> dict[str, str]:
        store.clues["clock"] = ClueIdentity(
            clue_id="clock",
            title="停住的钟",
            clue_revision=f"rev_{uuid4().hex}",
            created_at="2026-11-02T00:00:00+08:00",
            created_by="author",
        )
        return {"clue_id": "clock"}

    ledger.mutate(
        expected_revision=ledger.read().revision,
        request_key="seed-clock",
        kind="test_identity",
        payload={},
        apply=apply,
    )
    clue_revision = ledger.read().ledger.clues["clock"].clue_revision

    snapshot = freeze_context(
        Workspace(tmp_path / "book"),
        ContextRequest(
            selected_path=path,
            discussion_focus=DiscussionFocus(
                kind="object",
                object_kind="clue",
                object_id="clock",
                clue_revision=clue_revision,
            ),
        ),
        state_root=root,
    )

    assert snapshot.story is not None
    assert snapshot.story.purpose == "design"
    assert [item.clue_id for item in snapshot.clues] == ["clock"]
    assert snapshot.discussion_focus is not None
    assert snapshot.discussion_focus.variant_id is None
    # 阅读与讨论都不建立主方案：只有作者明确要求时才写结构。
    assert StoryStore(root).read().document is None


def test_selection_focus_is_rechecked_before_run(tmp_path: Path) -> None:
    workspace_id = "w_focus_selection"
    path, text = write_chapter(tmp_path, "# 第一章\n\n木板没有响。\n")
    root, workspace, _, _ = seed(tmp_path, workspace_id)
    quote = "木板没有响"
    start = text.find(quote)
    digest = workspace.read(path).digest

    snapshot = freeze_context(
        workspace,
        ContextRequest(
            selected_path=path,
            discussion_focus=DiscussionFocus(
                kind="selection",
                path=path,
                digest=digest,
                start=start,
                end=start + len(quote),
                quote=quote,
            ),
        ),
        state_root=root,
    )
    assert "本轮讨论选段" in materials_section(snapshot, ())
    verify_context(workspace, snapshot, state_root=root)

    (tmp_path / "book" / path).write_text("# 第一章\n\n木板响了一声。\n", encoding="utf-8")
    with pytest.raises(WorkspaceError) as raised:
        verify_context(workspace, snapshot, state_root=root)
    assert raised.value.code == "PROSE_SOURCE_STALE"


def test_stale_chapter_focus_is_rejected_at_accept(tmp_path: Path) -> None:
    workspace_id = "w_focus_stale"
    path, _ = write_chapter(tmp_path, "# 第一章\n\n内容。\n")
    root, workspace, _, _ = seed(tmp_path, workspace_id)

    with pytest.raises(WorkspaceError) as raised:
        freeze_context(
            workspace,
            ContextRequest(
                discussion_focus=DiscussionFocus(kind="chapter", path=path, digest="0" * 64),
            ),
            state_root=root,
        )
    assert raised.value.code == "PROSE_SOURCE_STALE"


def test_focus_shape_is_validated_at_the_http_boundary(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "讨论这条线索",
            "request_key": "focus-shape",
            "context": {"discussion_focus": {"kind": "object"}},
        },
    )

    assert response.status_code == 422, response.text
