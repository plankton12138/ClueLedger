"""账本条目的直接维护命令（V03-3.2）：作者显式创建/修订，不经模型候选。

与 `canon.commands` 的分工：

- `commands.py` 服务于模型规划输出与角色/线索/真相的领域规则；
- 本模块服务于写作台「直接编辑并保存」：按可见账本种类把作者输入解码到
  同一套类型化模型，保留引用校验（关系两端、线索指向、认知对象）与
  revision CAS（不变量 5）。

两类内容不进入正式账本：

- **假设**：没有对应 kind，调用方传入未知种类时拒绝；未确认的构想留在
  候选组（V03-3.2 第二部分），不产生 Commit。
- **证词/认知**：以 ``story.knowledge`` 原类型保存（含 ``mode``），不把
  角色声称或猜测升级为 ``story.truth_fact``（I09）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import (
    CHARACTER_ROLES,
    Character,
    Clue,
    MysteryTruth,
    Place,
    Relationship,
    StoryPremise,
    TimelineEvent,
    WorldRule,
)
from hastings.domain.story.catalog import StoryCatalog, display_name
from hastings.domain.story.changes import DomainChange
from hastings.domain.story.errors import violation
from hastings.domain.story.mystery.models import (
    KNOWLEDGE_MODES,
    KnowledgeState,
    TruthFact,
)
from hastings.domain.story.refs import (
    NewId,
    check_revision,
    create_change,
    new_artifact_id,
    require_ref,
    revise_change,
)

__all__ = [
    "LEDGER_FIELDS",
    "LEDGER_KINDS",
    "create_entry",
    "revise_entry",
]

#: 可在写作台直接维护的账本种类；顺序即前端「新增」下拉的默认顺序。
LEDGER_KINDS: tuple[str, ...] = (
    kinds.KIND_CHARACTER,
    kinds.KIND_PLACE,
    kinds.KIND_RELATIONSHIP,
    kinds.KIND_CLUE,
    kinds.KIND_MYSTERY_TRUTH,
    kinds.KIND_TRUTH_FACT,
    kinds.KIND_KNOWLEDGE,
    kinds.KIND_TIMELINE_EVENT,
    kinds.KIND_WORLD_RULE,
    kinds.KIND_STORY_PREMISE,
)

_MODELS: dict[str, Any] = {
    kinds.KIND_STORY_PREMISE: StoryPremise,
    kinds.KIND_CHARACTER: Character,
    kinds.KIND_RELATIONSHIP: Relationship,
    kinds.KIND_PLACE: Place,
    kinds.KIND_WORLD_RULE: WorldRule,
    kinds.KIND_MYSTERY_TRUTH: MysteryTruth,
    kinds.KIND_CLUE: Clue,
    kinds.KIND_TIMELINE_EVENT: TimelineEvent,
    kinds.KIND_TRUTH_FACT: TruthFact,
    kinds.KIND_KNOWLEDGE: KnowledgeState,
}

#: 每种 kind 允许作者写入的字段；``schema_version``/``legacy_metadata`` 与
#: 已退役的 ``disclosure_plan``（引用节拍）不在其中。
LEDGER_FIELDS: dict[str, frozenset[str]] = {
    kinds.KIND_STORY_PREMISE: frozenset({"premise", "genre", "reader_promise", "thematic_bounds"}),
    kinds.KIND_CHARACTER: frozenset(
        {"name", "identity", "goal", "need", "secret", "arc", "role", "traits"}
    ),
    kinds.KIND_RELATIONSHIP: frozenset(
        {"source_id", "target_id", "relation_type", "public_state", "true_state", "evolution"}
    ),
    kinds.KIND_PLACE: frozenset(
        {"name", "description", "entry_conditions", "adjacent", "features"}
    ),
    kinds.KIND_WORLD_RULE: frozenset({"rule", "conditions", "exceptions", "cost"}),
    kinds.KIND_MYSTERY_TRUTH: frozenset(
        {"culprit_id", "motive", "method", "cover_up", "accomplice_ids", "true_timeline"}
    ),
    kinds.KIND_CLUE: frozenset({"statement", "source", "points_to", "necessity"}),
    kinds.KIND_TIMELINE_EVENT: frozenset(
        {"time", "place_id", "participants", "before_state", "after_state"}
    ),
    kinds.KIND_TRUTH_FACT: frozenset({"statement", "event_id"}),
    kinds.KIND_KNOWLEDGE: frozenset({"character_id", "fact_id", "mode", "note"}),
}

#: 引用字段 → 允许的目标种类；单值与列表字段共用。
_REF_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    kinds.KIND_RELATIONSHIP: {
        "source_id": (kinds.KIND_CHARACTER,),
        "target_id": (kinds.KIND_CHARACTER,),
    },
    kinds.KIND_MYSTERY_TRUTH: {
        "culprit_id": (kinds.KIND_CHARACTER,),
        "accomplice_ids": (kinds.KIND_CHARACTER,),
        "true_timeline": (kinds.KIND_TIMELINE_EVENT,),
    },
    kinds.KIND_CLUE: {
        "points_to": (kinds.KIND_TRUTH_FACT, kinds.KIND_MYSTERY_TRUTH),
    },
    kinds.KIND_TIMELINE_EVENT: {
        "place_id": (kinds.KIND_PLACE,),
        "participants": (kinds.KIND_CHARACTER,),
    },
    kinds.KIND_TRUTH_FACT: {"event_id": (kinds.KIND_TIMELINE_EVENT,)},
    kinds.KIND_KNOWLEDGE: {
        "character_id": (kinds.KIND_CHARACTER,),
        "fact_id": (kinds.KIND_TRUTH_FACT,),
    },
}


def _require_kind(kind: str, *, cmd: str) -> None:
    if kind not in _MODELS:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            f"{cmd}.kind",
            f"{kind!r} 不是可直存的账本种类；假设不能作为正式条目",
            f"使用以下之一：{sorted(_MODELS)}；未确认的构想留在候选组",
            kind=kind,
            allowed=sorted(_MODELS),
        )


def _clean(kind: str, fields: Mapping[str, Any], *, cmd: str) -> dict[str, Any]:
    """校验字段白名单并去掉不可写字段；未知字段显式拒绝，不静默丢弃。"""
    allowed = LEDGER_FIELDS[kind]
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            f"{cmd}.fields",
            f"字段 {unknown} 不能直接写入 {kind}",
            f"只写以下字段之一：{sorted(allowed)}；假设与退役字段不进入正式账本",
            kind=kind,
            unknown=unknown,
            allowed=sorted(allowed),
        )
    return {key: value for key, value in fields.items() if key in allowed}


def _check_refs(catalog: StoryCatalog, kind: str, content: Mapping[str, Any], *, cmd: str) -> None:
    for field, allowed in _REF_FIELDS.get(kind, {}).items():
        value = content.get(field)
        if not value:
            continue
        targets = [str(value)] if isinstance(value, str) else [str(v) for v in value]
        for target in targets:
            require_ref(catalog, target, allowed, cmd=cmd, field=field)


def _validate(catalog: StoryCatalog, kind: str, content: Mapping[str, Any], *, cmd: str) -> None:
    """种类级枚举与引用校验；字段必填由模型 ``from_content`` 负责。"""
    if kind == kinds.KIND_CHARACTER:
        role = content.get("role", "support")
        if role not in CHARACTER_ROLES:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{cmd}.role",
                f"角色作用 {role!r} 不是受支持的值",
                f"改用以下之一：{sorted(CHARACTER_ROLES)}",
                role=role,
                allowed=sorted(CHARACTER_ROLES),
            )
    if kind == kinds.KIND_KNOWLEDGE:
        mode = content.get("mode", "unaware")
        if mode not in KNOWLEDGE_MODES:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{cmd}.mode",
                f"认知模式 {mode!r} 不是受支持的值",
                f"改用以下之一：{sorted(KNOWLEDGE_MODES)}；证词与猜测保留认知类型",
                mode=mode,
                allowed=sorted(KNOWLEDGE_MODES),
            )
    _check_refs(catalog, kind, content, cmd=cmd)


def _title(kind: str, content: Mapping[str, Any], artifact_id: str) -> str:
    name = display_name(kind, dict(content))
    return str(name) if name else artifact_id


def create_entry(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    kind: str,
    fields: Mapping[str, Any],
) -> DomainChange:
    """新建一个账本条目；字段经类型化模型校验后才生成变更。"""
    _require_kind(kind, cmd="CreateLedgerEntry")
    content = _clean(kind, fields, cmd="CreateLedgerEntry")
    _validate(catalog, kind, content, cmd="CreateLedgerEntry")
    model = _MODELS[kind].from_content(content)
    artifact_id = new_artifact_id(catalog, new_id, kind, cmd="CreateLedgerEntry")
    change = create_change(artifact_id, kind, model.to_content())
    return DomainChange(
        artifact_changes=(change,),
        summary=f"新建账本条目 {kind}：{_title(kind, model.to_content(), artifact_id)}",
    )


def revise_entry(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    entry_id: str,
    fields: Mapping[str, Any],
    expected_revision: str | None = None,
) -> DomainChange:
    """部分修订：只覆盖显式提供的字段；内容未变化时返回合法空结果。"""
    del new_id  # 修订不产生新身份；保留参数以与 create 同签名
    entry = catalog.require(entry_id, where="ReviseLedgerEntry.entry_id")
    _require_kind(entry.kind, cmd="ReviseLedgerEntry")
    check_revision(entry, expected_revision, cmd="ReviseLedgerEntry")
    updates = _clean(entry.kind, fields, cmd="ReviseLedgerEntry")
    merged = {**entry.content, **updates}
    merged.pop("schema_version", None)
    _validate(catalog, entry.kind, merged, cmd="ReviseLedgerEntry")
    model = _MODELS[entry.kind].from_content(merged)
    content = model.to_content()
    if content == entry.content:
        return DomainChange(summary=f"账本条目 {entry_id} 内容未变化（合法空结果）")
    return DomainChange(
        artifact_changes=(revise_change(entry, content),),
        summary=f"修订账本条目 {entry.kind}：{_title(entry.kind, content, entry_id)}",
    )
