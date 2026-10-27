"""作品默认存放位置（V04-7）。

契约：作品与模块归入 ClueLedger；系统文档目录不可用时改用主目录下的总目录。
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

    assert default_workspace_root() == documents / "ClueLedger" / "作品"
    assert not (documents / "ClueLedger").exists()
    assert locations.default_data_root() == documents / "ClueLedger"


def test_default_root_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有文档目录时仍保持 ClueLedger 总目录结构。"""
    monkeypatch.setattr(locations, "documents_dir", lambda: None)

    assert default_workspace_root() == Path.home() / "ClueLedger" / "作品"


def test_documents_dir_is_an_existing_directory_when_resolved() -> None:
    resolved = documents_dir()

    assert resolved is None or (resolved.is_dir() and resolved.name.lower() == "documents")
