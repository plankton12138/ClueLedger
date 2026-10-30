"""1.0 S1：真实文件上的身份、原子发布和竞争，不调用模型。"""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.clue_models import (
    ClueCoverage,
    ClueEntry,
    ClueIdentity,
)
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clues import ClueStore, build_clues, publish_clues
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.state import RecentWorkspaces, workspace_state_root

FIXTURE = Path(__file__).parents[1] / "fixtures" / "clues" / "letter-case.json"
FIRST = "正文/0001 遗书.md"
SECOND = "正文/0002 照片.md"
THIRD = "正文/0003 回收.md"


def clue_count(store) -> int:
    """当前账本里的线索条数；资料视图已随资料页移除，测试直接读账本。"""
    ledger = store.read().ledger
    return 0 if ledger is None else len(ledger.clues)


def test_missing_ledger_read_does_not_initialize_or_read_old_files(book):
    workspace, store = book
    assert not store.root.exists()
    assert clue_count(store) == 0
    assert not store.root.exists()
    store.root.mkdir()
    for name in ("clues.json", "materials-coverage.json"):
        (store.root / name).write_text("broken abandoned data", encoding="utf-8")
    assert store.read().entries() == ()
    assert store.read().coverage() == ()
    assert clue_count(store) == 0
    assert not store.file.exists()
    assert not store.lock_file.exists()


def test_previous_ledger_format_is_abandoned_without_migration_or_aliases(book):
    workspace, store = book
    store.root.mkdir()
    old = {
        "schema_version": 1,
        "legacy_aliases": {"old": "clue_old"},
        "clues": {"clue_old": {"title": "旧线索"}},
    }
    store.file.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    before = store.file.read_bytes()
    assert store.read().revision == "empty"
    assert clue_count(store) == 0
    assert store.file.read_bytes() == before
    result = publish(workspace, store, (clue(workspace),))
    ledger = store.read().ledger
    assert ledger is not None and ledger.schema_version == 3
    assert list(ledger.clues) == result["clue_ids"] and "clue_old" not in ledger.clues
    assert "legacy_aliases" not in ledger.model_dump()
    assert not (store.root / "clue-migration-backups").exists()


@pytest.fixture
def book(tmp_path: Path) -> tuple[Workspace, ClueLedgerStore]:
    workspace = Workspace.create(tmp_path / "遗书")
    for item in json.loads(FIXTURE.read_text(encoding="utf-8"))["chapters"]:
        workspace.write(item["path"], item["text"], expected_digest=None)
    return workspace, ClueLedgerStore(tmp_path / "state")


def proof(
    workspace: Workspace, path: str = FIRST, quote: str = "背面还写着一句话"
) -> ObservedEvidence:
    view = workspace.read(path)
    start = view.text.index(quote)
    return ObservedEvidence(path, view.digest, start, start + len(quote), quote)


def clue(workspace: Workspace, title: str = "遗书背面的字", **kwargs: Any) -> ClueEntry:
    return ClueEntry(title, 1, None, "open", "", proof(workspace), source_chapter=1, **kwargs)


def coverage(workspace: Workspace, path: str = FIRST, outcome: str = "found") -> ClueCoverage:
    return ClueCoverage(
        path=path,
        digest=workspace.read(path).digest,
        outcome=outcome,  # type: ignore[arg-type]
        task_id="task",
        at="2026-10-27T10:00:00+08:00",
        chapter=1 if path == FIRST else 2,
    )


def publish(
    workspace: Workspace,
    store: ClueLedgerStore,
    entries: tuple[ClueEntry, ...],
    *,
    path: str = FIRST,
    outcome: str = "found",
    key: str = "first",
    **kwargs: Any,
) -> dict[str, Any]:
    return store.publish(
        entries,
        source_chapter=1 if path == FIRST else 2,
        expected_revision=kwargs.pop("expected_revision", store.read().revision),
        request_key=key,
        coverage=coverage(workspace, path, outcome),
        **kwargs,
    )


def test_new_same_title_never_implies_same_identity(book):
    workspace, store = book
    result = publish(workspace, store, (clue(workspace), clue(workspace)))
    first, second = result["clue_ids"]
    assert first != second
    assert clue_count(store) == 2
    before = store.file.read_bytes()
    with pytest.raises(WorkspaceError, match="CLUE_IDENTITY_CONFLICT"):
        publish(workspace, store, (clue(workspace),), key="ambiguous")
    assert store.file.read_bytes() == before
    other = publish(workspace, store, (clue(workspace, new_identity=True),), key="explicit-new")
    assert other["clue_ids"][0] not in {first, second}
    assert len(store.read().ledger.clues) == 3


