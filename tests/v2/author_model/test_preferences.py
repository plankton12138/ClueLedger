"""T14.6 偏好归纳与激活：显式声明、证据阈值、作用域优先级、冲突与撤销。

覆盖计划 §6 测试先行清单 11–15（多个证据形成 candidate/active；scope 优先级；
revoke 后查询排除；每条 active 可反查 evidence；项目偏好不会自动成为全局）。

两条设计约束在测试里显式出现：阈值与作用域是**可注入策略**（``InductionPolicy``），
不散落魔法数字；**撤销即封锁**——人工撤销过的 (类别, 作用域) 不再被自动归纳复活。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from hastings.application.handlers.author_model_commands import author_model_command
from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.queries.author_profile import AuthorProfileQueries
from hastings.domain.author_model import commands, kinds, rules
from hastings.domain.author_model import error_codes as ec
from hastings.domain.author_model.errors import AuthorModelViolation
from hastings.domain.author_model.models import (
    AUTHOR_CATEGORIES,
    CATEGORY_FACT,
    CATEGORY_STYLE,
    OBSERVATION_CANDIDATE,
    ORIGIN_EXPLICIT,
    ORIGIN_INDUCED,
    PREFERENCE_ACTIVE,
    PREFERENCE_CANDIDATE,
    PREFERENCE_REVOKED,
    SCOPE_CHAPTER,
    SCOPE_GLOBAL,
    SCOPE_PROJECT,
    AuthorModelChange,
    PreferenceHypothesis,
)
from hastings.domain.manuscript import commands as manuscript_commands
from hastings.domain.manuscript import operations as ops
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import CommitId, ProjectId, RevisionId
from hastings.domain.repository.requests import ArtifactChange

from .conftest import AuthorEnv, observation_entry, preference_entry

CHAPTER = "art_ch1"

MakeCatalog = Callable[..., Any]
NewId = Callable[[str], str]
Entry = tuple[str, str, dict[str, Any]]


# ---------------------------------------------------------------------------
# 夹具助手
# ---------------------------------------------------------------------------
def _pref(change: AuthorModelChange, index: int = 0) -> PreferenceHypothesis:
    """从命令结果中解码第 index 条偏好变化。"""
    return PreferenceHypothesis.from_content(change.artifact_changes[index].content)


def _seeded(
    make_catalog: MakeCatalog, entries: list[Entry], changes: tuple[ArtifactChange, ...]
) -> Any:
    """把上一轮命令产出的偏好变化并回目录，模拟「已落盘的既有状态」。"""
    created = [(c.artifact_id.value, kinds.KIND_PREFERENCE, c.content) for c in changes]
    return make_catalog([*entries, *created])


# 方向可判定的风格观察：默认「由长变短」（concise），可换成长变短（expand）
_STYLE_LONG = "他慢慢地、非常小心地走进那间空荡的房间。"
_STYLE_SHORT = "他走进房间。"


def _style_observations(
    *commits: str,
    start: int = 1,
    before: str = _STYLE_LONG,
    after: str = _STYLE_SHORT,
) -> list[Entry]:
    """一组语言风格观察：每 Commit 一条（类别已定 + 真实前后文本，供方向归纳）。

    风格规则置信度 0.55 → candidate，但仍高于 ``min_confidence``，因此它仍是
    **合格证据**；是否 active 由独立 Commit 数与方向（concise/expand）共同决定。
    """
    return [
        observation_entry(
            f"art_o{index}",
            commit_id=commit,
            unit_id=f"art_u{index}",
            category=CATEGORY_STYLE,
            confidence=0.55,
            status=OBSERVATION_CANDIDATE,
            before=before,
            after=after,
        )
        for index, commit in enumerate(commits, start=start)
    ]


def _pref_object(
    preference_id: str,
    *,
    scope_kind: str,
    scope_target: str = "",
    status: str = PREFERENCE_ACTIVE,
    category: str = CATEGORY_STYLE,
) -> PreferenceHypothesis:
    return PreferenceHypothesis(
        preference_id=preference_id,
        category=category,
        statement=f"{scope_kind} 上的偏好",
        scope_kind=scope_kind,
        scope_target=scope_target,
        status=status,
        origin=ORIGIN_EXPLICIT,
    )


# ---------------------------------------------------------------------------
# 人工声明与取代（supersede）
# ---------------------------------------------------------------------------
def test_declare_explicit_preference_is_active(make_catalog: MakeCatalog, next_id: NewId) -> None:
    change = commands.declare_preference(
        make_catalog([]), next_id, category=CATEGORY_STYLE, statement="偏好更精简的措辞"
    )

    assert len(change.artifact_changes) == 1
    created = change.artifact_changes[0]
    assert created.kind == kinds.KIND_PREFERENCE
    assert created.old_revision_id is None  # 新身份，不是修订
    preference = _pref(change)
    assert preference.status == PREFERENCE_ACTIVE and preference.origin == ORIGIN_EXPLICIT
    assert preference.identity() == (CATEGORY_STYLE, SCOPE_PROJECT, "")
    assert preference.evidence == ()


def test_declare_same_statement_is_legal_noop(make_catalog: MakeCatalog, next_id: NewId) -> None:
    catalog = make_catalog(
        [preference_entry("art_p1", category=CATEGORY_STYLE, statement="偏好更精简的措辞")]
    )

    change = commands.declare_preference(
        catalog, next_id, category=CATEGORY_STYLE, statement="偏好更精简的措辞"
    )

    assert change.is_empty()
    assert "合法空结果" in change.summary


def test_declare_conflicting_statement_supersedes_old(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    catalog = make_catalog(
        [preference_entry("art_p1", category=CATEGORY_STYLE, statement="旧陈述")]
    )

    change = commands.declare_preference(
        catalog, next_id, category=CATEGORY_STYLE, statement="新陈述"
    )

    assert len(change.artifact_changes) == 2
    superseded = PreferenceHypothesis.from_content(change.artifact_changes[0].content)
    assert superseded.preference_id == "art_p1" and superseded.is_revoked
    assert superseded.reason  # 撤销理由可解释，不静默消失
    assert change.artifact_changes[0].old_revision_id == RevisionId("rev_art_p1")
    replacement = _pref(change, 1)
    assert replacement.statement == "新陈述" and replacement.is_active
    assert superseded.superseded_by == replacement.preference_id  # 历史链路可追溯


def test_declare_rejects_invalid_category_scope_and_statement(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    with pytest.raises(AuthorModelViolation) as exc:
        commands.declare_preference(make_catalog([]), next_id, category="nonsense", statement="x")
    assert exc.value.code == ec.AUTHOR_MODEL_INVALID_PREFERENCE
    assert exc.value.where and exc.value.why and exc.value.fix  # 三要素齐全

    with pytest.raises(AuthorModelViolation):
        commands.declare_preference(
            make_catalog([]), next_id, category=CATEGORY_STYLE, statement="x", scope_kind="galaxy"
        )
    with pytest.raises(AuthorModelViolation) as blank:
        commands.declare_preference(
            make_catalog([]), next_id, category=CATEGORY_STYLE, statement="   "
        )
    assert blank.value.code == ec.AUTHOR_MODEL_MISSING_REQUIRED_FIELD


# ---------------------------------------------------------------------------
# 撤销：状态变更而非删除，且幂等
# ---------------------------------------------------------------------------
def test_revoke_marks_revoked_and_keeps_history(make_catalog: MakeCatalog, next_id: NewId) -> None:
    catalog = make_catalog(
        [preference_entry("art_p1", category=CATEGORY_STYLE, statement="偏好更精简的措辞")]
    )

    change = commands.revoke_preference(catalog, next_id, preference_id="art_p1", reason="误判")

    assert len(change.artifact_changes) == 1
    revoked = _pref(change)
    assert revoked.status == PREFERENCE_REVOKED and revoked.reason == "误判"
    assert revoked.preference_id == "art_p1"  # 撤销不改身份
    assert change.artifact_changes[0].old_revision_id == RevisionId("rev_art_p1")


def test_revoke_is_idempotent_noop(make_catalog: MakeCatalog, next_id: NewId) -> None:
    catalog = make_catalog(
        [
            preference_entry(
                "art_p1",
                category=CATEGORY_STYLE,
                statement="偏好更精简的措辞",
                status=PREFERENCE_REVOKED,
            )
        ]
    )

    assert commands.revoke_preference(catalog, next_id, preference_id="art_p1").is_empty()


def test_revoke_unknown_preference_is_violation(make_catalog: MakeCatalog, next_id: NewId) -> None:
    with pytest.raises(AuthorModelViolation) as exc:
        commands.revoke_preference(make_catalog([]), next_id, preference_id="art_missing")
    assert exc.value.code == ec.AUTHOR_MODEL_UNKNOWN_ARTIFACT
    assert exc.value.where and exc.value.why and exc.value.fix


# ---------------------------------------------------------------------------
# 证据与归纳（计划 §6 清单 11）
# ---------------------------------------------------------------------------
def test_evidence_counts_distinct_commits(make_catalog: MakeCatalog) -> None:
    """同一 Commit 的同类编辑只算一次证据，避免伪造「多次独立证据」。"""
    catalog = make_catalog(
        [
            observation_entry(
                "art_o1",
                commit_id="cmt_1",
                unit_id="art_u1",
                category=CATEGORY_STYLE,
                confidence=0.55,
                status=OBSERVATION_CANDIDATE,
            ),
            observation_entry(
                "art_o2",
                commit_id="cmt_1",
                unit_id="art_u2",
                category=CATEGORY_STYLE,
                confidence=0.55,
                status=OBSERVATION_CANDIDATE,
            ),
        ]
    )

    assert rules.evidence_commits(catalog.observations()) == {CATEGORY_STYLE: ("cmt_1",)}


def test_conflicting_observation_is_not_evidence(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            observation_entry(
                "art_o1",
                commit_id="cmt_1",
                unit_id="art_u1",
                category=None,
                confidence=0.9,
                status=OBSERVATION_CANDIDATE,
                alternatives=("format", "structure_revision"),
            )
        ]
    )

    assert rules.evidence_commits(catalog.observations()) == {}


def test_induce_below_threshold_stays_candidate(make_catalog: MakeCatalog, next_id: NewId) -> None:
    change = commands.induce_preferences(make_catalog(_style_observations("cmt_1")), next_id)

    preference = _pref(change)
    assert preference.status == PREFERENCE_CANDIDATE
    assert preference.origin == ORIGIN_INDUCED
    assert preference.evidence == ("cmt_1",)
    assert preference.statement == rules.statement_for(
        CATEGORY_STYLE, direction=rules.DIRECTION_CONCISE
    )


def test_induce_activates_at_threshold(make_catalog: MakeCatalog, next_id: NewId) -> None:
    change = commands.induce_preferences(
        make_catalog(_style_observations("cmt_1", "cmt_2")), next_id
    )

    preference = _pref(change)
    assert preference.status == PREFERENCE_ACTIVE
    assert preference.evidence == ("cmt_1", "cmt_2")


def test_low_confidence_candidates_are_not_activated(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    """评审 #1：置信度极低的候选观察不得被当成证据激活偏好。

    复现场景：两个独立 Commit 的 candidate 置信度均为 0.01——若只看类别，会
    误得 active 偏好；把证据可信度纳入激活策略后，它们达不到合格线，不参与归纳。
    """
    entries = [
        observation_entry(
            "art_o1",
            commit_id="cmt_1",
            unit_id="art_u1",
            category=CATEGORY_STYLE,
            confidence=0.01,
            status=OBSERVATION_CANDIDATE,
            before=_STYLE_LONG,
            after=_STYLE_SHORT,
        ),
        observation_entry(
            "art_o2",
            commit_id="cmt_2",
            unit_id="art_u2",
            category=CATEGORY_STYLE,
            confidence=0.01,
            status=OBSERVATION_CANDIDATE,
            before=_STYLE_LONG,
            after=_STYLE_SHORT,
        ),
    ]

    change = commands.induce_preferences(make_catalog(entries), next_id)

    assert change.is_empty()  # 低可信证据不产生任何偏好（更不会 active）


def test_min_confidence_is_injectable_policy(make_catalog: MakeCatalog, next_id: NewId) -> None:
    """证据可信度是注入策略：调高门槛后，同样证据不再合格。"""
    strict = rules.InductionPolicy(evidence_threshold=3, min_confidence=0.9)

    change = commands.induce_preferences(
        make_catalog(_style_observations("cmt_1", "cmt_2")), next_id, policy=strict
    )

    assert change.is_empty()


def test_induce_style_direction_from_concise_edits(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    """由长变短的持续编辑 → 归纳出「更精简」方向。"""
    change = commands.induce_preferences(
        make_catalog(_style_observations("cmt_1", "cmt_2")), next_id
    )

    preference = _pref(change)
    assert preference.status == PREFERENCE_ACTIVE
    assert preference.statement == rules.statement_for(
        CATEGORY_STYLE, direction=rules.DIRECTION_CONCISE
    )


def test_induce_style_direction_from_expanding_edits(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    """评审 #2：作者持续扩写，归纳必须得「详尽」，绝不能被判成「精简」。"""
    entries = _style_observations("cmt_1", "cmt_2", before=_STYLE_SHORT, after=_STYLE_LONG)

    change = commands.induce_preferences(make_catalog(entries), next_id)

    preference = _pref(change)
    assert preference.status == PREFERENCE_ACTIVE
    assert preference.statement == rules.statement_for(
        CATEGORY_STYLE, direction=rules.DIRECTION_EXPAND
    )
    assert "精简" not in preference.statement


