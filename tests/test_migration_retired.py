"""开发期直接使用 V2：迁移入口退出后，新建与正文写入仍可用。"""

from pathlib import Path

from fastapi.testclient import TestClient

from hastings.bible.store import StoryWorkspace
from hastings.cli import bible_command_output
from hastings.server.app import create_app
from hastings.server.v2 import V2Runtime
from test_server_app import stub_registry


def test_workspace_migration_routes_are_retired(tmp_path: Path) -> None:
    runtime = V2Runtime.memory()
    app = create_app(stub_registry(tmp_path), v2_runtime=runtime)
    assert not any("v2-migration" in path for path in app.openapi()["paths"])
    client = TestClient(app)
    for method, endpoint in (("GET", "status"), ("POST", "dry-run"), ("POST", "apply")):
        response = client.request(method, f"/api/old-story/v2-migration/{endpoint}")
        assert response.status_code == 404
    assert not list(tmp_path.iterdir()), "访问退役端点不得创建旧工作区"

    project = client.post("/api/v2/projects", json={"name": "新故事"})
    assert project.status_code == 200
    project_id = project.json()["project_id"]
    chapter = client.post(
        f"/api/v2/projects/{project_id}/chapters/import",
        json={"idempotency_key": "first", "title": "第一章", "text": "雨落在窗外。"},
    )
    assert chapter.status_code == 200
    assert len(client.get(f"/api/v2/projects/{project_id}/manuscript").json()["chapters"]) == 1


def test_cli_v2_workspace_points_to_workbench_without_migration(tmp_path: Path) -> None:
    workspace = StoryWorkspace(root=tmp_path, slug="old-story")
    workspace.ensure_dirs()
    workspace.settings_path.write_text("architecture_version: v2\n", encoding="utf-8")
    output = bible_command_output(workspace, "")
    assert "V2" in output and "工作台" in output
    assert "迁移" not in output