def test_rename_and_rebuild_keep_identity_and_unrelated_revision(book):
    workspace, store = book
    result = publish(workspace, store, (clue(workspace), clue(workspace, "钥匙")))
    first, other = result["clue_ids"]
    unrelated_revision = store.read().ledger.clues[other].clue_revision
    existing = store.read().entries()
    publish(
        workspace, store, (replace(existing[0], clue="林岚知道背面内容"), existing[1]), key="rename"
    )
    assert store.read().entries()[0].clue_id == first
    before = store.read().ledger.clues[first].clue_revision
    # 另一章的贡献不应改变未涉及条目的版本。
    publish(
        workspace,
        store,
        (replace(store.read().entries()[0], source_chapter=2),),
        path=SECOND,
        key="chapter-two",
    )
    assert store.read().ledger.clues[first].clue_revision != before
    # 上次同章重建涉及 other，所以从该次提交后冻结比较值。
    assert unrelated_revision != store.read().ledger.clues[other].clue_revision
    frozen_other = store.read().ledger.clues[other].clue_revision
    publish(
        workspace,
        store,
        (replace(store.read().entries()[-1], supports="仍有其他解释"),),
        path=SECOND,
        key="rebuild-two",
    )
    assert store.read().ledger.clues[other].clue_revision == frozen_other
    assert len(store.read().ledger.contributions) == 3


def test_empty_is_atomic_and_retains_identity_and_other_chapter(book):
    workspace, store = book
    first = publish(workspace, store, (clue(workspace),))["clue_ids"][0]
    second_entry = replace(clue(workspace, "另一线索"), source_chapter=2)
    publish(workspace, store, (second_entry,), path=SECOND, key="second")
    publish(workspace, store, (), outcome="empty", key="empty")
    ledger = store.read().ledger
    assert first in ledger.clues
    assert ledger.coverage[FIRST].outcome == "empty"
    assert {row.content.source_chapter for row in ledger.contributions.values()} == {2}


def test_insufficient_preserves_old_contribution_and_clue_revision(book):
    workspace, store = book
    publish(workspace, store, (clue(workspace),))
    before = store.read().ledger
    publish(workspace, store, (), outcome="insufficient", key="insufficient")
    after = store.read().ledger
    assert after.contributions == before.contributions
    assert after.clues == before.clues
    assert after.coverage[FIRST].outcome == "insufficient"


@pytest.mark.parametrize("change", ["missing_field", "bad_reference", "boolean_version"])
def test_damaged_current_ledger_is_not_overwritten(book, change):
    workspace, store = book
    publish(workspace, store, (clue(workspace),))
    data = json.loads(store.file.read_text(encoding="utf-8"))
    if change == "missing_field":
        del data["clues"]
    elif change == "bad_reference":
        next(iter(data["contributions"].values()))["clue_id"] = "missing"
    else:
        data["schema_version"] = True
    store.file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    before = store.file.read_bytes()
    with pytest.raises(WorkspaceError, match="CLUE_STATE_FAILED"):
        store.read()
    with pytest.raises(WorkspaceError, match="CLUE_STATE_FAILED"):
        publish(workspace, store, (), outcome="empty", key="damaged")
    assert store.file.read_bytes() == before


def test_unknown_or_unseen_identity_cannot_replace_known_records(book):
    workspace, store = book
    first = publish(workspace, store, (clue(workspace),))["clue_ids"][0]
    before = store.file.read_bytes()
    for invalid_id, allowed in (("foreign", None), (first, [])):
        with pytest.raises(WorkspaceError, match="CLUE_IDENTITY_CONFLICT"):
            publish(
                workspace,
                store,
                (clue(workspace, clue_id=invalid_id),),
                key="invalid",
                allowed_ids=allowed,
            )
        assert store.file.read_bytes() == before


def test_retry_returns_original_result_and_changed_payload_conflicts(book):
    workspace, store = book
    original = store.read().revision
    result = publish(workspace, store, (clue(workspace),), expected_revision=original)
    before = store.file.read_bytes()
    assert publish(workspace, store, (clue(workspace),), expected_revision=original) == result
    assert store.file.read_bytes() == before
    with pytest.raises(WorkspaceError, match="CLUE_REVISION_CONFLICT"):
        publish(workspace, store, (clue(workspace, "新内容"),), expected_revision=original)
    assert store.file.read_bytes() == before


