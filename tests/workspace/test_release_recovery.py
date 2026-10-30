"""1.0 S8：发布相关行为——备份恢复、重新登记、版本与可见名称。

只走真实文件与账本；不调用模型。备份按用户实际操作来做：复制作品目录与状态目录，
删掉原件后再从备份还原。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

from fastapi.testclient import TestClient

import hastings
from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.server.file_app import create_workspace_app
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clues import create_clue_identity
from hastings.workspace.files import Workspace
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root

ROOT = Path(__file__).resolve().parents[2]
FIRST = "正文/0001 遗书.md"
SECOND = "正文/0002 照片.md"


def _rmtree(path: Path) -> None:
    """Git 对象是只读的；Windows 上先去掉只读位再删除。"""

    def retry(func: object, target: str, error: BaseException) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)  # type: ignore[operator]

    shutil.rmtree(path, onexc=retry)


def _no_model() -> object:
    raise AssertionError("发布验收不应调用模型")


def _book(root: Path) -> Workspace:
    workspace = Workspace.create(root)
    workspace.write(FIRST, "# 遗书\n\n信封背面有字。\n", expected_digest=None)
    workspace.write(SECOND, "# 照片\n\n照片证明林岚看过背面。\n", expected_digest=None)
    return workspace


def _ledger(root: Path) -> tuple[ClueLedgerStore, str]:
    store = ClueLedgerStore(root)
    clue_id = str(
        create_clue_identity(
            store,
            title="公开的照片",
            request_key="release-clue",
            expected_revision=store.read().revision,
        )["clue_id"]
    )
    return store, clue_id


def _conversation(root: Path) -> str:
    store = ConversationStore(root)
    accepted = store.create_with_first_turn(
        text="先看看这条线索。", request_key="release-chat", turn_id="release-chat-turn"
    )
    store.append(
        accepted.conversation_id,
        {"type": "run_finished", "turn_id": "release-chat-turn", "status": "completed"},
    )
    return accepted.conversation_id


def test_backup_copy_restores_workspace_and_state(tmp_path: Path) -> None:
    """DS01：作品目录与应用状态一起备份、还原后，正文、账本与会话都能继续使用。"""
    registry = RecentWorkspaces(tmp_path / "state")
    client = TestClient(
        create_workspace_app(workspaces=registry, provider_factory=_no_model)  # type: ignore[arg-type]
    )
    root = tmp_path / "书架" / "遗书"
    root.parent.mkdir()
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    workspace_id = str(created["id"])
    _book(root)
    state_root = conversation_root(registry.root, workspace_id)
    store, clue_id = _ledger(state_root)
    conversation_id = _conversation(state_root)
    lineage = store.read().ledger
    assert lineage is not None
    before_lineage = lineage.workspace_lineage

    backup = tmp_path / "备份"
    shutil.copytree(root, backup / "作品")
    shutil.copytree(state_root, backup / "状态")

    # 模拟用户误删或换机：原件全部清掉，再从备份还原。
    _rmtree(root)
    _rmtree(state_root)
    shutil.copytree(backup / "作品", root)
    shutil.copytree(backup / "状态", state_root)

    reopened = TestClient(
        create_workspace_app(workspaces=RecentWorkspaces(registry.root), provider_factory=_no_model)  # type: ignore[arg-type]
    )
    files = reopened.get(f"/api/file-workspaces/{workspace_id}/files")
    assert files.status_code == 200, files.text
    chapters = {item["path"] for item in files.json()["chapters"]}
    assert chapters >= {FIRST, SECOND}

    ledger = ClueLedgerStore(state_root).read().ledger
    assert ledger is not None
    assert ledger.workspace_lineage == before_lineage
    assert clue_id in ledger.clues

    messages = reopened.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == 200, messages.text
    events = messages.json()["events"]
    assert any(event["type"] == "run_finished" for event in events)

    # 数据格式与发布版本相互独立：版本升到 1.0.0 不等于账本格式变化。
    raw = json.loads((state_root / "clue-ledger.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 3


def test_backup_restores_module_library_identity(tmp_path: Path) -> None:
    workspace = _book(tmp_path / "book")
    state = tmp_path / "state"
    _ledger(state)
    library = ModuleLibrary(tmp_path / "library", cache_file=tmp_path / "cache.json")
    created = library.create_module(
        kind="plot",
        name="遗书计划",
        entries=[EntryDraft("letter", "plot_plan", "背面", "原计划：第三章出现照片。")],
        request_key="backup-module",
    )
    module_id = str(created["module"]["module_id"])
    revision = str(created["revision"]["revision_id"])
    AssemblyService(library, workspace).bind(
        module_id=module_id, revision_id=revision, expected_digest=None, request_key="backup-bind"
    )
    text, _digest = library.storage.read_entry(module_id, revision, "letter")
    newer = library.publish(
        module_id=module_id,
        base_revision=revision,
        expected_head=revision,
        entries=[EntryDraft("letter", "plot_plan", "背面", "新计划：第六章出现照片。")],
        request_key="backup-new-head",
    )
    identity = library.library_id
    roots = [workspace.root, state, library.root]
    for index, directory in enumerate(roots):
        backup = tmp_path / f"backup-{index}"
        shutil.copytree(directory, backup)
        _rmtree(directory)
        shutil.copytree(backup, directory)
    restored_library = ModuleLibrary(library.root, cache_file=tmp_path / "restored-cache.json")
    assert restored_library.library_id == identity
    assert restored_library.storage.read_entry(module_id, revision, "letter")[0] == text
    assert restored_library.storage.read_entry(module_id, newer.revision_id, "letter")[0] != text


def test_prune_and_reregister_keeps_state(tmp_path: Path) -> None:
    """DS03：目录暂时不在时清掉登记，放回后重新登记仍指向同一份状态。"""
    registry = RecentWorkspaces(tmp_path / "state")
    client = TestClient(
        create_workspace_app(workspaces=registry, provider_factory=_no_model)  # type: ignore[arg-type]
    )
    root = tmp_path / "雪山旅馆"
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    workspace_id = str(created["id"])
    _book(root)
    state_root = conversation_root(registry.root, workspace_id)
    _, clue_id = _ledger(state_root)

    moved = tmp_path / "搬到别处"
    shutil.move(str(root), moved)
    pruned = client.delete("/api/file-workspaces/stale")
    assert pruned.status_code == 200, pruned.text
    assert pruned.json()["workspaces"] == []

    shutil.move(str(moved), root)
    again = client.post("/api/file-workspaces", json={"path": str(root)})
    assert again.status_code == 200, again.text
    assert again.json()["id"] == workspace_id

    detail = client.get(f"/api/file-workspaces/{workspace_id}/clues/{clue_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["clue_id"] == clue_id


def test_release_version_and_visible_name(tmp_path: Path) -> None:
    """主包、前端与锁文件同为 1.0.3；对外名称是线簿 / ClueLedger。"""
    assert hastings.__version__ == "1.0.3"

    def declares(path: Path, pattern: str) -> None:
        text = path.read_text(encoding="utf-8")
        assert pattern in text, f"{path} 缺少 {pattern}"

    declares(ROOT / "pyproject.toml", 'version = "1.0.3"')
    declares(ROOT / "frontend/package.json", '"version": "1.0.3"')
    declares(ROOT / "frontend/package-lock.json", '"version": "1.0.3"')
    assert 'name = "hastings-ai"\nversion = "1.0.3"' in (ROOT / "uv.lock").read_text(
        encoding="utf-8"
    )

    client = TestClient(
        create_workspace_app(
            workspaces=RecentWorkspaces(tmp_path / "state"),
            provider_factory=_no_model,  # type: ignore[arg-type]
        )
    )
    app = client.app  # type: ignore[attr-defined]
    assert app.version == "1.0.3"
    assert app.title == "线簿 ClueLedger"
