"""文件制工作区：Story Bible 的 YAML 读写。

设计约束：
- 全部 yaml 调用收敛在本模块，业务代码只面对 Pydantic 模型（mypy strict 下
  隔离 safe_load 返回的 Any）；
- 实体名是唯一写盘路径来源，必须过 slug 校验，杜绝路径穿越。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from hastings.bible.models import Character

# 首字符须为字母/数字/中文，其后允许词字符、中文、空格与连字符；禁点号、
# 斜杠、反斜杠等路径相关字符。
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff][\w\u4e00-\u9fff -]{0,63}$")

# Windows 保留设备名（大小写不敏感）；即便拼上 .yaml 后缀，写盘仍会 OSError。
_WINDOWS_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class InvalidEntityNameError(ValueError):
    """实体名不合法（空、超长或含路径穿越字符）。"""


def validate_entity_name(name: str) -> str:
    """校验实体名并原样返回；不合法则抛 InvalidEntityNameError。"""
    if not _NAME_PATTERN.fullmatch(name):
        msg = (
            f"Invalid entity name: {name!r}. "
            "Use letters, digits, CJK characters, spaces or hyphens (no path separators)."
        )
        raise InvalidEntityNameError(msg)
    if name.upper() in _WINDOWS_RESERVED:
        msg = f"Reserved Windows device name: {name!r}. Please pick another name."
        raise InvalidEntityNameError(msg)
    return name


def validate_slug(slug: str) -> str:
    """校验工作区 slug（目录名来源）；规则与实体名一致，杜绝路径穿越。"""
    try:
        return validate_entity_name(slug)
    except InvalidEntityNameError as exc:
        msg = f"Invalid story slug: {slug!r}（{exc}）。Slug 将成为目录名，禁含路径字符。"
        raise InvalidEntityNameError(msg) from exc


@dataclass(frozen=True, slots=True)
class StoryWorkspace:
    """一个故事工作区的路径解析与目录管理。

    布局（DESIGN.md §2.2）：
        <root>/<slug>/bible/characters/*.yaml
        <root>/<slug>/chapters/*.md
    """

    root: Path
    slug: str

    def __post_init__(self) -> None:
        validate_slug(self.slug)

    @property
    def story_dir(self) -> Path:
        return self.root / self.slug

    @property
    def bible_dir(self) -> Path:
        return self.story_dir / "bible"

    @property
    def characters_dir(self) -> Path:
        return self.bible_dir / "characters"

    @property
    def chapters_dir(self) -> Path:
        return self.story_dir / "chapters"

    def ensure_dirs(self) -> None:
        self.characters_dir.mkdir(parents=True, exist_ok=True)
        self.chapters_dir.mkdir(parents=True, exist_ok=True)


def character_path(workspace: StoryWorkspace, name: str) -> Path:
    """角色卡路径；名字不合法直接抛错，是角色写盘的唯一入口。"""
    return workspace.characters_dir / f"{validate_entity_name(name)}.yaml"


def save_character(workspace: StoryWorkspace, character: Character) -> Path:
    """落盘角色卡（同名覆盖），返回文件路径。"""
    workspace.ensure_dirs()
    path = character_path(workspace, character.name)
    path.write_text(
        yaml.safe_dump(character.model_dump(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def load_character(workspace: StoryWorkspace, name: str) -> Character | None:
    """按名读取角色卡；不存在返回 None。"""
    path = character_path(workspace, name)
    if not path.exists():
        return None
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Character.model_validate(raw)


def character_to_yaml(character: Character) -> str:
    """角色卡序列化为 YAML 文本（bible_query 的结果展示用）。"""
    return yaml.safe_dump(character.model_dump(), allow_unicode=True, sort_keys=False)


def list_characters(workspace: StoryWorkspace) -> list[Character]:
    """列出全部角色卡，按文件名排序；目录不存在视为空库。"""
    if not workspace.characters_dir.exists():
        return []
    return [
        Character.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(workspace.characters_dir.glob("*.yaml"))
    ]


__all__ = [
    "InvalidEntityNameError",
    "StoryWorkspace",
    "character_path",
    "character_to_yaml",
    "list_characters",
    "load_character",
    "save_character",
    "validate_entity_name",
    "validate_slug",
]
