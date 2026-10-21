"""角色文件的端到端契约：目录、工具、上下文、HTTP 与真实 Git。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.context import build_system_prompt
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, digest_bytes, managed_kind
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession
from hastings.workspace.state import RecentWorkspaces

ROLE = "设定集/角色小传/林远.md"


def test_character_lifecycle_and_git_revert(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "作品")
    original = workspace.write(ROLE, "# 林远\n\n身份：侦探。\n", expected_digest=None)
    repo = GitRepo(workspace.root)
    repo.init()
    assert repo.status().clean  # 初始化必须包含嵌套角色文件。

    with pytest.raises(WorkspaceError, match="改动") as stale:
        workspace.move(ROLE, "设定集/角色小传/林侦探.md", expected_digest=digest_bytes(b"stale"))
    assert stale.value.code == "FILE_STALE_CONFLICT"

    renamed = workspace.move(ROLE, "设定集/角色小传/林侦探.md", expected_digest=original.digest)
    assert workspace.catalog().characters[0].title == "林远"
    rename_commit = repo.record("角色改名")
    assert rename_commit is not None
    assert repo.status().clean
    repo.revert_latest(rename_commit)
    assert workspace.read(ROLE).text == original.text
    assert not (workspace.root / renamed.path).exists()

    workspace.delete(ROLE, expected_digest=workspace.read(ROLE).digest)
    deletion = repo.record("删除角色小传")
    assert deletion is not None
    assert workspace.catalog().characters == ()
    repo.revert_latest(deletion)
    assert workspace.read(ROLE).text == original.text


def test_role_discovery_search_and_context_use_names_not_biographies(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "作品")
    workspace.write(
        ROLE, "# 林远\n\n唯一秘密：银钥匙属于他。\n别名：林医生。\n", expected_digest=None
    )
    workspace.write("设定集/角色小传/007.md", "没有标题\n", expected_digest=None)
    assert [entry.title for entry in workspace.catalog().characters] == ["007", "林远"]
    assert [hit.path for hit in workspace.search("林医生").hits] == [ROLE]
    prompt = build_system_prompt(workspace)
    assert ROLE in prompt
    assert "银钥匙属于他" not in prompt
    assert "先读取其小传" in prompt
    assert "设定集/角色小传.md" not in prompt


@pytest.mark.parametrize(
    "path",
    [
        "设定集/角色小传.md",
        "设定集/角色小传/../真相.md",
        "设定集/角色小传/配角/林远.md",
        "设定集/角色小传//林远.md",
        "设定集/角色小传/林远.MD",
        "设定集/角色小传/CON.backup.md",
        "设定集/角色小传/.md",
    ],
)
def test_non_character_paths_cannot_bypass_boundary(path: str, tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "作品")
    assert managed_kind(path) is None
    with pytest.raises(WorkspaceError) as rejected:
        workspace.write(path, "不能写出", expected_digest=None)
    assert rejected.value.code == "PATH_NOT_MANAGED"


@pytest.mark.parametrize("inside", [False, True])
def test_linked_character_directory_is_rejected(tmp_path: Path, inside: bool) -> None:
    workspace = Workspace.create(tmp_path / "作品")
    target = (workspace.root if inside else tmp_path) / "其他目录"
    target.mkdir()
    (target / "林远.md").write_text("不可读取\n", encoding="utf-8")
    roles = workspace.root / "设定集/角色小传"
    roles.rmdir()
    try:
        roles.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("当前系统不允许创建符号链接")
    for operation in (lambda: workspace.read(ROLE), workspace.catalog):
        with pytest.raises(WorkspaceError) as rejected:
            operation()
        assert rejected.value.code == "PATH_NOT_MANAGED"


def test_characters_cannot_be_moved_into_chapters_or_fixed_settings(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "作品")
    role = workspace.write(ROLE, "# 林远\n", expected_digest=None)
    for target, code in [
        ("正文/0001 林远.md", "FILE_KIND_MISMATCH"),
        ("设定集/线索.md", "SETTING_PATH_FIXED"),
    ]:
        with pytest.raises(WorkspaceError) as rejected:
            workspace.move(ROLE, target, expected_digest=role.digest)
        assert rejected.value.code == code
    assert workspace.read(ROLE).digest == role.digest


@pytest.mark.anyio
async def test_agent_can_create_read_rename_and_delete_a_character(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "作品")
    repo = GitRepo(workspace.root)
    repo.init()
    session = WriteSession(workspace, instruction="创建并整理林远的小传")
    tools = {tool.name: tool for tool in build_tools(workspace, RunContext(workspace, session))}
    created = await tools["write_file"].execute(
        "create", {"path": ROLE, "text": "# 林远\n\n侦探。\n"}
    )
    digest = json.loads(created.text)["digest"]
    listing = json.loads((await tools["list_files"].execute("list", {})).text)
    assert listing["characters"][0]["path"] == ROLE
    assert len(listing["settings"]) == 2
    assert "侦探" in (await tools["read_file"].execute("read", {"path": ROLE})).text
    moved = await tools["move_file"].execute(
        "move", {"source": ROLE, "target": "设定集/角色小传/林医生.md", "digest": digest}
    )
    assert json.loads(moved.text)["path"] == "设定集/角色小传/林医生.md"
    result = session.finish()
    assert result.recording == "committed"
    assert result.changed_paths == ("设定集/角色小传/林医生.md",)
    await tools["delete_file"].execute(
        "delete", {"path": "设定集/角色小传/林医生.md", "digest": digest}
    )
    assert workspace.catalog().characters == ()


def test_character_http_catalog_read_search_and_legacy_rejection(tmp_path: Path) -> None:
    def no_model() -> object:
        raise AssertionError("文件接口不应调用模型")

    workspaces = RecentWorkspaces(tmp_path / "state")
    with TestClient(
        create_workspace_app(workspaces=workspaces, provider_factory=no_model)  # type: ignore[arg-type]
    ) as client:
        root = tmp_path / "作品"
        wid = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()[
            "id"
        ]
        (root / ROLE).write_text("# 林侦探\n\n别名：林医生。\n", encoding="utf-8")
        catalog = client.get(f"/api/file-workspaces/{wid}/files").json()
        assert [(entry["path"], entry["kind"]) for entry in catalog["characters"]] == [
            (ROLE, "character")
        ]
        view = client.get(f"/api/file-workspaces/{wid}/file", params={"path": ROLE}).json()
        assert view["kind"] == "character"
        assert view["title"] == "林侦探"
        search = client.get(f"/api/file-workspaces/{wid}/search", params={"q": "林医生"})
        assert [hit["path"] for hit in search.json()["hits"]] == [ROLE]
        legacy = client.get(
            f"/api/file-workspaces/{wid}/file", params={"path": "设定集/角色小传.md"}
        )
        assert legacy.status_code == 400
        assert legacy.json()["error"]["code"] == "PATH_NOT_MANAGED"
