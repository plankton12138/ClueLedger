"""本书约定（V03-4.1）：作者显式的作品级要求，版本化保存，无模型维护。

设计口径（产品方案 §2.2、计划 M07）：

- **只记作者明确说过的全书要求**，不从一次采用、人工修改或旧偏好推断；
  任务级临时要求留在任务里，不写入本模块——这是「不自动长期化」的实现方式：
  写入只能由作者显式调用，没有任何推断入口。
- **冲突按主题维度判定**，不做语义推断：同一 ``topic`` 在任一时刻最多一条
  ``active``。新要求与生效项同主题时，要么显式替换（旧项 ``retired/superseded``），
  要么停在 ``pending_choice`` 等作者选择——待选择项不算生效约束。
- **修改与移除都不追改正文**：本模块只写 Artifact/Revision，正文由统一发布管线管；
  已经启动的任务使用自己的 ``AgreementSnapshot``，改约定不改旧任务输入。
- **保留来源**：``source_kind`` + ``source_message_id`` 记录约定从哪条消息或哪次
  作品设置保存来；按消息来源创建时消息 ID 必填，否则无从追溯。

本模块是纯函数命令层：不碰存储、不产生模型调用；提交由应用服务完成。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from hastings.domain.repository.requests import ArtifactChange, RelationChange
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.changes import DomainChange, RelationSpec
from hastings.domain.story.errors import violation
from hastings.domain.story.refs import (
    NewId,
    check_revision,
    create_change,
    new_artifact_id,
    revise_change,
)
from hastings.domain.story.schema import SCHEMA_VERSION, require_str, split_legacy

__all__ = [
    "AGREEMENT_FIELDS",
    "AGREEMENT_SCOPES",
    "AGREEMENT_STATUSES",
    "AGREEMENT_TOPICS",
    "RETIRE_REASONS",
    "SOURCE_KINDS",
    "STATUS_ACTIVE",
    "STATUS_PENDING",
    "STATUS_RETIRED",
    "AgreementRef",
    "AgreementSnapshot",
    "BookAgreement",
    "create_agreement",
    "revise_agreement",
    "snapshot",
]

KIND = kinds.KIND_AGREEMENT

#: 主题维度：确定性的冲突判定依据。新增维度先在此登记，前后端共用同一份口径；
#: 不做「两句话意思冲突」的语义判断——那是模型的事，不属于无模型可维护的路径。
AGREEMENT_TOPICS: dict[str, str] = {
    "narrative_person": "叙述人称",
    "narrative_tense": "叙述时态",
    "viewpoint": "视角与聚焦",
    "tone": "语气与文风",
    "pacing": "节奏",
    "length": "篇幅",
    "terminology": "用语与译名",
    "taboo": "禁忌与边界",
    "structure": "结构",
    "other": "其他",
}

#: 适用范围：约定本身是作品级的，这里区分它约束哪一类任务。
AGREEMENT_SCOPES: dict[str, str] = {
    "all": "全部任务",
    "manuscript": "仅正文写作",
    "discussion": "仅讨论回答",
}

STATUS_ACTIVE = "active"
STATUS_PENDING = "pending_choice"
STATUS_RETIRED = "retired"

#: 停用原因：被新要求替换，或作者直接移除。两者都保留历史，不做物理删除。
RETIRE_SUPERSEDED = "superseded"
RETIRE_REMOVED = "removed"
RETIRE_REASONS: dict[str, str] = {
    RETIRE_SUPERSEDED: "已被新要求替换",
    RETIRE_REMOVED: "作者移除",
}

AGREEMENT_STATUSES: dict[str, str] = {
    STATUS_ACTIVE: "生效",
    STATUS_PENDING: "待选择",
    STATUS_RETIRED: "已停用",
}

#: 来源：作者在作品设置里手写、从某条讨论消息记录、或随导入带入。
SOURCE_SETTINGS = "settings_edit"
SOURCE_MESSAGE = "author_message"
SOURCE_IMPORT = "import"
SOURCE_KINDS: dict[str, str] = {
    SOURCE_SETTINGS: "作品设置",
    SOURCE_MESSAGE: "讨论消息",
    SOURCE_IMPORT: "导入带入",
}

#: 作者可写字段；``superseded_by`` 只由替换命令写入，不接受客户端指定。
AGREEMENT_FIELDS: frozenset[str] = frozenset(
    {
        "statement",
        "topic",
        "scope",
        "status",
        "source_kind",
        "source_message_id",
        "retired_reason",
    }
)

_KNOWN_KEYS = frozenset({*AGREEMENT_FIELDS, "superseded_by", "schema_version"})


def _label() -> str:
    return "agreement"


@dataclass(frozen=True, slots=True)
class BookAgreement:
    """一条本书约定：明确要求 + 主题维度 + 状态 + 来源。"""

    statement: str
    topic: str = "other"
    scope: str = "all"
    status: str = STATUS_ACTIVE
    source_kind: str = SOURCE_SETTINGS
    source_message_id: str = ""
    retired_reason: str = ""
    superseded_by: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        where = _label()
        if self.topic not in AGREEMENT_TOPICS:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{where}.topic",
                f"未知的主题维度 {self.topic!r}",
                f"使用以下之一：{sorted(AGREEMENT_TOPICS)}；冲突判定依赖该维度",
                topic=self.topic,
                allowed=sorted(AGREEMENT_TOPICS),
            )
        if self.scope not in AGREEMENT_SCOPES:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{where}.scope",
                f"未知的适用范围 {self.scope!r}",
                f"使用以下之一：{sorted(AGREEMENT_SCOPES)}",
                scope=self.scope,
                allowed=sorted(AGREEMENT_SCOPES),
            )
        if self.status not in AGREEMENT_STATUSES:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{where}.status",
                f"未知的约定状态 {self.status!r}",
                f"使用以下之一：{sorted(AGREEMENT_STATUSES)}",
                status=self.status,
                allowed=sorted(AGREEMENT_STATUSES),
            )
        if self.source_kind not in SOURCE_KINDS:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{where}.source_kind",
                f"未知的来源类型 {self.source_kind!r}",
                f"使用以下之一：{sorted(SOURCE_KINDS)}",
                source_kind=self.source_kind,
                allowed=sorted(SOURCE_KINDS),
            )
        if self.source_kind == SOURCE_MESSAGE and not self.source_message_id:
            raise violation(
                ec.STORY_MISSING_REQUIRED_FIELD,
                f"{where}.source_message_id",
                "来源类型是讨论消息，但缺少 source_message_id",
                "补上产生该要求的消息 ID；无法追溯来源的约定不记入本书约定",
            )
        if self.status == STATUS_RETIRED and self.retired_reason not in RETIRE_REASONS:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{where}.retired_reason",
                f"停用原因 {self.retired_reason!r} 不是合法取值",
                f"停用时给出原因：{sorted(RETIRE_REASONS)}",
                retired_reason=self.retired_reason,
                allowed=sorted(RETIRE_REASONS),
            )
        if self.status != STATUS_RETIRED and self.retired_reason:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{where}.retired_reason",
                "未停用的约定不应带停用原因",
                "生效/待选择项不要写 retired_reason；停用请把 status 一起改为 retired",
            )

    # -- 编解码（与 Canon 模型同一套 schema 约定） --------------------------
    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "statement": self.statement,
            "topic": self.topic,
            "scope": self.scope,
            "status": self.status,
            "source_kind": self.source_kind,
            "source_message_id": self.source_message_id,
            "retired_reason": self.retired_reason,
            "superseded_by": self.superseded_by,
        }

    @classmethod
    def from_content(cls, content: Mapping[str, Any]) -> BookAgreement:
        where = _label()
        payload = dict(content)
        return cls(
            statement=require_str(payload.get("statement"), field="statement", where=where),
            topic=str(payload.get("topic", "other")),
            scope=str(payload.get("scope", "all")),
            status=str(payload.get("status", STATUS_ACTIVE)),
            source_kind=str(payload.get("source_kind", SOURCE_SETTINGS)),
            source_message_id=str(payload.get("source_message_id", "")),
            retired_reason=str(payload.get("retired_reason", "")),
            superseded_by=str(payload.get("superseded_by", "")),
            schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(payload, _KNOWN_KEYS),
        )

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE


# ---------------------------------------------------------------------------
# 快照：任务输入按 Commit 冻结，后续改约定不改变旧任务看到的版本
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgreementRef:
    """快照中的一条生效约定：身份 + Revision + 判定所需的最小内容。"""

    agreement_id: str
    revision_id: str
    topic: str
    scope: str
    statement: str

    def to_wire(self) -> dict[str, str]:
        return {
            "agreement_id": self.agreement_id,
            "revision_id": self.revision_id,
            "topic": self.topic,
            "scope": self.scope,
            "statement": self.statement,
        }


@dataclass(frozen=True, slots=True)
class AgreementSnapshot:
    """某 Commit 下的生效约定集合；``content_hash`` 是稳定指纹。

    任务开始前取一次快照并与 Job 绑定（计划 §4.1 ``ContextSnapshot``），
    之后作者改约定不会偷偷换掉已启动任务的输入（M07 第 3 条）。
    """

    commit_id: str | None
    items: tuple[AgreementRef, ...] = ()
    content_hash: str = ""

    @property
    def topics(self) -> tuple[str, ...]:
        return tuple(item.topic for item in self.items)

    def statement_of(self, topic: str) -> str:
        return next((i.statement for i in self.items if i.topic == topic), "")

    def to_wire(self) -> dict[str, Any]:
        return {
            "commit_id": self.commit_id,
            "content_hash": self.content_hash,
            "agreements": [item.to_wire() for item in self.items],
        }


def snapshot(catalog: StoryCatalog) -> AgreementSnapshot:
    """取当前目录里的生效约定；排序固定，哈希因此可复算。"""
    active = [
        entry
        for entry in catalog.by_kind(KIND)
        if BookAgreement.from_content(entry.content).is_active
    ]
    active.sort(key=lambda e: (str(e.content.get("topic", "")), e.artifact_id))
    items = tuple(
        AgreementRef(
            agreement_id=entry.artifact_id,
            revision_id=entry.revision_id,
            topic=str(entry.content.get("topic", "")),
            scope=str(entry.content.get("scope", "all")),
            statement=str(entry.content.get("statement", "")),
        )
        for entry in active
    )
    # 指纹只算约定内容（含各条 Revision），不含 commit_id：无关的正文提交
    # 不应让「约定版本」看起来变了，任务背景的缓存与失效才有稳定依据。
    digest = content_hash({"agreements": [item.to_wire() for item in items]})
    return AgreementSnapshot(commit_id=catalog.commit_id, items=items, content_hash=digest)


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------


def _clean(fields: Mapping[str, Any], *, cmd: str) -> dict[str, Any]:
    """只接受作者可写字段；未知字段显式拒绝，不静默丢弃。"""
    unknown = sorted(set(fields) - AGREEMENT_FIELDS)
    if unknown:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            f"{cmd}.fields",
            f"字段 {unknown} 不能直接写入本书约定",
            f"只写以下字段之一：{sorted(AGREEMENT_FIELDS)}；替换关系由命令维护",
            unknown=unknown,
            allowed=sorted(AGREEMENT_FIELDS),
        )
    return {key: value for key, value in fields.items() if key in AGREEMENT_FIELDS}


def _active_on_topic(
    catalog: StoryCatalog, topic: str, *, exclude: Sequence[str] = ()
) -> list[StoryEntry]:
    skip = set(exclude)
    result: list[StoryEntry] = []
    for entry in catalog.by_kind(KIND):
        if entry.artifact_id in skip:
            continue
        agreement = BookAgreement.from_content(entry.content)
        if agreement.is_active and agreement.topic == topic:
            result.append(entry)
    return result


def _supersede_targets(
    catalog: StoryCatalog,
    ids: Sequence[str],
    *,
    topic: str,
    cmd: str,
) -> list[StoryEntry]:
    """校验替换目标：必须存在、是本模块条目、且与新要求同一主题维度。"""
    targets: list[StoryEntry] = []
    for old_id in ids:
        entry = catalog.require(old_id, where=f"{cmd}.supersedes")
        if entry.kind != KIND:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{cmd}.supersedes",
                f"{old_id!r} 的类型是 {entry.kind!r}，不是本书约定",
                "只替换本书约定条目；账本事实请在账本里修订",
                agreement_id=old_id,
                kind=entry.kind,
            )
        old = BookAgreement.from_content(entry.content)
        if old.topic != topic:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{cmd}.supersedes",
                f"待替换项的主题是 {old.topic!r}，与新要求的 {topic!r} 不同",
                "替换只在同一主题维度内发生；不同主题请分别保存或先移除旧项",
                agreement_id=old_id,
                old_topic=old.topic,
                topic=topic,
            )
        if not old.is_active:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{cmd}.supersedes",
                f"待替换项 {old_id!r} 当前状态是 {old.status!r}，不是生效约定",
                "只替换生效中的约定；已停用项保留历史即可",
                agreement_id=old_id,
                status=old.status,
            )
        targets.append(entry)
    return targets


def _retire_changes(targets: Sequence[StoryEntry], *, new_id: str) -> list[ArtifactChange]:
    """把被替换项改为 ``retired/superseded``，并记录被谁替换（历史可读）。"""
    changes: list[ArtifactChange] = []
    for entry in targets:
        content = {
            **entry.content,
            "status": STATUS_RETIRED,
            "retired_reason": RETIRE_SUPERSEDED,
            "superseded_by": new_id,
        }
        changes.append(revise_change(entry, content))
    return changes


def _relations(targets: Sequence[StoryEntry], *, new_id: str) -> list[RelationChange]:
    return [
        RelationSpec(
            action="add",
            source=new_id,
            relation_type=kinds.REL_SUPERSEDES,
            target=entry.artifact_id,
            metadata={"reason": "作者明确替换"},
        ).to_change()
        for entry in targets
    ]


def _reject_retired_on_create(agreement: BookAgreement) -> None:
    if agreement.status == STATUS_RETIRED:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            "CreateAgreement.status",
            "新建约定不能直接是已停用状态",
            "新建时用 active 或 pending_choice；要停用已有项请 PATCH 那条约定",
            status=agreement.status,
        )


def create_agreement(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    fields: Mapping[str, Any],
    supersedes: Sequence[str] = (),
) -> DomainChange:
    """新建一条约定；与生效项同主题时必须显式替换或停在待选择。"""
    cmd = "CreateAgreement"
    content = _clean(fields, cmd=cmd)
    agreement = BookAgreement.from_content(content)
    _reject_retired_on_create(agreement)
    if agreement.status == STATUS_PENDING and supersedes:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            f"{cmd}.supersedes",
            "待选择的约定不能同时声明替换",
            "先保存为待选择，作者选定后再 PATCH 为 active 并给出 supersedes",
        )
    targets = _supersede_targets(catalog, supersedes, topic=agreement.topic, cmd=cmd)
    if agreement.is_active:
        conflicts = _active_on_topic(
            catalog, agreement.topic, exclude=[t.artifact_id for t in targets]
        )
        if conflicts:
            raise violation(
                ec.AGREEMENT_TOPIC_CONFLICT,
                f"{cmd}.topic",
                f"主题 {agreement.topic!r} 已有生效约定 {conflicts[0].artifact_id}",
                "在 supersedes 里指明替换哪一项，或先保存为 pending_choice 等作者选择",
                topic=agreement.topic,
                active_id=conflicts[0].artifact_id,
                candidates=[e.artifact_id for e in conflicts],
            )
    artifact_id = new_artifact_id(catalog, new_id, KIND, cmd=cmd)
    changes = [create_change(artifact_id, KIND, agreement.to_content())]
    changes.extend(_retire_changes(targets, new_id=artifact_id))
    return DomainChange(
        artifact_changes=tuple(changes),
        relation_changes=tuple(_relations(targets, new_id=artifact_id)),
        summary=f"新增本书约定：{agreement.statement[:24]}",
    )


def revise_agreement(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    agreement_id: str,
    fields: Mapping[str, Any],
    expected_revision: str | None = None,
    supersedes: Sequence[str] = (),
) -> DomainChange:
    """部分修订：只覆盖显式字段；内容无变化时返回合法空结果。"""
    del new_id  # 修订不产生新身份；保留参数以与 create 同签名
    cmd = "ReviseAgreement"
    entry = catalog.require(agreement_id, where=f"{cmd}.agreement_id")
    if entry.kind != KIND:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            f"{cmd}.agreement_id",
            f"{agreement_id!r} 的类型是 {entry.kind!r}，不是本书约定",
            "用本书约定的 ID；账本条目在账本接口里修订",
            agreement_id=agreement_id,
            kind=entry.kind,
        )
    check_revision(entry, expected_revision, cmd=cmd)
    updates = _clean(fields, cmd=cmd)
    merged = {**entry.content, **updates}
    merged.pop("schema_version", None)
    agreement = BookAgreement.from_content(merged)
    if not agreement.is_active and supersedes:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            f"{cmd}.supersedes",
            "只有生效的约定可以替换旧项",
            "先改为 active 再给出 supersedes；待选择项不改变其他约定的状态",
        )
    targets = _supersede_targets(
        catalog,
        [old for old in supersedes if old != agreement_id],
        topic=agreement.topic,
        cmd=cmd,
    )
    if agreement.is_active:
        conflicts = _active_on_topic(
            catalog,
            agreement.topic,
            exclude=[agreement_id, *(t.artifact_id for t in targets)],
        )
        if conflicts:
            raise violation(
                ec.AGREEMENT_TOPIC_CONFLICT,
                f"{cmd}.topic",
                f"主题 {agreement.topic!r} 已有生效约定 {conflicts[0].artifact_id}",
                "在 supersedes 里指明替换哪一项，或保持 pending_choice 不动其他约定",
                topic=agreement.topic,
                active_id=conflicts[0].artifact_id,
                candidates=[e.artifact_id for e in conflicts],
            )
    content = agreement.to_content()
    if len(targets) == 0 and content == entry.content:
        return DomainChange(summary=f"本书约定 {agreement_id} 内容未变化（合法空结果）")
    changes = [revise_change(entry, content)]
    changes.extend(_retire_changes(targets, new_id=agreement_id))
    return DomainChange(
        artifact_changes=tuple(changes),
        relation_changes=tuple(_relations(targets, new_id=agreement_id)),
        summary=f"修订本书约定：{agreement.statement[:24]}",
    )