@pytest.mark.parametrize("existing", [False, True])
def test_replace_failure_retains_current_state_and_retry_is_possible(book, monkeypatch, existing):
    workspace, store = book
    if existing:
        publish(workspace, store, (clue(workspace),))
        old = {store.file.name: store.file.read_bytes()}
        entries = store.read().entries()
    else:
        old = {}
        entries = (clue(workspace),)
    expected = store.read().revision
    real_replace = os.replace

    def denied(source, target):
        if Path(target) == store.file:
            raise PermissionError("injected write failure")
        return real_replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr("hastings.workspace.atomic_io.os.replace", denied)
        with pytest.raises(WorkspaceError, match="CLUE_WRITE_FAILED"):
            publish(workspace, store, entries, key="failure", expected_revision=expected)
    for name, data in old.items():
        assert (store.root / name).read_bytes() == data
    assert not list(store.root.rglob("*.tmp"))
    result = publish(workspace, store, entries, key="failure", expected_revision=expected)
    assert result["published"] == 1


@pytest.mark.parametrize(
    "name,payload",
    [
        ("clue-ledger.json", "{"),
        ("clue-ledger.json", '{"schema_version":99,"recipe_version":1}'),
    ],
)
def test_corrupt_or_unknown_state_is_never_empty_or_overwritten(book, name, payload):
    workspace, store = book
    store.root.mkdir()
    file = store.root / name
    file.write_text(payload, encoding="utf-8")
    before = file.read_bytes()
    with pytest.raises(WorkspaceError, match="CLUE_STATE_FAILED"):
        store.read()
    with pytest.raises(WorkspaceError, match="CLUE_STATE_FAILED"):
        publish(workspace, store, (clue(workspace),))
    assert file.read_bytes() == before


def _compete(root: str, revision: str, barrier: Any, queue: Any, number: int) -> None:
    store = ClueLedgerStore(Path(root))
    barrier.wait(timeout=15)

    def apply(ledger):
        identity = f"clue_{number}"
        ledger.clues[identity] = ClueIdentity(
            clue_id=identity,
            title=str(number),
            clue_revision="revision",
            created_at="date",
            created_by="competition",
        )
        return {"winner": number}

    try:
        store.mutate(
            expected_revision=revision,
            request_key=f"process-{number}",
            kind="competition",
            payload={"number": number},
            apply=apply,
        )
        queue.put("committed")
    except WorkspaceError as exc:
        queue.put(exc.code)


def test_two_processes_using_same_revision_have_exactly_one_commit(book):
    _, store = book
    revision = store.read().revision
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [
        context.Process(target=_compete, args=(str(store.root), revision, barrier, queue, i))
        for i in range(2)
    ]
    try:
        for process in processes:
            process.start()
        results = [queue.get(timeout=30), queue.get(timeout=30)]
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0
        assert sorted(results) == ["CLUE_REVISION_CONFLICT", "committed"]
        assert len(store.read().ledger.clues) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        queue.close()


def test_same_task_can_publish_next_chapter_but_external_change_conflicts(book):
    workspace, store = book
    base = store.read().revision
    result = publish(workspace, store, (clue(workspace),), expected_revision=base, task_id="task")
    entry = replace(store.read().entries()[0], source_chapter=2, supports="另一解释")
    publish(
        workspace,
        store,
        (entry,),
        path=SECOND,
        key="second",
        expected_revision=base,
        task_id="task",
    )
    revision = store.read().revision
    store.mutate(
        expected_revision=revision,
        request_key="outside",
        kind="outside",
        payload={},
        apply=lambda ledger: {},
    )
    with pytest.raises(WorkspaceError, match="CLUE_REVISION_CONFLICT"):
        publish(
            workspace,
            store,
            (),
            path=SECOND,
            outcome="empty",
            key="third",
            expected_revision=base,
            task_id="task",
        )
    # 已提交请求的重放不受其后外部写入影响。
    assert (
        publish(workspace, store, (clue(workspace),), expected_revision=base, task_id="task")
        == result
    )


