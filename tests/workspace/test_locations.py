"""作品默认存放位置（V04-7）。

契约：新作品默认落在作者的「文档」目录；取不到文档目录时退回用户主目录，
不让「新建作品」因为路径不存在而失败。位置只由服务端解析，界面不猜本机路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace import locations
from hastings.workspace.locations import default_workspace_root, documents_dir


def test_default_root_prefers_documents_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents = tmp_path / "Documents"
    documents.mkdir()
    monkeypatch.setattr(locations, "documents_dir", lambda: documents)

    assert default_workspace_root() == documents


def test_default_root_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有文档目录时放主目录：位置要一定存在，作品才能落盘。"""
    monkeypatch.setattr(locations, "documents_dir", lambda: None)

    assert default_workspace_root() == Path.home()


def test_documents_dir_is_an_existing_directory_when_resolved() -> None:
    resolved = documents_dir()

    assert resolved is None or (resolved.is_dir() and resolved.name.lower() == "documents")
