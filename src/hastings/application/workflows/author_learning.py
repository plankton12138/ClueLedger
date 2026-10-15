"""作者学习用例（Phase 14 T14.5/T14.6）：把正文 Commit 的编辑分类登记为观察，并归纳偏好。

**规则优先，模型补充**（计划 §T14.5）：先由领域规则（``domain/author_model/rules.py``）
对每个编辑样本分类；只有规则未断言（低置信度或冲突）时，才请可选的模型分类器
经 ``AgentRunner`` 端口给出 Proposal 补充。模型不可用只告警并回退规则结果，
不中断分析（四透镜③④：环境故障明确归因、不静默吞）。

编辑样本由**两个 Commit 的正文差异**推出：分析消费者拿到分析 Job 的
``commit_id`` 与保存前的 ``base_commit_id``，逐段比较活动文本。这样观察始终
指向真实发生的变化，而不是调用方自述的意图。

登记观察后**自动归纳偏好**（T14.6）：归纳用独立幂等键，与观察登记各自幂等——
观察已落盘而归纳失败时，重试只重跑归纳，不会重复登记证据（四透镜③：两步各自
幂等，局部失败不放大）。归纳的阈值与作用域由 ``InductionPolicy`` 注入。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass

from hastings.application.handlers.author_model_commands import (
    AuthorModelCommandHandler,
    AuthorModelCommandResult,
    author_model_command,
)
from hastings.application.ports.agents import AgentRunner
from hastings.application.ports.repository import RepositoryReader
from hastings.domain.author_model import commands as author_commands
from hastings.domain.author_model import rules as author_rules
from hastings.domain.author_model.models import (
    AUTHOR_CATEGORIES,
    OBSERVATION_CANDIDATE,
    OBSERVATION_CONFIRMED,
    SCOPE_PROJECT,
    SOURCE_MODEL,
    Classification,
    EditSample,
    ObservationDraft,
)
from hastings.domain.manuscript import kinds as manuscript_kinds
from hastings.domain.manuscript import operations as ops
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.models import TextUnit
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.repository.results import COMMITTED, NO_CHANGE

__all__ = [
    "MODEL_TASK_KIND",
    "AuthorLearning",
    "AuthorLearningResult",
    "ModelObservationClassifier",
    "classification_from_proposal",
    "combine_classification",
    "edit_samples_from_commits",
]

MODEL_TASK_KIND = "author_observation"

_MODEL_FALLBACK_WARNING = (
    "[author_learning] 模型分类补充失败，改用规则结果（不中断分析）。"
    "为什么错：{error}。"
    "怎样算对：检查模型 Provider 与网络，稍后重跑分析；规则结果已登记为观察。"
)


# ---------------------------------------------------------------------------
# 编辑样本：从两个 Commit 的正文差异推出（不采信调用方自述的意图）
# ---------------------------------------------------------------------------
def edit_samples_from_commits(
    reader: RepositoryReader,
    *,
    project_id: ProjectId,
    commit_id: CommitId,
    base_commit_id: CommitId,
    chapter_id: str,
) -> tuple[EditSample, ...]:
    """逐段比较 base 与当前 Commit 的章内文本，产出编辑样本（顺序确定）。"""
    before = ManuscriptCatalog.from_snapshot(reader.get_snapshot(project_id, base_commit_id))
    after = ManuscriptCatalog.from_snapshot(reader.get_snapshot(project_id, commit_id))
    samples: list[EditSample] = []
    for entry in after.by_kind(manuscript_kinds.KIND_TEXT_UNIT):
        if str(entry.content.get("chapter_id", "")) != chapter_id:
            continue
        unit = TextUnit.from_content(entry.content)
        before_text = _active_text(before, entry.artifact_id)
        after_text = unit.text if unit.is_active else ""
        if before_text == after_text:
            continue  # 未变化的段落不是观察对象
        samples.append(
            EditSample(
                unit_id=entry.artifact_id,
                chapter_id=chapter_id,
                before=before_text,
                after=after_text,
                operation=_operation_of(before_text, after_text),
            )
        )
    return tuple(samples)


def _active_text(catalog: ManuscriptCatalog, unit_id: str) -> str:
    """段落在某 Commit 的**活动**文本；不存在或已删除返回空串。"""
    entry = catalog.unit_entry(unit_id)
    if entry is None:
        return ""
    unit = TextUnit.from_content(entry.content)
    return unit.text if unit.is_active else ""


def _operation_of(before: str, after: str) -> str:
    if not before:
        return ops.OP_INSERT
    if not after:
        return ops.OP_DELETE
    return ops.OP_REPLACE


# ---------------------------------------------------------------------------
# 模型补充：经 AgentRunner 端口返回 Proposal
# ---------------------------------------------------------------------------
def classification_from_proposal(
    proposal: Mapping[str, object], *, threshold: float = author_rules.DEFAULT_CONFIDENCE_THRESHOLD
) -> Classification | None:
    """把工作者 Proposal 解析为分类；不可采纳时返回 None（规则结果兜底）。

    模型补充是**可选**的：类别缺失或不在 ``AUTHOR_CATEGORIES`` 内时视为未提供
    （合法空结果），不阻断批处理；置信度低于阈值同样保持 candidate。
    """
    category = proposal.get("category")
    if not isinstance(category, str) or category not in AUTHOR_CATEGORIES:
        return None
    confidence = _confidence(proposal.get("confidence"))
    status = OBSERVATION_CONFIRMED if confidence >= threshold else OBSERVATION_CANDIDATE
    signals = proposal.get("signals")
    signal_tuple = (
        tuple(str(item) for item in signals) if isinstance(signals, (list, tuple)) else ()
    )
    return Classification(
        category=category,
        confidence=confidence,
        status=status,
        signals=signal_tuple,
        source=SOURCE_MODEL,
    )


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return min(max(float(value), 0.0), 1.0)


def combine_classification(rule: Classification, model: Classification | None) -> Classification:
    """规则优先：规则已断言则保留；否则采用模型补充（若模型未给出可采纳类别则回退）。"""
    if rule.is_confirmed or model is None:
        return rule
    return model


class ModelObservationClassifier:
    """可选的模型分类补充：把编辑样本交给工作者分类，返回结构化 Proposal。

    Proposal 与规则结果一样只是候选解释，不是提交证据（ADR-003）。
    """

    def __init__(
        self,
        runner: AgentRunner,
        *,
        task_kind: str = MODEL_TASK_KIND,
        threshold: float = author_rules.DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._runner = runner
        self._task_kind = task_kind
        self._threshold = threshold

    async def classify(self, sample: EditSample) -> Classification | None:
        proposal = await self._runner.run(self._task_kind, {"edit": sample.to_content()})
        return classification_from_proposal(proposal, threshold=self._threshold)


# ---------------------------------------------------------------------------
# 用例：分类并登记观察
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AuthorLearningResult:
    """分析结果：Commit 状态 + 登记观察数 + 归纳偏好数 + 告警。"""

    status: str
    summary: str
    observations: int
    preference_changes: int = 0
    warnings: tuple[str, ...] = ()
    commit_id: str | None = None

    @property
    def is_committed(self) -> bool:
        return self.status == COMMITTED

    @property
    def is_no_change(self) -> bool:
        return self.status == NO_CHANGE


class AuthorLearning:
    """把一次正文 Commit 的编辑分类登记为观察，并据证据归纳偏好。

    ``policy`` 是注入的归纳策略：证据阈值不写死在流程里，产品与测试可各用各的松紧。
    """

    def __init__(
        self,
        reader: RepositoryReader,
        handler: AuthorModelCommandHandler,
        *,
        classifier: ModelObservationClassifier | None = None,
        threshold: float = author_rules.DEFAULT_CONFIDENCE_THRESHOLD,
        policy: author_rules.InductionPolicy = author_rules.DEFAULT_INDUCTION_POLICY,
    ) -> None:
        self._reader = reader
        self._handler = handler
        self._classifier = classifier
        self._threshold = threshold
        self._policy = policy

    async def observe(
        self,
        *,
        project_id: ProjectId,
        commit_id: CommitId,
        base_commit_id: CommitId,
        chapter_id: str,
        idempotency_key: str,
        summary: str = "分析作者编辑",
    ) -> AuthorLearningResult:
        """分类本次 Commit 的章内编辑并登记观察，随后归纳偏好；无编辑时为合法空结果。"""
        samples = edit_samples_from_commits(
            self._reader,
            project_id=project_id,
            commit_id=commit_id,
            base_commit_id=base_commit_id,
            chapter_id=chapter_id,
        )
        if not samples:
            return AuthorLearningResult(
                status=NO_CHANGE, summary="没有可分类的编辑（合法空结果）", observations=0
            )
        drafts, warnings = await self._classify(samples, commit_id=commit_id)
        observation_result = self._handler.execute(
            author_model_command(author_commands.record_observations, drafts=drafts),
            project_id=project_id,
            idempotency_key=idempotency_key,
            summary=summary,
        )
        preference_result = self._induce(project_id, idempotency_key=idempotency_key)
        warnings = warnings + preference_result.change.warnings
        status = (
            COMMITTED
            if (observation_result.is_committed or preference_result.is_committed)
            else NO_CHANGE
        )
        return AuthorLearningResult(
            status=status,
            summary=_combine_summary(observation_result, preference_result),
            observations=len(observation_result.change.artifact_changes),
            preference_changes=len(preference_result.change.artifact_changes),
            warnings=warnings,
            commit_id=preference_result.commit_id or observation_result.commit_id,
        )

    def _induce(self, project_id: ProjectId, *, idempotency_key: str) -> AuthorModelCommandResult:
        """登记观察后自动归纳偏好；独立幂等键使两步各自的幂等互不干扰。"""
        return self._handler.execute(
            author_model_command(
                author_commands.induce_preferences,
                scope_kind=SCOPE_PROJECT,
                scope_target="",
                policy=self._policy,
            ),
            project_id=project_id,
            idempotency_key=f"{idempotency_key}:induce",
            summary="归纳作者偏好",
        )

    async def _classify(
        self, samples: tuple[EditSample, ...], *, commit_id: CommitId
    ) -> tuple[tuple[ObservationDraft, ...], tuple[str, ...]]:
        drafts: list[ObservationDraft] = []
        warnings: list[str] = []
        for sample in samples:
            classification = author_rules.classify_edit(sample, threshold=self._threshold)
            if self._classifier is not None and not classification.is_confirmed:
                classification, warning = await self._model_supplement(sample, classification)
                if warning:
                    warnings.append(warning)
            drafts.append(
                ObservationDraft(
                    edit=sample, classification=classification, commit_id=commit_id.value
                )
            )
        return tuple(drafts), tuple(warnings)

    async def _model_supplement(
        self, sample: EditSample, rule_result: Classification
    ) -> tuple[Classification, str]:
        if self._classifier is None:  # 不可达：调用方已判空
            return rule_result, ""
        try:
            model_result = await self._classifier.classify(sample)
        except Exception as exc:  # noqa: BLE001 - 边界泄压阀：模型补充失败不阻断批处理
            warning = _MODEL_FALLBACK_WARNING.format(error=exc)
            print(warning, file=sys.stderr)
            return rule_result, warning
        return combine_classification(rule_result, model_result), ""


def _combine_summary(
    observation_result: AuthorModelCommandResult, preference_result: AuthorModelCommandResult
) -> str:
    """两步摘要合并：只报真正发生的变化，未提交的一步不把摘要搅浑。"""
    if not preference_result.is_committed:
        return observation_result.summary
    if not observation_result.is_committed:
        return preference_result.summary
    return f"{observation_result.summary}；{preference_result.summary}"
