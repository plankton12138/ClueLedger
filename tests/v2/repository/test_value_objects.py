"""T12.1 领域值对象测试：ID 校验规则与不可变性。"""

from __future__ import annotations

import pytest

from hastings.domain.repository.ids import (
    Actor,
    ArtifactId,
    BranchId,
    CommitId,
    ContentHash,
    JobId,
    ProjectId,
    RevisionId,
    Scope,
)

INVALID_ID_VALUES = [
    "",
    "   ",
    "a/b",
    "a\\b",
    "..",
    ".",
    "带 换行\n",
]


@pytest.mark.parametrize("value", INVALID_ID_VALUES)
@pytest.mark.parametrize(
    "cls",
    [ProjectId, ArtifactId, RevisionId, CommitId, BranchId, JobId],
)
def test_ids_reject_empty_and_path_like_values(cls: type, value: str) -> None:
    with pytest.raises(ValueError):
        cls(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "cls",
    [ProjectId, ArtifactId, RevisionId, CommitId, BranchId, JobId],
)
def test_ids_are_frozen_and_keep_raw_value(cls: type) -> None:
    obj = cls("abc_123")  # type: ignore[arg-type]
    assert obj.value == "abc_123"
    with pytest.raises(AttributeError):
        obj.value = "other"  # type: ignore[misc]


def test_ids_reject_non_string() -> None:
    with pytest.raises(TypeError):
        ProjectId(123)  # type: ignore[arg-type]


def test_content_hash_requires_lower_hex_sha256() -> None:
    good = "a" * 64
    assert ContentHash(good).value == good
    with pytest.raises(ValueError):
        ContentHash("ZZ" * 32)
    with pytest.raises(ValueError):
        ContentHash("a" * 63)


def test_actor_validates_kind() -> None:
    actor = Actor(kind="agent", id="a_1")
    assert actor.kind == "agent"
    with pytest.raises(ValueError):
        Actor(kind="robot", id="a_1")


def test_scope_default_is_unfiltered() -> None:
    scope = Scope()
    assert scope.artifact_kinds == ()
    assert scope.is_unfiltered()
    assert not Scope(artifact_kinds=("chapter",)).is_unfiltered()
