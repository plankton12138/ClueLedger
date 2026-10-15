"""T14.5 作者学习用例：由两个 Commit 的正文差异分类并登记观察。

覆盖计划 §6 测试先行清单 10/11（格式/事实变化不进入文风；多个证据形成 candidate）
与用例契约：规则优先、模型补充可选且不得覆盖已断言的规则结果、模型故障只告警
不中断、无编辑是合法空结果、重跑分析幂等。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.workflows.author_learning import (
    MODEL_TASK_KIND,
    AuthorLearning,
    ModelObservationClassifier,
    classification_from_proposal,
    combine_classification,
    edit_samples_from_commits,
)
from hastings.domain.author_model.models import (
    CATEGORY_FACT,
    CATEGORY_FORMAT,
    CATEGORY_STYLE,
    OBSERVATION_CANDIDATE,
    OBSERVATION_CONFIRMED,
    SOURCE_MODEL,
    SOURCE_RULE,
    Classification,
)
from hastings.domain.manuscript import commands as manuscript_commands
from hastings.domain.manuscript import operations as ops
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.repository.results import NO_CHANGE

from .conftest import AuthorEnv

CHAPTER = "art_ch1"
TEXTS = ("第一段", "第二段", "第三段")


# ---------------------------------------------------------------------------
# 夹具助手：用正文 handler 造两个 Commit
# ---------------------------------------------------------------------------
def _import_chapter(env: AuthorEnv, project_id: ProjectId) -> Any:
    return env.manuscript_handler.execute(
        manuscript_command(
            manuscript_commands.import_chapter,
            chapter_id=CHAPTER,
            title="第一章",
            text="\n\n".join(TEXTS),
        ),
        project_id=project_id,
        idempotency_key="import",
        summary="导入",
    )


def _unit_ids(env: AuthorEnv, project_id: ProjectId, head: str) -> list[str]:
    catalog = ManuscriptCatalog.from_snapshot(env.service.get_snapshot(project_id, CommitId(head)))
    return [entry.artifact_id for entry in catalog.units_of(CHAPTER)]


def _apply(env: AuthorEnv, project_id: ProjectId, operations: tuple[Any, ...], *, key: str) -> Any:
    return env.manuscript_handler.execute(
        manuscript_command(
            manuscript_commands.apply_edits, chapter_id=CHAPTER, operations=operations
        ),
        project_id=project_id,
        idempotency_key=key,
        summary="改正文",
    )


class FakeRunner:
    """AgentRunner 测试桩：返回固定 Proposal（或抛错）并记录调用。"""

    def __init__(
        self, proposal: Mapping[str, object] | None = None, error: Exception | None = None
    ) -> None:
        self._proposal = dict(proposal or {})
        self._error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def run(
        self, task_kind: str, context_package: Mapping[str, object]
    ) -> Mapping[str, object]:
        self.calls.append((task_kind, dict(context_package)))
        if self._error is not None:
            raise self._error
        return self._proposal


# ---------------------------------------------------------------------------
# 编辑样本推导
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_edit_samples_include_insert_and_delete(author_env: AuthorEnv) -> None:
    project = author_env.manuscript_handler.create_project("冬馆")
    imported = _import_chapter(author_env, project.id)
    units = _unit_ids(author_env, project.id, imported.new_head)
    edited = _apply(
        author_env,
        project.id,
        (ops.Insert(after_unit_id=None, text="新开的一段"), ops.Delete(unit_id=units[2])),
        key="save",
    )

    samples = edit_samples_from_commits(
        author_env.service,
        project_id=project.id,
        commit_id=CommitId(edited.new_head),
        base_commit_id=CommitId(imported.new_head),
        chapter_id=CHAPTER,
    )

    assert {sample.operation for sample in samples} == {ops.OP_INSERT, ops.OP_DELETE}
    inserted = next(s for s in samples if s.operation == ops.OP_INSERT)
    deleted = next(s for s in samples if s.operation == ops.OP_DELETE)
    assert inserted.before == "" and inserted.after == "新开的一段"
    assert deleted.after == "" and deleted.before == "第三段"


# ---------------------------------------------------------------------------
# 用例：分类并登记观察
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_observe_records_candidate_from_real_commit(author_env: AuthorEnv) -> None:
    project = author_env.manuscript_handler.create_project("冬馆")
    imported = _import_chapter(author_env, project.id)
    units = _unit_ids(author_env, project.id, imported.new_head)
    edited = _apply(
        author_env,
        project.id,
        (ops.Replace(unit_id=units[1], text="他慢慢地走进房间。"),),
        key="save",
    )

    result = await author_env.learning.observe(
        project_id=project.id,
        commit_id=CommitId(edited.new_head),
        base_commit_id=CommitId(imported.new_head),
        chapter_id=CHAPTER,
        idempotency_key="analysis-1",
    )

    assert result.is_committed and result.observations == 1 and result.commit_id is not None
    observations = author_env.catalog_at(project.id, result.commit_id).observations()
    assert len(observations) == 1
    observation = observations[0]
    assert observation.edit.unit_id == units[1]
    assert observation.category == CATEGORY_STYLE
    assert observation.status == OBSERVATION_CANDIDATE  # 低置信度 → 保持 candidate


@pytest.mark.anyio
async def test_observe_is_idempotent_on_rerun(author_env: AuthorEnv) -> None:
    project = author_env.manuscript_handler.create_project("冬馆")
    imported = _import_chapter(author_env, project.id)
    units = _unit_ids(author_env, project.id, imported.new_head)
    edited = _apply(
        author_env,
        project.id,
        (ops.Replace(unit_id=units[1], text="他慢慢地走进房间。"),),
        key="save",
    )
    common = {
        "project_id": project.id,
        "commit_id": CommitId(edited.new_head),
        "base_commit_id": CommitId(imported.new_head),
        "chapter_id": CHAPTER,
    }
    first = await author_env.learning.observe(**common, idempotency_key="analysis-1")
    second = await author_env.learning.observe(**common, idempotency_key="analysis-2")

    assert first.is_committed
    assert second.status == NO_CHANGE and second.observations == 0
    assert first.commit_id is not None
    catalog = author_env.catalog_at(project.id, first.commit_id)
    assert len(catalog.observations()) == 1  # 不因重跑而伪造第二条证据


@pytest.mark.anyio
async def test_observe_without_changes_is_legal_empty(author_env: AuthorEnv) -> None:
    project = author_env.manuscript_handler.create_project("冬馆")
    imported = _import_chapter(author_env, project.id)

    result = await author_env.learning.observe(
        project_id=project.id,
        commit_id=CommitId(imported.new_head),
        base_commit_id=CommitId(imported.new_head),
        chapter_id=CHAPTER,
        idempotency_key="analysis-empty",
    )

    assert result.status == NO_CHANGE
    assert result.observations == 0
    assert author_env.catalog_at(project.id, imported.new_head).observations() == ()


# ---------------------------------------------------------------------------
# 模型补充：可选、规则优先、失败只告警
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_model_supplement_resolves_candidate(author_env: AuthorEnv) -> None:
    project = author_env.manuscript_handler.create_project("冬馆")
    imported = _import_chapter(author_env, project.id)
    units = _unit_ids(author_env, project.id, imported.new_head)
    edited = _apply(
        author_env,
        project.id,
        (ops.Replace(unit_id=units[1], text="他慢慢地走进房间。"),),
        key="save",
    )
    runner = FakeRunner({"category": CATEGORY_FACT, "confidence": 0.9, "signals": ["模型判断"]})
    learning = AuthorLearning(
        author_env.service,
        author_env.author_handler,
        classifier=ModelObservationClassifier(runner),
    )

    result = await learning.observe(
        project_id=project.id,
        commit_id=CommitId(edited.new_head),
        base_commit_id=CommitId(imported.new_head),
        chapter_id=CHAPTER,
        idempotency_key="analysis-model",
    )

    assert runner.calls and runner.calls[0][0] == MODEL_TASK_KIND
    assert result.commit_id is not None
    observation = author_env.catalog_at(project.id, result.commit_id).observations()[0]
    assert observation.category == CATEGORY_FACT
    assert observation.classification.source == SOURCE_MODEL
    assert observation.status == OBSERVATION_CONFIRMED
    assert result.warnings == ()


@pytest.mark.anyio
async def test_rule_first_confirmed_result_is_not_overridden(author_env: AuthorEnv) -> None:
    project = author_env.manuscript_handler.create_project("冬馆")
    imported = _import_chapter(author_env, project.id)
    units = _unit_ids(author_env, project.id, imported.new_head)
    edited = _apply(
        author_env,
        project.id,
        (ops.Replace(unit_id=units[1], text="第二段。"),),  # 仅标点变化 → 格式/错字
        key="save",
    )
    runner = FakeRunner({"category": CATEGORY_FACT, "confidence": 0.9})
    learning = AuthorLearning(
        author_env.service,
        author_env.author_handler,
        classifier=ModelObservationClassifier(runner),
    )

    result = await learning.observe(
        project_id=project.id,
        commit_id=CommitId(edited.new_head),
        base_commit_id=CommitId(imported.new_head),
        chapter_id=CHAPTER,
        idempotency_key="analysis-rule",
    )

    assert result.commit_id is not None
    observation = author_env.catalog_at(project.id, result.commit_id).observations()[0]
    assert observation.category == CATEGORY_FORMAT
    assert observation.classification.source == SOURCE_RULE
    assert runner.calls == []  # 规则已断言 → 不打扰模型


@pytest.mark.anyio
async def test_model_failure_falls_back_with_warning(author_env: AuthorEnv) -> None:
    project = author_env.manuscript_handler.create_project("冬馆")
    imported = _import_chapter(author_env, project.id)
    units = _unit_ids(author_env, project.id, imported.new_head)
    edited = _apply(
        author_env,
        project.id,
        (ops.Replace(unit_id=units[1], text="他慢慢地走进房间。"),),
        key="save",
    )
    runner = FakeRunner(error=OSError("分类服务不可达"))
    learning = AuthorLearning(
        author_env.service,
        author_env.author_handler,
        classifier=ModelObservationClassifier(runner),
    )

    result = await learning.observe(
        project_id=project.id,
        commit_id=CommitId(edited.new_head),
        base_commit_id=CommitId(imported.new_head),
        chapter_id=CHAPTER,
        idempotency_key="analysis-fallback",
    )

    assert result.warnings and "改用规则结果" in result.warnings[0]
    assert result.commit_id is not None
    observation = author_env.catalog_at(project.id, result.commit_id).observations()[0]
    assert observation.category == CATEGORY_STYLE  # 回退规则结果，观察仍登记
    assert observation.classification.source == SOURCE_RULE


# ---------------------------------------------------------------------------
# Proposal 解析与合并
# ---------------------------------------------------------------------------
def test_classification_from_proposal_parses_and_rejects() -> None:
    parsed = classification_from_proposal({"category": CATEGORY_FACT, "confidence": 0.8})
    assert parsed is not None and parsed.category == CATEGORY_FACT
    assert parsed.source == SOURCE_MODEL and parsed.status == OBSERVATION_CONFIRMED

    low = classification_from_proposal({"category": CATEGORY_FACT, "confidence": 0.2})
    assert low is not None and low.status == OBSERVATION_CANDIDATE

    assert classification_from_proposal({"category": "nonsense"}) is None
    assert classification_from_proposal({}) is None


def test_combine_classification_prefers_confirmed_rule() -> None:
    rule = Classification(category=CATEGORY_FORMAT, confidence=0.95, status=OBSERVATION_CONFIRMED)
    model = Classification(category=CATEGORY_FACT, confidence=0.9, status=OBSERVATION_CONFIRMED)

    assert combine_classification(rule, model) is rule

    soft_rule = Classification(
        category=CATEGORY_STYLE, confidence=0.55, status=OBSERVATION_CANDIDATE
    )
    assert combine_classification(soft_rule, model) is model
    assert combine_classification(soft_rule, None) is soft_rule
