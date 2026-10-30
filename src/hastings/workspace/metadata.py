"""作品元信息：章序、显示名与人物身份标签（0.5 V05-3A）。

章序只来自文件的四位编号，不按列表位置重新编号：缺了第二章时，第三章仍然显示为
「第三章」。显示名优先采用开头明确的章节标题，其次采用有意义的一级标题，最后回退
文件名；与作品名相同或跨章重复的一级标题多半是书名，回退时留下诊断而不静默采用。

人物身份写在人物 Markdown 开头的结构化元信息里（``roles: [suspect, culprit]``），
和正文一起走指纹与 Git。这里只解析、校验并给出诊断：未知值、重复键与损坏的元信息
不会被猜成标签，原文仍按正文可读。解析结果供目录、稿纸、搜索定位和模型目录共用，
前置条件是不让 HTTP、工具与前端各自再拼一套规则。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

__all__ = [
    "ROLE_LABELS",
    "ChapterMeta",
    "ChapterType",
    "CharacterMeta",
    "DeclaredFact",
    "FrontMatter",
    "Role",
    "check_character_metadata",
    "chinese_number",
    "filename_without_order",
    "parse_chapter",
    "parse_chapters",
    "parse_character",
    "split_front_matter",
]

Role = Literal["detective", "assistant", "culprit", "suspect", "victim"]
ChapterType = Literal["regular", "prologue", "epilogue", "extra"]

#: 保存值到界面文字的唯一映射；顺序即标签展示顺序。
ROLE_LABELS: dict[Role, str] = {
    "detective": "侦探",
    "assistant": "助手",
    "culprit": "凶手",
    "suspect": "嫌疑人",
    "victim": "受害者",
}
PLACEHOLDER_TITLE = "（未命名）"

_ORDER = re.compile(r"^(\d{4})(?!\d)")
_H1 = re.compile(r"^#\s+(.+?)\s*#*\s*$")
_H2 = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_NUMBERED = re.compile(
    r"^第\s*([0-9０-９]+|[〇零一二三四五六七八九十百千两]+)\s*[章回节]"
    r"\s*[、,，.．:：\-—·]?\s*(.*)$"
)
_SPECIAL = re.compile(
    r"^(序章|序幕|楔子|引子|尾声|终章|后记|番外)(?:\s*[、,，.．:：\-—·]?\s*(.*))?$"
)
_SPECIAL_TYPES: dict[str, ChapterType] = {
    "序章": "prologue",
    "序幕": "prologue",
    "楔子": "prologue",
    "引子": "prologue",
    "尾声": "epilogue",
    "终章": "epilogue",
    "后记": "epilogue",
    "番外": "extra",
}
_TYPE_LABELS: dict[ChapterType, str] = {"prologue": "序章", "epilogue": "尾声", "extra": "番外"}

#: 标题只在开头这些行里找：章名应当在正文之前。
_HEADING_WINDOW = 20
#: 元信息只在文件开头这么大范围内解析；超出的按正文处理，避免整份文档被当成配置。
_METADATA_MAX_CHARS = 2000
_METADATA_MAX_LINES = 40

_CHINESE_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}
_DIGITS_TEXT = "零一二三四五六七八九"
_UNITS_TEXT = ("千", "百", "十", "")


def chinese_number(value: int) -> str:
    """阿拉伯数字转章节用中文序号；只覆盖 1—9999，超出时原样返回数字。"""
    if not 0 < value <= 9999:
        return str(value)
    parts: list[str] = []
    zero_pending = False
    for index, unit in enumerate(_UNITS_TEXT):
        base = 10 ** (3 - index)
        digit = value // base % 10
        if digit == 0:
            if parts and value % base:
                zero_pending = True
            continue
        if zero_pending:
            parts.append("零")
            zero_pending = False
        parts.append(_DIGITS_TEXT[digit] + unit)
    text = "".join(parts)
    # 10—19 读作「十」「十一」，不写成「一十」。
    return text[1:] if text.startswith("一十") else text


@dataclass(frozen=True, slots=True)
class ChapterMeta:
    """一章的显示信息：纯章名、带章序的显示名、编号与诊断。"""

    path: str
    title: str
    display_title: str
    number: int | None
    chapter_type: ChapterType
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeclaredFact:
    """作者声明的一条身份披露：第 ``chapter`` 章起读者已知（或计划在该章披露）。"""

    fact: str
    chapter: int


@dataclass(frozen=True, slots=True)
class CharacterMeta:
    """一名角色的显示信息；``display_text`` 去掉了元信息，原文仍在 ``text``。"""

    path: str
    title: str
    roles: tuple[Role, ...]
    display_text: str
    warnings: tuple[str, ...] = ()
    surface: str = ""
    aliases: tuple[str, ...] = ()
    revealed: tuple[DeclaredFact, ...] = ()
    planned: tuple[DeclaredFact, ...] = ()


@dataclass(frozen=True, slots=True)
class FrontMatter:
    """文件开头元信息的解析结果；``present`` 表示读到了成对的 ``---`` 围栏。"""

    fields: dict[str, list[str]]
    body: str
    warnings: tuple[str, ...]
    present: bool


def parse_chapter(path: str, text: str) -> ChapterMeta:
    """解析单章；跨文件的重复编号与重复书名由 :func:`parse_chapters` 补齐。"""
    signals = _scan_chapter(path, text)
    return _finish_chapter(signals, signals.warnings)


def filename_without_order(filename: str) -> str:
    """去掉文件名开头的四位编号与分隔符，其余部分（含「序章」这类标记）原样保留。

    导入时一级标题若是书名，回退用它而不是整条文件名：编号是存储用的，再拼一次会变成
    「0001 0001 暴雪来客.md」；而「序章」这类标记属于章名，去掉就丢了章节类型。
    """
    stem = PurePosixPath(filename).stem
    order = _ORDER.match(stem)
    base = stem[len(order.group(0)) :] if order else stem
    return base.strip(" -_·")


def parse_chapters(work_name: str, items: Iterable[tuple[str, str]]) -> tuple[ChapterMeta, ...]:
    """解析整本正文：先各自取候选，再统一判断重复书名与重复章序。"""
    signals = [_scan_chapter(path, text) for path, text in items]
    title_counts = Counter(
        _fold(item.heading_title)
        for item in signals
        if item.source == "heading" and item.heading_title
    )
    number_counts = Counter(
        item.number for item in signals if item.kind == "regular" and item.number is not None
    )
    folded_work = _fold(work_name)
    metas: list[ChapterMeta] = []
    for item in signals:
        warnings = list(item.warnings)
        name = item.name
        if item.source == "heading" and item.heading_title:
            folded = _fold(item.heading_title)
            if title_counts[folded] > 1:
                warnings.append(
                    f"一级标题「{item.heading_title}」在多章重复，已改用文件名中的章名"
                    "；确认是书名时请改成各章自己的标题。"
                )
                name = item.filename_name
            elif folded_work and folded == folded_work:
                warnings.append(
                    f"一级标题「{item.heading_title}」与作品名相同，已改用文件名中的章名"
                    "；这一行更像书名，不是章名。"
                )
                name = item.filename_name
        if item.kind == "regular" and item.number is not None and number_counts[item.number] > 1:
            warnings.append(
                f"正文里有第{chinese_number(item.number)}章的两个文件；"
                "显示沿用文件名，请改成不同章序。"
            )
        metas.append(_finish_chapter(item, warnings, name=name))
    return tuple(metas)


def parse_character(path: str, text: str) -> CharacterMeta:
    """解析人物小传：身份标签只来自元信息，格式问题给诊断，不猜测。"""
    front = split_front_matter(text)
    warnings = list(front.warnings)
    roles = _roles_from(front.fields, warnings) if front.present else ()
    surface = ""
    aliases: tuple[str, ...] = ()
    revealed: tuple[DeclaredFact, ...] = ()
    planned: tuple[DeclaredFact, ...] = ()
    if front.present:
        surface = _first(front.fields.get("surface"))
        aliases = tuple(dict.fromkeys(front.fields.get("aliases", [])))
        revealed = _facts_from(front.fields, "revealed", "已披露", warnings)
        planned = _facts_from(front.fields, "planned", "计划披露", warnings)
    return CharacterMeta(
        path=path,
        title=_first_h1(front.body) or PurePosixPath(path).stem,
        roles=roles,
        display_text=front.body,
        warnings=tuple(warnings),
        surface=surface,
        aliases=aliases,
        revealed=revealed,
        planned=planned,
    )


def check_character_metadata(text: str) -> tuple[str, ...]:
    """写入前检查人物元信息：返回必须拒绝的问题，空元组表示可以落盘。

    外部编辑器留下的损坏元信息按正文可读并给诊断；应用自己的写入不能把新的坏格式
    存进去，否则下一次读取只能猜测身份。
    """
    if not text.startswith("---\n"):
        return ()
    front = split_front_matter(text)
    problems = list(front.warnings)
    if front.present:
        _roles_from(front.fields, problems)
        if len(front.fields.get("surface", [])) > 1:
            problems.append("surface 只能写一个社会身份；多个身份请写成披露条目。")
        _facts_from(front.fields, "revealed", "已披露", problems)
        _facts_from(front.fields, "planned", "计划披露", problems)
    return tuple(problems)


def split_front_matter(text: str) -> FrontMatter:
    """拆出开头的 ``---`` 元信息；损坏或过长时保留原文，只加诊断。"""
    if not text.startswith("---\n"):
        return FrontMatter({}, text, (), False)
    lines = text.split("\n")
    closing = next(
        (
            index
            for index in range(1, min(len(lines), _METADATA_MAX_LINES))
            if _is_fence(lines[index])
        ),
        None,
    )
    if closing is None:
        return FrontMatter(
            {},
            text,
            ("小传开头的元信息没有结束的 ---，已按正文显示。",),
            False,
        )
    if len("\n".join(lines[: closing + 1])) > _METADATA_MAX_CHARS:
        return FrontMatter(
            {},
            text,
            ("小传开头的元信息过长，已跳过解析并按正文显示。",),
            False,
        )
    fields, warnings = _parse_fields(lines[1:closing])
    body = "\n".join(lines[closing + 1 :])
    if body.startswith("\n"):
        body = body[1:]
    return FrontMatter(fields, body, tuple(warnings), True)


# -- 内部：章节 -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ChapterSignals:
    path: str
    kind: ChapterType
    number: int | None
    name: str
    source: Literal["explicit", "heading", "filename"]
    heading_title: str | None
    filename_name: str
    warnings: tuple[str, ...]


def _scan_chapter(path: str, text: str) -> _ChapterSignals:
    filename = PurePosixPath(path).name
    stem = PurePosixPath(filename).stem
    order = _ORDER.match(filename)
    file_number = int(order.group(1)) if order else None
    # 去掉编号时按 stem 截取，避免把扩展名带进章名。
    base = stem[len(order.group(0)) :] if order else stem
    filename_kind, _, filename_name = _split_marker(base.strip(" -_·"))

    explicit: tuple[ChapterType | None, int | None, str] | None = None
    plain_heading: str | None = None
    for line in text.split("\n")[:_HEADING_WINDOW]:
        h2 = _H2.match(line)
        if h2:
            kind, heading_number, name = _split_marker(h2.group(1))
            if kind or heading_number is not None:
                explicit = (kind, heading_number, name)
                break
            continue
        h1 = _H1.match(line)
        if h1:
            kind, heading_number, name = _split_marker(h1.group(1))
            if kind or heading_number is not None:
                explicit = (kind, heading_number, name)
                break
            if plain_heading is None:
                plain_heading = h1.group(1).strip()

    warnings: list[str] = []
    kind = filename_kind or (explicit[0] if explicit else None) or "regular"
    if filename_kind and explicit and explicit[0] and explicit[0] != filename_kind:
        warnings.append("文件名与标题的章节类型不一致，显示沿用文件名。")
    if (
        file_number is not None
        and explicit
        and explicit[1] is not None
        and explicit[1] != file_number
    ):
        warnings.append(
            f"标题写的是第{chinese_number(explicit[1])}章，文件名编号是"
            f"第{chinese_number(file_number)}章；显示沿用文件名编号。"
        )
    # 章序只取文件名编号：标题里的「第三章」用来提取章名与给诊断，不改章序。
    # 否则同一本书会出现「标题写的章」与「文件排第几」两套顺序，缺章时更难对齐。
    if file_number is None and kind == "regular":
        if explicit and explicit[1] is not None:
            warnings.append(
                f"标题写的是第{chinese_number(explicit[1])}章，文件名没有四位章序；"
                "显示不编造编号，需要章序时把文件重命名为「0005 章名.md」。"
            )
        else:
            warnings.append(
                "文件名没有四位章序，显示不编造编号；需要章序时把文件重命名为「0005 章名.md」。"
            )

    if explicit:
        return _ChapterSignals(
            path=path,
            kind=kind,
            number=file_number,
            name=explicit[2],
            source="explicit",
            heading_title=None,
            filename_name=filename_name,
            warnings=tuple(warnings),
        )
    if plain_heading:
        return _ChapterSignals(
            path=path,
            kind=kind,
            number=file_number,
            name=plain_heading,
            source="heading",
            heading_title=plain_heading,
            filename_name=filename_name,
            warnings=tuple(warnings),
        )
    return _ChapterSignals(
        path=path,
        kind=kind,
        number=file_number,
        name=filename_name,
        source="filename",
        heading_title=None,
        filename_name=filename_name,
        warnings=tuple(warnings),
    )


def _finish_chapter(
    signals: _ChapterSignals, warnings: Iterable[str], *, name: str | None = None
) -> ChapterMeta:
    title = (signals.name if name is None else name).strip()
    display = _display_title(signals.kind, signals.number, title)
    return ChapterMeta(
        path=signals.path,
        title=title or PLACEHOLDER_TITLE,
        display_title=display,
        number=signals.number,
        chapter_type=signals.kind,
        warnings=tuple(warnings),
    )


def _display_title(kind: ChapterType, number: int | None, name: str) -> str:
    if kind == "regular":
        if number is None:
            return name or PLACEHOLDER_TITLE
        prefix = f"第{chinese_number(number)}章"
        return f"{prefix} {name}" if name else f"{prefix}{PLACEHOLDER_TITLE}"
    label = _TYPE_LABELS[kind]
    return f"{label} {name}" if name else label


def _split_marker(text: str) -> tuple[ChapterType | None, int | None, str]:
    """把「序章 暴雪前的信」「第三章 书房里的脚印」拆成类型、章序与纯章名。"""
    clean = text.strip(" -_·")
    special = _SPECIAL.match(clean)
    if special:
        return _SPECIAL_TYPES[special.group(1)], None, (special.group(2) or "").strip()
    numbered = _NUMBERED.match(clean)
    if numbered:
        return None, _number_value(numbered.group(1)), numbered.group(2).strip()
    return None, None, clean


def _number_value(token: str) -> int | None:
    text = token.strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if text.isdigit():
        return int(text)
    total = 0
    digit: int | None = None
    for char in text:
        if char in _CHINESE_DIGITS:
            digit = _CHINESE_DIGITS[char]
        elif char in _CHINESE_UNITS:
            total += (1 if digit is None else digit) * _CHINESE_UNITS[char]
            digit = None
        else:
            return None
    return total + (digit or 0)


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def _first_h1(text: str) -> str:
    for line in text.split("\n")[:_HEADING_WINDOW]:
        match = _H1.match(line)
        if match:
            return match.group(1).strip()
    return ""


# -- 内部：元信息 -----------------------------------------------------------


def _is_fence(line: str) -> bool:
    return line.strip() in {"---", "..."}


def _parse_fields(lines: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    fields: dict[str, list[str]] = {}
    warnings: list[str] = []
    current: str | None = None
    for raw in lines:
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        value = line.strip()
        if value.startswith("- "):
            if current is None:
                warnings.append(f"元信息里有一项没有字段名：{value[:40]}")
                continue
            item = _unquote(value[2:].strip())
            if item:
                fields[current].append(item)
            continue
        if ":" not in line:
            warnings.append(f"元信息行无法识别：{value[:40]}")
            current = None
            continue
        key, _, rest = line.partition(":")
        key = key.strip().lower()
        rest = rest.strip()
        if not key:
            warnings.append(f"元信息行缺少字段名：{value[:40]}")
            current = None
            continue
        if key in fields:
            warnings.append(f"元信息字段「{key}」重复，保留第一次的值。")
            current = None
            continue
        fields[key] = []
        if rest.startswith("[") and rest.endswith("]"):
            fields[key] = [_unquote(part) for part in rest[1:-1].split(",") if part.strip()]
            current = None
        elif rest:
            fields[key] = [_unquote(rest)]
            current = None
        else:
            # 空值后面可能跟一段「- 值」列表。
            current = key
    return fields, warnings


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value.strip()


def _roles_from(fields: dict[str, list[str]], warnings: list[str]) -> tuple[Role, ...]:
    roles: list[Role] = []
    for value in fields.get("roles", []):
        key = value.strip().casefold()
        if not key:
            continue
        if key not in ROLE_LABELS:
            allowed = "、".join(ROLE_LABELS.values())
            warnings.append(f"未知的人物身份「{value}」，已忽略；可用身份：{allowed}。")
            continue
        role: Role = key  # 已按 ROLE_LABELS 校验。
        if role not in roles:
            roles.append(role)
    return tuple(roles)


def _first(values: list[str] | None) -> str:
    """单值字段：只取第一项，多余的值由写入检查挡在落盘之前。"""
    return values[0].strip() if values else ""


def _facts_from(
    fields: dict[str, list[str]],
    key: str,
    label: str,
    warnings: list[str],
) -> tuple[DeclaredFact, ...]:
    """解析「章节号:事实」形式的披露条目；坏条目单独作废，不连累同一字段的其他条目。

    格式必须是完整一项：章节号是正整数、事实非空。缺一半、章节号写成中文或条目里
    又冒出一个冒号，都按该项作废并给诊断——解析器不替作者猜意思。
    """
    facts: list[DeclaredFact] = []
    seen: set[tuple[int, str]] = set()
    for value in fields.get(key, []):
        chapter_text, sep, fact = value.partition(":")
        fact = fact.strip()
        chapter_text = chapter_text.strip()
        if not sep or not fact:
            warnings.append(
                f"{label}条目「{value[:40]}」格式不对，已忽略；写法是「章节号:事实」，例如 9:凶手。"
            )
            continue
        if not chapter_text.isdigit() or int(chapter_text) <= 0:
            warnings.append(f"{label}条目「{value[:40]}」的章节号不是正整数，已忽略。")
            continue
        item = (int(chapter_text), fact)
        if item in seen:
            continue
        seen.add(item)
        facts.append(DeclaredFact(fact=fact, chapter=item[0]))
    facts.sort(key=lambda declared: declared.chapter)
    return tuple(facts)
