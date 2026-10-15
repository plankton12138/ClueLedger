"""ChangeSet 领域命令（T15.1）：open 与 advance。

命令是纯函数 ``fn(catalog, new_id, **intent) -> ChangeSetChange``，不触达
Repository。状态机校验（``status.require_transition``）与目标专属规则
（批准要作者凭证、committed 要 Commit ID）都在命令层拒绝——硬约束放工具层，
prompt 只负责解释世界（四透镜②）。
"""

from __future__ import annotations

from typing import Any

from hastings.domain.changes import error_codes as ec
from hastings.domain.changes import kinds
from hastings.domain.changes import status as st
from hastings.domain.changes.catalog import ChangesCatalog
from hastings.domain.changes.errors import violation
from hastings.domain.changes.impact import ImpactReport
from hastings.domain.changes.models import (
    EVENT_CHANGE_SET_OPENED,
    EVENT_CHANGE_SET_TRANSITIONED,
    SOURCES,
    ChangeScope,
    ChangeSet,
    ChangeSetChange,
    SeedChange,
    TransitionRecord,
)
from hastings.domain.repository.ids import Actor, ArtifactId, RevisionId
from hastings.domain.repository.requests import ArtifactChange

__all__ = ["NewId", "advance_change_set", "open_change_set", "record_impact_analysis"]

NewId = Any  # callable[[str], str]；由 handler 注入（领域不依赖端口）


def _unique_change_set_id(catalog: ChangesCatalog, new_id: NewId) -> str:
    """生成目录内唯一的 ChangeSet ID（防御生成器冲突，上限 1000 次）。"""
    for _ in range(1000):
        candidate: str = new_id("chg")
        if catalog.get(candidate) is None:
            return candidate
    raise violation(
        ec.CHANGES_INVALID_COMMAND,
        "changes.open",
        "连续 1000 次生成的 ChangeSet ID 都已存在",
        "检查 IdGenerator 是否配置正确（应产出递增唯一 ID）",
    )


def open_change_set(
    catalog: ChangesCatalog,
    new_id: NewId,
    *,
    intent: str,
    source: str,
    base_commit_id: str,
    seed_changes: tuple[SeedChange, ...] = (),
    scope: ChangeScope | None = None,
    actor: Actor,
    at: Any,
    reason: str,
) -> ChangeSetChange:
    """打开变更集：绑定基础 Commit、种子变化与范围，状态置 opened。"""
    where = "changes.open"
    _require_non_empty(intent, "intent", where)
    if source not in SOURCES:
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            f"source 只能是 {sorted(SOURCES)}，收到 {source!r}",
            "把 source 改为 manual / agent_proposal / import / migration",
            source=source,
        )
    _require_non_empty(base_commit_id, "base_commit_id", where)
    _require_non_empty(reason, "reason", where)
    for seed in seed_changes:
        _require_non_empty(seed.artifact_id, "seed_changes[].artifact_id", where)

    change_set_id = _unique_change_set_id(catalog, new_id)
    record = TransitionRecord(
        from_status="",
        to_status=st.CHANGE_OPENED,
        actor_kind=actor.kind,
        actor_id=actor.id,
        at=_iso(at),
        reason=reason,
        base_commit_id=base_commit_id,
    )
    model = ChangeSet(
        change_set_id=change_set_id,
        intent=intent,
        source=source,
        base_commit_id=base_commit_id,
        status=st.CHANGE_OPENED,
        history=(record,),
        seed_changes=tuple(seed_changes),
        scope=scope or ChangeScope(),
    )
    return ChangeSetChange(
        artifact_changes=(
            ArtifactChange(
                artifact_id=ArtifactId(change_set_id),
                content=model.to_content(),
                old_revision_id=None,
                kind=kinds.KIND_CHANGE_SET,
            ),
        ),
        events=(
            (
                EVENT_CHANGE_SET_OPENED,
                {
                    "change_set_id": change_set_id,
                    "base_commit_id": base_commit_id,
                    "source": source,
                },
            ),
        ),
        summary=f"打开变更集 {change_set_id}：{intent}",
    )


