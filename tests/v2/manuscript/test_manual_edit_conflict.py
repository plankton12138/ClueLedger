"""T14.3 乐观并发冲突：基于同一基础的两次保存，后提交者得 MANUSCRIPT_BASE_CONFLICT。

覆盖计划 §6 测试先行清单 6（并发保存冲突）。冲突信号携带**三方比较所需数据**：
base（客户端读到的旧基础）、ours（客户端提案，客户端自持）、theirs（当前服务端
版本：当前基础、章 Revision、整章文本、活动段落 ID）。服务端不自动 force overwrite。

另验证基础是**章节范围**的：其他章的改动不影响本章基础，本章任何文本/结构改动
都会移动基础（纯文本替换不移动章 Artifact 自身 Revision，故基础按章内活动段落的
``(unit_id, revision_id)`` 有序对计算）。
"""

from __future__ import annotations

import pytest

from hastings.application.errors import ResultCategory, ViolationError
from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.workflows.manual_edit import ManualEditIntent
from hastings.domain.manuscript import commands, operations
from hastings.domain.manuscript import error_codes as ec

from .conftest import ManualEditEnv

CHAPTER = "art_ch1"
OTHER = "art_ch2"


def _head(env: ManualEditEnv, project_id: object) -> object:
    branch = env.service.get_branch(project_id, "main")
    assert branch is not None and branch.head_commit_id is not None
    return branch.head_commit_id


def _import_chapter(
    env: ManualEditEnv,
    project_id: object,
    *,
    chapter_id: str = CHAPTER,
    title: str = "第一章",
    text: str = "第一段\n\n第二段\n\n第三段",
    key: str = "import",
) -> object:
    command = manuscript_command(
        commands.import_chapter, chapter_id=chapter_id, title=title, text=text
    )
    return env.handler.execute(command, project_id=project_id, idempotency_key=key, summary="导入")


def _unit_ids(env: ManualEditEnv, project_id: object, chapter_id: str = CHAPTER) -> list[str]:
    catalog = env.queries.catalog(project_id, _head(env, project_id))
    return [entry.artifact_id for entry in catalog.units_of(chapter_id)]


def _base(env: ManualEditEnv, project_id: object, chapter_id: str = CHAPTER) -> str:
    return env.queries.chapter_base(project_id, _head(env, project_id), chapter_id)


@pytest.mark.anyio
async def test_second_writer_gets_base_conflict_with_third_party_data(
    edit_env: ManualEditEnv,
) -> None:
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)
    base = _base(edit_env, project.id)

    # 客户端 A 先提交（成功）
    first = await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="A",
            operations=(operations.Replace(unit_id=units[1], text="A改"),),
            expected_chapter_base=base,
        )
    )
    assert first.is_committed
    head_after_a = _head(edit_env, project.id)

    # 客户端 B 基于**同一基础**提交 → 显式冲突
    with pytest.raises(ViolationError) as exc:
        await edit_env.workflow.save(
            ManualEditIntent(
                project_id=project.id,
                chapter_id=CHAPTER,
                idempotency_key="B",
                operations=(operations.Replace(unit_id=units[1], text="B改"),),
                expected_chapter_base=base,
            )
        )

    error = exc.value
    assert error.category == ResultCategory.VIOLATION
    assert error.code == ec.MANUSCRIPT_BASE_CONFLICT
    assert error.where and error.why and error.fix  # 三要素齐全
    # 三方比较所需数据：base / theirs（当前基础、当前文本、当前段落、当前章 Revision）
    assert error.details["expected"] == base  # base（客户端旧基础）
    assert error.details["current"] != base  # theirs 的基础
    assert error.details["current_text"] == "第一段\n\nA改\n\n第三段"  # theirs 文本
    assert error.details["current_unit_ids"] == units
    assert error.details["chapter_revision"]
    # 冲突不产生新 Commit（B 的修改未被自动 force）
    assert _head(edit_env, project.id) == head_after_a


@pytest.mark.anyio
async def test_writer_with_current_base_succeeds(edit_env: ManualEditEnv) -> None:
    """重新载入后（拿到新基础）再提交即成功——冲突是提示，不是死锁。"""
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)

    await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="A",
            operations=(operations.Replace(unit_id=units[1], text="A改"),),
            expected_chapter_base=_base(edit_env, project.id),
        )
    )
    # 重新载入：读取最新基础后再提交 B 的意图
    reloaded = await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="B2",
            operations=(operations.Replace(unit_id=units[1], text="B改"),),
            expected_chapter_base=_base(edit_env, project.id),
        )
    )

    assert reloaded.is_committed
    assert edit_env.queries.chapter_text(project.id, _head(edit_env, project.id), CHAPTER) == (
        "第一段\n\nB改\n\n第三段"
    )


@pytest.mark.anyio
async def test_unrelated_chapter_edit_does_not_conflict(edit_env: ManualEditEnv) -> None:
    """基础是章节范围的：改别章不移动本章基础，本章可继续保存。"""
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    _import_chapter(
        edit_env, project.id, chapter_id=OTHER, title="第二章", text="别章一段", key="i2"
    )
    base_ch1 = _base(edit_env, project.id)

    # 先改第二章
    other_unit = _unit_ids(edit_env, project.id, OTHER)[0]
    await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=OTHER,
            idempotency_key="other",
            operations=(operations.Replace(unit_id=other_unit, text="别章改"),),
        )
    )

    # 用改别章**之前**记下的基础保存第一章 → 不冲突
    units = _unit_ids(edit_env, project.id)
    result = await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="ch1",
            operations=(operations.Replace(unit_id=units[1], text="第一章改"),),
            expected_chapter_base=base_ch1,
        )
    )

    assert result.is_committed
