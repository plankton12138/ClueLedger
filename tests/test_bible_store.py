"""Bible 存储层测试：YAML 往返、非法名拒绝、空库行为。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from hastings.bible.models import Character
from hastings.bible.store import (
    InvalidEntityNameError,
    StoryWorkspace,
    list_characters,
    load_character,
    save_character,
)


def _ws(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path / "stories", slug="demo")


def test_character_requires_name() -> None:
    with pytest.raises(ValidationError):
        Character(name="")


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    character = Character(
        name="波洛",
        identity="退休侦探",
        motivation="维护正义",
        secret="过去的一桩悬案",
        relationships=["信任黑斯廷斯", "怀疑管家"],
        notes="小小的灰色细胞",
    )
    path = save_character(ws, character)

    assert path == ws.characters_dir / "波洛.yaml"
    assert path.exists()
    assert load_character(ws, "波洛") == character


def test_yaml_keeps_unicode(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = save_character(ws, Character(name="波洛", identity="退休侦探"))

    assert "退休侦探" in path.read_text(encoding="utf-8")


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_character(_ws(tmp_path), "不存在") is None


def test_list_empty_when_no_dir(tmp_path: Path) -> None:
    assert list_characters(_ws(tmp_path)) == []


def test_list_sorted_by_filename(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    save_character(ws, Character(name="b-嫌疑人"))
    save_character(ws, Character(name="a-侦探"))

    assert [c.name for c in list_characters(ws)] == ["a-侦探", "b-嫌疑人"]


@pytest.mark.parametrize(
    "bad_name",
    ["../evil", "/abs", "a/b", "a\\b", " leading", "dot.name", "semi;colon", "trailing."],
)
def test_invalid_names_rejected(tmp_path: Path, bad_name: str) -> None:
    ws = _ws(tmp_path)
    with pytest.raises(InvalidEntityNameError):
        save_character(ws, Character(name=bad_name))
    with pytest.raises(InvalidEntityNameError):
        load_character(ws, bad_name)


@pytest.mark.parametrize("reserved", ["CON", "nul", "Com1", "aux", "LPT2"])
def test_windows_reserved_names_rejected(tmp_path: Path, reserved: str) -> None:
    """R3 #3：保留设备名在任何大小写下都拒绝，避免写盘 OSError 让模型盲目重试。"""
    ws = _ws(tmp_path)
    with pytest.raises(InvalidEntityNameError, match="(?i)reserved"):
        save_character(ws, Character(name=reserved))


@pytest.mark.parametrize("bad_slug", ["../escape", "a/b", "a\\b", "dot.name", "..", ""])
def test_invalid_slug_rejected(tmp_path: Path, bad_slug: str) -> None:
    """R3 #2：slug 是目录名来源，与实体名同等穿越防护。"""
    with pytest.raises(InvalidEntityNameError):
        StoryWorkspace(root=tmp_path, slug=bad_slug)


def test_valid_slug_accepted(tmp_path: Path) -> None:
    ws = StoryWorkspace(root=tmp_path, slug="orient-express")
    assert ws.story_dir == tmp_path / "orient-express"
