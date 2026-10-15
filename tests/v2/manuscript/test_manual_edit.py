"""T14.2 人工保存用例：CommitManualEdit —— 保存只落 Commit，分析由 Outbox 消费者调度。

覆盖计划 §6 测试先行清单 4/5（人工保存产生 Commit/Outbox；分析调度失败不影响正文）
与评审 #3 的修订契约（保存不等待 jobs.create；补偿与去重落在持久化 Outbox）：

- 保存经 handler → Commit Service 落为新 Commit，并同事务写 Outbox（含章提交事件）；
- 保存**立即返回**，不创建任何分析 Job；分析 Job 由 Outbox 消费者按事件调度；
- 消费失败只让事件保持待发布（可补偿），正文不回滚；
- 重复投递同一事件只创建一个分析 Job；
- no-op 保存不提交、不产生事件；
- 保存意图越出当前章范围在领域层被拒（四要素边界转 ViolationError）；
- 同幂等键重试返回首次结果，不重跑命令。
"""

from __future__ import annotations

import pytest

from hastings.application.errors import ResultCategory, ViolationError
from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.workflows.analysis_dispatch import ANALYSIS_JOB_TYPE
from hastings.application.workflows.manual_edit import ManualEditIntent
from hastings.domain.manuscript import commands, operations
from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.models import EVENT_CHAPTER_COMMITTED
from hastings.domain.repository.results import COMMITTED, NO_CHANGE

from .conftest import ManualEditEnv

CHAPTER = "art_ch1"


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


def _text_of(env: ManualEditEnv, project_id: object, chapter_id: str = CHAPTER) -> str:
    return env.queries.chapter_text(project_id, _head(env, project_id), chapter_id)


# ---------------------------------------------------------------------------
# 保存产生 Commit 与 Outbox
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_manual_save_produces_commit_and_outbox(edit_env: ManualEditEnv) -> None:
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    before_head = _head(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)

    result = await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="save-1",
            summary="改第二段",
            operations=(operations.Replace(unit_id=units[1], text="改后的第二段"),),
        )
    )

    assert result.status == COMMITTED and result.is_committed
    assert result.commit_id is not None and result.new_head != before_head
    # 正文已落为新版本，读回可见
    assert _text_of(edit_env, project.id) == "第一段\n\n改后的第二段\n\n第三段"
    # Outbox 与 Commit 同事务落盘（ADR-007）；含章提交事件（下游分析的触发源）
    events = await edit_env.repo.pending(project.id.value, 50)
    kinds_seen = {event["type"] for event in events}
    assert "repository.commit.created" in kinds_seen
    assert any(event["type"].startswith("artifact.revision.created") for event in events)
    assert EVENT_CHAPTER_COMMITTED in kinds_seen
    chapter_event = next(e for e in events if e["type"] == EVENT_CHAPTER_COMMITTED)
    assert chapter_event["payload"]["chapter_id"] == CHAPTER


@pytest.mark.anyio
async def test_save_returns_without_scheduling_job(edit_env: ManualEditEnv) -> None:
    """评审 #3：保存立即返回，不等待 jobs.create；分析 Job 由 Outbox 消费者调度。"""
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)

    result = await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="save-1",
            operations=(operations.Replace(unit_id=units[1], text="改"),),
        )
    )

    assert result.is_committed
    assert edit_env.jobs.created == []  # 保存路径不创建任何任务

    # 消费者推进 Outbox → 恰好创建一个分析 Job
    await edit_env.publisher.publish_pending(project.id.value)
    assert len(edit_env.jobs.created) == 1
    job_type, payload = edit_env.jobs.created[0]
    assert job_type == ANALYSIS_JOB_TYPE
    assert payload["commit_id"] == result.commit_id
    assert payload["chapter_id"] == CHAPTER


@pytest.mark.anyio
async def test_dispatch_failure_keeps_event_pending_for_retry(
    edit_env: ManualEditEnv,
) -> None:
    """消费（JobStore）故障 → 事件保持待发布可补偿，正文不回滚；恢复后重投成功。"""
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)

    result = await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="save-1",
            operations=(operations.Replace(unit_id=units[1], text="改后的第二段"),),
        )
    )
    assert result.is_committed
    # 正文已为新版：后台故障不得回滚用户数据
    assert _text_of(edit_env, project.id) == "第一段\n\n改后的第二段\n\n第三段"

    edit_env.jobs.fail_with = OSError("任务库不可写")
    with pytest.raises(OSError):
        await edit_env.publisher.publish_pending(project.id.value)

    # 事件仍待发布：补偿入口就在持久化 Outbox 里，不在返回值
    pending = await edit_env.repo.pending(project.id.value, 50)
    assert any(event["type"] == EVENT_CHAPTER_COMMITTED for event in pending)
    assert edit_env.jobs.created == []

    # 故障恢复后重投 → 成功创建一次 Job
    edit_env.jobs.fail_with = None
    await edit_env.publisher.publish_pending(project.id.value)
    assert len(edit_env.jobs.created) == 1


