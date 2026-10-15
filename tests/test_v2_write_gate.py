"""Phase 17 T17.6 补验收：真实服务装配下 V1 写入口对 V2 项目必须不可达。

评审发现的漏点：不可达测试只检查独立的 ``build_v2_app``，而 ``create_app`` 实际
**同时**挂载 V1 REST。于是对 ``architecture_version: v2`` 的工作区，调用旧章节
保存端点仍返回 200 并把 Markdown 落盘——工具层拒绝桩覆盖不到 REST 这一条路径。

本文件在**真实装配**（``create_app`` + 注册表 + V2 运行时）下验证：
- V2 项目的旧写端点被拒绝，且文件字节不变；
- V1 项目的旧写端点行为不变（对照，确保没有误伤）；
- V1 设置写入不会抹掉项目架构元数据（否则 gate 可被一次保存静默解除）。
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

SLUG_V2 = "v2-project"
SLUG_V1 = "v1-project"
CHAPTER_ID = "ch01"
ORIGINAL = "# 第1章\n\n旧版正文。\n"
NEW_TEXT = "# 第1章\n\n被旧端点改写的新正文。\n"


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


def _save(client: TestClient, slug: str, text: str) -> object:
    return client.put(
        f"/api/{slug}/chapters/{CHAPTER_ID}",
        json={"content": text, "baseRevision": 1},
    )


def test_real_app_mounts_legacy_write_route() -> None:
    """前置事实：真实装配确实挂着旧写路由——所以下面的拒绝是「门」而非「404」。"""
    schema = create_app(stub_registry(Path(".")), v2_runtime=V2Runtime.memory()).openapi()
    paths = schema["paths"]
    assert "put" in paths["/api/{slug}/chapters/{chapter_id}"]
    assert "put" in paths["/api/{slug}/chapters/{chapter_id}/edit-mark"]


def test_legacy_chapter_save_rejected_for_v2_workspace(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path, SLUG_V2, "v2")
    client = _client(tmp_path)
    response = _save(client, SLUG_V2, NEW_TEXT)

    assert response.status_code >= 400, "V2 项目的旧章节保存端点必须被拒绝"
    detail = response.json()["detail"]
    assert "v2" in detail.lower() or "V2" in detail
    assert "怎样算对" in detail, "拒绝消息须带三要素"
    assert (workspace.chapters_dir / f"{CHAPTER_ID}.md").read_text(encoding="utf-8") == ORIGINAL
    assert not (workspace.chapters_dir / ".revisions").exists(), "旧归档链不得被触发"


def test_legacy_chapter_save_allowed_for_v1_workspace(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path, SLUG_V1, "v1")
    client = _client(tmp_path)
    response = _save(client, SLUG_V1, NEW_TEXT)

    assert response.status_code == 200, "V1 项目行为不得被本次改动波及"
    assert (workspace.chapters_dir / f"{CHAPTER_ID}.md").read_text(encoding="utf-8") == NEW_TEXT


def test_legacy_edit_mark_rejected_for_v2_workspace(tmp_path: Path) -> None:
    _make_workspace(tmp_path, SLUG_V2, "v2")
    client = _client(tmp_path)
    response = client.put(f"/api/{SLUG_V2}/chapters/{CHAPTER_ID}/edit-mark")
    assert response.status_code >= 400, "V2 项目的旧草稿锁端点必须被拒绝"


def test_settings_save_preserves_architecture_version(tmp_path: Path) -> None:
    """V1 设置写入不得抹掉项目架构元数据（否则 gate 可被一次保存解除）。"""
    workspace = _make_workspace(tmp_path, SLUG_V2, "v2")
    save_settings(workspace, StorySettings(writing_language="English"))

    assert workspace_architecture_version(workspace) == "v2"
    assert load_settings(workspace).writing_language == "English"
    raw = (workspace.story_dir / "settings.yaml").read_text(encoding="utf-8")
    assert "architecture_version: v2" in raw


@pytest.mark.parametrize("architecture", ["v1"])
def test_v1_workspace_architecture_version_default(tmp_path: Path, architecture: str) -> None:
    workspace = _make_workspace(tmp_path, "plain", architecture)
    assert workspace_architecture_version(workspace) == "v1"
