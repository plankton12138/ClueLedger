"""1.0.9 P1：当前格式、章节门槛与开始事务的领域测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.chapter_write_policy import ChapterWritePolicy
from hastings.workspace.chapter_writing import save_chapter_prose, start_chapter
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import BODY_DIR, Workspace
from hastings.workspace.story_models import (
    AddChapterPlan,
    AddInformationMove,
    AddInformationThread,
    AddNode,
    BindProse,
    BookIntent,
    ChapterPlan,
    InformationMove,
    InformationThread,
    ProseBinding,
    SetBookIntent,
    StoryChangeSet,
    StoryNode,
    UpdateChapterPlan,
    chapter_content_revision,
)
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore


@pytest.fixture
def env(tmp_path: Path) -> tuple[Workspace, StoryStore]:
    workspace = Workspace.create(tmp_path / "书")
    store = StoryStore(tmp_path / "state", workspace, format_version=3)
    return workspace, store


def setup_chapter(
    store: StoryStore,
    *,
    chapter_id: str = "ch1",
    title: str = "第一章",
    intent: str = "入住并起疑",
    statement: str = "章末出现湿手套",
    with_move: bool = True,
    intent_id: str = "b1",
) -> str:
    move = (
        InformationMove(
            id="m1",
            thread_id="t1",
            chapter_id=chapter_id,
            statement=statement,
        )
        if with_move
        else None
    )
    revision = chapter_content_revision(intent, [move] if move else [])
    plan = ChapterPlan(
        id=chapter_id,
        order=0,
        title=title,
        intent=intent,
        information_move_ids=[move.id] if move else [],
        revision=revision,
    )
    operations = [
        SetBookIntent(intent=BookIntent(text="暴雪旅馆的命案")),
        AddChapterPlan(plan=plan),
    ]
    if move is not None:
        operations.extend(
            [
                AddInformationThread(thread=InformationThread(id="t1", summary="湿手套")),
                AddInformationMove(move=move),
            ]
        )
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key=f"setup:{intent_id}",
        change=StoryChangeSet(
            id=f"setup_{intent_id}",
            base_revision=EMPTY_REVISION
            if store.read().document is None
            else store.require_variant(MAIN_VARIANT_ID).revision,
            operations=operations,
        ),
    )
    return revision


def error_code(exc: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return exc.value.code


def test_current_format_roundtrip_and_revision_scope(env: tuple[Workspace, StoryStore]) -> None:
    _, store = env
    revision = setup_chapter(store)
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert variant.format_version == 3
    assert variant.book_intent is not None
    assert variant.chapter_plans["ch1"].revision == revision
    assert variant.chapter_plans["ch1"].information_move_ids == ["m1"]

    plan = variant.chapter_plans["ch1"]
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="rename",
        change=StoryChangeSet(
            id="rename_ch1",
            base_revision=variant.revision,
            operations=[
                UpdateChapterPlan(
                    chapter_id="ch1",
                    plan=plan.model_copy(update={"title": "第一章 · 封山"}),
                )
            ],
        ),
    )
    renamed = store.require_variant(MAIN_VARIANT_ID).chapter_plans["ch1"]
    assert renamed.revision == revision
    assert renamed.title == "第一章 · 封山"

    new_revision = chapter_content_revision("入住、封山并起疑", [variant.information_moves["m1"]])
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="intent-change",
        change=StoryChangeSet(
            id="intent_ch1",
            base_revision=store.require_variant(MAIN_VARIANT_ID).revision,
            operations=[
                UpdateChapterPlan(
                    chapter_id="ch1",
                    plan=renamed.model_copy(
                        update={"intent": "入住、封山并起疑", "revision": new_revision}
                    ),
                )
            ],
        ),
    )
    updated = store.require_variant(MAIN_VARIANT_ID).chapter_plans["ch1"]
    assert updated.revision == new_revision
    assert updated.confirmed_revision is None
    assert store.require_variant(MAIN_VARIANT_ID).chapter_start_problem("ch1") == (
        "章节构思尚未按当前修订确认"
    )


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


def test_start_is_idempotent_and_creates_one_file(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    revision = setup_chapter(store)
    variant = store.require_variant(MAIN_VARIANT_ID)
    first = start_chapter(
        store,
        workspace,
        chapter_id="ch1",
        expected_story_revision=variant.revision,
        expected_chapter_revision=revision,
        request_key="start:ch1",
    )
    second = start_chapter(
        store,
        workspace,
        chapter_id="ch1",
        expected_story_revision=variant.revision,
        expected_chapter_revision=revision,
        request_key="start:ch1",
    )
    assert first.path == second.path
    assert first.committed_change_id == second.committed_change_id
    files = sorted(path.name for path in (workspace.root / BODY_DIR).glob("*.md"))
    assert files == [Path(first.path).name]
    plan = store.require_variant(MAIN_VARIANT_ID).chapter_plans["ch1"]
    assert plan.prose_path == first.path
    assert plan.confirmed_revision == revision


def test_start_requires_content_and_moves(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    revision = setup_chapter(store, with_move=False)
    variant = store.require_variant(MAIN_VARIANT_ID)
    with pytest.raises(WorkspaceError) as exc:
        start_chapter(
            store,
            workspace,
            chapter_id="ch1",
            expected_story_revision=variant.revision,
            expected_chapter_revision=revision,
            request_key="start:empty",
        )
    assert error_code(exc) == "CHAPTER_PLAN_REQUIRED"
    assert not list((workspace.root / BODY_DIR).glob("*.md"))


def test_policy_blocks_unplanned_write_and_allows_planned(
    env: tuple[Workspace, StoryStore],
) -> None:
    workspace, store = env
    workspace.chapter_policy = ChapterWritePolicy(store)
    with pytest.raises(WorkspaceError) as exc:
        workspace.write("正文/0001 未构思.md", "# 未构思\n", expected_digest=None)
    assert error_code(exc) == "CHAPTER_PLAN_REQUIRED"

    revision = setup_chapter(store)
    variant = store.require_variant(MAIN_VARIANT_ID)
    started = start_chapter(
        store,
        workspace,
        chapter_id="ch1",
        expected_story_revision=variant.revision,
        expected_chapter_revision=revision,
        request_key="start:policy",
    )
    view = workspace.read(started.path)
    workspace.write(started.path, view.text + "\n林砚走进旅馆。\n", expected_digest=view.digest)
    assert "林砚走进旅馆" in workspace.read(started.path).text


def test_import_can_create_unplanned_but_edit_is_blocked(
    env: tuple[Workspace, StoryStore],
) -> None:
    workspace, store = env
    workspace.chapter_policy = ChapterWritePolicy(store)
    (imported,) = workspace.import_text("旧稿.md", "# 旧稿\n\n原有正文\n".encode())
    assert imported.path.startswith("正文/")
    with pytest.raises(WorkspaceError) as exc:
        workspace.write(imported.path, imported.text + "修改", expected_digest=imported.digest)
    assert error_code(exc) == "CHAPTER_PLAN_REQUIRED"


def test_save_checks_digest_and_chapter_revision(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    revision = setup_chapter(store)
    variant = store.require_variant(MAIN_VARIANT_ID)
    started = start_chapter(
        store,
        workspace,
        chapter_id="ch1",
        expected_story_revision=variant.revision,
        expected_chapter_revision=revision,
        request_key="start:save",
    )
    with pytest.raises(WorkspaceError) as exc:
        save_chapter_prose(
            store,
            workspace,
            chapter_id="ch1",
            text="新正文",
            expected_digest="0" * 64,
            expected_chapter_revision=revision,
            request_key="save:bad-digest",
        )
    assert error_code(exc) == "PROSE_DIGEST_MISMATCH"

    with pytest.raises(WorkspaceError) as exc:
        save_chapter_prose(
            store,
            workspace,
            chapter_id="ch1",
            text="新正文",
            expected_digest=started.prose_digest,
            expected_chapter_revision="ch_stale",
            request_key="save:stale",
        )
    assert error_code(exc) == "CHAPTER_PLAN_STALE"

    saved = save_chapter_prose(
        store,
        workspace,
        chapter_id="ch1",
        text="# 第一章\n\n林砚走进旅馆。\n",
        expected_digest=started.prose_digest,
        expected_chapter_revision=revision,
        request_key="save:ok",
        draft_revision="local_1",
    )
    assert saved.committed_change_id
    assert "林砚走进旅馆" in workspace.read(saved.path).text

    retried = save_chapter_prose(
        store,
        workspace,
        chapter_id="ch1",
        text="# 第一章\n\n林砚走进旅馆。\n",
        expected_digest=started.prose_digest,
        expected_chapter_revision=revision,
        request_key="save:ok",
        draft_revision="local_1",
    )
    assert retried.committed_change_id == saved.committed_change_id
    assert workspace.read(saved.path).text == "# 第一章\n\n林砚走进旅馆。\n"


def test_attach_existing_prose_does_not_create_second_file(
    env: tuple[Workspace, StoryStore],
) -> None:
    workspace, store = env
    (imported,) = workspace.import_text("旧稿.md", "# 旧稿\n\n原有正文\n".encode())
    revision = setup_chapter(store)
    variant = store.require_variant(MAIN_VARIANT_ID)
    started = start_chapter(
        store,
        workspace,
        chapter_id="ch1",
        expected_story_revision=variant.revision,
        expected_chapter_revision=revision,
        request_key="start:attach",
        attach_path=imported.path,
        attach_digest=imported.digest,
    )
    assert started.path == imported.path
    assert workspace.read(imported.path).text == imported.text
    assert len(list((workspace.root / BODY_DIR).glob("*.md"))) == 1


def test_undo_restores_current_format_objects(env: tuple[Workspace, StoryStore]) -> None:
    _, store = env
    setup_chapter(store)
    receipt = store.receipt_for("setup:b1")
    assert receipt is not None
    store.undo(change_id=receipt.change_id, request_key="undo:setup")
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert variant.book_intent is None
    assert variant.chapter_plans == {}
    assert variant.information_threads == {}
    assert variant.information_moves == {}


def test_undo_start_removes_confirmation_and_initial_file(
    env: tuple[Workspace, StoryStore],
) -> None:
    workspace, store = env
    revision = setup_chapter(store)
    variant = store.require_variant(MAIN_VARIANT_ID)
    started = start_chapter(
        store,
        workspace,
        chapter_id="ch1",
        expected_story_revision=variant.revision,
        expected_chapter_revision=revision,
        request_key="start:undo",
    )
    receipt = store.receipt_for("start:undo")
    assert receipt is not None
    store.undo(change_id=receipt.change_id, request_key="undo:start")
    plan = store.require_variant(MAIN_VARIANT_ID).chapter_plans["ch1"]
    assert plan.confirmed_revision is None
    assert plan.prose_path is None
    assert not (workspace.root / started.path).exists()


def test_failed_start_leaves_no_confirmation_or_file(
    env: tuple[Workspace, StoryStore],
) -> None:
    workspace, store = env
    (imported,) = workspace.import_text("旧稿.md", "# 旧稿\n\n原有正文\n".encode())
    revision = setup_chapter(store)
    variant = store.require_variant(MAIN_VARIANT_ID)
    with pytest.raises(WorkspaceError) as exc:
        start_chapter(
            store,
            workspace,
            chapter_id="ch1",
            expected_story_revision=variant.revision,
            expected_chapter_revision=revision,
            request_key="start:attach-fail",
            attach_path=imported.path,
            attach_digest="0" * 64,
        )
    assert error_code(exc) == "PROSE_DIGEST_MISMATCH"
    plan = store.require_variant(MAIN_VARIANT_ID).chapter_plans["ch1"]
    assert plan.confirmed_revision is None
    assert plan.prose_path is None
    assert len(list((workspace.root / BODY_DIR).glob("*.md"))) == 1


def test_save_relocates_binding_and_reports_invalidation(
    env: tuple[Workspace, StoryStore],
) -> None:
    workspace, store = env
    revision = setup_chapter(store)
    variant = store.require_variant(MAIN_VARIANT_ID)
    started = start_chapter(
        store,
        workspace,
        chapter_id="ch1",
        expected_story_revision=variant.revision,
        expected_chapter_revision=revision,
        request_key="start:binding",
    )
    first = save_chapter_prose(
        store,
        workspace,
        chapter_id="ch1",
        text="# 第一章\n\n林砚走进旅馆，柜台上有一双湿手套。\n",
        expected_digest=started.prose_digest,
        expected_chapter_revision=revision,
        request_key="save:binding-1",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    move = variant.information_moves_for_chapter("ch1")[0]
    quote = "湿手套"
    start = workspace.read(first.path).text.index(quote)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="bind:1",
        change=StoryChangeSet(
            id="bind_1",
            base_revision=variant.revision,
            operations=[
                BindProse(
                    binding=ProseBinding(
                        id="b1",
                        move_id=move.id,
                        path=first.path,
                        digest=first.prose_digest,
                        start=start,
                        end=start + len(quote),
                        quote=quote,
                        source="author",
                    )
                )
            ],
        ),
    )

    moved = save_chapter_prose(
        store,
        workspace,
        chapter_id="ch1",
        text="# 第一章\n\n门外的雪更大了。\n\n林砚走进旅馆，柜台上有一双湿手套。\n",
        expected_digest=first.prose_digest,
        expected_chapter_revision=revision,
        request_key="save:binding-2",
    )
    binding = store.require_variant(MAIN_VARIANT_ID).bindings["b1"]
    assert binding.digest == moved.prose_digest
    assert binding.start == 30
    item = moved.index.items[0]
    assert item.placement == "written"
    assert item.occurrences[0].state == "exact"
    assert item.occurrences[0].start == 30

    deleted = save_chapter_prose(
        store,
        workspace,
        chapter_id="ch1",
        text="# 第一章\n\n门外的雪更大了。\n\n林砚走进旅馆。\n",
        expected_digest=moved.prose_digest,
        expected_chapter_revision=revision,
        request_key="save:binding-3",
    )
    assert deleted.index.items[0].occurrences[0].state == "missing"
    assert store.require_variant(MAIN_VARIANT_ID).bindings["b1"].start == 30
