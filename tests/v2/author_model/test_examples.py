"""T14.7 作者样例：正例/反例引用与删除语义。

覆盖计划 §T14.7：
- 作者把某个 TextUnit/Revision 标记为正例或反例；
- 只保存用户自有/授权文本**引用**（不复制正文）；
- 删除后新 Context（``effective_examples``）不再返回，但历史记录保留当时引用的 ID。

两条设计约束在测试里显式出现：软删除保留引用 ID（历史生成记录引用的 example_id
需始终可解析）；删除语义与偏好撤销**不同**——撤销即封锁，但同一段落的同一版本
可被重新标注（作者重下断言是自然行为，不构成矛盾）。
"""

from __future__ import annotations

import json
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
    EXAMPLE_ACTIVE,
    EXAMPLE_REMOVED,
    POLARITY_NEGATIVE,
    POLARITY_POSITIVE,
    AuthorExample,
)
from hastings.domain.manuscript import commands as manuscript_commands
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import CommitId, RevisionId

from .conftest import AuthorEnv, example_entry

CHAPTER = "art_ch1"
TEXT = "第一段\n\n第二段\n\n第三段"

MakeCatalog = Callable[..., Any]
NewId = Callable[[str], str]


# ---------------------------------------------------------------------------
# 领域层：标记、取代、软删除
# ---------------------------------------------------------------------------
def test_mark_positive_example_stores_reference_only(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    change = commands.mark_example(
        make_catalog([]),
        next_id,
        unit_id="art_u1",
        revision_id="rev_u1",
        commit_id="cmt_1",
        polarity=POLARITY_POSITIVE,
        note="这段节奏好",
    )

    assert len(change.artifact_changes) == 1
    created = change.artifact_changes[0]
    assert created.kind == kinds.KIND_EXAMPLE
    assert created.old_revision_id is None  # 新身份，不是修订
    example = AuthorExample.from_content(created.content)
    assert example.polarity == POLARITY_POSITIVE and example.status == EXAMPLE_ACTIVE
    assert (example.unit_id, example.revision_id, example.commit_id) == (
        "art_u1",
        "rev_u1",
        "cmt_1",
    )
    assert example.note == "这段节奏好"


def test_mark_negative_example(make_catalog: MakeCatalog, next_id: NewId) -> None:
    change = commands.mark_example(
        make_catalog([]),
        next_id,
        unit_id="art_u1",
        revision_id="rev_u1",
        commit_id="cmt_1",
        polarity=POLARITY_NEGATIVE,
    )

    assert AuthorExample.from_content(change.artifact_changes[0].content).polarity == (
        POLARITY_NEGATIVE
    )


def test_mark_rejects_missing_reference_and_invalid_polarity(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    with pytest.raises(AuthorModelViolation) as missing:
        commands.mark_example(
            make_catalog([]),
            next_id,
            unit_id="",
            revision_id="rev_u1",
            commit_id="cmt_1",
            polarity=POLARITY_POSITIVE,
        )
    assert missing.value.code == ec.AUTHOR_MODEL_MISSING_REQUIRED_FIELD
    assert missing.value.where and missing.value.why and missing.value.fix  # 三要素齐全

    with pytest.raises(AuthorModelViolation) as bad_polarity:
        commands.mark_example(
            make_catalog([]),
            next_id,
            unit_id="art_u1",
            revision_id="rev_u1",
            commit_id="cmt_1",
            polarity="meh",
        )
    assert bad_polarity.value.code == ec.AUTHOR_MODEL_INVALID_EXAMPLE

    with pytest.raises(AuthorModelViolation):
        commands.mark_example(
            make_catalog([]),
            next_id,
            unit_id="art_u1",
            revision_id="",
            commit_id="cmt_1",
            polarity=POLARITY_POSITIVE,
        )


def test_mark_same_reference_same_polarity_is_noop(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    catalog = make_catalog(
        [
            example_entry(
                "art_e1",
                unit_id="art_u1",
                revision_id="rev_u1",
                commit_id="cmt_1",
                polarity=POLARITY_POSITIVE,
                note="节奏好",
            )
        ]
    )

    change = commands.mark_example(
        catalog,
        next_id,
        unit_id="art_u1",
        revision_id="rev_u1",
        commit_id="cmt_1",
        polarity=POLARITY_POSITIVE,
        note="节奏好",
    )

    assert change.is_empty()
    assert "合法空结果" in change.summary


def test_mark_conflicting_polarity_supersedes_old(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    catalog = make_catalog(
        [
            example_entry(
                "art_e1",
                unit_id="art_u1",
                revision_id="rev_u1",
                commit_id="cmt_1",
                polarity=POLARITY_POSITIVE,
            )
        ]
    )

    change = commands.mark_example(
        catalog,
        next_id,
        unit_id="art_u1",
        revision_id="rev_u1",
        commit_id="cmt_1",
        polarity=POLARITY_NEGATIVE,
    )

    assert len(change.artifact_changes) == 2
    superseded = AuthorExample.from_content(change.artifact_changes[0].content)
    assert superseded.example_id == "art_e1" and superseded.is_removed
    assert superseded.reason  # 取代理由可解释，不静默消失
    assert change.artifact_changes[0].old_revision_id == RevisionId("rev_art_e1")
    replacement = AuthorExample.from_content(change.artifact_changes[1].content)
    assert replacement.polarity == POLARITY_NEGATIVE and replacement.is_active
    assert superseded.superseded_by == replacement.example_id  # 历史链路可追溯


def test_different_revision_is_distinct_reference(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    """同一段落的不同版本是两个独立引用：标后者不取代前者。"""
    catalog = make_catalog(
        [
            example_entry(
                "art_e1",
                unit_id="art_u1",
                revision_id="rev_u1",
                commit_id="cmt_1",
                polarity=POLARITY_POSITIVE,
            )
        ]
    )

    change = commands.mark_example(
        catalog,
        next_id,
        unit_id="art_u1",
        revision_id="rev_u2",
        commit_id="cmt_2",
        polarity=POLARITY_POSITIVE,
    )

    assert len(change.artifact_changes) == 1
    assert change.artifact_changes[0].old_revision_id is None


# ---------------------------------------------------------------------------
# 软删除：只改状态，保留引用 ID 供历史记录解析
# ---------------------------------------------------------------------------
def test_remove_marks_removed_and_keeps_reference(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    catalog = make_catalog(
        [
            example_entry(
                "art_e1",
                unit_id="art_u1",
                revision_id="rev_u1",
                commit_id="cmt_1",
                polarity=POLARITY_POSITIVE,
            )
        ]
    )

    change = commands.remove_example(catalog, next_id, example_id="art_e1", reason="误标")

    assert len(change.artifact_changes) == 1
    removed = AuthorExample.from_content(change.artifact_changes[0].content)
    assert removed.status == EXAMPLE_REMOVED and removed.reason == "误标"
    assert removed.example_id == "art_e1"  # 删除不改身份，ID 仍可解析
    assert (removed.unit_id, removed.revision_id, removed.commit_id) == (
        "art_u1",
        "rev_u1",
        "cmt_1",
    )
    assert change.artifact_changes[0].old_revision_id == RevisionId("rev_art_e1")


def test_remove_is_idempotent_noop(make_catalog: MakeCatalog, next_id: NewId) -> None:
    catalog = make_catalog(
        [
            example_entry(
                "art_e1",
                unit_id="art_u1",
                revision_id="rev_u1",
                commit_id="cmt_1",
                polarity=POLARITY_POSITIVE,
                status=EXAMPLE_REMOVED,
            )
        ]
    )

    assert commands.remove_example(catalog, next_id, example_id="art_e1").is_empty()


def test_remove_unknown_example_is_violation(make_catalog: MakeCatalog, next_id: NewId) -> None:
    with pytest.raises(AuthorModelViolation) as exc:
        commands.remove_example(make_catalog([]), next_id, example_id="art_missing")
    assert exc.value.code == ec.AUTHOR_MODEL_UNKNOWN_ARTIFACT
    assert exc.value.where and exc.value.why and exc.value.fix


def test_remarked_reference_creates_new_active_example(
    make_catalog: MakeCatalog, next_id: NewId
) -> None:
    """重新标注已删除的引用是合法新例：删除不封锁（与偏好撤销即封锁不同）。"""
    catalog = make_catalog(
        [
            example_entry(
                "art_e1",
                unit_id="art_u1",
                revision_id="rev_u1",
                commit_id="cmt_1",
                polarity=POLARITY_POSITIVE,
                status=EXAMPLE_REMOVED,
            )
        ]
    )

    change = commands.mark_example(
        catalog,
        next_id,
        unit_id="art_u1",
        revision_id="rev_u1",
        commit_id="cmt_1",
        polarity=POLARITY_POSITIVE,
    )

    assert len(change.artifact_changes) == 1  # 新建，不修订已删除的旧例
    assert change.artifact_changes[0].old_revision_id is None
    assert AuthorExample.from_content(change.artifact_changes[0].content).is_active


# ---------------------------------------------------------------------------
# 生效样例（生成上下文视角）
# ---------------------------------------------------------------------------
def test_effective_examples_excludes_removed() -> None:
    active = AuthorExample(
        example_id="art_e1",
        unit_id="u1",
        revision_id="r1",
        commit_id="c1",
        polarity=POLARITY_POSITIVE,
    )
    removed = AuthorExample(
        example_id="art_e2",
        unit_id="u2",
        revision_id="r2",
        commit_id="c2",
        polarity=POLARITY_NEGATIVE,
        status=EXAMPLE_REMOVED,
    )

    assert rules.effective_examples((active, removed)) == (active,)


def test_effective_examples_reads_catalog_by_kind(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            example_entry(
                "art_e1",
                unit_id="u1",
                revision_id="r1",
                commit_id="c1",
                polarity=POLARITY_POSITIVE,
            ),
            example_entry(
                "art_e2",
                unit_id="u2",
                revision_id="r2",
                commit_id="c2",
                polarity=POLARITY_NEGATIVE,
                status=EXAMPLE_REMOVED,
            ),
        ]
    )

    assert rules.effective_examples(catalog.examples()) == (catalog.examples()[0],)


# ---------------------------------------------------------------------------
# 应用层：删除后新上下文排除、历史引用保留、正文不被复制
# ---------------------------------------------------------------------------
def _import_chapter(env: AuthorEnv, project_id: Any) -> Any:
    return env.manuscript_handler.execute(
        manuscript_command(
            manuscript_commands.import_chapter,
            chapter_id=CHAPTER,
            title="第一章",
            text=TEXT,
        ),
        project_id=project_id,
        idempotency_key="import",
        summary="导入",
    )


def _mark(
    env: AuthorEnv,
    project_id: Any,
    *,
    unit_id: str,
    revision_id: str,
    commit_id: str,
    polarity: str,
    note: str = "",
    key: str,
) -> Any:
    return env.author_handler.execute(
        author_model_command(
            commands.mark_example,
            unit_id=unit_id,
            revision_id=revision_id,
            commit_id=commit_id,
            polarity=polarity,
            note=note,
        ),
        project_id=project_id,
        idempotency_key=key,
        summary="标记样例",
    )


@pytest.mark.anyio
async def test_removed_example_excluded_from_context_but_history_keeps_reference(
    author_env: AuthorEnv,
) -> None:
    project = author_env.author_handler.create_project("冬馆")
    imported = _import_chapter(author_env, project.id)
    catalog = ManuscriptCatalog.from_snapshot(
        author_env.service.get_snapshot(project.id, CommitId(imported.new_head))
    )
    unit = catalog.units_of(CHAPTER)[1]

    marked = _mark(
        author_env,
        project.id,
        unit_id=unit.artifact_id,
        revision_id=unit.revision_id,
        commit_id=imported.new_head,
        polarity=POLARITY_POSITIVE,
        key="mark-1",
    )
    queries = AuthorProfileQueries(author_env.service)
    example_id = marked.change.artifact_changes[0].artifact_id.value

    after_mark = CommitId(marked.new_head)
    active_ids = [e.example_id for e in queries.effective_examples(project.id, after_mark)]
    assert active_ids == [example_id]

    removed = author_env.author_handler.execute(
        author_model_command(commands.remove_example, example_id=example_id, reason="误标"),
        project_id=project.id,
        idempotency_key="remove-1",
        summary="删除样例",
    )
    after_remove = CommitId(removed.new_head)

    assert queries.effective_examples(project.id, after_remove) == ()
    history = queries.examples(project.id, after_remove)
    assert [e.example_id for e in history] == [example_id]  # 历史记录保留当时引用的 ID
    assert history[0].status == EXAMPLE_REMOVED
    assert (history[0].unit_id, history[0].revision_id) == (unit.artifact_id, unit.revision_id)


@pytest.mark.anyio
async def test_example_stores_reference_not_manuscript_text(author_env: AuthorEnv) -> None:
    """只存引用：正文受版权/授权约束，结构上不允许把正文复制进作者模型域。"""
    project = author_env.author_handler.create_project("冬馆")
    imported = _import_chapter(author_env, project.id)
    catalog = ManuscriptCatalog.from_snapshot(
        author_env.service.get_snapshot(project.id, CommitId(imported.new_head))
    )
    unit = catalog.units_of(CHAPTER)[1]
    paragraph_text = unit.model().text

    marked = _mark(
        author_env,
        project.id,
        unit_id=unit.artifact_id,
        revision_id=unit.revision_id,
        commit_id=imported.new_head,
        polarity=POLARITY_NEGATIVE,
        key="mark-1",
    )
    content = marked.change.artifact_changes[0].content

    assert paragraph_text not in json.dumps(content, ensure_ascii=False)
    example = AuthorExample.from_content(content)
    assert example.unit_id == unit.artifact_id  # 只有引用，没有正文副本
    assert example.polarity == POLARITY_NEGATIVE
