"""Phase 14 验收 9：历史恢复为新 Commit。

恢复 = 把某章内容回退到历史 Commit 的版本，但**不重写历史**——恢复本身是
一次新的正文提交（旧版本永远可追溯）。复用 ``save_full_text`` 的 diff 规范化：
恢复与人工保存走同一条写路径、同一套乐观并发检查与幂等重放。
"""

from __future__ import annotations

import asyncio

import pytest

from hastings.application.errors import ViolationError
from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.workflows.manual_edit import ManualEditIntent
from hastings.application.workflows.restore_version import RestoreChapterVersion, RestoreIntent
from hastings.domain.manuscript.commands import import_chapter
from hastings.domain.repository.ids import CommitId, ProjectId

from .conftest import ManualEditEnv


@pytest.fixture
def project(edit_env: ManualEditEnv) -> ProjectId:
    return edit_env.handler.create_project("冬馆").id


@pytest.fixture
def restore(edit_env: ManualEditEnv) -> RestoreChapterVersion:
    return RestoreChapterVersion(edit_env.handler, edit_env.service)


def _head(env: ManualEditEnv, project: ProjectId) -> CommitId | None:
    branch = env.service.get_branch(project, "main")
    assert branch is not None
    return branch.head_commit_id


def _import(env: ManualEditEnv, project: ProjectId, chapter_id: str, text: str, key: str) -> str:
    result = env.handler.execute(
        manuscript_command(import_chapter, chapter_id=chapter_id, title="第一章", text=text),
        project_id=project,
        idempotency_key=key,
        summary=f"导入 {chapter_id}",
    )
    assert result.is_committed
    assert result.commit_id is not None
    return result.commit_id


def _save(env: ManualEditEnv, project: ProjectId, chapter_id: str, text: str, key: str) -> str:
    result = asyncio.run(
        env.workflow.save(
            ManualEditIntent(
                project_id=project, chapter_id=chapter_id, idempotency_key=key, full_text=text
            )
        )
    )
    assert result.is_committed
    assert result.commit_id is not None
    return result.commit_id


class TestRestoreAsNewCommit:
    def test_restore_reverts_text_and_commits_new_head(
        self, edit_env: ManualEditEnv, project: ProjectId, restore: RestoreChapterVersion
    ) -> None:
        first = _import(
            edit_env, project, "art_ch1", "第一段。\n\n第二段。\n\n第三段。", "import-1"
        )
        _save(edit_env, project, "art_ch1", "第一段。\n\n第二段已被大改。\n\n第三段。", "save-1")

        result = restore.restore(
            RestoreIntent(
                project_id=project,
                chapter_id="art_ch1",
                source_commit_id=CommitId(first),
                idempotency_key="restore-1",
            )
        )
        assert result.is_committed
        assert result.commit_id not in (None, first)

        # 正文回到首版，且 head 前移（不重写历史）
        head = _head(edit_env, project)
        assert head is not None and head.value != first
        assert (
            edit_env.queries.chapter_text(project, head, "art_ch1")
            == "第一段。\n\n第二段。\n\n第三段。"
        )

    def test_restore_to_identical_content_is_no_change(
        self, edit_env: ManualEditEnv, project: ProjectId, restore: RestoreChapterVersion
    ) -> None:
        first = _import(edit_env, project, "art_ch1", "第一段。\n\n第二段。", "import-1")

        result = restore.restore(
            RestoreIntent(
                project_id=project,
                chapter_id="art_ch1",
                source_commit_id=CommitId(first),
                idempotency_key="restore-noop",
            )
        )
        assert result.is_no_change
        assert result.commit_id is None

    def test_restore_is_idempotent_on_retry(
        self, edit_env: ManualEditEnv, project: ProjectId, restore: RestoreChapterVersion
    ) -> None:
        first = _import(edit_env, project, "art_ch1", "第一段。\n\n第二段。", "import-1")
        _save(edit_env, project, "art_ch1", "第一段。\n\n改。", "save-1")
        intent = RestoreIntent(
            project_id=project,
            chapter_id="art_ch1",
            source_commit_id=CommitId(first),
            idempotency_key="restore-retry",
        )
        first_result = restore.restore(intent)
        replay = restore.restore(intent)
        assert first_result.commit_id == replay.commit_id

    def test_chapter_missing_at_source_commit_is_rejected(
        self, edit_env: ManualEditEnv, project: ProjectId, restore: RestoreChapterVersion
    ) -> None:
        first = _import(edit_env, project, "art_ch1", "第一段。", "import-1")
        _import(edit_env, project, "art_ch2", "另一章。", "import-2")

        with pytest.raises(ViolationError) as excinfo:
            restore.restore(
                RestoreIntent(
                    project_id=project,
                    chapter_id="art_ch2",
                    source_commit_id=CommitId(first),
                    idempotency_key="restore-missing",
                )
            )
        assert excinfo.value.code == "MANUSCRIPT_UNKNOWN_CHAPTER"
        assert "art_ch2" in excinfo.value.why
        # 错因归到「源」：where 指向 restore.source，不与 head 上「章不存在」混淆
        assert ".source" in excinfo.value.where

    def test_stale_base_conflict_blocks_restore(
        self, edit_env: ManualEditEnv, project: ProjectId, restore: RestoreChapterVersion
    ) -> None:
        first = _import(edit_env, project, "art_ch1", "第一段。\n\n第二段。", "import-1")
        _save(edit_env, project, "art_ch1", "第一段。\n\n改。", "save-1")

        stale_base = edit_env.queries.chapter_base(project, CommitId(first), "art_ch1")
        with pytest.raises(ViolationError) as excinfo:
            restore.restore(
                RestoreIntent(
                    project_id=project,
                    chapter_id="art_ch1",
                    source_commit_id=CommitId(first),
                    idempotency_key="restore-conflict",
                    expected_chapter_base=stale_base,
                )
            )
        assert excinfo.value.code == "MANUSCRIPT_BASE_CONFLICT"