def advance_change_set(
    catalog: ChangesCatalog,
    new_id: NewId,
    *,
    change_set_id: str,
    target: str,
    actor: Actor,
    at: Any,
    reason: str,
    approval_ref: str = "",
    commit_id: str = "",
) -> ChangeSetChange:
    """推进变更集状态：校验迁移合法性，追加审计记录并发出迁移事件。"""
    del new_id  # 迁移不创建新对象；签名与其它命令保持同构
    entry = catalog.require_change_set(change_set_id, where="changes.advance")
    model: ChangeSet = entry.model()
    where = f"changes.advance({change_set_id})"
    _require_non_empty(reason, "reason", where)
    st.require_transition(model.status, target, where=where)

    # 目标专属规则（硬约束，缺失即拒绝）
    if target == st.CHANGE_APPROVED:
        if actor.kind != "human":
            raise violation(
                ec.CHANGES_APPROVAL_REQUIRED,
                where,
                f"批准只能由作者用例签发（actor.kind=human），收到 {actor.kind!r}",
                "Agent 不能为自己的计划签发批准；由作者在授权界面确认后再调用",
                actor_kind=actor.kind,
                actor_id=actor.id,
            )
        if not approval_ref:
            raise violation(
                ec.CHANGES_APPROVAL_REQUIRED,
                where,
                "批准缺少凭证（approval_ref 为空）",
                "先经作者授权流程取得 Approval Receipt，再携带 receipt 引用批准",
                change_set_id=change_set_id,
            )
    if target == st.CHANGE_COMMITTED and not commit_id:
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            "提交迁移必须携带产生的 Commit ID（commit_id 为空）",
            "先完成接受与合并提交，再携带该 Commit ID 推进到 committed",
            change_set_id=change_set_id,
        )

    record = TransitionRecord(
        from_status=model.status,
        to_status=target,
        actor_kind=actor.kind,
        actor_id=actor.id,
        at=_iso(at),
        reason=reason,
        base_commit_id=model.base_commit_id,
        approval_ref=approval_ref if target == st.CHANGE_APPROVED else "",
        commit_id=commit_id if target == st.CHANGE_COMMITTED else "",
    )
    updated = ChangeSet(
        change_set_id=model.change_set_id,
        intent=model.intent,
        source=model.source,
        base_commit_id=model.base_commit_id,
        status=target,
        history=(*model.history, record),
        seed_changes=model.seed_changes,
        scope=model.scope,
        schema_version=model.schema_version,
    )
    return ChangeSetChange(
        artifact_changes=(
            ArtifactChange(
                artifact_id=ArtifactId(change_set_id),
                content=updated.to_content(),
                old_revision_id=RevisionId(entry.revision_id),
            ),
        ),
        events=(
            (
                EVENT_CHANGE_SET_TRANSITIONED,
                {"change_set_id": change_set_id, "from": model.status, "to": target},
            ),
        ),
        summary=f"ChangeSet {change_set_id}：{model.status} → {target}（{reason}）",
    )


