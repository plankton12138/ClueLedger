"""库条目的 Markdown 与头部字段（0.8 B）。

一条条目 = 受控头部 + 自由正文。头部只放可筛选的短字段，长说明写在正文里；同一条目在
模块内用稳定 ID 标识，改名不改 ID。这里负责渲染、解析与结构校验，不做发布与版本。

格式是 YAML 头部加正文，字段固定为 ``id``、``schema_version``、``kind``、``title``、
``aliases`` 与 ``fields``；``fields`` 里允许的类型由预设类型决定。未知字段直接拒绝，
避免库和检索对同一字段有两种理解。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

from hastings.library.ids import normalize_entry_id, source_path
from hastings.library.presets import spec
from hastings.workspace.errors import WorkspaceError

__all__ = [
    "MAX_BODY_CHARS",
    "MAX_ENTRIES",
    "MAX_TITLE_CHARS",
    "RECORD_SCHEMA_VERSION",
    "EntryDraft",
    "entry_digest",
    "entry_markdown",
    "entry_view",
    "parse_entry",
    "validate_entries",
]

RECORD_SCHEMA_VERSION = 1
MAX_TITLE_CHARS = 200
MAX_BODY_CHARS = 20_000
MAX_ENTRIES = 500
MAX_ALIASES = 20
MAX_ALIAS_CHARS = 64
MAX_FIELDS = 40


@dataclass(frozen=True, slots=True)
class EntryDraft:
    """一份准备发布的条目内容。"""

    entry_id: str
    kind: str
    title: str
    body: str = ""
    aliases: tuple[str, ...] = ()
    fields: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = RECORD_SCHEMA_VERSION

    @property
    def chars(self) -> int:
        return len(entry_markdown(self))

    def source_path(self, library_id: str, module_id: str, revision_id: str) -> str:
        return source_path(library_id, module_id, revision_id, self.entry_id)


def entry_markdown(draft: EntryDraft) -> str:
    """按固定字段顺序渲染；同样的内容总是得到同样的字节与指纹。"""
    header: dict[str, Any] = {
        "id": draft.entry_id,
        "schema_version": RECORD_SCHEMA_VERSION,
        "kind": draft.kind,
        "title": draft.title,
    }
    if draft.aliases:
        header["aliases"] = list(draft.aliases)
    if draft.fields:
        header["fields"] = dict(draft.fields)
    front = yaml.safe_dump(header, allow_unicode=True, sort_keys=False).rstrip("\n")
    body = draft.body.strip()
    return f"---\n{front}\n---\n{body}" if body else f"---\n{front}\n---\n"


def entry_digest(markdown: str) -> str:
    """指纹按实际写入的 UTF-8 字节计算，与文件读取使用同一口径。"""
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def parse_entry(text: str, *, module_kind: str) -> EntryDraft:
    """解析并校验一段条目原文；格式问题按位置报错，不猜测缺省值。"""
    return _validate(_parse(text), module_kind=module_kind)


def validate_entries(drafts: list[EntryDraft], *, module_kind: str) -> tuple[EntryDraft, ...]:
    """整批校验：空清单、重复 ID 与单条格式问题都在写入前拒绝。"""
    if len(drafts) > MAX_ENTRIES:
        raise WorkspaceError(
            code="RECORD_CONTENT_TOO_LARGE",
            where="library.records",
            why=f"一个版本最多 {MAX_ENTRIES} 条，当前 {len(drafts)} 条",
            fix="拆成多个模块，或先精简这一版的内容",
            details={"entries": len(drafts), "limit": MAX_ENTRIES},
        )
    seen: set[str] = set()
    checked: list[EntryDraft] = []
    for draft in drafts:
        normalized = _validate(draft, module_kind=module_kind)
        if normalized.entry_id in seen:
            raise WorkspaceError(
                code="RECORD_ID_CONFLICT",
                where="library.records",
                why=f"同一版本里出现重复条目 ID：{normalized.entry_id}",
                fix="给每个条目一个稳定且不重复的 ID；改名时保留原 ID",
                details={"entry_id": normalized.entry_id},
            )
        seen.add(normalized.entry_id)
        checked.append(normalized)
    return tuple(checked)


def entry_view(draft: EntryDraft) -> dict[str, object]:
    return {
        "entry_id": draft.entry_id,
        "kind": draft.kind,
        "title": draft.title,
        "aliases": list(draft.aliases),
        "fields": dict(draft.fields),
        "chars": draft.chars,
    }


def _parse(text: str) -> EntryDraft:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise WorkspaceError(
            code="RECORD_HEADER_INVALID",
            where="library.records",
            why="条目缺少开头的 --- 头部",
            fix="按「---、id/schema_version/kind/title、---、正文」的格式写",
        )
    closing = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"),
        None,
    )
    if closing is None:
        raise WorkspaceError(
            code="RECORD_HEADER_INVALID",
            where="library.records",
            why="条目的头部没有结束的 ---",
            fix="补上结束的 ---；头部与正文之间不要留其他分隔符",
        )
    try:
        header = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as exc:
        raise WorkspaceError(
            code="RECORD_HEADER_INVALID",
            where="library.records",
            why=f"条目头部无法解析：{exc}",
            fix="检查缩进与冒号；字段值写在同一行或用 - 列表",
        ) from exc
    if not isinstance(header, dict):
        raise WorkspaceError(
            code="RECORD_HEADER_INVALID",
            where="library.records",
            why="条目头部必须是一个键值映射",
            fix="至少写 id、schema_version、kind 与 title",
        )
    body = "\n".join(lines[closing + 1 :]).strip()
    return EntryDraft(
        entry_id=str(header.get("id") or ""),
        kind=str(header.get("kind") or ""),
        title=str(header.get("title") or ""),
        body=body,
        aliases=_aliases(header.get("aliases")),
        fields=_fields(header.get("fields")),
        schema_version=int(header.get("schema_version") or 0),
    )


def _aliases(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise WorkspaceError(
            code="RECORD_HEADER_INVALID",
            where="library.records",
            why="aliases 必须是列表",
            fix="写成「aliases: [别名一, 别名二]」；没有别名就省略这一行",
        )
    aliases = [str(item).strip() for item in raw]
    return tuple(alias for alias in aliases if alias)


def _fields(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise WorkspaceError(
            code="RECORD_HEADER_INVALID",
            where="library.records",
            why="fields 必须是键值映射",
            fix="把可筛选字段写进 fields，例如 planned_plant_chapter: 3",
        )
    return {str(key): value for key, value in raw.items()}


def _validate(draft: EntryDraft, *, module_kind: str) -> EntryDraft:
    preset = spec(module_kind)
    entry_id = normalize_entry_id(draft.entry_id, where="library.records")
    schema_version = draft.schema_version
    if schema_version != RECORD_SCHEMA_VERSION:
        raise WorkspaceError(
            code="RECORD_HEADER_INVALID",
            where=f"library.records:{entry_id}",
            why=f"不支持的条目格式版本：{schema_version}",
            fix=f"把头部的 schema_version 写成 {RECORD_SCHEMA_VERSION}",
            details={"entry_id": entry_id, "schema_version": schema_version},
        )
    if draft.kind not in preset.source_kinds:
        raise WorkspaceError(
            code="RECORD_KIND_INVALID",
            where=f"library.records:{entry_id}",
            why=f"{preset.label}模块不接受 kind={draft.kind or '（空）'} 的条目",
            fix="用这类模块允许的条目种类：" + "、".join(preset.source_kinds),
            details={"entry_id": entry_id, "allowed": list(preset.source_kinds)},
        )
    title = draft.title.strip()
    if not title:
        raise WorkspaceError(
            code="RECORD_HEADER_INVALID",
            where=f"library.records:{entry_id}",
            why="条目标题不能为空",
            fix="写一个便于辨认的标题；改名不影响 ID",
            details={"entry_id": entry_id},
        )
    if len(title) > MAX_TITLE_CHARS:
        raise WorkspaceError(
            code="RECORD_CONTENT_TOO_LARGE",
            where=f"library.records:{entry_id}",
            why=f"标题最长 {MAX_TITLE_CHARS} 字符，当前 {len(title)}",
            fix="缩短标题，把说明写进正文",
            details={"entry_id": entry_id},
        )
    aliases = tuple(dict.fromkeys(alias.strip() for alias in draft.aliases if alias.strip()))
    if len(aliases) > MAX_ALIASES:
        raise WorkspaceError(
            code="RECORD_CONTENT_TOO_LARGE",
            where=f"library.records:{entry_id}",
            why=f"别名最多 {MAX_ALIASES} 个，当前 {len(aliases)}",
            fix="只保留检索需要的写法",
            details={"entry_id": entry_id},
        )
    for alias in aliases:
        if len(alias) > MAX_ALIAS_CHARS:
            raise WorkspaceError(
                code="RECORD_CONTENT_TOO_LARGE",
                where=f"library.records:{entry_id}",
                why=f"别名过长：{alias[:20]}…",
                fix="每个别名不超过 64 字符",
                details={"entry_id": entry_id},
            )
    fields = dict(draft.fields)
    if len(fields) > MAX_FIELDS:
        raise WorkspaceError(
            code="RECORD_CONTENT_TOO_LARGE",
            where=f"library.records:{entry_id}",
            why=f"头部字段最多 {MAX_FIELDS} 个，当前 {len(fields)}",
            fix="只保留需要筛选的字段，说明写进正文",
            details={"entry_id": entry_id},
        )
    for key, value in fields.items():
        if key not in preset.allowed_fields:
            raise WorkspaceError(
                code="RECORD_FIELD_UNKNOWN",
                where=f"library.records:{entry_id}",
                why=f"{preset.label}模块没有这个头部字段：{key}",
                fix="用这类模块允许的字段：" + "、".join(preset.allowed_fields),
                details={"entry_id": entry_id, "field": key},
            )
        _check_scalar(key, value, entry_id)
    body = draft.body.strip()
    if len(body) > MAX_BODY_CHARS:
        raise WorkspaceError(
            code="RECORD_CONTENT_TOO_LARGE",
            where=f"library.records:{entry_id}",
            why=f"条目正文最长 {MAX_BODY_CHARS} 字符，当前 {len(body)}",
            fix="拆成多条条目，或把长文放到自由资料",
            details={"entry_id": entry_id},
        )
    if not body and not fields:
        raise WorkspaceError(
            code="RECORD_CONTENT_EMPTY",
            where=f"library.records:{entry_id}",
            why="条目既没有正文也没有可筛选字段",
            fix="写一段正文，或至少给出一个头部字段",
            details={"entry_id": entry_id},
        )
    return EntryDraft(
        entry_id=entry_id,
        kind=draft.kind,
        title=title,
        body=body,
        aliases=aliases,
        fields=fields,
    )


def _check_scalar(key: str, value: object, entry_id: str) -> None:
    if isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list) and all(isinstance(item, (str, int, float, bool)) for item in value):
        return
    raise WorkspaceError(
        code="RECORD_FIELD_UNKNOWN",
        where=f"library.records:{entry_id}",
        why=f"字段 {key} 的值只能是标量或标量列表",
        fix="把嵌套结构写成多条条目，或放进正文",
        details={"entry_id": entry_id, "field": key},
    )
