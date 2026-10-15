"""文件制工作区：Story Bible 的 YAML 读写。

设计约束：
- 全部 yaml 调用收敛在本模块，业务代码只面对 Pydantic 模型（mypy strict 下
  隔离 safe_load 返回的 Any）；
- 实体名是唯一写盘路径来源，必须过 slug 校验，杜绝路径穿越。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel

from hastings.bible.models import Character, Clue, Foreshadow, Method, OutlineEntry, TimelineEntry
from hastings.bible.story_state import StoryDoc

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

    布局（DESIGN.md §2.2 + 计划 D7）：
        <root>/<slug>/bible/characters/*.yaml
        <root>/<slug>/bible/{clues,foreshadows,method,timeline}.yaml
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

    @property
    def settings_path(self) -> Path:
        """工作区级设置文件（P6 写作语言）；缺文件按默认值处理。"""
        return self.story_dir / "settings.yaml"

    @property
    def story_path(self) -> Path:
        """工作区生命周期文件（P8 T1，计划 D1）；缺文件按 intake 兜底。"""
        return self.story_dir / "story.yaml"

    @property
    def clues_path(self) -> Path:
        return self.bible_dir / "clues.yaml"

    @property
    def foreshadows_path(self) -> Path:
        return self.bible_dir / "foreshadows.yaml"

    @property
    def method_path(self) -> Path:
        return self.bible_dir / "method.yaml"

    @property
    def timeline_path(self) -> Path:
        return self.bible_dir / "timeline.yaml"

    @property
    def outline_path(self) -> Path:
        """章纲账本（P8 T3，计划 D3）：outlines.yaml 列表项。"""
        return self.bible_dir / "outlines.yaml"

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


def model_to_yaml(entity: BaseModel) -> str:
    """任意 Bible 实体序列化为 YAML 文本（bible_query 的结果展示用）。"""
    return yaml.safe_dump(entity.model_dump(), allow_unicode=True, sort_keys=False)


def list_characters(workspace: StoryWorkspace) -> list[Character]:
    """列出全部角色卡，按文件名排序；目录不存在视为空库。"""
    if not workspace.characters_dir.exists():
        return []
    return [
        Character.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(workspace.characters_dir.glob("*.yaml"))
    ]


# --- P2 T2：单文件账本（D7：列表 + name 主键；upsert 合并逻辑在工具层） ---

# 约束型类型参数：model_validate 返回 Self，load_ledger 调用点收窄为传入的模型类型。


class LedgerFormatError(ValueError):
    """账本文件根节点非列表等结构性损坏。

    区别于"空账本"（合法状态，返回 []）与 pydantic 字段错误（原样上抛），
    这是环境/数据故障，需要显式暴露给用户修复。
    """


def load_ledger[M: (Clue, Foreshadow, Method, OutlineEntry, TimelineEntry)](
    path: Path, model_cls: type[M]
) -> list[M]:
    """读取单文件账本；缺文件或空文件视为空账本（P1 旧工作区兼容）。"""
    if not path.exists():
        return []
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        msg = f"账本文件损坏（根节点应为 YAML 列表，实际为 {type(raw).__name__}）：{path}"
        raise LedgerFormatError(msg)
    return [model_cls.model_validate(item) for item in raw]


def save_ledger[M: (Clue, Foreshadow, Method, OutlineEntry, TimelineEntry)](
    path: Path, items: Sequence[M]
) -> Path:
    """整表覆盖写单文件账本，返回文件路径；自建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [item.model_dump() for item in items]
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


# --- P8 T1b：story.yaml 生命周期（V1 弃用后只读；save_story_doc 仅为夹具级工具） ---


def load_story_doc(workspace: StoryWorkspace) -> StoryDoc:
    """读取 story.yaml；缺文件（含 P7 及更早的旧工作区）按 intake 兜底——
    历史工作区可能没有该文件；V1 弃用后不再有生产写入口，缺失即按缺省读。"""
    path = workspace.story_path
    if not path.exists():
        return StoryDoc(slug=workspace.slug)
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return StoryDoc.model_validate(raw)


def save_story_doc(workspace: StoryWorkspace, doc: StoryDoc) -> Path:
    """整文件覆盖写 story.yaml，返回文件路径；自建父目录。"""
    workspace.story_dir.mkdir(parents=True, exist_ok=True)
    path = workspace.story_path
    path.write_text(
        yaml.safe_dump(doc.model_dump(by_alias=True), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


__all__ = [
    "InvalidEntityNameError",
    "LedgerFormatError",
    "StoryWorkspace",
    "character_path",
    "list_characters",
    "load_character",
    "load_ledger",
    "load_story_doc",
    "model_to_yaml",
    "save_character",
    "save_ledger",
    "save_story_doc",
    "validate_entity_name",
    "validate_slug",
]
