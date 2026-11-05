"""v1.3 P0：章级线索登记随事务自动维护（事实层契约）。

与 ``test_ledger_transactions`` 同一契约形状：登记只由事务层写（随章提交）、
撤销与退回骨架时撤除、恢复路径重放同一调用（幂等）、空响在提交门拦截。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hastings.workspace.chapter_clues import (
    ClueEntry,
    ResolvesRef,
    aggregate_clues,
    anchor_paragraph,
    clues_path,
    read_chapter_clues,
)
from hastings.workspace.chapter_write_policy import ChapterWritePolicy
from hastings.workspace.chapter_writing import ProseSaveResult, save_chapter_prose
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_store import EMPTY_REVISION, StoryStore
from hastings.workspace.writing_actions import ChapterCreateResult, create_chapter

CH1 = "# 第一章\n\n箱扣上的漆掉了一块，露出底下的铁色。\n老魏没再说话。\n"


def _planted(id_: str = "CL-01", note: str = "箱扣缺漆，来历未明") -> ClueEntry:
    return ClueEntry(
        id=id_,
        kind="伏笔",
        status="planted",
        origin="planned",
        note=note,
        anchor=anchor_paragraph("箱扣上的漆掉了一块，露出底下的铁色。", "r1"),
    )


def _resolved(id_: str, planted_in: int, note: str) -> ClueEntry:
    return ClueEntry(
        id=id_,
        kind="伏笔",
        status="resolved",
        origin="planned",
        note=note,
        resolves=ResolvesRef(clue_id="CL-01", planted_in=planted_in),
    )


@pytest.fixture
def env(tmp_path: Path) -> tuple[Workspace, StoryStore]:
    workspace = Workspace.create(tmp_path / "书")
    store = StoryStore(tmp_path / "state", workspace, format_version=5)
    workspace.chapter_policy = ChapterWritePolicy(store)
    return workspace, store


def _seed_chapter(
    store: StoryStore,
    workspace: Workspace,
    *,
    key: str,
    title: str = "第一章",
) -> ChapterCreateResult:
    variant = store.read().variant("main")
    expected = variant.revision if variant is not None else EMPTY_REVISION
    return create_chapter(
        store,
        workspace,
        title=title,
        after_chapter_id=None,
        expected_plan_revision=expected,
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
    clue_entries: list[ClueEntry] | None = None,
) -> ProseSaveResult:
    return save_chapter_prose(
        store,
        workspace,
        chapter_id=chapter.chapter_id,
        text=text,
        expected_digest=expected_digest or chapter.prose_digest,
        request_key=key,
        clue_entries=clue_entries,
    )


def test_commit_writes_chapter_clues(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="a")
    # 建章只有骨架，无登记提交——线索文件不存在。
    assert not clues_path(workspace.root, 1).exists()

    _save(
        store,
        workspace,
        chapter,
        key="prose:a",
        text=CH1,
        clue_entries=[_planted()],
    )

    document = read_chapter_clues(workspace.root, 1)
    assert document is not None
    assert document.chapter == 1
    assert [entry.id for entry in document.entries] == ["CL-01"]
    assert document.entries[0].anchor is not None


def test_commit_without_prose_rejected(env: tuple[Workspace, StoryStore]) -> None:
    """骨架提交不产生线索条目；带登记的骨架提交是确定性矛盾，直接拒绝。"""
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="b")
    skeleton_variant = "# 第一章  \n"  # 与建章骨架不同摘要，但仍是骨架
    with pytest.raises(WorkspaceError) as excinfo:
        _save(
            store,
            workspace,
            chapter,
            key="prose:b",
            text=skeleton_variant,
            clue_entries=[_planted()],
        )
    assert excinfo.value.code == "CLUE_COMMIT_WITHOUT_PROSE"
    assert not clues_path(workspace.root, 1).exists()


def test_invalid_entries_rejected_before_persist(env: tuple[Workspace, StoryStore]) -> None:
    """非法状态在提交门拒绝：整笔提交不落盘，正文也不写入。"""
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="c")
    bad = ClueEntry(id="CL-01", kind="伏笔", status="planted", origin="planned")
    before = store.read().revision
    with pytest.raises(WorkspaceError) as excinfo:
        _save(store, workspace, chapter, key="prose:c", text=CH1, clue_entries=[bad])
    assert excinfo.value.code == "CLUE_ANCHOR_REQUIRED"
    assert store.read().revision == before
    assert not clues_path(workspace.root, 1).exists()


def test_commit_gate_blocks_orphan_resolution(env: tuple[Workspace, StoryStore]) -> None:
    """空响是确定性矛盾：回收没有匹配章号埋设记录的线索，提交被拦。"""
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="d")
    before = store.read().revision
    with pytest.raises(WorkspaceError) as excinfo:
        _save(
            store,
            workspace,
            chapter,
            key="prose:d",
            text=CH1,
            clue_entries=[_resolved("CL-77", planted_in=1, note="无中生有的回收")],
        )
    assert excinfo.value.code == "CLUE_ORPHAN_RESOLUTION"
    assert store.read().revision == before
    assert not clues_path(workspace.root, 1).exists()


def test_cross_chapter_resolution_merges(env: tuple[Workspace, StoryStore]) -> None:
    """第 1 章埋、第 2 章收：同 id 状态更新，聚合视图归并出 resolved。"""
    workspace, store = env
    first = _seed_chapter(store, workspace, key="e1")
    _save(store, workspace, first, key="prose:e1", text=CH1, clue_entries=[_planted()])
    second = _seed_chapter(store, workspace, key="e2", title="第二章")
    _save(
        store,
        workspace,
        second,
        key="prose:e2",
        text="# 第二章\n\n老魏终于开口了。\n",
        clue_entries=[_resolved("CL-01", planted_in=1, note="老魏说出箱子来历")],
    )
    aggregate = aggregate_clues(workspace.root, [1, 2])
    assert aggregate.states["CL-01"].status == "resolved"
    assert aggregate.states["CL-01"].planted_in == 1
    assert aggregate.states["CL-01"].resolved_in == 2


def test_undo_removes_clue_file(env: tuple[Workspace, StoryStore]) -> None:
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="f")
    saved = _save(store, workspace, chapter, key="prose:f", text=CH1, clue_entries=[_planted()])
    assert clues_path(workspace.root, 1).exists()

    store.undo(change_id=saved.committed_change_id, request_key="undo:f")

    assert not clues_path(workspace.root, 1).exists()


def test_revert_to_skeleton_removes_clue_file(env: tuple[Workspace, StoryStore]) -> None:
    """章节被改回只剩骨架时撤除线索文件——锚指向的正文没了，登记不假装还在。"""
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="g")
    saved = _save(store, workspace, chapter, key="prose:g", text=CH1, clue_entries=[_planted()])
    assert clues_path(workspace.root, 1).exists()

    _save(
        store,
        workspace,
        chapter,
        key="prose:g2",
        text="# 第一章\n",
        expected_digest=saved.prose_digest,
    )

    assert not clues_path(workspace.root, 1).exists()


def test_resave_without_entries_keeps_file(env: tuple[Workspace, StoryStore]) -> None:
    """再次入档但未带登记：文件保留（锚可能失联，由报告提示，不静默删除）。"""
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="h")
    saved = _save(store, workspace, chapter, key="prose:h", text=CH1, clue_entries=[_planted()])
    before = clues_path(workspace.root, 1).read_bytes()

    _save(
        store,
        workspace,
        chapter,
        key="prose:h2",
        text=CH1 + "他点了灯。\n",
        expected_digest=saved.prose_digest,
        clue_entries=None,
    )

    assert clues_path(workspace.root, 1).read_bytes() == before


def test_recovery_replays_clue_write(
    env: tuple[Workspace, StoryStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """崩溃恢复重放：线索落盘在事务末尾，中断后由恢复路径补完（幂等）。"""
    workspace, store = env
    chapter = _seed_chapter(store, workspace, key="i")
    original = store._apply_clues
    calls: list[object] = []

    def crashing_apply(rows: object, clues: object, revision: object) -> None:
        calls.append(clues)
        if clues is not None and len(calls) == 1:
            raise OSError("simulated crash after decision")
        original(rows, clues, revision)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "_apply_clues", crashing_apply)
    with pytest.raises(WorkspaceError) as excinfo:
        _save(store, workspace, chapter, key="prose:i", text=CH1, clue_entries=[_planted()])
    assert excinfo.value.code == "STORY_WRITE_FAILED"
    # 正文已应用、线索未落盘：崩溃点后的中间态。
    assert not clues_path(workspace.root, 1).exists()

    monkeypatch.setattr(store, "_apply_clues", original)
    store.read()  # 触发确定性恢复：有决定就补完剩余步骤

    document = read_chapter_clues(workspace.root, 1)
    assert document is not None
    assert document.entries[0].id == "CL-01"
    # 幂等：重放后的文件内容与决定一致。
    raw = json.loads(clues_path(workspace.root, 1).read_text(encoding="utf-8"))
    assert raw["revision"] == document.revision