@pytest.mark.anyio
async def test_duplicate_event_delivery_creates_one_job(edit_env: ManualEditEnv) -> None:
    """同一事件被重复投递（发布/标记崩溃窗口）只产生一次业务效果。"""
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)
    await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="save-dup",
            operations=(operations.Replace(unit_id=units[1], text="改"),),
        )
    )
    events = await edit_env.repo.pending(project.id.value, 50)
    target = next(event for event in events if event["type"] == EVENT_CHAPTER_COMMITTED)

    await edit_env.dispatcher.publish(target)
    await edit_env.dispatcher.publish(target)  # 重复投递

    assert len(edit_env.jobs.created) == 1


@pytest.mark.anyio
async def test_retry_same_key_replays_and_does_not_double_commit(
    edit_env: ManualEditEnv,
) -> None:
    """同键重试返回首次结果（同一 Commit），不产生第二个提交或第二个事件。"""
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)
    intent = ManualEditIntent(
        project_id=project.id,
        chapter_id=CHAPTER,
        idempotency_key="save-dup",
        operations=(operations.Replace(unit_id=units[1], text="改"),),
    )

    first = await edit_env.workflow.save(intent)
    retry = await edit_env.workflow.save(intent)
    await edit_env.publisher.publish_pending(project.id.value)

    assert first.commit_id == retry.commit_id
    assert len(edit_env.jobs.created) == 1  # 每个 Commit 至多一个同型 Job


# ---------------------------------------------------------------------------
# 合法空结果与范围约束
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_noop_save_is_legal_empty_and_skips_analysis(edit_env: ManualEditEnv) -> None:
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    units = _unit_ids(edit_env, project.id)
    before_head = _head(edit_env, project.id)

    result = await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="save-noop",
            operations=(operations.Replace(unit_id=units[1], text="第二段"),),  # 与现文一致
        )
    )

    assert result.status == NO_CHANGE and result.is_no_change
    assert result.commit_id is None
    assert edit_env.jobs.created == []  # 无新版本 → 无新事件 → 无分析
    assert _head(edit_env, project.id) == before_head


@pytest.mark.anyio
async def test_full_text_save_is_supported(edit_env: ManualEditEnv) -> None:
    """兼容模式：客户端提交整章文本，服务器算 diff 并规范化。"""
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)

    result = await edit_env.workflow.save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="save-full",
            full_text="第一段\n\n第二段改\n\n第三段",
        )
    )

    assert result.is_committed
    assert _text_of(edit_env, project.id) == "第一段\n\n第二段改\n\n第三段"


@pytest.mark.anyio
async def test_save_cannot_touch_another_chapter(edit_env: ManualEditEnv) -> None:
    """保存意图只作用于当前章：引用他章段落被领域层拒（范围约束）。"""
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)
    other = _import_chapter(
        edit_env, project.id, chapter_id="art_ch2", title="第二章", text="别章的段落", key="imp2"
    )
    del other
    other_unit = _unit_ids(edit_env, project.id, "art_ch2")[0]

    with pytest.raises(ViolationError) as exc:
        await edit_env.workflow.save(
            ManualEditIntent(
                project_id=project.id,
                chapter_id=CHAPTER,
                idempotency_key="save-cross",
                operations=(operations.Replace(unit_id=other_unit, text="越界改"),),
            )
        )

    error = exc.value
    assert error.category == ResultCategory.VIOLATION
    assert error.code == ec.MANUSCRIPT_UNKNOWN_TEXT_UNIT
    assert error.where and error.why and error.fix  # 三要素齐全


@pytest.mark.anyio
async def test_unknown_chapter_becomes_application_error(edit_env: ManualEditEnv) -> None:
    project = edit_env.handler.create_project("冬馆")
    _import_chapter(edit_env, project.id)

    with pytest.raises(ViolationError) as exc:
        await edit_env.workflow.save(
            ManualEditIntent(
                project_id=project.id,
                chapter_id="art_missing",
                idempotency_key="save-x",
                full_text="任意",
            )
        )
    assert exc.value.category == ResultCategory.VIOLATION
    assert exc.value.code == ec.MANUSCRIPT_UNKNOWN_CHAPTER


def test_intent_requires_exactly_one_content() -> None:
    from hastings.domain.repository.ids import ProjectId

    with pytest.raises(ValueError):
        ManualEditIntent(project_id=ProjectId("p"), chapter_id=CHAPTER, idempotency_key="k")
    with pytest.raises(ValueError):
        ManualEditIntent(
            project_id=ProjectId("p"),
            chapter_id=CHAPTER,
            idempotency_key="k",
            operations=(operations.Delete(unit_id="art_u1"),),
            full_text="两样都给",
        )
