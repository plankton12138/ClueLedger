"""T14.5 观察登记命令：身份去重（重跑幂等）、合法空结果与序列化往返。

身份键 ``(来源 Commit, 段落 ID)`` 是硬约束：同一编辑重复登记会伪造「多次独立
证据」，让偏好归纳误判为已达激活阈值（计划 §9 风险控制）。重复不是故障，
是合法空结果（四透镜③），由 handler 跳过提交。
"""

from __future__ import annotations

import pytest

from hastings.domain.author_model import commands, kinds
from hastings.domain.author_model import error_codes as ec
from hastings.domain.author_model.errors import AuthorModelViolation
from hastings.domain.author_model.models import (
    AUTHOR_CATEGORIES,
    CATEGORY_STYLE,
    OBSERVATION_CANDIDATE,
    OBSERVATION_CONFIRMED,
    SOURCE_MODEL,
    AuthorObservation,
    Classification,
    EditSample,
    ObservationDraft,
)

from .conftest import build_catalog, observation_entry

CHAPTER = "art_ch1"


def _draft(
    *,
    commit_id: str = "cmt_2",
    unit_id: str = "art_u1",
    before: str = "他缓缓地走进了房间。",
    after: str = "他慢慢地走进房间。",
    category: str | None = CATEGORY_STYLE,
    confidence: float = 0.55,
    status: str = OBSERVATION_CANDIDATE,
    source: str = SOURCE_MODEL,
) -> ObservationDraft:
    return ObservationDraft(
        edit=EditSample(unit_id=unit_id, chapter_id=CHAPTER, before=before, after=after),
        classification=Classification(
            category=category, confidence=confidence, status=status, source=source
        ),
        commit_id=commit_id,
    )


def test_record_observations_creates_observation_artifacts() -> None:
    change = commands.record_observations(
        build_catalog([]), lambda kind: f"art_{kind}", drafts=(_draft(),)
    )

    assert not change.is_empty()
    assert len(change.artifact_changes) == 1
    artifact_change = change.artifact_changes[0]
    assert artifact_change.kind == kinds.KIND_OBSERVATION
    assert artifact_change.old_revision_id is None
    assert artifact_change.content["commit_id"] == "cmt_2"


def test_existing_identity_is_skipped_as_legal_empty() -> None:
    catalog = build_catalog(
        [
            observation_entry(
                "art_obs1",
                commit_id="cmt_2",
                unit_id="art_u1",
                category=CATEGORY_STYLE,
                confidence=0.55,
                status=OBSERVATION_CANDIDATE,
            )
        ]
    )

    change = commands.record_observations(catalog, lambda kind: f"art_{kind}", drafts=(_draft(),))

    assert change.is_empty()
    assert "合法空结果" in change.summary


def test_duplicate_keys_in_one_batch_are_collapsed() -> None:
    change = commands.record_observations(
        build_catalog([]),
        lambda kind: f"art_{kind}",
        drafts=(_draft(), _draft(before="甲", after="乙")),
    )

    assert len(change.artifact_changes) == 1


def test_missing_commit_id_is_violation_with_three_elements() -> None:
    with pytest.raises(AuthorModelViolation) as exc:
        commands.record_observations(
            build_catalog([]), lambda kind: f"art_{kind}", drafts=(_draft(commit_id=""),)
        )

    error = exc.value
    assert error.code == ec.AUTHOR_MODEL_MISSING_REQUIRED_FIELD
    assert error.where and error.why and error.fix


def test_observation_round_trips_through_content() -> None:
    observation = AuthorObservation(
        observation_id="art_obs1",
        commit_id="cmt_2",
        edit=EditSample(
            unit_id="art_u1", chapter_id=CHAPTER, before="甲", after="乙", note="太啰嗦"
        ),
        classification=Classification(
            category=None,
            confidence=0.9,
            status=OBSERVATION_CANDIDATE,
            signals=("冲突",),
            alternatives=("collaboration_feedback", "structure_revision"),
        ),
    )

    restored = AuthorObservation.from_content(observation.to_content())

    assert restored == observation
    assert restored.category is None
    assert set(restored.classification.alternatives) == set(observation.classification.alternatives)


def test_catalog_projects_and_locates_observations() -> None:
    catalog = build_catalog(
        [
            observation_entry(
                "art_obs1",
                commit_id="cmt_2",
                unit_id="art_u1",
                category=CATEGORY_STYLE,
                confidence=0.55,
                status=OBSERVATION_CANDIDATE,
            ),
            observation_entry(
                "art_obs2",
                commit_id="cmt_3",
                unit_id="art_u2",
                category=None,
                confidence=0.9,
                status=OBSERVATION_CANDIDATE,
                signals=("冲突",),
                alternatives=("fact_revision", "language_style"),
            ),
        ]
    )

    assert len(catalog.observations()) == 2
    found = catalog.observation_for("cmt_3", "art_u2")
    assert found is not None and found.observation_id == "art_obs2"
    assert catalog.observation_for("cmt_3", "art_missing") is None
    with pytest.raises(AuthorModelViolation) as exc:
        catalog.require("art_unknown", where="test")
    assert exc.value.code == ec.AUTHOR_MODEL_UNKNOWN_ARTIFACT


def test_observation_requires_edit_and_classification() -> None:
    with pytest.raises(AuthorModelViolation):
        AuthorObservation.from_content({"observation_id": "x", "commit_id": "c", "edit": None})
    with pytest.raises(AuthorModelViolation):
        AuthorObservation.from_content(
            {"observation_id": "x", "commit_id": "c", "edit": {}, "classification": []}
        )


def test_classification_from_content_rejects_unknown_status() -> None:
    with pytest.raises(AuthorModelViolation) as exc:
        Classification.from_content(
            {"category": CATEGORY_STYLE, "confidence": 0.5, "status": "halfling"},
            where="test",
        )
    assert exc.value.code == ec.AUTHOR_MODEL_INVALID_OBSERVATION


def test_all_categories_are_serializable() -> None:
    for category in AUTHOR_CATEGORIES:
        classification = Classification(
            category=category, confidence=0.7, status=OBSERVATION_CONFIRMED
        )
        assert (
            Classification.from_content(classification.to_content(), where="test") == classification
        )
