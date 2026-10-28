"""阅读位置随作品状态恢复，拒绝越权路径与删除后的迟到保存。"""

from pathlib import Path

from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.files import Workspace
from hastings.workspace.state import RecentWorkspaces, conversation_root


def test_reading_position_survives_restart_and_rename_without_model(tmp_path: Path) -> None:
    registry = RecentWorkspaces(tmp_path / "state")

    def client() -> TestClient:
        return TestClient(
            create_workspace_app(
                workspaces=registry,
                provider_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            )
        )

    api = client()
    created = api.post(
        "/api/file-workspaces", json={"path": str(tmp_path / "book"), "create": True}
    ).json()
    wid = created["id"]
    root = conversation_root(registry.root, wid)
    url = f"/api/file-workspaces/{wid}/reading-position"
    assert api.get(url).json()["path"] is None
    assert not (root / "reading-position.json").exists()
    workspace = Workspace(Path(created["path"]))
    source = workspace.write("正文/0002 第二章.md", "# 正文\n\n内容。\n", expected_digest=None)
    position = {"path": source.path, "digest": source.digest, "offset": 432.5}
    assert api.patch(url, json=position).status_code == 200
    assert client().get(url).json() == position
    assert api.patch(url, json={**position, "path": "../../secret.txt"}).status_code == 422
    assert api.patch(url, json={**position, "offset": -1}).status_code == 422
    renamed = api.patch(f"/api/file-workspaces/{wid}", json={"name": "renamed"}).json()["workspace"]
    new_url = f"/api/file-workspaces/{renamed['id']}/reading-position"
    assert client().get(new_url).json() == position
    assert api.patch(url, json=position).status_code == 404
    Workspace(Path(renamed["path"])).write(
        source.path, "# 改后的正文\n", expected_digest=source.digest
    )
    assert api.get(new_url).json()["offset"] == 0
    assert api.patch(new_url, json=position).status_code == 409
    deleted = api.request(
        "DELETE", f"/api/file-workspaces/{renamed['id']}", json={"confirmed_path": renamed["path"]}
    )
    assert deleted.status_code == 200, deleted.text
    assert api.patch(new_url, json=position).status_code == 404
    assert not conversation_root(registry.root, renamed["id"]).exists()