def test_induce_style_opposing_evidence_stays_candidate(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    """相反证据并存 → 不强行断言方向，保持 candidate 并告警（四透镜③④）。"""
    entries = _style_observations("cmt_1") + _style_observations(
        "cmt_2", start=2, before=_STYLE_SHORT, after=_STYLE_LONG
    )

    change = commands.induce_preferences(make_catalog(entries), next_id)

    preference = _pref(change)
    assert preference.status == PREFERENCE_CANDIDATE
    assert change.warnings  # 方向冲突可见，不静默


def test_induce_threshold_is_injectable_policy(make_catalog: MakeCatalog, next_id: NewId) -> None:
    strict = rules.InductionPolicy(evidence_threshold=3)

    change = commands.induce_preferences(
        make_catalog(_style_observations("cmt_1", "cmt_2")), next_id, policy=strict
    )

    assert _pref(change).status == PREFERENCE_CANDIDATE


def test_induce_appends_new_evidence_to_existing_preference(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    first_entries = _style_observations("cmt_1")
    first = commands.induce_preferences(make_catalog(first_entries), next_id)
    assert _pref(first).status == PREFERENCE_CANDIDATE

    entries = [*first_entries, *_style_observations("cmt_2")]
    seeded = _seeded(make_catalog, entries, first.artifact_changes)

    change = commands.induce_preferences(seeded, next_id)

    updated = _pref(change)
    assert updated.status == PREFERENCE_ACTIVE  # 新证据把 candidate 推过阈值
    assert updated.evidence == ("cmt_1", "cmt_2")
    assert change.artifact_changes[0].old_revision_id is not None  # 修订既有身份


def test_induce_rerun_is_legal_noop(make_catalog: MakeCatalog, next_id: NewId) -> None:
    """证据只增不减：重跑归纳不制造新版本（幂等）。"""
    entries = _style_observations("cmt_1", "cmt_2")
    first = commands.induce_preferences(make_catalog(entries), next_id)
    seeded = _seeded(make_catalog, entries, first.artifact_changes)

    second = commands.induce_preferences(seeded, next_id)

    assert second.is_empty()
    assert "合法空结果" in second.summary
    assert second.warnings == ()


def test_induce_does_not_override_explicit_declaration(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    """人工声明优先：同身份的显式偏好存在时，归纳只告警、不改写。"""
    entries: list[Entry] = [
        *_style_observations("cmt_1", "cmt_2"),
        preference_entry("art_p1", category=CATEGORY_STYLE, statement="我自己的说法"),
    ]

    change = commands.induce_preferences(make_catalog(entries), next_id)

    assert change.is_empty()
    assert change.warnings and "人工声明" in change.warnings[0]
    assert change.artifact_changes == ()


def test_induce_does_not_revive_revoked_preference(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    """撤销即封锁：同身份存在 revoked 记录时，归纳不自动复活它。"""
    entries: list[Entry] = [
        *_style_observations("cmt_1", "cmt_2"),
        preference_entry(
            "art_p1",
            category=CATEGORY_STYLE,
            statement="已撤销的偏好",
            status=PREFERENCE_REVOKED,
            origin=ORIGIN_INDUCED,
            reason="误判",
        ),
    ]

    change = commands.induce_preferences(make_catalog(entries), next_id)

    assert change.is_empty()


def test_induce_handles_multiple_categories(make_catalog: MakeCatalog, next_id: NewId) -> None:
    entries: list[Entry] = [
        *_style_observations("cmt_1", "cmt_2"),
        observation_entry(
            "art_o9",
            commit_id="cmt_3",
            unit_id="art_u9",
            category=CATEGORY_FACT,
            confidence=0.75,
        ),
    ]

    change = commands.induce_preferences(make_catalog(entries), next_id)

    statuses = {
        PreferenceHypothesis.from_content(item.content).category: PreferenceHypothesis.from_content(
            item.content
        ).status
        for item in change.artifact_changes
    }
    assert statuses == {CATEGORY_STYLE: PREFERENCE_ACTIVE, CATEGORY_FACT: PREFERENCE_CANDIDATE}


def test_every_category_has_statement_template() -> None:
    """策略与类别集合必须同步：新增类别却漏了陈述模板，是契约缺口。"""
    assert set(rules.CATEGORY_STATEMENTS) == set(AUTHOR_CATEGORIES)
    for category in AUTHOR_CATEGORIES:
        assert rules.statement_for(category).strip()


# ---------------------------------------------------------------------------
# 作用域优先级（计划 §6 清单 12/15）
# ---------------------------------------------------------------------------
def test_resolve_preferences_prefers_most_specific_scope() -> None:
    project_preference = _pref_object("art_p1", scope_kind=SCOPE_PROJECT)
    chapter_preference = _pref_object("art_p2", scope_kind=SCOPE_CHAPTER, scope_target=CHAPTER)

    at_chapter = rules.resolve_preferences(
        (project_preference, chapter_preference), scope_kind=SCOPE_CHAPTER, scope_target=CHAPTER
    )
    at_project = rules.resolve_preferences(
        (project_preference, chapter_preference), scope_kind=SCOPE_PROJECT
    )

    assert at_chapter == (chapter_preference,)  # 具体作用域覆盖宽作用域
    assert at_project == (project_preference,)  # 反向不越界


def test_resolve_preferences_excludes_candidates_and_revoked() -> None:
    candidate = _pref_object("art_p1", scope_kind=SCOPE_PROJECT, status=PREFERENCE_CANDIDATE)
    revoked = _pref_object("art_p2", scope_kind=SCOPE_PROJECT, status=PREFERENCE_REVOKED)

    assert rules.resolve_preferences((candidate, revoked), scope_kind=SCOPE_PROJECT) == ()


def test_project_preference_does_not_apply_to_global_scope() -> None:
    """项目偏好不会自动成为全局（计划 §3 非目标 + 清单 15）。"""
    project_preference = _pref_object("art_p1", scope_kind=SCOPE_PROJECT)

    assert rules.resolve_preferences((project_preference,), scope_kind=SCOPE_GLOBAL) == ()
    assert rules.resolve_preferences((project_preference,), scope_kind=SCOPE_PROJECT) == (
        project_preference,
    )


def test_chapter_preference_does_not_leak_into_other_chapter() -> None:
    chapter_preference = _pref_object("art_p1", scope_kind=SCOPE_CHAPTER, scope_target=CHAPTER)

    assert (
        rules.resolve_preferences(
            (chapter_preference,), scope_kind=SCOPE_CHAPTER, scope_target="art_ch9"
        )
        == ()
    )


def test_resolve_preferences_keeps_one_per_category() -> None:
    style = _pref_object("art_p1", scope_kind=SCOPE_PROJECT)
    fact = _pref_object("art_p2", scope_kind=SCOPE_PROJECT, category=CATEGORY_FACT)

    # 每条类别各解析出一条；返回顺序按类别稳定排序（fact_revision < language_style）
    assert rules.resolve_preferences((style, fact), scope_kind=SCOPE_PROJECT) == (fact, style)


def test_resolve_preferences_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError) as exc:
        rules.resolve_preferences((), scope_kind="galaxy")
    assert "未知作用域" in str(exc.value)


# ---------------------------------------------------------------------------
# 应用层：查询排除撤销、证据可反查、学习流程自动归纳
# ---------------------------------------------------------------------------
def _declare(env: AuthorEnv, project_id: ProjectId, *, statement: str, key: str) -> Any:
    return env.author_handler.execute(
        author_model_command(
            commands.declare_preference, category=CATEGORY_STYLE, statement=statement
        ),
        project_id=project_id,
        idempotency_key=key,
        summary="声明偏好",
    )


def _import_chapter(env: AuthorEnv, project_id: ProjectId) -> Any:
    return env.manuscript_handler.execute(
        manuscript_command(
            manuscript_commands.import_chapter,
            chapter_id=CHAPTER,
            title="第一章",
            text="第一段\n\n第二段\n\n第三段",
        ),
        project_id=project_id,
        idempotency_key="import",
        summary="导入",
    )


def _rewrite(env: AuthorEnv, project_id: ProjectId, text: str, *, key: str) -> Any:
    """把第二段改写为给定文本（风格类编辑），返回新的正文 Commit 结果。"""
    branch = env.service.get_branch(project_id, "main")
    assert branch is not None and branch.head_commit_id is not None
    catalog = ManuscriptCatalog.from_snapshot(
        env.service.get_snapshot(project_id, branch.head_commit_id)
    )
    units = [entry.artifact_id for entry in catalog.units_of(CHAPTER)]
    return env.manuscript_handler.execute(
        manuscript_command(
            manuscript_commands.apply_edits,
            chapter_id=CHAPTER,
            operations=(ops.Replace(unit_id=units[1], text=text),),
        ),
        project_id=project_id,
        idempotency_key=key,
        summary="改正文",
    )


@pytest.mark.anyio
async def test_revoked_preference_excluded_from_context_query(author_env: AuthorEnv) -> None:
    """计划 §6 清单 13：撤销后查询不再返回该偏好，但状态仍可查（历史不删除）。"""
    project = author_env.author_handler.create_project("冬馆")
    declared = _declare(author_env, project.id, statement="偏好更精简的措辞", key="declare-1")
    queries = AuthorProfileQueries(author_env.service)
    head = CommitId(declared.new_head)
    preference_id = declared.change.artifact_changes[0].artifact_id.value

    assert [p.preference_id for p in queries.effective_preferences(project.id, head)] == [
        preference_id
    ]

    revoked = author_env.author_handler.execute(
        author_model_command(
            commands.revoke_preference, preference_id=preference_id, reason="误判"
        ),
        project_id=project.id,
        idempotency_key="revoke-1",
        summary="撤销偏好",
    )

    after = CommitId(revoked.new_head)
    assert queries.effective_preferences(project.id, after) == ()
    assert [p.status for p in queries.preferences(project.id, after)] == [PREFERENCE_REVOKED]


@pytest.mark.anyio
async def test_project_preference_is_not_promoted_to_global(author_env: AuthorEnv) -> None:
    project = author_env.author_handler.create_project("冬馆")
    declared = _declare(author_env, project.id, statement="偏好更精简的措辞", key="declare-1")
    queries = AuthorProfileQueries(author_env.service)
    head = CommitId(declared.new_head)

    assert queries.effective_preferences(project.id, head, scope_kind=SCOPE_PROJECT) != ()
    assert queries.effective_preferences(project.id, head, scope_kind=SCOPE_GLOBAL) == ()


@pytest.mark.anyio
async def test_active_preference_is_traceable_to_evidence(author_env: AuthorEnv) -> None:
    """计划 §6 清单 14 + 验收场景 3–6：两次独立 Commit 的证据 → active 且可反查。"""
    project = author_env.author_handler.create_project("冬馆")
    imported = _import_chapter(author_env, project.id)

    first = _rewrite(author_env, project.id, "他慢慢地走进房间。", key="save-1")
    learned_first = await author_env.learning.observe(
        project_id=project.id,
        commit_id=CommitId(first.new_head),
        base_commit_id=CommitId(imported.new_head),
        chapter_id=CHAPTER,
        idempotency_key="analysis-1",
    )
    second = _rewrite(author_env, project.id, "他缓缓地走进了房间。", key="save-2")
    learned = await author_env.learning.observe(
        project_id=project.id,
        commit_id=CommitId(second.new_head),
        base_commit_id=CommitId(first.new_head),
        chapter_id=CHAPTER,
        idempotency_key="analysis-2",
    )

    assert learned_first.preference_changes == 1  # 首次证据 → candidate
    assert learned.commit_id is not None
    queries = AuthorProfileQueries(author_env.service)
    head = CommitId(learned.commit_id)
    preferences = queries.effective_preferences(project.id, head)

    assert learned.is_committed and learned.preference_changes == 1
    assert len(preferences) == 1
    preference = preferences[0]
    assert preference.category == CATEGORY_STYLE
    assert preference.status == PREFERENCE_ACTIVE
    assert preference.scope_kind == SCOPE_PROJECT
    assert preference.evidence == (first.new_head, second.new_head)

    evidence = queries.preference_evidence(project.id, head, preference.preference_id)
    assert len(evidence) == 2
    assert {observation.commit_id for observation in evidence} == {first.new_head, second.new_head}
    assert all(observation.edit.after for observation in evidence)  # 证据可读，不只是 ID


@pytest.mark.anyio
async def test_explicit_declaration_blocks_auto_induction(author_env: AuthorEnv) -> None:
    project = author_env.author_handler.create_project("冬馆")
    declared = _declare(author_env, project.id, statement="我自己的说法", key="declare-1")
    imported = _import_chapter(author_env, project.id)
    edited = _rewrite(author_env, project.id, "他慢慢地走进房间。", key="save-1")

    learned = await author_env.learning.observe(
        project_id=project.id,
        commit_id=CommitId(edited.new_head),
        base_commit_id=CommitId(imported.new_head),
        chapter_id=CHAPTER,
        idempotency_key="analysis-1",
    )

    assert learned.warnings and "人工声明" in learned.warnings[0]
    queries = AuthorProfileQueries(author_env.service)
    head = CommitId(declared.new_head)
    stored = queries.preferences(project.id, head)
    assert [p.statement for p in stored] == ["我自己的说法"]  # 归纳未改写人工声明
    assert stored[0].origin == ORIGIN_EXPLICIT and stored[0].evidence == ()