def test_changed_contribution_chapter_invalidates_even_if_planting_still_matches(book):
    workspace, store = book
    identity = publish(workspace, store, (clue(workspace),))["clue_ids"][0]
    entry = replace(
        clue(workspace, clue_id=identity), source_chapter=2, supports="照片提供另一种来源"
    )
    publish(workspace, store, (entry,), path=SECOND, key="second")
    assert len(build_clues(workspace, ClueStore(store.root), as_of_chapter=2).open) == 1
    old = workspace.read(SECOND)
    workspace.write(SECOND, old.text + "\n这一处说明已修改。", expected_digest=old.digest)
    # 原埋设处没变，第二章整理所得说明仍然需要失效，不能冒充当前版本。
    assert ClueStore(store.root).status(workspace, store.read().entries()[-1]) == "stale"
    assert build_clues(workspace, ClueStore(store.root), as_of_chapter=2).open == ()
    assert len(build_clues(workspace, ClueStore(store.root), as_of_chapter=1).open) == 1


def test_stop_or_source_change_before_commit_does_not_publish(book):
    workspace, store = book
    entry = clue(workspace)
    initial = store.read().revision

    def stopped():
        raise WorkspaceError(
            code="CLUE_STOPPED_CONFLICT", where="clues.publish", why="已停止", fix="重新发起"
        )

    with pytest.raises(WorkspaceError, match="CLUE_STOPPED_CONFLICT"):
        publish(workspace, store, (entry,), preflight=stopped)
    assert not store.file.exists()
    changed = False

    def change_source():
        nonlocal changed
        if not changed:
            current = workspace.read(FIRST)
            workspace.write(FIRST, current.text + "\n🔑", expected_digest=current.digest)
            changed = True

    with pytest.raises(WorkspaceError, match="STATE_SOURCE_STALE_CONFLICT"):
        publish_clues(
            workspace,
            ClueStore(store.root),
            (entry,),
            source_chapter=1,
            expected_revision=initial,
            preflight=change_source,
        )
    assert not store.file.exists()


def test_early_view_never_uses_future_contribution_or_resolution(book):
    workspace, store = book
    first = publish(workspace, store, (clue(workspace),))["clue_ids"][0]
    future = replace(
        clue(workspace, clue_id=first),
        source_chapter=2,
        supports="照片已解释来源",
        status="resolved",
        resolution_chapter=3,
        resolution_evidence=proof(workspace, THIRD, "读过那张公开照片"),
    )
    publish(workspace, store, (future,), path=SECOND, key="future")
    early = build_clues(workspace, ClueStore(store.root), as_of_chapter=1)
    # 后章才有的解释、状态与回收证据都不能出现在前面的章节视图里。
    assert early.open[0].clue != "已经证明的真相"
    assert early.open[0].supports == ""
    assert early.open[0].status == "open"
    assert early.closed == ()


def test_rename_backup_restore_and_unbind_keep_current_lineage(tmp_path):
    workspace = Workspace.create(tmp_path / "原名")
    for item in json.loads(FIXTURE.read_text(encoding="utf-8"))["chapters"]:
        workspace.write(item["path"], item["text"], expected_digest=None)
    registry = RecentWorkspaces(tmp_path / "app-state")
    binding = registry.bind(workspace.root)
    store = ClueLedgerStore(workspace_state_root(registry.root, binding.id))
    publish(workspace, store, (clue(workspace),))
    before = store.read().ledger
    backup = tmp_path / "backup"
    shutil.copytree(store.root, backup)
    renamed = registry.rename(binding.id, "新名")
    moved = ClueLedgerStore(workspace_state_root(registry.root, renamed.id))
    assert moved.read().ledger == before
    restored = ClueLedgerStore(backup)
    assert restored.read().ledger == before
    registry.unbind(renamed.id)
    assert moved.read().ledger == before


def test_ledger_write_takes_story_lock_before_ledger_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager

    import hastings.workspace.clue_store as clue_store_module

    entered: list[str] = []

    @contextmanager
    def recording_lock(path: Path):
        entered.append(Path(path).name)
        yield

    monkeypatch.setattr(clue_store_module, "locked", recording_lock)
    store = ClueLedgerStore(tmp_path / "state")

    def apply(ledger: Any) -> dict[str, str]:
        return {"ok": "1"}

    store.mutate(
        expected_revision="empty",
        request_key="lock-order",
        kind="test",
        payload={},
        apply=apply,
    )
    assert entered == [".story.lock", ".clue-ledger.lock"]
