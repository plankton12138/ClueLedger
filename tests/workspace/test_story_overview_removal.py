"""移除总览后，旧接口不可用且作者文件与历史记录保留。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.service import TurnService
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root


def no_model() -> None:
    raise AssertionError("不得调用模型")


def test_overview_routes_removed_without_deleting_author_files_or_old_data(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "作品")
    source = workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n雨仍未停。", expected_digest=None)
    workspaces = RecentWorkspaces(tmp_path / "app")
    book = workspaces.bind(workspace.root)
    root = conversation_root(workspaces.root, book.id)
    historical = root / "overview" / "old-result.json"
    historical.parent.mkdir(parents=True)
    historical.write_text('{"historical":true}', encoding="utf-8")
    with TestClient(
        create_workspace_app(workspaces=workspaces, provider_factory=no_model)
    ) as client:
        base = f"/api/file-workspaces/{book.id}"
        for suffix in (
            "",
            "/events",
            "/bindings/old",
            "/bindings/old/facets/old",
            "/relations",
            "/updates/old",
        ):
            assert client.get(base + "/story-overview" + suffix).status_code == 404
        assert client.post(base + "/story-overview/updates", json={}).status_code == 404
        assert (
            client.get(base + "/file", params={"path": source.path}).json()["text"] == source.text
        )
        assert client.get(base + "/story-events").status_code == 200
        assert not any(
            "story-overview" in path for path in client.get("/openapi.json").json()["paths"]
        )
    assert historical.read_text(encoding="utf-8") == '{"historical":true}'


@pytest.mark.anyio
async def test_removed_purpose_cannot_fall_back_to_writing_or_resume_old_tasks(
    tmp_path: Path,
) -> None:
    workspace = Workspace.create(tmp_path / "作品")
    store = ConversationStore(tmp_path / "state")
    old = store.create_with_first_turn(
        text="更新故事总览",
        request_key="old-overview",
        turn_id="old-turn",
        purpose="organize_overview",
        material_scope={"domain": "overview"},
        origin="materials",
    )
    service = TurnService(workspace, store, no_model)
    service.mark_interrupted()
    before = store.events(old.conversation_id)
    for key in ("new-overview", "old-overview"):
        with pytest.raises(WorkspaceError, match="TURN_PURPOSE_UNAVAILABLE"):
            await service.accept("更新故事总览", key, purpose="organize_overview")
    assert store.events(old.conversation_id) == before
    assert not service.busy
