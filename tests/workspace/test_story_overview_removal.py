"""移除总览与故事资料后，旧接口不可用且作者文件与历史记录保留。"""

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
        assert not any(
            "story-overview" in path for path in client.get("/openapi.json").json()["paths"]
        )
    assert historical.read_text(encoding="utf-8") == '{"historical":true}'


def test_materials_routes_are_gone_and_return_json_not_a_page(tmp_path: Path) -> None:
    """资料页与整理任务退出：旧地址必须回 JSON 的 404，而不是静态站点回退的 200 页面。"""
    workspace = Workspace.create(tmp_path / "作品")
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n雨仍未停。", expected_digest=None)
    workspaces = RecentWorkspaces(tmp_path / "app")
    book = workspaces.bind(workspace.root)
    with TestClient(
        create_workspace_app(workspaces=workspaces, provider_factory=no_model)
    ) as client:
        base = f"/api/file-workspaces/{book.id}"
        removed = (
            ("get", base + "/story-materials/story_index", None),
            ("get", base + "/story-materials/clues/entries/entry_1", None),
            ("get", base + "/story-events", None),
            ("get", base + "/material-tasks", None),
            ("get", base + "/material-tasks/turn_1", None),
            ("post", base + "/material-tasks", {"request_key": "k", "scope": {}}),
        )
        for method, path, payload in removed:
            response = (
                client.get(path) if method == "get" else client.post(path, json=payload or {})
            )
            assert response.status_code == 404, (method, path, response.status_code)
            assert response.headers["content-type"].startswith("application/json")
            # 路由不存在与「目标不存在」不是一回事：这里要的是路由本身不在。
            assert response.json()["detail"] == "Not Found"

        paths = client.get("/openapi.json").json()["paths"]
        assert not any(
            marker in path
            for path in paths
            for marker in ("story-materials", "story-events", "material-tasks")
        )
        # 写作相关的读取仍在：移除的是资料页，不是作品的读取能力。
        assert client.get(base + "/files").status_code == 200


def test_stop_route_is_gone_and_pause_replaces_it(tmp_path: Path) -> None:
    """旧停止入口退出：只保留按操作暂停，避免两套中断语义并存。"""
    workspace = Workspace.create(tmp_path / "作品")
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n雨仍未停。", expected_digest=None)
    workspaces = RecentWorkspaces(tmp_path / "app")
    book = workspaces.bind(workspace.root)
    with TestClient(
        create_workspace_app(workspaces=workspaces, provider_factory=no_model)
    ) as client:
        base = f"/api/file-workspaces/{book.id}"
        response = client.post(base + "/turns/turn_1/stop")
        assert response.status_code == 404
        assert response.json()["detail"] == "Not Found"
        assert "/api/operations/{operation_id}/pause" in client.get("/openapi.json").json()["paths"]


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


@pytest.mark.anyio
async def test_materials_purpose_is_rejected_instead_of_writing_as_chat(tmp_path: Path) -> None:
    """旧资料任务不能再以「整理资料」的用途跑成一次写作。"""
    workspace = Workspace.create(tmp_path / "作品")
    store = ConversationStore(tmp_path / "state")
    service = TurnService(workspace, store, no_model)
    with pytest.raises(WorkspaceError) as excinfo:
        await service.accept("整理线索", "old-materials", purpose="organize_materials")
    assert excinfo.value.code == "TURN_PURPOSE_UNAVAILABLE"
    assert store.conversations() == ()
