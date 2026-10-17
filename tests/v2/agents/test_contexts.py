from typing import Any

import pytest
from changes.test_branch_executor import _seed_chain

from hastings.application.contexts import ContextBuilder


def test_selection_is_replayable_scoped_and_budgeted(changes_env: Any):
    project, base = _seed_chain(changes_env)
    builder = ContextBuilder(changes_env.service)
    first = builder.build(
        "writer", project.id, base, ("art_b",), instruction="按节拍写一章", token_budget=6000
    )
    second = builder.build(
        "writer", project.id, base, ("art_b",), instruction="按节拍写一章", token_budget=6000
    )
    assert first.model_dump() == second.model_dump()
    assert first.estimated_tokens <= 6000
    assert {i.artifact_id for i in first.selected_inputs} >= {"art_a", "art_b"}
    assert all(i.reason and i.revision_id for i in first.selected_inputs)
    small = builder.build(
        "writer",
        project.id,
        base,
        ("art_b",),
        instruction="按节拍写一章",
        token_budget=first.estimated_tokens - 80,
    )
    assert small.omissions
    with pytest.raises(ValueError):
        builder.build("writer", project.id, base, ("art_b",), token_budget=10)


def test_repair_keeps_approval_and_failure_evidence(changes_env: Any):
    project, base = _seed_chain(changes_env)
    ctx = ContextBuilder(changes_env.service).build(
        "reviser",
        project.id,
        base,
        ("tu_x",),
        evidence={
            "approval": "receipt",
            "errors": [{"where": "scope", "why": "outside", "expected": "tu_x"}],
        },
    )
    assert ctx.evidence["approval"] == "receipt"
    assert ctx.evidence["errors"][0]["expected"] == "tu_x"
    assert "approval_grant" not in ctx.capability.allowed_tools


def test_context_resolves_chapter_preferences_and_active_examples(changes_env):
    from hastings.domain.author_model.models import AuthorExample, PreferenceHypothesis
    from hastings.domain.repository.ids import Actor, ArtifactId, CommitId
    from hastings.domain.repository.requests import ArtifactChange, CommitRequest

    env = changes_env
    project, base = _seed_chain(env)
    branch = env.service.get_branch(project.id, "main")
    unit = env.service.get_artifact(ArtifactId("tu_x"), CommitId(base))
    prefs = [
        PreferenceHypothesis("project_pref", "language_style", "项目偏好", status="active"),
        PreferenceHypothesis(
            "chapter_pref",
            "language_style",
            "本章偏好",
            scope_kind="chapter",
            scope_target="ch1",
            status="active",
        ),
        PreferenceHypothesis("candidate_pref", "format", "未确认", status="candidate"),
    ]
    example = AuthorExample(
        "example", "tu_x", unit.revision.id.value, base, "negative", note="不要过量修饰"
    )
    result = env.service.commit(
        CommitRequest(
            project.id,
            branch.id,
            branch.head_commit_id,
            Actor("human", "author"),
            "profiles",
            "作者偏好与反例",
            changes=tuple(
                ArtifactChange(
                    ArtifactId(p.preference_id), p.to_content(), kind="author_model.preference"
                )
                for p in prefs
            )
            + (
                ArtifactChange(
                    ArtifactId("example"), example.to_content(), kind="author_model.example"
                ),
            ),
        )
    )
    ctx = ContextBuilder(env.service).build("writer", project.id, result.commit_id.value, ("tu_x",))
    # 0.3（V03-0.4）：作者模型档案不再常驻注入任务背景（排除项）。
    assert ctx.author_preferences == []
    assert ctx.author_examples == []
    # 原始数据只读保留：查询路径不受切断影响。
    from hastings.application.queries.author_profile import AuthorProfileQueries

    profiles = AuthorProfileQueries(env.service)
    effective = profiles.effective_preferences(
        project.id, result.commit_id, scope_kind="chapter", scope_target="ch1"
    )
    assert [p.preference_id for p in effective] == ["chapter_pref"]
