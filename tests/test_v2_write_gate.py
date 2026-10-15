"""V1 弃用后的写路径退役证明：旧 REST 写端点整体下线（不只对 V2 拒绝）。

历史演进：T17.6 时旧写端点仍挂载、靠 409 架构门拒 V2 项目；V1 直接弃用后
（项目未上线，无存量用户），旧写端点**整体删除**——本文件证明它们不再可达，
且设置写入仍保留项目架构元数据（迁移导入与 CLI V2 路由仍依赖它）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.bible.store import StoryWorkspace
from hastings.server.app import create_app
from hastings.server.v2 import V2Runtime
from hastings.settings import (
    StorySettings,
    load_settings,
    save_settings,
    workspace_architecture_version,
)
from test_server_app import stub_registry

SLUG = "some-project"
CHAPTER_ID = "ch01"
ORIGINAL = "# 第1章\n\n旧版正文。\n"


def _make_workspace(root: Path, slug: str, architecture: str) -> StoryWorkspace:
    workspace = StoryWorkspace(root=root, slug=slug)
    workspace.ensure_dirs()
    (workspace.story_dir / "settings.yaml").write_text(
        f"architecture_version: {architecture}\n", encoding="utf-8"
    )
    (workspace.chapters_dir / f"{CHAPTER_ID}.md").write_text(ORIGINAL, encoding="utf-8")
    return workspace


def _client(root: Path) -> TestClient:
    """真实装配：V1 REST + WS 与 V2 REST 同时挂载（server/app.create_app）。"""
    return TestClient(create_app(stub_registry(root), v2_runtime=V2Runtime.memory()))


def test_legacy_write_routes_are_gone() -> None:
    """旧写端点（章节保存、草稿锁）已从路由表整体删除。"""
    schema = create_app(stub_registry(Path(".")), v2_runtime=V2Runtime.memory()).openapi()
    paths = schema["paths"]
    assert "put" not in paths["/api/{slug}/chapters/{chapter_id}"]
    assert "/api/{slug}/chapters/{chapter_id}/edit-mark" not in paths


def test_legacy_chapter_save_is_404_for_any_workspace(tmp_path: Path) -> None:
    """端点下线后对任意工作区都不可达（404），文件字节不变。"""
    workspace = _make_workspace(tmp_path, SLUG, "v1")
    client = _client(tmp_path)
    response = client.put(
        f"/api/{SLUG}/chapters/{CHAPTER_ID}",
        json={"content": "# 第1章\n\n新正文。\n", "baseRevision": 1},
    )

    assert response.status_code in (404, 405), "旧章节保存端点必须不再可达"
    assert (workspace.chapters_dir / f"{CHAPTER_ID}.md").read_text(encoding="utf-8") == ORIGINAL
    assert not (workspace.chapters_dir / ".revisions").exists(), "旧归档链不得被触发"


def test_legacy_edit_mark_is_404_for_any_workspace(tmp_path: Path) -> None:
    _make_workspace(tmp_path, SLUG, "v1")
    client = _client(tmp_path)
    response = client.put(f"/api/{SLUG}/chapters/{CHAPTER_ID}/edit-mark")
    assert response.status_code in (404, 405), "旧草稿锁端点必须不再可达"


def test_settings_save_preserves_architecture_version(tmp_path: Path) -> None:
    """V1 设置写入不得抹掉项目架构元数据（迁移导入与 CLI V2 路由仍依赖它）。"""
    workspace = _make_workspace(tmp_path, SLUG, "v2")
    save_settings(workspace, StorySettings(writing_language="English"))

    assert workspace_architecture_version(workspace) == "v2"
    assert load_settings(workspace).writing_language == "English"
    raw = (workspace.story_dir / "settings.yaml").read_text(encoding="utf-8")
    assert "architecture_version: v2" in raw


@pytest.mark.parametrize("architecture", ["v1"])
def test_v1_workspace_architecture_version_default(tmp_path: Path, architecture: str) -> None:
    workspace = _make_workspace(tmp_path, "plain", architecture)
    assert workspace_architecture_version(workspace) == "v1"
