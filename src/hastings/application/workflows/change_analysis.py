"""影响分析工作流（Phase 15 T15.2/T15.3）：两阶段分析的应用层编排。

编排五步（模块文档 §4.1 两阶段分析）：
1. 读取变更集并前置检查状态——快速失败，不让调用方先跑一遍遍历再挨拒
   （四透镜①「机制前置展示」）；
2. opened → 先推进 analyzing（迁移与结果落库分两次提交，各自幂等可重试；
   若在两步之间崩溃，重试时状态已是 analyzing，直接走记录步恢复）；
3. 在变更集绑定的 base_commit_id 快照上执行**确定性遍历**——报告绑定
   基础版本（不变量 1），head 前移后 ``is_stale`` 可判定；
4. **正文引用补充**（T15.3）：扫描活动段落的字面 mentions，追加带
   confidence 的语义项——确定性已覆盖的段落跳过（renders 等结构信号
   优先），低置信降为 review，永不 must_fix；
5. 经领域命令 ``record_impact_analysis`` 落库：首次分析同提交转入 analyzed；
   review/executing 的重算只替换报告 Revision，不改变状态。

状态裁决（有意的状态机语义扩展，记入构建笔记）：analyzed 状态重复 analyze
视为**幂等重放**——同一变更集的种子、scope 与 base 全部不可变，确定性遍历
必然得到同一结果，直接返回已落库报告，不报错也不产生新提交（四透镜③：
「已完成」不是故障，报错会把调用方推进道歉-重试循环）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.handlers.change_commands import (
    ChangeSetCommandHandler,
    ChangeSetCommandResult,
    change_set_command,
)
from hastings.application.ports.clock import Clock
from hastings.application.ports.repository import RepositoryReader
from hastings.application.ports.semantic_impact import SemanticImpact
from hastings.domain.changes import error_codes as ec
from hastings.domain.changes import kinds
from hastings.domain.changes import status as st
from hastings.domain.changes.catalog import ChangesCatalog
from hastings.domain.changes.commands import advance_change_set, record_impact_analysis
from hastings.domain.changes.impact import (
    ImpactItem,
    ImpactReport,
    ImpactStats,
    RelationGraph,
    TruncatedEntry,
    traverse_impact,
)
from hastings.domain.changes.mentions import (
    MentionCandidate,
    MentionScanStats,
    TextUnitRef,
    scan_text_mentions,
)
from hastings.domain.changes.models import ChangeSet
from hastings.domain.changes.scope import expand_scope
from hastings.domain.manuscript import kinds as ms_kinds
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.models import TextUnit
from hastings.domain.repository import error_codes as repo_ec
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId
from hastings.domain.repository.models import Snapshot

__all__ = ["AnalyzeChangeImpact", "AnalyzeIntent", "AnalyzeResult", "read_change_state"]

# analyzed 走幂等重放；opened/analyzing/review/executing 走正常分析
_PRECHECK_FIX: dict[str, str] = {
    st.CHANGE_APPROVED: "approved 状态等待执行；如需重算影响，先推进到 executing/review",
    st.CHANGE_FAILED: "从 failed 先 advance 回 analyzing（合法迁移）再重试分析",
    st.CHANGE_COMMITTED: "变更集已提交（终态不可迁移）；对新的修改请新建 ChangeSet",
    st.CHANGE_ABANDONED: "变更集已放弃（终态不可迁移）；对新的修改请新建 ChangeSet",
}


def read_change_state(
    reader: RepositoryReader,
    project_id: ProjectId,
    change_set_id: str,
    *,
    branch_name: str = "main",
    where: str = "changes",
) -> tuple[ChangesCatalog, ChangeSet]:
    """读分支 head 上的变更目录并定位 ChangeSet（分析/计划工作流共用）。

    前置拒绝（四透镜①「机制前置展示」）：分支不存在、head 为空、变更集
    未知都在入口处失败，不让调用方先跑一遍重活再挨拒。
    """
    branch = reader.get_branch(project_id, branch_name)
    if branch is None:
        raise ViolationError(
            code=repo_ec.REPO_BRANCH_NOT_FOUND,
            where=f"{where}({project_id.value})",
            why=f"项目 {project_id.value} 下没有名为 {branch_name!r} 的分支",
            fix="先用 create_project 建立项目与 main 分支，再执行影响分析",
            details={"project_id": project_id.value, "branch_name": branch_name},
        )
    head = branch.head_commit_id
    if head is None:
        raise ViolationError(
            code=ec.CHANGES_UNKNOWN_CHANGE_SET,
            where=f"{where}({change_set_id})",
            why="项目还没有任何提交，当前 head 为空",
            fix="先播种对象与关系（提交），再 open_change_set 并分析",
            details={"project_id": project_id.value, "head": None},
        )
    catalog = ChangesCatalog.from_snapshot(reader.get_snapshot(project_id, head))
    entry = catalog.get(change_set_id)
    if entry is None or entry.kind != kinds.KIND_CHANGE_SET:
        raise ViolationError(
            code=ec.CHANGES_UNKNOWN_CHANGE_SET,
            where=f"{where}({change_set_id})",
            why=f"变更集 {change_set_id!r} 在当前 head 上不存在",
            fix="改用 open_change_set 返回的 change_set_id（先查变更中心列表）",
            details={"change_set_id": change_set_id, "head": head.value},
        )
    model: ChangeSet = entry.model()
    return catalog, model


@dataclass(frozen=True, slots=True)
class AnalyzeIntent:
    """一次影响分析意图：目标变更集 + 幂等键前缀（两次提交共用）。"""

    project_id: ProjectId
    change_set_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class AnalyzeResult:
    """分析结果：报告身份 + 统计 + 变更集终态。

    ``commit_id`` 为本次记录提交；幂等重放（analyzed 状态重复调用）为 None。
    ``truncated_count`` 单列：界面上「没查到」与「查到但被截断」必须可区分。
    ``mention_hits`` 为第二阶段正文引用补充的项数（含低置信 review 项）。
    """

    change_set_id: str
    report_id: str
    status: str
    item_count: int
    truncated_count: int
    commit_id: str | None
    mention_hits: int = 0


class AnalyzeChangeImpact:
    """把确定性影响遍历接到 Commit Service 的应用层用例。"""

    def __init__(
        self,
        handler: ChangeSetCommandHandler,
        reader: RepositoryReader,
        clock: Clock,
        actor: Actor,
        *,
        branch_name: str = "main",
        semantic: SemanticImpact | None = None,
    ) -> None:
        self._handler = handler
        self._reader = reader
        self._clock = clock
        self._actor = actor
        self._branch_name = branch_name
        self._semantic = semantic

    def analyze(self, intent: AnalyzeIntent) -> AnalyzeResult:
        catalog, model = self._read_state(intent)
        if model.status == st.CHANGE_ANALYZED:
            return self._replay_analyzed(catalog, model, intent)
        fix = _PRECHECK_FIX.get(model.status)
        if fix is not None:
            raise ViolationError(
                code=ec.CHANGES_INVALID_TRANSITION,
                where=f"changes.analyze({intent.change_set_id})",
                why=f"变更集当前状态是 {model.status!r}，不能执行影响分析",
                fix=fix,
                details={"status": model.status, "change_set_id": intent.change_set_id},
            )
        if model.status == st.CHANGE_OPENED:
            self._advance_analyzing(intent)
        snapshot = self._reader.get_snapshot(intent.project_id, CommitId(model.base_commit_id))
        analysis_model = replace(model, scope=expand_scope(model.scope, snapshot))
        deterministic = traverse_impact(
            RelationGraph.from_snapshot(snapshot),
            tuple(seed.artifact_id for seed in model.seed_changes),
            scope=analysis_model.scope,
            kind_of=self._kind_of(snapshot),
        )
        mention_items, mention_extra, scan_stats = self._scan_mentions(
            analysis_model, snapshot, deterministic
        )
        seed_items = tuple(
            ImpactItem(s.artifact_id, "must_fix", "intent", (), s.reason or model.intent)
            for s in model.seed_changes
            if s.proposed_content is not None
        )
        semantic_items: tuple[ImpactItem, ...] = ()
        coverage = "deterministic"
        warnings: tuple[str, ...] = ()
        if self._semantic is not None:
            try:
                additions = self._semantic(
                    {
                        "base_commit_id": model.base_commit_id,
                        "intent": model.intent,
                        "seed_changes": [s.to_content() for s in model.seed_changes],
                    }
                )
                semantic_items = tuple(
                    replace(
                        i,
                        severity="review" if i.confidence < 0.8 else "should_fix",
                        status="open",
                        path=(),
                    )
                    for i in additions
                    if ArtifactId(i.target_artifact_id) in snapshot.artifacts
                    and self._unit_in_scope(
                        analysis_model,
                        i.target_artifact_id,
                        self._kind_of(snapshot)(i.target_artifact_id),
                    )[0]
                )
                coverage = "semantic_supplemented"
            except Exception as exc:
                coverage, warnings = (
                    "deterministic_only",
                    (f"语义补充失败：{type(exc).__name__}: {exc}",),
                )
        merged = ImpactReport(
            report_id="",
            change_set_id="",
            base_commit_id="",
            items=(*seed_items, *deterministic.items, *mention_items, *semantic_items),
            coverage=coverage,
            warnings=warnings,
            truncated=(*deterministic.truncated, *mention_extra),
            stats=ImpactStats(
                seed_count=deterministic.stats.seed_count,
                nodes_visited=deterministic.stats.nodes_visited,
                paths_explored=deterministic.stats.paths_explored,
                cycles_skipped=deterministic.stats.cycles_skipped,
                truncated_count=len(deterministic.truncated) + len(mention_extra),
                item_count=len(seed_items)
                + len(deterministic.items)
                + len(mention_items)
                + len(semantic_items),
                mention_units_scanned=scan_stats.unit_count,
                mention_hits=scan_stats.hit_count,
                mention_skipped_covered=scan_stats.skipped_covered,
            ),
        )
        record = self._record(model, merged, intent)
        final = self._read_state(intent)[1]
        report_change = next(
            (c for c in record.change.artifact_changes if c.kind == kinds.KIND_IMPACT_REPORT),
            None,
        )
        assert report_change is not None  # record 命令保证报告变更存在
        return AnalyzeResult(
            change_set_id=intent.change_set_id,
            report_id=report_change.artifact_id.value,
            status=final.status,
            item_count=merged.stats.item_count,
            truncated_count=merged.stats.truncated_count,
            commit_id=record.commit_id,
            mention_hits=merged.stats.mention_hits,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _read_state(self, intent: AnalyzeIntent) -> tuple[ChangesCatalog, ChangeSet]:
        return read_change_state(
            self._reader,
            intent.project_id,
            intent.change_set_id,
            branch_name=self._branch_name,
            where="changes.analyze",
        )

    def _advance_analyzing(self, intent: AnalyzeIntent) -> ChangeSetCommandResult:
        return self._handler.execute(
            change_set_command(
                advance_change_set,
                change_set_id=intent.change_set_id,
                target=st.CHANGE_ANALYZING,
                actor=self._actor,
                at=self._clock.now(),
                reason="开始影响分析",
            ),
            project_id=intent.project_id,
            idempotency_key=f"{intent.idempotency_key}:start",
            summary=f"变更集 {intent.change_set_id} 开始影响分析",
        )

    def _kind_of(self, snapshot: Snapshot) -> Any:
        """节点→kind 解析器：scope.kinds 白名单与正文 scope 过滤共用。"""
        return {
            view.artifact.id.value: view.artifact.kind for view in snapshot.artifacts.values()
        }.get

    def _scan_mentions(
        self, model: ChangeSet, snapshot: Snapshot, deterministic: ImpactReport
    ) -> tuple[tuple[ImpactItem, ...], tuple[TruncatedEntry, ...], MentionScanStats]:
        """正文引用补充（第二阶段）：候选从种子对象投影，段落取活动 TextUnit。

        scope 过滤与确定性遍历同规则（roots/kinds 白名单），被过滤段落记
        TruncatedEntry——「没扫」与「扫不到」可区分（四透镜③）。
        """
        candidates = tuple(
            MentionCandidate(
                artifact_id=seed.artifact_id, aliases=self._aliases_of(snapshot, seed.artifact_id)
            )
            for seed in model.seed_changes
        )
        candidates = tuple(c for c in candidates if c.aliases)
        covered = frozenset(item.target_artifact_id for item in deterministic.items)
        kind_of = self._kind_of(snapshot)
        catalog = ManuscriptCatalog.from_snapshot(snapshot)
        units: list[TextUnitRef] = []
        truncated: list[TruncatedEntry] = []
        for entry in catalog.by_kind(ms_kinds.KIND_TEXT_UNIT):
            kind = kind_of(entry.artifact_id) or ""
            in_scope, reason = self._unit_in_scope(model, entry.artifact_id, kind)
            if not in_scope:
                truncated.append(
                    TruncatedEntry(
                        artifact_id=entry.artifact_id, relation_type="mentions", reason=reason
                    )
                )
                continue
            unit: TextUnit = entry.model()
            if not unit.is_active:
                continue  # 已删除段落不参与扫描；不计截断（是明确的无须检查）
            units.append(TextUnitRef(text_unit_id=entry.artifact_id, text=unit.text))
        result = scan_text_mentions(candidates, tuple(units), covered_ids=covered)
        return result.items, tuple(truncated), result.stats

    @staticmethod
    def _unit_in_scope(model: ChangeSet, unit_id: str, kind: str) -> tuple[bool, str]:
        scope = model.scope
        if scope.roots and unit_id not in scope.roots:
            return False, f"scope.roots 白名单不含 {unit_id}"
        if scope.kinds and kind not in scope.kinds:
            return False, f"scope.kinds 白名单不含 {kind}"
        return True, ""

    @staticmethod
    def _aliases_of(snapshot: Snapshot, artifact_id: str) -> tuple[str, ...]:
        """从对象内容投影名称别名：name + aliases；无名称候选不参与扫描。"""
        view = snapshot.artifacts.get(ArtifactId(artifact_id))
        if view is None or view.revision is None:
            return ()
        content = view.revision.content
        names: list[str] = []
        name = content.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name)
        raw_aliases = content.get("aliases")
        if isinstance(raw_aliases, (list, tuple)):
            names.extend(a for a in raw_aliases if isinstance(a, str) and a.strip())
        return tuple(dict.fromkeys(names))  # 去重保序

    def _record(
        self, model: ChangeSet, report: ImpactReport, intent: AnalyzeIntent
    ) -> ChangeSetCommandResult:
        return self._handler.execute(
            change_set_command(
                record_impact_analysis,
                change_set_id=intent.change_set_id,
                base_commit_id=model.base_commit_id,
                report=report,
                actor=self._actor,
                at=self._clock.now(),
            ),
            project_id=intent.project_id,
            idempotency_key=f"{intent.idempotency_key}:record",
            summary=f"变更集 {intent.change_set_id} 影响分析落库",
        )

    def _replay_analyzed(
        self, catalog: ChangesCatalog, model: ChangeSet, intent: AnalyzeIntent
    ) -> AnalyzeResult:
        """analyzed 状态的重复 analyze = 幂等重放：返回已落库报告，不产生新提交。"""
        entry = next(
            (
                e
                for e in catalog.by_kind(kinds.KIND_IMPACT_REPORT)
                if isinstance(e.model(), ImpactReport)
                and e.model().change_set_id == intent.change_set_id
            ),
            None,
        )
        if entry is None:
            raise ViolationError(
                code=ec.CHANGES_INVALID_TRANSITION,
                where=f"changes.analyze({intent.change_set_id})",
                why="变更集状态是 analyzed，但找不到已落库的影响报告（异常历史）",
                fix="新建 ChangeSet 重新走 open → analyze 流程",
                details={"change_set_id": intent.change_set_id},
            )
        report: ImpactReport = entry.model()
        return AnalyzeResult(
            change_set_id=intent.change_set_id,
            report_id=entry.artifact_id,
            status=model.status,
            item_count=report.stats.item_count,
            truncated_count=report.stats.truncated_count,
            commit_id=None,
        )
