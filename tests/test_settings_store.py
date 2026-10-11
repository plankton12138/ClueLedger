"""工作区设置存储测试（P6）：默认值 / 往返 / 结构损坏。

沿用 bible 账本测试的哲学：缺文件与空文件是合法状态（回落默认），
只有结构性损坏（根节点非映射）才是需要显式暴露的数据故障。
"""

from pathlib import Path

import pytest
import yaml

from hastings.bible.store import StoryWorkspace
from hastings.settings import (
    DEFAULT_WRITING_LANGUAGE,
    WRITING_LANGUAGES,
    SettingsFormatError,
    StorySettings,
    load_settings,
    save_settings,
)


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path, slug="demo")


def test_default_language_is_chinese() -> None:
    assert DEFAULT_WRITING_LANGUAGE == "中文"
    assert DEFAULT_WRITING_LANGUAGE in WRITING_LANGUAGES


def test_load_settings_missing_file_returns_default(workspace: StoryWorkspace) -> None:
    """旧工作区无 settings.yaml → 默认中文（向后兼容）。"""
    assert load_settings(workspace).writing_language == DEFAULT_WRITING_LANGUAGE


def test_load_settings_empty_file_returns_default(workspace: StoryWorkspace) -> None:
    workspace.ensure_dirs()
    workspace.settings_path.write_text("", encoding="utf-8")

    assert load_settings(workspace).writing_language == DEFAULT_WRITING_LANGUAGE


def test_save_then_load_roundtrip(workspace: StoryWorkspace) -> None:
    save_settings(workspace, StorySettings(writing_language="English"))

    assert workspace.settings_path.exists()
    assert load_settings(workspace).writing_language == "English"
    raw = yaml.safe_load(workspace.settings_path.read_text(encoding="utf-8"))
    assert raw == {
        "writing_language": "English",
        "provider_profile": "default",
        "model": "",
        "context_window_tokens": 128_000,
    }


def test_load_settings_non_mapping_root_raises(workspace: StoryWorkspace) -> None:
    workspace.ensure_dirs()
    workspace.settings_path.write_text("- 只是列表\n", encoding="utf-8")

    with pytest.raises(SettingsFormatError):
        load_settings(workspace)


def test_save_settings_overwrites(workspace: StoryWorkspace) -> None:
    save_settings(workspace, StorySettings(writing_language="日本語"))
    save_settings(workspace, StorySettings(writing_language="中文"))

    assert load_settings(workspace).writing_language == "中文"
