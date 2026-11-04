"""1.2 P1：格式 5、无全局摘要门槛与建章事务的领域测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.chapter_write_policy import ChapterWritePolicy
from hastings.workspace.chapter_writing import save_chapter_prose
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import BODY_DIR, Workspace
from hastings.workspace.index_confirmation import confirm_index_candidate
from hastings.workspace.story_index_jobs import IndexJob, IndexJobCandidate, IndexJobStore
from hastings.workspace.story_models import (
    AddNode,
    BindProse,
    ProseBinding,
    StoryChangeSet,
    StoryNode,
)
from hastings.workspace.story_store import (
    EMPTY_REVISION,
    MAIN_VARIANT_ID,
    StoryStore,
    new_id,
)
from hastings.workspace.writing_actions import ChapterCreateResult, create_chapter
from hastings.workspace.writing_view import build_chapter_index


@pytest.fixture
def env(tmp_path: Path) -> tuple[Workspace, StoryStore]:
    workspace = Workspace.create(tmp_path / "书")
    store = StoryStore(tmp_path / "state", workspace, format_version=5)
    workspace.chapter_policy = ChapterWritePolicy(store)
    return workspace, store


def error_code(exc: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return exc.value.code


def setup_chapter(
    store: StoryStore,
    workspace: Workspace,
    *,
    title: str = "第一章",
    request_key: str = "chapter:1",
) -> ChapterCreateResult:
    variant = store.read().variant(MAIN_VARIANT_ID)
    return create_chapter(
        store,
        workspace,
        title=title,
        after_chapter_id=None,
        expected_plan_revision=variant.revision if variant is not None else EMPTY_REVISION,
        request_key=request_key,
    )


def test_chapter_creation_creates_prose_and_is_idempotent(
    env: tuple[Workspace, StoryStore],
) -> None:
    workspace, store = env
    first = setup_chapter(store, workspace)
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert variant.format_version == 5
    plan = variant.chapter(first.chapter_id)
    assert plan is not None
    assert plan.prose_path == first.path
    assert plan.title == "第一章"
    assert (workspace.root / first.path).read_text(encoding="utf-8") == "# 第一章\n"

    second = create_chapter(
        store,
        workspace,
        title="第一章",
        after_chapter_id=None,
        expected_plan_revision=variant.revision,
        request_key="chapter:1",
    )
    assert second.chapter_id == first.chapter_id
    assert second.path == first.path
    assert second.receipt.change_id == first.receipt.change_id
    files = sorted(path.name for path in (workspace.root / BODY_DIR).glob("*.md"))
    assert files == [Path(first.path).name]


def test_first_chapter_initializes_current_format(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    created = create_chapter(
        store,
        workspace,
        title=None,
        after_chapter_id=None,
        expected_plan_revision=EMPTY_REVISION,
        request_key="chapter:first",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert variant.format_version == 5
    assert variant.chapter(created.chapter_id) is not None
    assert list((workspace.root / BODY_DIR).glob("*.md"))


def test_legacy_operation_is_rejected_in_current_format(
    env: tuple[Workspace, StoryStore],
) -> None:
    _, store = env
    with pytest.raises(WorkspaceError) as exc:
        store.commit_change(
            variant_id=MAIN_VARIANT_ID,
            request_key="legacy",
            change=StoryChangeSet(
                id="legacy",
                base_revision=EMPTY_REVISION,
                operations=[AddNode(node=StoryNode(id="n1", title="节点", action="发生"))],
            ),
        )
    assert error_code(exc) == "STORY_GRAPH_INVALID"
    assert "旧格式" in exc.value.why


def test_write_policy_has_no_semantic_gate(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    standalone = workspace.write("正文/0001 未构思.md", "# 未构思\n", expected_digest=None)
    assert standalone.path == "正文/0001 未构思.md"

    chapter = setup_chapter(store, workspace)
    view = workspace.read(chapter.path)
    workspace.write(chapter.path, view.text + "\n林砚走进旅馆。\n", expected_digest=view.digest)
    assert "林砚走进旅馆" in workspace.read(chapter.path).text


def test_imported_unplanned_prose_can_be_edited(
    env: tuple[Workspace, StoryStore],
) -> None:
    workspace, store = env
    (imported,) = workspace.import_text("旧稿.md", "# 旧稿\n\n原有正文\n".encode())
    assert imported.path.startswith("正文/")
    updated = workspace.write(
        imported.path, imported.text + "修改", expected_digest=imported.digest
    )
    assert updated.text.endswith("修改")


def test_save_checks_digest_and_retry(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    chapter = setup_chapter(store, workspace)
    with pytest.raises(WorkspaceError) as exc:
        save_chapter_prose(
            store,
            workspace,
            chapter_id=chapter.chapter_id,
            text="新正文",
            expected_digest="0" * 64,
            request_key="save:bad-digest",
        )
    assert error_code(exc) == "PROSE_DIGEST_MISMATCH"

    saved = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text="# 第一章\n\n林砚走进旅馆。\n",
        expected_digest=chapter.prose_digest,
        request_key="save:ok",
        draft_revision="local_1",
    )
    assert saved.committed_change_id
    assert "林砚走进旅馆" in workspace.read(saved.path).text

    retried = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text="# 第一章\n\n林砚走进旅馆。\n",
        expected_digest=chapter.prose_digest,
        request_key="save:ok",
        draft_revision="local_1",
    )
    assert retried.committed_change_id == saved.committed_change_id
    assert workspace.read(saved.path).text == "# 第一章\n\n林砚走进旅馆。\n"


def test_save_does_not_require_structure_file(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    chapter = setup_chapter(store, workspace)
    saved = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text="# 第一章\n\n正文\n",
        expected_digest=chapter.prose_digest,
        request_key="save:no-structure",
    )
    assert saved.prose_digest == workspace.read(chapter.path).digest


def test_undo_chapter_creation_removes_file(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    chapter = setup_chapter(store, workspace)
    receipt = store.receipt_for("chapter:1")
    assert receipt is not None
    store.undo(change_id=receipt.change_id, request_key="undo:chapter")
    assert store.require_variant(MAIN_VARIANT_ID).chapter_plans == {}
    assert not (workspace.root / chapter.path).exists()


def _seed_ready_job(
    store: StoryStore,
    *,
    chapter_id: str,
    digest: str,
    statement: str,
    quote: str,
) -> IndexJob:
    jobs = IndexJobStore(store.root)
    job = IndexJob(
        job_id="job_1",
        chapter_id=chapter_id,
        status="ready",
        based_on_digest=digest,
        context_revision="ctx",
        plan_revision=store.require_variant(MAIN_VARIANT_ID).revision,
        changed_text=quote,
        candidates=[
            IndexJobCandidate(
                item_id=f"{chapter_id}:0",
                label=statement,
                statement=statement,
                quote=quote,
            )
        ],
        created_at="2026-11-02T00:00:00",
        updated_at="2026-11-02T00:00:00",
    )
    jobs.save(job)
    return job


def test_confirm_candidate_creates_ledger_and_undo(
    env: tuple[Workspace, StoryStore],
) -> None:
    workspace, store = env
    chapter = setup_chapter(store, workspace)
    text = "# 第一章\n\n林砚走进旅馆，柜台上有一双湿手套。\n"
    saved = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text=text,
        expected_digest=chapter.prose_digest,
        request_key="save:confirm",
    )
    job = _seed_ready_job(
        store,
        chapter_id=chapter.chapter_id,
        digest=saved.prose_digest,
        statement="湿手套首次出现",
        quote="一双湿手套",
    )
    jobs = IndexJobStore(store.root)
    result = confirm_index_candidate(
        store,
        workspace,
        jobs,
        chapter_id=chapter.chapter_id,
        candidate_id=job.candidates[0].item_id,
        expected_prose_digest=saved.prose_digest,
        request_key="confirm:1",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert result.move.statement == "湿手套首次出现"
    assert result.binding.quote == "一双湿手套"
    assert variant.information_moves[result.move.id].chapter_id == chapter.chapter_id
    assert variant.information_threads[result.thread.id].summary == "湿手套首次出现"
    assert workspace.read(saved.path).text == text

    retried = confirm_index_candidate(
        store,
        workspace,
        jobs,
        chapter_id=chapter.chapter_id,
        candidate_id=job.candidates[0].item_id,
        expected_prose_digest=saved.prose_digest,
        request_key="confirm:1",
    )
    assert retried.receipt.change_id == result.receipt.change_id

    store.undo(change_id=result.receipt.change_id, request_key="undo:confirm")
    reverted = store.require_variant(MAIN_VARIANT_ID)
    assert reverted.information_moves == {}
    assert reverted.bindings == {}
    assert reverted.information_threads == {}


def test_confirm_rejects_stale_digest_and_duplicate(
    env: tuple[Workspace, StoryStore],
) -> None:
    workspace, store = env
    chapter = setup_chapter(store, workspace)
    saved = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text="# 第一章\n\n林砚走进旅馆，柜台上有一双湿手套。\n",
        expected_digest=chapter.prose_digest,
        request_key="save:confirm",
    )
    job = _seed_ready_job(
        store,
        chapter_id=chapter.chapter_id,
        digest=saved.prose_digest,
        statement="湿手套首次出现",
        quote="一双湿手套",
    )
    jobs = IndexJobStore(store.root)
    with pytest.raises(WorkspaceError) as exc:
        confirm_index_candidate(
            store,
            workspace,
            jobs,
            chapter_id=chapter.chapter_id,
            candidate_id=job.candidates[0].item_id,
            expected_prose_digest="0" * 64,
            request_key="confirm:stale",
        )
    assert error_code(exc) == "INDEX_RESULT_STALE"

    confirmed = confirm_index_candidate(
        store,
        workspace,
        jobs,
        chapter_id=chapter.chapter_id,
        candidate_id=job.candidates[0].item_id,
        expected_prose_digest=saved.prose_digest,
        request_key="confirm:ok",
    )
    assert confirmed.receipt.change_id
    with pytest.raises(WorkspaceError) as exc:
        confirm_index_candidate(
            store,
            workspace,
            jobs,
            chapter_id=chapter.chapter_id,
            candidate_id=job.candidates[0].item_id,
            expected_prose_digest=saved.prose_digest,
            request_key="confirm:again",
        )
    assert error_code(exc) == "INDEX_RESULT_STALE"


def test_save_relocates_binding_and_reports_invalidation(
    env: tuple[Workspace, StoryStore],
) -> None:
    workspace, store = env
    chapter = setup_chapter(store, workspace)
    first = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text="# 第一章\n\n林砚走进旅馆，柜台上有一双湿手套。\n",
        expected_digest=chapter.prose_digest,
        request_key="save:binding-1",
    )
    job = _seed_ready_job(
        store,
        chapter_id=chapter.chapter_id,
        digest=first.prose_digest,
        statement="湿手套首次出现",
        quote="湿手套",
    )
    confirmed = confirm_index_candidate(
        store,
        workspace,
        IndexJobStore(store.root),
        chapter_id=chapter.chapter_id,
        candidate_id=job.candidates[0].item_id,
        expected_prose_digest=first.prose_digest,
        request_key="confirm:binding",
    )

    moved = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text="# 第一章\n\n门外的雪更大了。\n\n林砚走进旅馆，柜台上有一双湿手套。\n",
        expected_digest=first.prose_digest,
        request_key="save:binding-2",
    )
    binding = store.require_variant(MAIN_VARIANT_ID).bindings[confirmed.binding.id]
    assert binding.digest == moved.prose_digest
    item = next(row for row in moved.index.items if row.placement == "written")
    assert item.occurrences[0].state == "exact"
    assert item.occurrences[0].start == binding.start

    deleted = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text="# 第一章\n\n门外的雪更大了。\n\n林砚走进旅馆。\n",
        expected_digest=moved.prose_digest,
        request_key="save:binding-3",
    )
    item = next(row for row in deleted.index.items if row.placement == "written")
    assert item.occurrences[0].state == "missing"
    saved_bindings = store.require_variant(MAIN_VARIANT_ID).bindings
    assert saved_bindings[confirmed.binding.id].start == binding.start


def test_binding_recovers_after_quote_is_restored(env: tuple[Workspace, StoryStore]) -> None:
    """A12：删掉唯一原句后失效，恢复同一原句后重新定位，不猜相似位置。"""
    workspace, store = env
    chapter = setup_chapter(store, workspace)
    first = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text="# 第一章\n\n林砚走进旅馆，柜台上有一双湿手套。\n",
        expected_digest=chapter.prose_digest,
        request_key="save:recover-1",
    )
    job = _seed_ready_job(
        store,
        chapter_id=chapter.chapter_id,
        digest=first.prose_digest,
        statement="湿手套首次出现",
        quote="一双湿手套",
    )
    confirmed = confirm_index_candidate(
        store,
        workspace,
        IndexJobStore(store.root),
        chapter_id=chapter.chapter_id,
        candidate_id=job.candidates[0].item_id,
        expected_prose_digest=first.prose_digest,
        request_key="confirm:recover",
    )
    original_start = confirmed.binding.start

    deleted = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text="# 第一章\n\n林砚走进旅馆，柜台上空着。\n",
        expected_digest=first.prose_digest,
        request_key="save:recover-2",
    )
    item = next(row for row in deleted.index.items if row.placement == "written")
    assert item.occurrences[0].state == "missing"
    assert item.occurrences[0].start is None

    restored = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text="# 第一章\n\n林砚走进旅馆，柜台上有一双湿手套。\n",
        expected_digest=deleted.prose_digest,
        request_key="save:recover-3",
    )
    item = next(row for row in restored.index.items if row.placement == "written")
    assert item.occurrences[0].state == "exact"
    assert item.occurrences[0].start == original_start


def test_one_move_keeps_each_occurrence_span(env: tuple[Workspace, StoryStore]) -> None:
    """A13：同一信息线在一章出现多处时，每处各自保留可定位出处。"""
    workspace, store = env
    chapter = setup_chapter(store, workspace)
    text = (
        "# 第一章\n\n林砚看见湿手套。\n\n"
        "过了半晌，他又把湿手套放回柜台。\n"
    )
    saved = save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text=text,
        expected_digest=chapter.prose_digest,
        request_key="save:multi-1",
    )
    job = _seed_ready_job(
        store,
        chapter_id=chapter.chapter_id,
        digest=saved.prose_digest,
        statement="湿手套先被看见又被放回",
        quote="林砚看见湿手套。",
    )
    confirmed = confirm_index_candidate(
        store,
        workspace,
        IndexJobStore(store.root),
        chapter_id=chapter.chapter_id,
        candidate_id=job.candidates[0].item_id,
        expected_prose_digest=saved.prose_digest,
        request_key="confirm:multi",
    )
    second_quote = "他又把湿手套放回柜台。"
    second_start = text.find(second_quote)
    variant = store.require_variant(MAIN_VARIANT_ID)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(
            id=new_id("change"),
            base_revision=variant.revision,
            operations=[
                BindProse(
                    binding=ProseBinding(
                        id=new_id("binding"),
                        move_id=confirmed.move.id,
                        document_id=chapter.chapter_id,
                        path=saved.path,
                        digest=saved.prose_digest,
                        start=second_start,
                        end=second_start + len(second_quote),
                        quote=second_quote,
                        source="manual",
                    )
                )
            ],
            requested_scope="acceptance:a13",
        ),
        request_key="bind:multi-2",
    )

    current = store.require_variant(MAIN_VARIANT_ID)
    plan = current.chapter(chapter.chapter_id)
    assert plan is not None
    index = build_chapter_index(
        current,
        plan,
        path=saved.path,
        text=text,
        digest=saved.prose_digest,
    )
    item = next(row for row in index.items if row.move_id == confirmed.move.id)
    assert len(item.occurrences) == 2
    assert {row.start for row in item.occurrences} == {
        confirmed.binding.start,
        second_start,
    }
    assert all(row.state == "exact" for row in item.occurrences)


