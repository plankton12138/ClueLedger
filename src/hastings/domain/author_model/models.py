"""作者模型领域模型（Phase 14 T14.5/T14.6）：编辑样本、分类结果、观察与偏好假设。

一次人工编辑是事实，分类是对它的解释，偏好是从多次解释中归纳出的可执行结论。
规则优先的管线把编辑归类为格式/错字、事实修订、结构修订、语言风格、协作反馈；
证据不足（低置信度）或类别冲突时**不强行断言**，保持 ``candidate``——不确定是
合法状态，不是故障（四透镜③泄压阀：模型未被强迫在证据不足时编出类别，下游据此
要求更多证据或人工确认）。

偏好（T14.6）把「作者改了什么」升级为「作者要什么」：身份是
``(类别, 作用域, 作用域目标)``，证据是**来源 Commit ID**（按 Commit 去重，
同一 Commit 的同类编辑只算一次），因此重跑分析不会伪造「多次独立证据」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hastings.domain.author_model import error_codes as ec
from hastings.domain.author_model.errors import violation
from hastings.domain.author_model.schema import (
    SCHEMA_VERSION,
    require_str,
    split_legacy,
    tuple_of_str,
)
from hastings.domain.manuscript import operations as ops
from hastings.domain.repository.requests import ArtifactChange

__all__ = [
    "AUTHOR_CATEGORIES",
    "CATEGORY_COLLABORATION",
    "CATEGORY_FACT",
    "CATEGORY_FORMAT",
    "CATEGORY_STRUCTURE",
    "CATEGORY_STYLE",
    "EXAMPLE_ACTIVE",
    "EXAMPLE_REMOVED",
    "EXAMPLE_STATUSES",
    "OBSERVATION_CANDIDATE",
    "OBSERVATION_CONFIRMED",
    "OBSERVATION_SOURCES",
    "OBSERVATION_STATUSES",
    "ORIGIN_EXPLICIT",
    "ORIGIN_INDUCED",
    "POLARITY_KINDS",
    "POLARITY_NEGATIVE",
    "POLARITY_POSITIVE",
    "PREFERENCE_ACTIVE",
    "PREFERENCE_CANDIDATE",
    "PREFERENCE_ORIGINS",
    "PREFERENCE_REVOKED",
    "PREFERENCE_STATUSES",
    "SCOPE_ARC",
    "SCOPE_CHAPTER",
    "SCOPE_GLOBAL",
    "SCOPE_KINDS",
    "SCOPE_ORDER",
    "SCOPE_PROJECT",
    "SOURCE_HUMAN",
    "SOURCE_MODEL",
    "SOURCE_RULE",
    "AuthorExample",
    "AuthorModelChange",
    "AuthorObservation",
    "Classification",
    "EditSample",
    "ObservationDraft",
    "PreferenceHypothesis",
]

# --- 分类类别（计划 §T14.5） ------------------------------------------------
CATEGORY_FORMAT = "format"  # 格式/错字：标点、空白、近似字
CATEGORY_FACT = "fact_revision"  # 事实修订：数字、日期、专名等事实内容
CATEGORY_STRUCTURE = "structure_revision"  # 结构修订：段落的增删拆分合并移动
CATEGORY_STYLE = "language_style"  # 语言风格：措辞与句式改写
CATEGORY_COLLABORATION = "collaboration_feedback"  # 协作反馈：作者对 Agent 的评注

AUTHOR_CATEGORIES = frozenset(
    (
        CATEGORY_FORMAT,
        CATEGORY_FACT,
        CATEGORY_STRUCTURE,
        CATEGORY_STYLE,
        CATEGORY_COLLABORATION,
    )
)

# --- 观察状态（低置信度/冲突 → candidate；证据充分 → confirmed） -----------
OBSERVATION_CANDIDATE = "candidate"
OBSERVATION_CONFIRMED = "confirmed"
OBSERVATION_STATUSES = frozenset((OBSERVATION_CANDIDATE, OBSERVATION_CONFIRMED))

# --- 分类来源（规则优先，模型为可选补充，人工可覆盖） ---------------------
SOURCE_RULE = "rule"
SOURCE_MODEL = "model"
SOURCE_HUMAN = "human"
OBSERVATION_SOURCES = frozenset((SOURCE_RULE, SOURCE_MODEL, SOURCE_HUMAN))

# --- 偏好状态（T14.6）------------------------------------------------------
PREFERENCE_CANDIDATE = "candidate"  # 证据未达阈值：可见但未参与生成
PREFERENCE_ACTIVE = "active"  # 生效：参与生成上下文
PREFERENCE_REVOKED = "revoked"  # 撤销：从生成上下文排除，且封锁自动复活
PREFERENCE_STATUSES = frozenset((PREFERENCE_CANDIDATE, PREFERENCE_ACTIVE, PREFERENCE_REVOKED))

# --- 偏好来源（人工显式声明 vs 从观察归纳）---------------------------------
ORIGIN_EXPLICIT = "explicit"
ORIGIN_INDUCED = "induced"
PREFERENCE_ORIGINS = frozenset((ORIGIN_EXPLICIT, ORIGIN_INDUCED))

# --- 作用域（越靠后越具体；解析时具体作用域覆盖宽作用域）--------------------
SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"
SCOPE_ARC = "arc"
SCOPE_CHAPTER = "chapter"
SCOPE_KINDS = frozenset((SCOPE_GLOBAL, SCOPE_PROJECT, SCOPE_ARC, SCOPE_CHAPTER))
SCOPE_ORDER: dict[str, int] = {SCOPE_GLOBAL: 0, SCOPE_PROJECT: 1, SCOPE_ARC: 2, SCOPE_CHAPTER: 3}

# --- 样例极性（T14.7：正例=作者认可的写法，反例=作者否定的写法）-----------
POLARITY_POSITIVE = "positive"
POLARITY_NEGATIVE = "negative"
POLARITY_KINDS = frozenset((POLARITY_POSITIVE, POLARITY_NEGATIVE))

# --- 样例状态（软删除：删除只改状态，引用 ID 仍可解析）--------------------
EXAMPLE_ACTIVE = "active"
EXAMPLE_REMOVED = "removed"
EXAMPLE_STATUSES = frozenset((EXAMPLE_ACTIVE, EXAMPLE_REMOVED))

_SAMPLE_KEYS = frozenset(
    {"schema_version", "unit_id", "chapter_id", "operation", "before", "after", "note"}
)
_OBSERVATION_KEYS = frozenset(
    {"schema_version", "observation_id", "commit_id", "edit", "classification"}
)
_PREFERENCE_KEYS = frozenset(
    {
        "schema_version",
        "preference_id",
        "category",
        "statement",
        "scope_kind",
        "scope_target",
        "status",
        "origin",
        "evidence",
        "superseded_by",
        "reason",
    }
)
_EXAMPLE_KEYS = frozenset(
    {
        "schema_version",
        "example_id",
        "unit_id",
        "revision_id",
        "commit_id",
        "polarity",
        "note",
        "status",
        "superseded_by",
        "reason",
    }
)


@dataclass(frozen=True, slots=True)
class EditSample:
    """一次待分类的编辑样本：段落前后文本 + 操作种类 + 作者显式评注。

    ``before``/``after`` 为**段落文本**：新增段落 before 为空串，删除段落
    after 为空串。``note`` 是作者保存时附上的显式评注（协作反馈信号），
    没有评注时为空串（空评注是合法状态，不是缺失字段）。
    """

    unit_id: str
    before: str
    after: str
    operation: str = ops.OP_REPLACE
    chapter_id: str = ""
    note: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "unit_id": self.unit_id,
            "chapter_id": self.chapter_id,
            "operation": self.operation,
            "before": self.before,
            "after": self.after,
            "note": self.note,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> EditSample:
        where = "author_model.edit_sample"
        return cls(
            unit_id=require_str(content.get("unit_id"), field="unit_id", where=where),
            before=str(content.get("before", "")),
            after=str(content.get("after", "")),
            operation=str(content.get("operation", ops.OP_REPLACE)),
            chapter_id=str(content.get("chapter_id", "")),
            note=str(content.get("note", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(content, _SAMPLE_KEYS),
        )


@dataclass(frozen=True, slots=True)
class Classification:
    """一次分类结果：类别（冲突时为 None）+ 置信度 + 状态 + 触发信号。

    ``status`` 只有两种：``confirmed``（单一规则达到阈值）与 ``candidate``
    （低置信度、无匹配或类别冲突）。``signals``/``alternatives`` 提供可解释性：
    前者说明「为什么这样判」，后者列出冲突时并列的候选类别，供人工或模型裁定。
    """

    category: str | None
    confidence: float
    status: str
    signals: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    source: str = SOURCE_RULE

    def __post_init__(self) -> None:
        object.__setattr__(self, "signals", tuple(self.signals))
        object.__setattr__(self, "alternatives", tuple(self.alternatives))
        if self.category is not None and self.category not in AUTHOR_CATEGORIES:
            raise ValueError(
                f"Classification.category 非法（哪里错：得到 {self.category!r}；"
                f"为什么错：只接受 {sorted(AUTHOR_CATEGORIES)} 或 None；"
                "怎样算对：用 AUTHOR_CATEGORIES 中的类别，冲突时给 None）"
            )
        if self.status not in OBSERVATION_STATUSES:
            raise ValueError(
                f"Classification.status 非法（哪里错：得到 {self.status!r}；"
                f"为什么错：只接受 {sorted(OBSERVATION_STATUSES)}；"
                "怎样算对：达标用 confirmed，不确定用 candidate）"
            )
        if self.source not in OBSERVATION_SOURCES:
            raise ValueError(
                f"Classification.source 非法（哪里错：得到 {self.source!r}；"
                f"为什么错：只接受 {sorted(OBSERVATION_SOURCES)}；"
                "怎样算对：规则用 rule，模型补充用 model，人工确认用 human）"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"Classification.confidence 越界（哪里错：得到 {self.confidence!r}；"
                "为什么错：置信度是概率，只能落在 [0, 1]；怎样算对：给 0~1 之间的小数）"
            )

    @property
    def is_confirmed(self) -> bool:
        return self.status == OBSERVATION_CONFIRMED

    @property
    def is_candidate(self) -> bool:
        return self.status == OBSERVATION_CANDIDATE

    def to_content(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "status": self.status,
            "signals": list(self.signals),
            "alternatives": list(self.alternatives),
            "source": self.source,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any], *, where: str) -> Classification:
        category = content.get("category")
        if category is not None and not isinstance(category, str):
            raise violation(
                ec.AUTHOR_MODEL_INVALID_OBSERVATION,
                f"{where}.classification.category",
                f"类别必须是字符串或 null，收到 {type(category).__name__}",
                f"用 {sorted(AUTHOR_CATEGORIES)} 之一；类别待定时写 null",
                category=category,
            )
        status = str(content.get("status", OBSERVATION_CANDIDATE))
        source = str(content.get("source", SOURCE_RULE))
        if status not in OBSERVATION_STATUSES:
            raise violation(
                ec.AUTHOR_MODEL_INVALID_OBSERVATION,
                f"{where}.classification.status",
                f"观察状态只能是 {sorted(OBSERVATION_STATUSES)}，收到 {status!r}",
                "把状态改为 candidate 或 confirmed",
                status=status,
            )
        if source not in OBSERVATION_SOURCES:
            raise violation(
                ec.AUTHOR_MODEL_INVALID_OBSERVATION,
                f"{where}.classification.source",
                f"分类来源只能是 {sorted(OBSERVATION_SOURCES)}，收到 {source!r}",
                "把来源改为 rule/model/human 之一",
                source=source,
            )
        try:
            confidence = float(content.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise violation(
                ec.AUTHOR_MODEL_INVALID_OBSERVATION,
                f"{where}.classification.confidence",
                f"置信度不是数字：{content.get('confidence')!r}",
                "提供 0~1 之间的小数",
                confidence=content.get("confidence"),
            ) from exc
        return cls(
            category=category,
            confidence=confidence,
            status=status,
            signals=tuple_of_str(content.get("signals")),
            alternatives=tuple_of_str(content.get("alternatives")),
            source=source,
        )


@dataclass(frozen=True, slots=True)
class AuthorObservation:
    """一次已分类的作者编辑观察（kind ``author_model.observation``）。

    观察绑定来源 Commit 与段落：``(commit_id, edit.unit_id)`` 是它的身份键，
    同一 Commit 的同一段落只留一条（重跑分析是合法 no-op，不产生重复证据）。
    """

    observation_id: str
    commit_id: str
    edit: EditSample
    classification: Classification
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    @property
    def category(self) -> str | None:
        return self.classification.category

    @property
    def status(self) -> str:
        return self.classification.status

    @property
    def is_confirmed(self) -> bool:
        return self.classification.is_confirmed

    def identity_key(self) -> tuple[str, str]:
        """观察身份键：来源 Commit + 段落 ID（用于重跑去重）。"""
        return (self.commit_id, self.edit.unit_id)

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "commit_id": self.commit_id,
            "edit": self.edit.to_content(),
            "classification": self.classification.to_content(),
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> AuthorObservation:
        where = "author_model.observation"
        edit = content.get("edit")
        if not isinstance(edit, dict):
            raise violation(
                ec.AUTHOR_MODEL_INVALID_OBSERVATION,
                f"{where}.edit",
                f"观察缺少 edit 字段（当前值：{edit!r}）",
                "观察必须携带它所分类的编辑样本（unit_id/before/after）",
            )
        classification = content.get("classification")
        if not isinstance(classification, dict):
            raise violation(
                ec.AUTHOR_MODEL_INVALID_OBSERVATION,
                f"{where}.classification",
                f"观察缺少 classification 字段（当前值：{classification!r}）",
                "观察必须携带分类结果（category/confidence/status）",
            )
        return cls(
            observation_id=require_str(
                content.get("observation_id"), field="observation_id", where=where
            ),
            commit_id=require_str(content.get("commit_id"), field="commit_id", where=where),
            edit=EditSample.from_content(edit),
            classification=Classification.from_content(classification, where=where),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(content, _OBSERVATION_KEYS),
        )


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    """尚未落盘的观察草案：编辑样本 + 分类结果 + 来源 Commit。"""

    edit: EditSample
    classification: Classification
    commit_id: str


@dataclass(frozen=True, slots=True)
class PreferenceHypothesis:
    """一条写作偏好假设（kind ``author_model.preference``，T14.6）。

    身份是 ``(category, scope_kind, scope_target)``：同一类别同一作用域上只应有一条
    非撤销偏好。``evidence`` 是支持它的**来源 Commit ID**（不是观察 ID）——按 Commit
    去重，同一 Commit 内的同类编辑只算一次，避免把重跑分析当成「多次独立证据」。

    ``superseded_by`` 指向取代它的新偏好；撤销不删除历史，只把状态改为
    ``revoked``（撤销即封锁：自动归纳不得复活被人工撤销的偏好）。
    """

    preference_id: str
    category: str
    statement: str
    scope_kind: str = SCOPE_PROJECT
    scope_target: str = ""
    status: str = PREFERENCE_CANDIDATE
    origin: str = ORIGIN_INDUCED
    evidence: tuple[str, ...] = ()
    superseded_by: str | None = None
    reason: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))
        if self.category not in AUTHOR_CATEGORIES:
            raise ValueError(
                f"PreferenceHypothesis.category 非法（哪里错：得到 {self.category!r}；"
                f"为什么错：只接受 {sorted(AUTHOR_CATEGORIES)}；"
                "怎样算对：用 AUTHOR_CATEGORIES 中的类别）"
            )
        if self.status not in PREFERENCE_STATUSES:
            raise ValueError(
                f"PreferenceHypothesis.status 非法（哪里错：得到 {self.status!r}；"
                f"为什么错：只接受 {sorted(PREFERENCE_STATUSES)}；"
                "怎样算对：证据不足用 candidate，生效用 active，撤销用 revoked）"
            )
        if self.origin not in PREFERENCE_ORIGINS:
            raise ValueError(
                f"PreferenceHypothesis.origin 非法（哪里错：得到 {self.origin!r}；"
                f"为什么错：只接受 {sorted(PREFERENCE_ORIGINS)}；"
                "怎样算对：人工声明用 explicit，归纳用 induced）"
            )
        if self.scope_kind not in SCOPE_KINDS:
            raise ValueError(
                f"PreferenceHypothesis.scope_kind 非法（哪里错：得到 {self.scope_kind!r}；"
                f"为什么错：只接受 {sorted(SCOPE_KINDS)}；"
                "怎样算对：按偏好适用范围选 global/project/arc/chapter）"
            )
        if self.scope_kind == SCOPE_GLOBAL and self.scope_target:
            raise ValueError(
                f"PreferenceHypothesis.scope_target 非法（哪里错：全局作用域却带了目标 "
                f"{self.scope_target!r}；为什么错：全局偏好没有更窄的目标；"
                "怎样算对：全局作用域的 scope_target 必须为空串）"
            )

    @property
    def is_active(self) -> bool:
        return self.status == PREFERENCE_ACTIVE

    @property
    def is_candidate(self) -> bool:
        return self.status == PREFERENCE_CANDIDATE

    @property
    def is_revoked(self) -> bool:
        return self.status == PREFERENCE_REVOKED

    def identity(self) -> tuple[str, str, str]:
        """偏好身份键：类别 + 作用域 + 作用域目标（同一身份只应有一条非撤销偏好）。"""
        return (self.category, self.scope_kind, self.scope_target)

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "preference_id": self.preference_id,
            "category": self.category,
            "statement": self.statement,
            "scope_kind": self.scope_kind,
            "scope_target": self.scope_target,
            "status": self.status,
            "origin": self.origin,
            "evidence": list(self.evidence),
            "superseded_by": self.superseded_by,
            "reason": self.reason,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> PreferenceHypothesis:
        where = "author_model.preference"
        for field_name, legal in (
            ("category", sorted(AUTHOR_CATEGORIES)),
            ("status", sorted(PREFERENCE_STATUSES)),
            ("origin", sorted(PREFERENCE_ORIGINS)),
            ("scope_kind", sorted(SCOPE_KINDS)),
        ):
            value = str(content.get(field_name, ""))
            if value not in legal:
                raise violation(
                    ec.AUTHOR_MODEL_INVALID_PREFERENCE,
                    f"{where}.{field_name}",
                    f"字段只能是 {legal} 之一，收到 {value!r}",
                    f"把 {field_name} 改为 {legal} 中的合法值",
                    field=field_name,
                )
        superseded_by = content.get("superseded_by")
        if superseded_by is not None and not isinstance(superseded_by, str):
            raise violation(
                ec.AUTHOR_MODEL_INVALID_PREFERENCE,
                f"{where}.superseded_by",
                f"superseded_by 必须是偏好 ID 或 null，收到 {type(superseded_by).__name__}",
                "填写取代本条的偏好 ID；没有取代者时写 null",
                superseded_by=superseded_by,
            )
        return cls(
            preference_id=require_str(
                content.get("preference_id"), field="preference_id", where=where
            ),
            category=str(content.get("category", "")),
            statement=require_str(content.get("statement"), field="statement", where=where),
            scope_kind=str(content.get("scope_kind", SCOPE_PROJECT)),
            scope_target=str(content.get("scope_target", "")),
            status=str(content.get("status", PREFERENCE_CANDIDATE)),
            origin=str(content.get("origin", ORIGIN_INDUCED)),
            evidence=tuple_of_str(content.get("evidence")),
            superseded_by=superseded_by,
            reason=str(content.get("reason", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(content, _PREFERENCE_KEYS),
        )


@dataclass(frozen=True, slots=True)
class AuthorExample:
    """作者标注的正例/反例引用（kind ``author_model.example``，T14.7）。

    只保存**引用**（unit/revision/commit）与极性、备注，**不复制正文**：正文是受
    版权与授权约束的资产，样例的价值在于「指向哪一段」，不在于「存了那段字」。
    因此本模型没有文本字段——结构上杜绝把正文复制进作者模型域（硬约束放模型层）。

    身份是 ``(unit_id, revision_id)``：同一段落的同一版本至多一条非 removed 标注；
    同一段落的不同 Revision 是两个独立引用。删除是**软删除**（``status=removed``）
    而非物理删除：历史生成记录引用的 example_id 必须始终可解析（不可变引用语义），
    删除只需让它不再进入新上下文。删除不封锁重新标注——与偏好「撤销即封锁」不同，
    作者重下断言是自然行为。
    """

    example_id: str
    unit_id: str
    revision_id: str
    commit_id: str
    polarity: str
    note: str = ""
    status: str = EXAMPLE_ACTIVE
    superseded_by: str | None = None
    reason: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))
        if self.polarity not in POLARITY_KINDS:
            raise ValueError(
                f"AuthorExample.polarity 非法（哪里错：得到 {self.polarity!r}；"
                f"为什么错：只接受 {sorted(POLARITY_KINDS)}；"
                "怎样算对：作者认可用 positive，作者否定用 negative）"
            )
        if self.status not in EXAMPLE_STATUSES:
            raise ValueError(
                f"AuthorExample.status 非法（哪里错：得到 {self.status!r}；"
                f"为什么错：只接受 {sorted(EXAMPLE_STATUSES)}；"
                "怎样算对：在效用 active，撤回用 removed）"
            )

    @property
    def is_active(self) -> bool:
        return self.status == EXAMPLE_ACTIVE

    @property
    def is_removed(self) -> bool:
        return self.status == EXAMPLE_REMOVED

    def identity(self) -> tuple[str, str]:
        """样例身份键：段落 ID + Revision（同一版本的同一标注只应有一条非 removed）。"""
        return (self.unit_id, self.revision_id)

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "example_id": self.example_id,
            "unit_id": self.unit_id,
            "revision_id": self.revision_id,
            "commit_id": self.commit_id,
            "polarity": self.polarity,
            "note": self.note,
            "status": self.status,
            "superseded_by": self.superseded_by,
            "reason": self.reason,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> AuthorExample:
        where = "author_model.example"
        for field_name, legal in (
            ("polarity", sorted(POLARITY_KINDS)),
            ("status", sorted(EXAMPLE_STATUSES)),
        ):
            value = str(content.get(field_name, ""))
            if value not in legal:
                raise violation(
                    ec.AUTHOR_MODEL_INVALID_EXAMPLE,
                    f"{where}.{field_name}",
                    f"字段只能是 {legal} 之一，收到 {value!r}",
                    f"把 {field_name} 改为 {legal} 中的合法值",
                    field=field_name,
                )
        superseded_by = content.get("superseded_by")
        if superseded_by is not None and not isinstance(superseded_by, str):
            raise violation(
                ec.AUTHOR_MODEL_INVALID_EXAMPLE,
                f"{where}.superseded_by",
                f"superseded_by 必须是样例 ID 或 null，收到 {type(superseded_by).__name__}",
                "填写取代本条的样例 ID；没有取代者时写 null",
                superseded_by=superseded_by,
            )
        return cls(
            example_id=require_str(content.get("example_id"), field="example_id", where=where),
            unit_id=require_str(content.get("unit_id"), field="unit_id", where=where),
            revision_id=require_str(content.get("revision_id"), field="revision_id", where=where),
            commit_id=require_str(content.get("commit_id"), field="commit_id", where=where),
            polarity=str(content.get("polarity", "")),
            note=str(content.get("note", "")),
            status=str(content.get("status", EXAMPLE_ACTIVE)),
            superseded_by=superseded_by,
            reason=str(content.get("reason", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(content, _EXAMPLE_KEYS),
        )


@dataclass(frozen=True, slots=True)
class AuthorModelChange:
    """命令结果：要提交的 Artifact 变化 + 警告 + 摘要。

    ``is_empty()`` 为真表示合法空结果（如重跑分析没有新观察），handler 据此
    跳过提交、不抛异常（四透镜③）。
    """

    artifact_changes: tuple[ArtifactChange, ...] = ()
    warnings: tuple[str, ...] = ()
    summary: str = ""

    def is_empty(self) -> bool:
        return not self.artifact_changes
