"""v1.1 M4：正文台账随事务自动维护（事实层契约）。

台账只由事务层写：三元提交成功时按正文路径章号登记最近入档修订，
撤销时回退；模型无写入口。恢复路径重放同一调用（幂等）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.chapter_write_policy import ChapterWritePolicy
from hastings.workspace.chapter_writing import ProseSaveResult, save_chapter_prose
from hastings.workspace.files import Workspace
from hastings.workspace.prose_ledger import (
    LEDGER_FILENAME,
    load_ledger,
    record_chapter,
    save_ledger,
)
from hastings.workspace.story_store import EMPTY_REVISION, StoryStore
from hastings.workspace.writing_actions import ChapterCreateResult, create_chapter


@pytest.fixture
def env(tmp_path: Path) -> tuple[Workspace, StoryStore]:
    workspace = Workspace.create(tmp_path / "书")
    store = StoryStore(tmp_path / "state", workspace, format_version=5)
    workspace.chapter_policy = ChapterWritePolicy(store)
    return workspace, store


def _seed_chapter(
    store: StoryStore, workspace: Workspace, *, key: str
) -> ChapterCreateResult:
    """建一章（只有骨架正文），返回建章结果。"""
    return create_chapter(
        store,
        workspace,
        title="第一章",
        after_chapter_id=None,
        expected_plan_revision=EMPTY_REVISION,
        request_key=f"chapter:{key}",
    )


def _save(
    store: StoryStore,
    workspace: Workspace,
    chapter: ChapterCreateResult,
    *,
    key: str,
    text: str,
    expected_digest: str | None = None,
) -> ProseSaveResult:
    return save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text=text,
        expected_digest=expected_digest or chapter.prose_digest,
        request_key=key,
    )


def test_commit_records_chapter_in_ledger(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="a")
    ledger_path = workspace.root / LEDGER_FILENAME
    # 建章只建骨架，台账此时仍为空——「有正文」以正文入档为准。
    assert load_ledger(ledger_path).chapters == {}

    _save(store, workspace, chapter, key="prose:a", text="# 第一章\n\n林砚推开了旅馆的门。\n")

    ledger = load_ledger(ledger_path)
    assert set(ledger.chapters) == {1}
    assert ledger.chapters[1]  # 登记的是修订号，非空即可


def test_undo_removes_ledger_entry(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="b")
    saved = _save(
        store, workspace, chapter, key="prose:b", text="# 第一章\n\n林砚推开了旅馆的门。\n"
    )
    ledger_path = workspace.root / LEDGER_FILENAME
    assert set(load_ledger(ledger_path).chapters) == {1}

    store.undo(change_id=saved.committed_change_id, request_key="undo:b")

    assert load_ledger(ledger_path).chapters == {}


def test_recommit_overwrites_revision(env: tuple[Workspace, StoryStore]) -> None:
    """重复入档按覆盖处理，修订号取最新（契约第 2 节）。"""
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="c")
    ledger_path = workspace.root / LEDGER_FILENAME
    first = _save(store, workspace, chapter, key="prose:c1", text="# 第一章\n\n第一版。\n")
    recorded_first = load_ledger(ledger_path).chapters[1]

    second = _save(
        store,
        workspace,
        chapter,
        key="prose:c2",
        text="# 第一章\n\n第二版。\n",
        expected_digest=first.prose_digest,
    )

    assert first.committed_change_id != second.committed_change_id
    ledger = load_ledger(ledger_path)
    assert ledger.chapters == {1: second.plan_revision} or ledger.chapters[1] != recorded_first
    assert set(ledger.chapters) == {1}


def test_ledger_survives_recovery_replay(env: tuple[Workspace, StoryStore]) -> None:
    """恢复路径重放登记：同一修订再登记一次结果不变（幂等），撤销照常回退。"""
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="d")
    saved = _save(
        store, workspace, chapter, key="prose:d", text="# 第一章\n\n林砚推开了旅馆的门。\n"
    )
    ledger_path = workspace.root / LEDGER_FILENAME
    recorded = load_ledger(ledger_path).chapters[1]

    # 模拟恢复重放：同一章按同一修订再登记一次，台账不变。
    save_ledger(ledger_path, record_chapter(load_ledger(ledger_path), 1, recorded))
    assert load_ledger(ledger_path).chapters == {1: recorded}

    # 撤销路径照常回退。
    store.undo(change_id=saved.committed_change_id, request_key="undo:d")
    assert load_ledger(ledger_path).chapters == {}


def test_reverting_to_skeleton_removes_entry(env: tuple[Workspace, StoryStore]) -> None:
    """章节被改回只剩骨架时撤除登记——台账永远如实反映「哪几章有正文」。"""
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="e")
    ledger_path = workspace.root / LEDGER_FILENAME
    saved = _save(store, workspace, chapter, key="prose:e", text="# 第一章\n\n有正文。\n")
    assert set(load_ledger(ledger_path).chapters) == {1}

    _save(
        store,
        workspace,
        chapter,
        key="prose:e2",
        text="# 第一章\n",
        expected_digest=saved.prose_digest,
    )

    assert load_ledger(ledger_path).chapters == {}
