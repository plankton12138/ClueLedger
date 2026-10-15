"""作者模型命令（Phase 14 T14.5/T14.6/T14.7）：登记观察、声明/撤销/归纳偏好、标记/删除样例。

命令是**纯函数** ``fn(catalog, new_id, ...) -> AuthorModelChange``，不触达
Repository；唯一写路径是应用层 handler（读 Snapshot → 执行命令 → Commit）。

三条硬约束都落在命令层，而不是调用方约定：

- 观察按 ``(来源 Commit, 段落 ID)`` 去重——同一编辑重复出现会伪造「多次独立证据」，
  让偏好归纳误判为已到激活阈值（计划 §9 风险控制「保存事件幂等」）；
- 偏好按身份键 ``(类别, 作用域, 作用域目标)`` 唯一：人工声明不同陈述时，旧记录被
  标记 ``revoked`` 且 ``superseded_by`` 指向新记录（supersede），历史不删除；
- **撤销即封锁**：存在同身份的 revoked 偏好时，自动归纳不再创建/复活它——用户
  撤销过的偏好不能被后台任务悄悄复活（否则「撤销」是一个不可信的承诺）；
- **样例只存引用、删除只改状态**（T14.7）：正/反例只记录 ``(unit, revision, commit)``
  引用，正文永不复制进本域；删除是软删除（``removed``），使历史生成记录引用的
  example_id 始终可解析——删除不封锁重新标注（与偏好「撤销即封锁」刻意不同）。

没有变化时返回空变化，由 handler 跳过提交（四透镜③：合法空结果，不是异常）。
偏好冲突只产出警告：人工声明优先，归纳不静默覆盖（四透镜④：冲突可见）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from hastings.domain.author_model import error_codes as ec
from hastings.domain.author_model import kinds, rules
from hastings.domain.author_model.catalog import AuthorModelCatalog, AuthorModelEntry
from hastings.domain.author_model.errors import violation
from hastings.domain.author_model.models import (
    AUTHOR_CATEGORIES,
    CATEGORY_STYLE,
    EXAMPLE_ACTIVE,
    EXAMPLE_REMOVED,
    ORIGIN_EXPLICIT,
    ORIGIN_INDUCED,
    POLARITY_KINDS,
    POLARITY_POSITIVE,
    PREFERENCE_ACTIVE,
    PREFERENCE_CANDIDATE,
    PREFERENCE_ORIGINS,
    PREFERENCE_REVOKED,
    SCOPE_KINDS,
    SCOPE_PROJECT,
    AuthorExample,
    AuthorModelChange,
    AuthorObservation,
    ObservationDraft,
    PreferenceHypothesis,
)
from hastings.domain.author_model.schema import require_str
from hastings.domain.repository.ids import ArtifactId, RevisionId
from hastings.domain.repository.requests import ArtifactChange

__all__ = [
    "NewId",
    "declare_preference",
    "induce_preferences",
    "mark_example",
    "record_observations",
    "remove_example",
    "revoke_preference",
]

# new_id：由 kind 生成一个未被占用的新 ArtifactId（application 侧接 IdGenerator 端口）
NewId = Callable[[str], str]

_EXPLICIT_CONFLICT_WARNING = (
    "[author_model] 类别 {category}（作用域 {scope_kind}）已有人工声明的偏好，"
    "自动归纳不覆盖。为什么保留：人工声明优先于从观察归纳；"
    "怎样改：若要采用归纳结论，先撤销该声明，再重跑分析。"
)

_STYLE_CONFLICT_WARNING = (
    "[author_model] 风格证据自相矛盾（作用域 {scope_kind}）：既有扩写又有精简，"
    "方向无法断言。为什么保留候选：从相反证据里挑一个方向会得出错误偏好；"
    "怎样算对：累积更多同向证据，或人工声明明确方向。"
)


def record_observations(
    catalog: AuthorModelCatalog,
    new_id: NewId,
    *,
    drafts: tuple[ObservationDraft, ...],
) -> AuthorModelChange:
    """登记一批已分类观察；已存在的 (commit_id, unit_id) 跳过（幂等）。"""
    where = "author_model.record_observations"
    seen: set[tuple[str, str]] = set()
    changes: list[ArtifactChange] = []
    skipped = 0
    for index, draft in enumerate(drafts):
        field_where = f"{where}.drafts[{index}]"
        commit_id = require_str(draft.commit_id, field="commit_id", where=field_where)
        unit_id = require_str(draft.edit.unit_id, field="unit_id", where=f"{field_where}.edit")
        key = (commit_id, unit_id)
        if key in seen or catalog.observation_for(commit_id, unit_id) is not None:
            skipped += 1
            continue
        seen.add(key)
        observation_id = new_id("art")
        observation = AuthorObservation(
            observation_id=observation_id,
            commit_id=commit_id,
            edit=draft.edit,
            classification=draft.classification,
        )
        changes.append(
            ArtifactChange(
                artifact_id=ArtifactId(observation_id),
                content=observation.to_content(),
                old_revision_id=None,
                kind=kinds.KIND_OBSERVATION,
            )
        )
    if not changes:
        return AuthorModelChange(summary="没有新的作者观察（合法空结果）")
    summary = f"登记 {len(changes)} 条作者观察"
    if skipped:
        summary += f"（跳过 {skipped} 条已存在的观察）"
    return AuthorModelChange(artifact_changes=tuple(changes), summary=summary)


# ---------------------------------------------------------------------------
# 偏好：人工声明、撤销、从观察归纳（T14.6）
# ---------------------------------------------------------------------------
def declare_preference(
    catalog: AuthorModelCatalog,
    new_id: NewId,
    *,
    category: str,
    statement: str,
    scope_kind: str = SCOPE_PROJECT,
    scope_target: str = "",
    origin: str = ORIGIN_EXPLICIT,
) -> AuthorModelChange:
    """人工声明一条偏好；同身份不同陈述时取代旧记录（supersede）。

    同身份同陈述是合法空结果（重复声明不产生新版本）。取代让「陈述」可演进而身份
    保持稳定：旧记录标记 ``revoked`` 并写 ``superseded_by``，历史可追溯。
    """
    where = "author_model.declare_preference"
    category = _require_category(category, where=where)
    statement = require_str(statement, field="statement", where=where)
    _require_scope(scope_kind, where=where)
    if origin not in PREFERENCE_ORIGINS:
        raise violation(
            ec.AUTHOR_MODEL_INVALID_PREFERENCE,
            where,
            f"来源只能是 {sorted(PREFERENCE_ORIGINS)}，收到 {origin!r}",
            "人工声明用 explicit；induced 只由归纳命令写入",
            origin=origin,
        )
    matches = catalog.preferences_for(
        category=category, scope_kind=scope_kind, scope_target=scope_target
    )
    current = _current_preference(matches)
    if current is not None and current.statement == statement:
        return AuthorModelChange(summary="偏好声明与现有记录一致（合法空结果）")
    new_preference_id = new_id("art")
    changes: list[ArtifactChange] = []
    if current is not None:
        superseded = replace(
            current,
            status=PREFERENCE_REVOKED,
            superseded_by=new_preference_id,
            reason="被新的声明取代",
        )
        changes.append(_revise_preference(catalog, superseded, where=where))
    changes.append(
        _create_preference(
            PreferenceHypothesis(
                preference_id=new_preference_id,
                category=category,
                statement=statement,
                scope_kind=scope_kind,
                scope_target=scope_target,
                status=PREFERENCE_ACTIVE,
                origin=origin,
            )
        )
    )
    return AuthorModelChange(artifact_changes=tuple(changes), summary=f"声明偏好：{statement}")


def revoke_preference(
    catalog: AuthorModelCatalog,
    new_id: NewId,
    *,
    preference_id: str,
    reason: str = "",
) -> AuthorModelChange:
    """撤销一条偏好：只改状态，不删除历史（修订既有 Artifact，不新建身份）。

    已撤销的偏好再撤销是合法空结果——撤销是幂等的，不需要调用方先查状态。
    """
    del new_id  # 撤销不改身份：修订既有 Artifact，无需新 ID
    where = "author_model.revoke_preference"
    entry = catalog.require_preference(preference_id, where=where)
    preference = _decode_preference(entry)
    if preference.is_revoked:
        return AuthorModelChange(summary="偏好已处于撤销状态（合法空结果）")
    revoked = replace(preference, status=PREFERENCE_REVOKED, reason=reason)
    return AuthorModelChange(
        artifact_changes=(_revise_preference(catalog, revoked, where=where),),
        summary=f"撤销偏好：{preference.statement}",
    )


def induce_preferences(
    catalog: AuthorModelCatalog,
    new_id: NewId,
    *,
    scope_kind: str = SCOPE_PROJECT,
    scope_target: str = "",
    policy: rules.InductionPolicy = rules.DEFAULT_INDUCTION_POLICY,
) -> AuthorModelChange:
    """从已登记观察按策略归纳偏好：达阈值 → active，否则 candidate。

    人工声明优先：同身份已有 explicit 有效偏好时只告警、不改写。已被撤销的同身份
    偏好封锁自动归纳（撤销即封锁）。证据只增不减，因此重跑归纳是合法 no-op。
    """
    where = "author_model.induce_preferences"
    _require_scope(scope_kind, where=where)
    changes: list[ArtifactChange] = []
    warnings: list[str] = []
    for category, observations in rules.evidence_observations(
        catalog.observations(), policy
    ).items():
        commits = tuple(sorted({observation.commit_id for observation in observations}))
        # 分类只回答「改动属于什么」；方向要另算：风格由净长度变化归纳 concise/expand，
        # 方向未定或相反证据并存时不激活（评审 #2）
        direction = rules.style_direction(observations) if category == CATEGORY_STYLE else None
        statement = rules.statement_for(category, direction=direction)
        status = rules.activation_status(commits, policy)
        if category == CATEGORY_STYLE:
            if rules.style_evidence_conflicts(observations):
                warnings.append(_STYLE_CONFLICT_WARNING.format(scope_kind=scope_kind))
            if direction is None:
                status = PREFERENCE_CANDIDATE  # 方向未定 → 保持候选，不强行断言
        matches = catalog.preferences_for(
            category=category, scope_kind=scope_kind, scope_target=scope_target
        )
        current = _current_preference(matches)
        if current is None:
            if matches:  # 有同身份的撤销历史 → 不自动复活
                continue
            changes.append(
                _create_preference(
                    PreferenceHypothesis(
                        preference_id=new_id("art"),
                        category=category,
                        statement=statement,
                        scope_kind=scope_kind,
                        scope_target=scope_target,
                        status=status,
                        origin=ORIGIN_INDUCED,
                        evidence=commits,
                    )
                )
            )
            continue
        if current.origin == ORIGIN_EXPLICIT:
            if rules.conflicts_with_explicit(current, statement=statement):
                warnings.append(
                    _EXPLICIT_CONFLICT_WARNING.format(category=category, scope_kind=scope_kind)
                )
            continue
        merged = tuple(sorted(set(current.evidence) | set(commits)))
        merged_status = rules.activation_status(merged, policy)
        if category == CATEGORY_STYLE and direction is None:
            merged_status = PREFERENCE_CANDIDATE
        if (
            merged == current.evidence
            and statement == current.statement
            and merged_status == current.status
        ):
            continue  # 证据、陈述与状态均无变化 → 合法 no-op（重跑归纳不制造新版本）
        updated = replace(current, evidence=merged, status=merged_status, statement=statement)
        changes.append(_revise_preference(catalog, updated, where=where))
    if not changes:
        return AuthorModelChange(
            warnings=tuple(warnings), summary="没有需要更新的偏好（合法空结果）"
        )
    return AuthorModelChange(
        artifact_changes=tuple(changes),
        warnings=tuple(warnings),
        summary=f"归纳 {len(changes)} 条偏好",
    )


# ---------------------------------------------------------------------------
# 作者样例：标记正例/反例、软删除（T14.7）
# ---------------------------------------------------------------------------
def mark_example(
    catalog: AuthorModelCatalog,
    new_id: NewId,
    *,
    unit_id: str,
    revision_id: str,
    commit_id: str,
    polarity: str,
    note: str = "",
) -> AuthorModelChange:
    """把某段落的某版本标为正例或反例；只存引用，不复制正文。

    同一引用身份 ``(unit_id, revision_id)`` 上：同极性同备注是合法空结果；极性或
    备注变化时旧记录标记 ``removed`` 且 ``superseded_by`` 指向新记录（取代），历史
    不删除。引用已删除（removed）时重新标注是新例，不修订旧例——删除不封锁。
    """
    where = "author_model.mark_example"
    unit_id = require_str(unit_id, field="unit_id", where=where)
    revision_id = require_str(revision_id, field="revision_id", where=where)
    commit_id = require_str(commit_id, field="commit_id", where=where)
    _require_polarity(polarity, where=where)
    current = _current_example(catalog.examples_for(unit_id=unit_id, revision_id=revision_id))
    if current is not None and current.polarity == polarity and current.note == note:
        return AuthorModelChange(summary="样例标记与现有记录一致（合法空结果）")
    new_example_id = new_id("art")
    changes: list[ArtifactChange] = []
    if current is not None:
        superseded = replace(
            current,
            status=EXAMPLE_REMOVED,
            superseded_by=new_example_id,
            reason="被新的样例标记取代",
        )
        changes.append(_revise_example(catalog, superseded, where=where))
    changes.append(
        _create_example(
            AuthorExample(
                example_id=new_example_id,
                unit_id=unit_id,
                revision_id=revision_id,
                commit_id=commit_id,
                polarity=polarity,
                note=note,
                status=EXAMPLE_ACTIVE,
            )
        )
    )
    label = "正例" if polarity == POLARITY_POSITIVE else "反例"
    return AuthorModelChange(artifact_changes=tuple(changes), summary=f"标记{label}：{unit_id}")


def remove_example(
    catalog: AuthorModelCatalog,
    new_id: NewId,
    *,
    example_id: str,
    reason: str = "",
) -> AuthorModelChange:
    """删除样例：只改状态为 ``removed``，不删除历史（引用 ID 仍可解析）。

    已删除的样例再删除是合法空结果——删除是幂等的，不需要调用方先查状态。
    """
    del new_id  # 删除不改身份：修订既有 Artifact，无需新 ID
    where = "author_model.remove_example"
    entry = catalog.require_example(example_id, where=where)
    example = _decode_example(entry)
    if example.is_removed:
        return AuthorModelChange(summary="样例已处于删除状态（合法空结果）")
    removed = replace(example, status=EXAMPLE_REMOVED, reason=reason)
    return AuthorModelChange(
        artifact_changes=(_revise_example(catalog, removed, where=where),),
        summary=f"删除样例：{example.unit_id}",
    )


# ---------------------------------------------------------------------------
# 内部助手
# ---------------------------------------------------------------------------
def _require_polarity(polarity: str, *, where: str) -> str:
    if polarity not in POLARITY_KINDS:
        raise violation(
            ec.AUTHOR_MODEL_INVALID_EXAMPLE,
            where,
            f"样例极性非法（得到 {polarity!r}）",
            f"用 POLARITY_KINDS 之一：{sorted(POLARITY_KINDS)}",
            polarity=polarity,
        )
    return polarity


def _current_example(matches: tuple[AuthorExample, ...]) -> AuthorExample | None:
    """同引用上的当前样例：非删除者为准；命令保证至多一条。"""
    candidates = [example for example in matches if not example.is_removed]
    return candidates[-1] if candidates else None


def _decode_example(entry: AuthorModelEntry) -> AuthorExample:
    example = entry.model()
    if not isinstance(example, AuthorExample):  # 不可达：kind 已在目录内过滤
        raise violation(
            ec.AUTHOR_MODEL_INVALID_EXAMPLE,
            "author_model.example",
            f"对象 {entry.artifact_id!r} 不是样例",
            "只对 kind=author_model.example 的对象调用样例命令",
        )
    return example


def _create_example(example: AuthorExample) -> ArtifactChange:
    return ArtifactChange(
        artifact_id=ArtifactId(example.example_id),
        content=example.to_content(),
        old_revision_id=None,
        kind=kinds.KIND_EXAMPLE,
    )


def _revise_example(
    catalog: AuthorModelCatalog, example: AuthorExample, *, where: str
) -> ArtifactChange:
    """修订既有样例：带 old_revision_id（乐观并发，不变量 5），不新建身份。"""
    entry = catalog.require_example(example.example_id, where=where)
    return ArtifactChange(
        artifact_id=ArtifactId(example.example_id),
        content=example.to_content(),
        old_revision_id=RevisionId(entry.revision_id),
    )


def _require_category(category: str, *, where: str) -> str:
    if category not in AUTHOR_CATEGORIES:
        raise violation(
            ec.AUTHOR_MODEL_INVALID_PREFERENCE,
            where,
            f"偏好类别非法（得到 {category!r}）",
            f"用 AUTHOR_CATEGORIES 之一：{sorted(AUTHOR_CATEGORIES)}",
            category=category,
        )
    return category


def _require_scope(scope_kind: str, *, where: str) -> None:
    if scope_kind not in SCOPE_KINDS:
        raise violation(
            ec.AUTHOR_MODEL_INVALID_PREFERENCE,
            where,
            f"作用域非法（得到 {scope_kind!r}）",
            f"用 SCOPE_KINDS 之一：{sorted(SCOPE_KINDS)}",
            scope_kind=scope_kind,
        )


def _current_preference(
    matches: tuple[PreferenceHypothesis, ...],
) -> PreferenceHypothesis | None:
    """同身份上的当前偏好：非撤销者为准；命令保证至多一条。"""
    candidates = [preference for preference in matches if not preference.is_revoked]
    return candidates[-1] if candidates else None


def _decode_preference(entry: AuthorModelEntry) -> PreferenceHypothesis:
    preference = entry.model()
    if not isinstance(preference, PreferenceHypothesis):  # 不可达：kind 已在目录内过滤
        raise violation(
            ec.AUTHOR_MODEL_INVALID_PREFERENCE,
            "author_model.preference",
            f"对象 {entry.artifact_id!r} 不是偏好",
            "只对 kind=author_model.preference 的对象调用偏好命令",
        )
    return preference


def _create_preference(preference: PreferenceHypothesis) -> ArtifactChange:
    return ArtifactChange(
        artifact_id=ArtifactId(preference.preference_id),
        content=preference.to_content(),
        old_revision_id=None,
        kind=kinds.KIND_PREFERENCE,
    )


def _revise_preference(
    catalog: AuthorModelCatalog, preference: PreferenceHypothesis, *, where: str
) -> ArtifactChange:
    """修订既有偏好：带 old_revision_id（乐观并发，不变量 5），不新建身份。"""
    entry = catalog.require_preference(preference.preference_id, where=where)
    return ArtifactChange(
        artifact_id=ArtifactId(preference.preference_id),
        content=preference.to_content(),
        old_revision_id=RevisionId(entry.revision_id),
    )
