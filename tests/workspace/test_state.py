"""最近目录登记（0.4 V04-1，计划 M05 的状态部分）。

契约：绑定 ID 由规范化绝对路径算出，同一目录重复打开只有一条记录；登记文件放在
应用状态目录，不复制作品正文；文件损坏按空登记处理，不阻断打开目录。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.state import RecentWorkspaces, binding_id, default_state_root


def _directory(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    target.mkdir()
    return target


def test_bind_is_stable_and_deduplicated(tmp_path: Path) -> None:
    root = _directory(tmp_path, "雪山旅馆")
    book = RecentWorkspaces(tmp_path / "state")

    first = book.bind(root)
    again = RecentWorkspaces(tmp_path / "state").bind(root)

    assert first.id == again.id == binding_id(root.resolve())
    assert first.name == "雪山旅馆"
    assert first.path == str(root.resolve())
    assert [binding.id for binding in book.recent()] == [first.id]


def test_recent_lists_newest_first(tmp_path: Path) -> None:
    book = RecentWorkspaces(tmp_path / "state")
    older = book.bind(_directory(tmp_path, "旧作"))
    newer = book.bind(_directory(tmp_path, "新作"))

    assert [binding.id for binding in book.recent()] == [newer.id, older.id]


def test_unknown_binding_is_not_found(tmp_path: Path) -> None:
    book = RecentWorkspaces(tmp_path / "state")

    with pytest.raises(WorkspaceError) as unknown:
        book.get("missing")
    assert unknown.value.code == "WORKSPACE_NOT_FOUND"

    with pytest.raises(WorkspaceError) as resolved:
        book.workspace("missing")
    assert resolved.value.code == "WORKSPACE_NOT_FOUND"


def test_deleted_directory_is_reported(tmp_path: Path) -> None:
    root = _directory(tmp_path, "会被删掉")
    book = RecentWorkspaces(tmp_path / "state")
    binding = book.bind(root)
    root.rmdir()

    assert book.get(binding.id).exists is False
    with pytest.raises(WorkspaceError) as gone:
        book.workspace(binding.id)
    assert gone.value.code == "WORKSPACE_NOT_FOUND"


def test_corrupt_registry_is_treated_as_empty(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "workspaces.json").write_text("{ not json", encoding="utf-8")
    book = RecentWorkspaces(state)

    assert book.recent() == ()

    binding = book.bind(_directory(tmp_path, "作品"))
    assert [item.id for item in book.recent()] == [binding.id]
    saved = json.loads((state / "workspaces.json").read_text(encoding="utf-8"))
    assert saved["items"][0]["path"] == binding.path


def test_bind_rejects_missing_path(tmp_path: Path) -> None:
    book = RecentWorkspaces(tmp_path / "state")

    with pytest.raises(WorkspaceError) as missing:
        book.bind(tmp_path / "不存在")
    assert missing.value.code == "WORKSPACE_NOT_FOUND"


def test_state_root_follows_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "自定义状态"
    monkeypatch.setenv("HASTINGS_STATE_DIR", str(custom))

    assert default_state_root() == custom
    assert RecentWorkspaces().root == custom.resolve()