def record_impact_analysis(
    catalog: ChangesCatalog,
    new_id: NewId,
    *,
    change_set_id: str,
    base_commit_id: str,
    report: ImpactReport,
    actor: Actor,
    at: Any,
    reason: str = "影响分析完成",
) -> ChangeSetChange:
    """登记影响分析结果：写报告 Artifact，并在首次分析时推进 analyzing→analyzed。

    首次分析（status=analyzing）→ 同一提交内转入 analyzed（「分析完成」与
    「结果落库」原子生效）。重算（status=review/executing，T15.7 部分接受后
    自动重算剩余影响）→ 只替换报告版本，不改变状态。
    空报告（无下游影响）是合法空结果的**载体**：报告照常保存，只是没有
    ImpactItem——后续据此不建修订分支（模块文档不变量 9）。
    """
    entry = catalog.require_change_set(change_set_id, where="changes.record_analysis")
    model: ChangeSet = entry.model()
    where = f"changes.record_analysis({change_set_id})"
    if model.status not in (st.CHANGE_ANALYZING, st.CHANGE_REVIEW, st.CHANGE_EXECUTING):
        raise violation(
            ec.CHANGES_INVALID_TRANSITION,
            where,
            f"只有 analyzing/review/executing 状态能登记分析结果，当前是 {model.status!r}",
            "先 advance 到 analyzing 再执行分析；重开已终态的变更集请新建 ChangeSet",
            status=model.status,
        )
    if base_commit_id != model.base_commit_id:
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            f"分析绑定的基础 Commit {base_commit_id!r} 与变更集的 {model.base_commit_id!r} 不一致",
            "在变更集的 base_commit_id 对应快照上重新执行遍历（报告与计划都绑定基础版本）",
            base_commit_id=base_commit_id,
            expected=model.base_commit_id,
        )

    changes: list[ArtifactChange] = []
    # 报告 Artifact：同一变更集重算时复用同一 Artifact（新 Revision），历史可追溯
    existing = next(
        (
            e
            for e in catalog.by_kind(kinds.KIND_IMPACT_REPORT)
            if isinstance(e.model(), ImpactReport) and e.model().change_set_id == change_set_id
        ),
        None,
    )
    if existing is not None:
        report_id = existing.artifact_id
        old_revision: RevisionId | None = RevisionId(existing.revision_id)
    else:
        report_id = _unique_artifact_id(catalog, new_id)
        old_revision = None
    stamped = ImpactReport(
        report_id=report_id,
        change_set_id=change_set_id,
        base_commit_id=base_commit_id,
        items=report.items,
        truncated=report.truncated,
        stats=report.stats,
        analyzed_by=f"{actor.kind}:{actor.id}",
        at=_iso(at),
    )
    changes.append(
        ArtifactChange(
            artifact_id=ArtifactId(report_id),
            content=stamped.to_content(),
            old_revision_id=old_revision,
            kind=kinds.KIND_IMPACT_REPORT,
        )
    )

    events: list[tuple[str, dict[str, Any]]] = [
        (
            "changes.impact.recorded",
            {
                "change_set_id": change_set_id,
                "report_id": report_id,
                "base_commit_id": base_commit_id,
                "item_count": report.stats.item_count,
            },
        )
    ]
    summary = (
        f"变更集 {change_set_id} 影响分析：{report.stats.item_count} 项"
        f"（截断 {report.stats.truncated_count}）"
    )

    if model.status == st.CHANGE_ANALYZING:
        record = TransitionRecord(
            from_status=model.status,
            to_status=st.CHANGE_ANALYZED,
            actor_kind=actor.kind,
            actor_id=actor.id,
            at=_iso(at),
            reason=reason,
            base_commit_id=model.base_commit_id,
        )
        updated = ChangeSet(
            change_set_id=model.change_set_id,
            intent=model.intent,
            source=model.source,
            base_commit_id=model.base_commit_id,
            status=st.CHANGE_ANALYZED,
            history=(*model.history, record),
            seed_changes=model.seed_changes,
            scope=model.scope,
            schema_version=model.schema_version,
        )
        changes.append(
            ArtifactChange(
                artifact_id=ArtifactId(change_set_id),
                content=updated.to_content(),
                old_revision_id=RevisionId(entry.revision_id),
            )
        )
        events.append(
            (
                EVENT_CHANGE_SET_TRANSITIONED,
                {
                    "change_set_id": change_set_id,
                    "from": model.status,
                    "to": st.CHANGE_ANALYZED,
                },
            )
        )
        summary = f"{summary}；状态转入 analyzed"

    return ChangeSetChange(
        artifact_changes=tuple(changes),
        events=tuple(events),
        summary=summary,
    )


def _unique_artifact_id(catalog: ChangesCatalog, new_id: NewId) -> str:
    """生成目录内唯一的报告 Artifact ID（防御生成器冲突，上限 1000 次）。"""
    for _ in range(1000):
        candidate: str = new_id("rep")
        if catalog.get(candidate) is None:
            return candidate
    raise violation(
        ec.CHANGES_INVALID_COMMAND,
        "changes.record_analysis",
        "连续 1000 次生成的报告 ID 都已存在",
        "检查 IdGenerator 是否配置正确（应产出递增唯一 ID）",
    )


def _require_non_empty(value: str, field_name: str, where: str) -> str:
    if not value or not value.strip():
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            f"{field_name} 不能为空",
            f"提供非空的 {field_name}（它是审计与影响分析的输入，缺省无法解释本次变化）",
            field=field_name,
        )
    return value


def _iso(at: Any) -> str:
    """时间统一存 ISO-8601 字符串（内容是 JSON 兼容数据）。"""
    return at.isoformat() if hasattr(at, "isoformat") else str(at)
