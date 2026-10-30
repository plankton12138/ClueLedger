"""联合提交的真实文件、恢复、幂等与撤销边界。"""

import json
from pathlib import Path

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_models import (
    AddNode,
    ProsePatch,
    ReadDependency,
    StoryChangeSet,
    StoryNode,
)
from hastings.workspace.story_store import StoryStore


def setup(tmp_path: Path) -> tuple[StoryStore, Workspace, StoryChangeSet]:
    workspace = Workspace.create(tmp_path / "book")
    view = workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n甲😀乙。", expected_digest=None)
    store = StoryStore(tmp_path / "state", workspace)
    change = StoryChangeSet(
        id="joint",
        base_revision="empty",
        operations=[AddNode(node=StoryNode(id="n1", title="雨夜", action="抵达"))],
        prose_patches=[
            ProsePatch(
                path=view.path,
                base_digest=view.digest,
                start=6,
                end=8,
                expected="甲😀",
                replacement="丙😀",
            )
        ],
        read_dependencies=[ReadDependency(kind="prose", id=view.path, revision=view.digest)],
    )
    return store, workspace, change


def submit(store: StoryStore, change: StoryChangeSet) -> object:
    return store.commit_change(variant_id="main", change=change, request_key="joint")


def test_joint_commit_retry_and_undo(tmp_path: Path) -> None:
    store, workspace, change = setup(tmp_path)
    receipt = submit(store, change)
    assert "丙😀乙" in workspace.read(change.prose_patches[0].path).text
    assert submit(store, change) == receipt
    workspace.write("正文/0002 次日.md", "# 次日\n\n无关正文。", expected_digest=None)
    store.undo(change_id="joint", request_key="undo")
    assert "甲😀乙" in workspace.read(change.prose_patches[0].path).text
    assert "n1" not in store.require_variant("main").nodes
    assert "无关正文" in workspace.read("正文/0002 次日.md").text


@pytest.mark.parametrize("point", ["prose", "head", "snapshot", "log"])
def test_decided_joint_recovers_each_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
) -> None:
    store, workspace, change = setup(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("injected")

    with monkeypatch.context() as patch:
        if point == "prose":
            patch.setattr(workspace, "write", fail)
        elif point == "head":
            import hastings.workspace.story_store as module

            original = module.atomic_write_json

            def fail_head(path: Path, *args: object, **kwargs: object) -> None:
                if path == store.file:
                    raise OSError("injected")
                original(path, *args, **kwargs)  # type: ignore[arg-type]

            patch.setattr(module, "atomic_write_json", fail_head)
        else:
            patch.setattr(store, "_write_snapshot" if point == "snapshot" else "_append_log", fail)
        with pytest.raises(WorkspaceError):
            submit(store, change)
    recovered = StoryStore(store.root, workspace)
    assert "n1" in recovered.require_variant("main").nodes
    assert "丙😀乙" in workspace.read(change.prose_patches[0].path).text
    assert submit(recovered, change)
    assert not recovered.pending.exists()


def test_recovery_preserves_external_prose_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, workspace, change = setup(tmp_path)
    with monkeypatch.context() as patch:

        def fail(*args: object, **kwargs: object) -> None:
            raise OSError("injected")

        patch.setattr(workspace, "write", fail)
        with pytest.raises(WorkspaceError):
            submit(store, change)
    path = change.prose_patches[0].path
    workspace.write(path, "# 雨夜\n\n外部编辑。", expected_digest=workspace.read(path).digest)
    with pytest.raises(WorkspaceError):
        store.read()
    assert "外部编辑" in workspace.read(path).text
    assert store.pending.exists()


def test_dependency_conflict_writes_nothing(tmp_path: Path) -> None:
    store, workspace, change = setup(tmp_path)
    change.read_dependencies[0].revision = "stale"
    with pytest.raises(WorkspaceError, match="STORY_DEPENDENCY_CONFLICT"):
        submit(store, change)
    assert not store.file.exists()
    assert "甲😀乙" in workspace.read(change.prose_patches[0].path).text


def test_removed_pending_prose_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, workspace, change = setup(tmp_path)
    with monkeypatch.context() as patch:

        def fail(*args: object, **kwargs: object) -> None:
            raise OSError("injected")

        patch.setattr(workspace, "write", fail)
        with pytest.raises(WorkspaceError):
            submit(store, change)
    payload = json.loads(store.pending.read_text(encoding="utf-8"))
    payload.pop("prose")
    store.pending.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorkspaceError, match="STORY_STATE_FAILED"):
        store.read()
    assert not store.file.exists()


def test_undo_refuses_later_edit_to_same_file(tmp_path: Path) -> None:
    store, workspace, change = setup(tmp_path)
    submit(store, change)
    path = change.prose_patches[0].path
    workspace.write(path, "# 雨夜\n\n作者后改。", expected_digest=workspace.read(path).digest)
    revision = store.read().revision
    with pytest.raises(WorkspaceError):
        store.undo(change_id="joint", request_key="undo")
    assert store.read().revision == revision
    assert "作者后改" in workspace.read(path).text


def test_recovery_after_second_prose_file_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, workspace, change = setup(tmp_path)
    view = workspace.write("正文/0002 次日.md", "# 次日\n\n原文。", expected_digest=None)
    change.prose_patches.append(
        ProsePatch(
            path=view.path,
            base_digest=view.digest,
            start=6,
            end=8,
            expected="原文",
            replacement="新文",
        )
    )
    original = workspace.write

    def fail_second(path: str, text: str, *, expected_digest: str | None) -> object:
        if path == view.path:
            raise OSError("injected second file")
        return original(path, text, expected_digest=expected_digest)

    with monkeypatch.context() as patch:
        patch.setattr(workspace, "write", fail_second)
        with pytest.raises(WorkspaceError):
            submit(store, change)
    assert not store.file.exists()
    assert "丙😀乙" in workspace.read(change.prose_patches[0].path).text
    assert "原文" in workspace.read(view.path).text
    store.read()
    assert "新文" in workspace.read(view.path).text
    store.undo(change_id="joint", request_key="undo")
    assert "原文" in workspace.read(view.path).text


def test_joint_submit_through_http(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from hastings.server.file_app import create_workspace_app
    from hastings.workspace.state import RecentWorkspaces

    _, workspace, change = setup(tmp_path)
    workspaces = RecentWorkspaces(tmp_path / "app")
    book = workspaces.bind(workspace.root)

    def no_model() -> None:
        raise AssertionError("no model")

    with TestClient(
        create_workspace_app(workspaces=workspaces, provider_factory=no_model)
    ) as client:
        endpoint = f"/api/file-workspaces/{book.id}/story/changes"
        payload = {
            "variant_id": "main",
            "expected_revision": "empty",
            "request_key": "joint",
            "operations": [op.model_dump(mode="json") for op in change.operations],
            "prose_patches": [p.model_dump(mode="json") for p in change.prose_patches],
            "read_dependencies": [d.model_dump(mode="json") for d in change.read_dependencies],
        }
        first = client.post(endpoint, json=payload)
        assert first.status_code == 201, first.text
        assert client.post(endpoint, json=payload).json() == first.json()
        assert "丙😀乙" in workspace.read(change.prose_patches[0].path).text
        assert (
            client.post(
                endpoint, json={**payload, "operations": [], "prose_patches": []}
            ).status_code
            == 422
        )


def test_reopening_workspace_recovers_joint_before_file_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hastings.workspace.state import RecentWorkspaces, conversation_root

    _, workspace, change = setup(tmp_path)
    workspaces = RecentWorkspaces(tmp_path / "app")
    book = workspaces.bind(workspace.root)
    store = StoryStore(conversation_root(workspaces.root, book.id), workspace)
    with monkeypatch.context() as patch:

        def fail(*args: object, **kwargs: object) -> None:
            raise OSError("injected")

        patch.setattr(workspace, "write", fail)
        with pytest.raises(WorkspaceError):
            submit(store, change)
    opened = workspaces.workspace(book.id)
    assert "丙😀乙" in opened.read(change.prose_patches[0].path).text
    assert not store.pending.exists()


def test_create_chapter_and_undo_removes_only_created_file(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    store = StoryStore(tmp_path / "state", workspace)
    change = StoryChangeSet(
        id="new-scene",
        base_revision="empty",
        prose_patches=[
            ProsePatch(
                path="正文/0001 雨夜.md",
                base_digest="absent",
                start=0,
                end=0,
                expected="",
                replacement="# 雨夜\n\n雨停了。",
            )
        ],
    )
    receipt = submit(store, change)
    assert workspace.read("正文/0001 雨夜.md").text.endswith("雨停了。")
    assert receipt
    store.undo(change_id="new-scene", request_key="undo-new")
    with pytest.raises(WorkspaceError, match="FILE_NOT_FOUND"):
        workspace.read("正文/0001 雨夜.md")


def test_recover_new_chapter_after_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = Workspace.create(tmp_path / "book")
    store = StoryStore(tmp_path / "state", workspace)
    change = StoryChangeSet(
        id="new-scene",
        base_revision="empty",
        prose_patches=[
            ProsePatch(
                path="正文/0001 雨夜.md",
                base_digest="absent",
                start=0,
                end=0,
                expected="",
                replacement="# 雨夜\n\n雨停了。",
            )
        ],
    )
    with monkeypatch.context() as patch:

        def fail(*args: object, **kwargs: object) -> None:
            raise OSError("injected")

        patch.setattr(workspace, "write", fail)
        with pytest.raises(WorkspaceError):
            submit(store, change)
    store.read()
    assert workspace.read("正文/0001 雨夜.md").text.endswith("雨停了。")


# -- 备选正文副本与读取依赖 -------------------------------------------------

MAIN_TEXT = "# 雨夜\n\n甲😀乙。\n\n末尾。"


def variant_setup(
    tmp_path: Path,
) -> tuple[Workspace, StoryStore, str, str, str]:
    """建立主方案与一个备选；返回作品、存储、正文路径、主方案摘要与备选标识。"""
    workspace = Workspace.create(tmp_path / "book")
    path = "正文/0001 雨夜.md"
    view = workspace.write(path, MAIN_TEXT, expected_digest=None)
    store = StoryStore(tmp_path / "state", workspace)
    seed = store.commit_change(
        variant_id="main",
        request_key="seed",
        change=StoryChangeSet(
            id="seed",
            base_revision="empty",
            operations=[AddNode(node=StoryNode(id="n1", title="雨夜", action="抵达"))],
        ),
    )
    fork = store.create_variant(
        name="备选",
        source_variant_id="main",
        source_revision=seed.revision,
        request_key="fork",
    )
    return workspace, store, path, view.digest, str(fork.result["variant_id"])


def test_variant_prose_written_to_managed_copy(tmp_path: Path) -> None:
    workspace, store, path, digest, variant_id = variant_setup(tmp_path)
    # 创建备选时已复制基线：正文来自受管副本，而不是继续沿用主方案。
    assert store.prose_state(path, variant_id) == (digest, MAIN_TEXT, False)
    revision = store.require_variant(variant_id).revision
    change = StoryChangeSet(
        id="variant-prose",
        base_revision=revision,
        prose_patches=[
            ProsePatch(
                path=path, base_digest=digest, start=6, end=8,
                expected="甲😀", replacement="丙😀",
            )
        ],
    )
    store.commit_change(variant_id=variant_id, change=change, request_key="variant-prose")
    assert workspace.read(path).text == MAIN_TEXT
    new_digest, new_text, inherited = store.prose_state(path, variant_id)
    assert "丙😀乙" in new_text
    assert new_digest != digest
    assert inherited is False
    copy = store.dir / "variants" / variant_id / "prose" / "正文" / "0001 雨夜.md"
    assert copy.exists()
    store.undo(change_id="variant-prose", request_key="undo-variant")
    assert store.read_prose(path, variant_id)[1] == MAIN_TEXT
    assert workspace.read(path).text == MAIN_TEXT


def test_variant_prose_delete_leaves_main_file(tmp_path: Path) -> None:
    workspace, store, path, digest, variant_id = variant_setup(tmp_path)
    revision = store.require_variant(variant_id).revision
    change = StoryChangeSet(
        id="variant-delete",
        base_revision=revision,
        prose_patches=[
            ProsePatch(
                path=path, base_digest=digest, start=0, end=len(MAIN_TEXT),
                expected=MAIN_TEXT, replacement="", delete=True,
            )
        ],
    )
    store.commit_change(variant_id=variant_id, change=change, request_key="variant-delete")
    assert store.prose_state(path, variant_id)[0] == "absent"
    marker = store.dir / "variants" / variant_id / "prose" / "正文" / "0001 雨夜.md.absent"
    assert marker.exists()
    assert workspace.read(path).text == MAIN_TEXT


# -- 备选正文基线：创建时固定来源实际可见的内容 -----------------------------

ALTERNATIVE_TEXT = "# 雨夜\n\n备选甲的不同原文。"


def write_variant_prose(
    store: StoryStore, variant_id: str, path: str, text: str, key: str
) -> None:
    digest, current, _ = store.prose_state(path, variant_id)
    revision = store.require_variant(variant_id).revision
    store.commit_change(
        variant_id=variant_id,
        request_key=key,
        change=StoryChangeSet(
            id=key,
            base_revision=revision,
            prose_patches=[
                ProsePatch(
                    path=path,
                    base_digest=digest,
                    start=0,
                    end=len(current),
                    expected=current,
                    replacement=text,
                )
            ],
        ),
    )


def test_fork_copies_the_source_variant_visible_prose(tmp_path: Path) -> None:
    workspace, store, path, digest, alternative_id = variant_setup(tmp_path)
    write_variant_prose(store, alternative_id, path, ALTERNATIVE_TEXT, "alt-prose")
    alternative = store.require_variant(alternative_id)

    second = store.create_variant(
        name="乙",
        source_variant_id=alternative_id,
        source_revision=alternative.revision,
        request_key="fork-from-alternative",
    )
    second_id = str(second.result["variant_id"])

    assert store.read_prose(path, second_id)[1] == ALTERNATIVE_TEXT
    assert store.prose_state(path, second_id)[2] is False
    # 修改主方案与来源备选，都不改变乙的正文。
    workspace.write(path, "# 雨夜\n\n主方案后来修改。", expected_digest=digest)
    assert store.read_prose(path, second_id)[1] == ALTERNATIVE_TEXT
    write_variant_prose(store, alternative_id, path, "# 雨夜\n\n备选甲又改了。", "alt-again")
    assert store.read_prose(path, second_id)[1] == ALTERNATIVE_TEXT


def test_fork_keeps_deleted_prose_absent(tmp_path: Path) -> None:
    workspace, store, path, digest, alternative_id = variant_setup(tmp_path)
    revision = store.require_variant(alternative_id).revision
    store.commit_change(
        variant_id=alternative_id,
        request_key="alt-delete",
        change=StoryChangeSet(
            id="alt-delete",
            base_revision=revision,
            prose_patches=[
                ProsePatch(
                    path=path,
                    base_digest=digest,
                    start=0,
                    end=len(MAIN_TEXT),
                    expected=MAIN_TEXT,
                    replacement="",
                    delete=True,
                )
            ],
        ),
    )
    alternative = store.require_variant(alternative_id)

    second = store.create_variant(
        name="乙",
        source_variant_id=alternative_id,
        source_revision=alternative.revision,
        request_key="fork-deleted-prose",
    )
    second_id = str(second.result["variant_id"])

    assert store.prose_state(path, second_id)[0] == "absent"
    assert workspace.read(path).text == MAIN_TEXT
    marker = store.dir / "variants" / second_id / "prose" / "正文" / "0001 雨夜.md.absent"
    assert marker.exists()


def test_undo_preserves_later_edit_in_same_file(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    path = "正文/0001 雨夜.md"
    view = workspace.write(path, MAIN_TEXT, expected_digest=None)
    store = StoryStore(tmp_path / "state", workspace)
    store.commit_change(
        variant_id="main",
        request_key="trim",
        change=StoryChangeSet(
            id="trim",
            base_revision="empty",
            prose_patches=[
                ProsePatch(
                    path=path, base_digest=view.digest, start=6, end=8,
                    expected="甲😀", replacement="丙😀",
                )
            ],
        ),
    )
    edited = store.read_prose(path)[1] + "\n\n作者补记。"
    workspace.write(path, edited, expected_digest=workspace.read(path).digest)
    store.undo(change_id="trim", request_key="undo-trim")
    assert workspace.read(path).text == MAIN_TEXT + "\n\n作者补记。"


def test_undo_conflicts_when_changed_segment_moved(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    path = "正文/0001 雨夜.md"
    view = workspace.write(path, MAIN_TEXT, expected_digest=None)
    store = StoryStore(tmp_path / "state", workspace)
    store.commit_change(
        variant_id="main",
        request_key="trim",
        change=StoryChangeSet(
            id="trim",
            base_revision="empty",
            prose_patches=[
                ProsePatch(
                    path=path, base_digest=view.digest, start=6, end=8,
                    expected="甲😀", replacement="丙😀",
                )
            ],
        ),
    )
    workspace.write(
        path, "# 雨夜\n\n甲😀乙。\n\n末尾。", expected_digest=workspace.read(path).digest
    )
    with pytest.raises(WorkspaceError, match="STORY_PROSE_CONFLICT"):
        store.undo(change_id="trim", request_key="undo-trim")


class _Library:
    """只实现依赖核对需要的模块存在性检查。"""

    def __init__(self, known: set[tuple[str, str]]) -> None:
        self.storage = self
        self.known = known

    def revision_exists(self, module_id: str, revision: str) -> bool:
        return (module_id, revision) in self.known


def test_module_and_conversation_dependencies(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    workspace.write("正文/0001 雨夜.md", "雨", expected_digest=None)
    sequenced = {"conv_1": "4"}
    store = StoryStore(
        tmp_path / "state",
        workspace,
        library=_Library({("mod_a", "rev_a")}),
        conversation_seq=lambda conversation_id: sequenced.get(conversation_id),
    )
    first = store.commit_change(
        variant_id="main",
        request_key="deps-ok",
        change=StoryChangeSet(
            id="deps-ok",
            base_revision="empty",
            operations=[AddNode(node=StoryNode(id="n1", title="雨夜", action="抵达"))],
            read_dependencies=[
                ReadDependency(kind="module", id="mod_a", revision="rev_a"),
                ReadDependency(kind="conversation", id="conv_1", revision="4"),
            ],
        ),
    )
    revision = first.revision

    def submit_dependency(kind: str, dependency_id: str, revision_text: str) -> None:
        store.commit_change(
            variant_id="main",
            request_key=f"dep-{kind}-{dependency_id}-{revision_text}",
            change=StoryChangeSet(
                id=f"dep-{kind}-{dependency_id}-{revision_text}",
                base_revision=store.require_variant("main").revision,
                operations=[AddNode(node=StoryNode(id=f"n-{kind}-{revision_text}",
                                                   title="检查", action="检查"))],
                read_dependencies=[
                    ReadDependency(kind=kind, id=dependency_id, revision=revision_text)
                ],
            ),
        )

    with pytest.raises(WorkspaceError, match="STORY_DEPENDENCY_CONFLICT"):
        submit_dependency("module", "mod_a", "rev_missing")
    with pytest.raises(WorkspaceError, match="STORY_DEPENDENCY_CONFLICT"):
        submit_dependency("module", "mod_missing", "rev_a")
    sequenced["conv_1"] = "5"
    with pytest.raises(WorkspaceError, match="STORY_DEPENDENCY_CONFLICT"):
        submit_dependency("conversation", "conv_1", "4")
    assert store.require_variant("main").revision == revision


def test_unavailable_dependency_entry_is_reported(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    store = StoryStore(tmp_path / "state", workspace)
    with pytest.raises(WorkspaceError, match="STORY_DEPENDENCY_CONFLICT"):
        store.commit_change(
            variant_id="main",
            request_key="dep-module",
            change=StoryChangeSet(
                id="dep-module",
                base_revision="empty",
                operations=[AddNode(node=StoryNode(id="n1", title="雨夜", action="抵达"))],
                read_dependencies=[ReadDependency(kind="module", id="mod_a", revision="rev_a")],
            ),
        )
    assert not store.file.exists()


def test_ledger_target_recovers_after_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hastings.workspace.clue_store import ClueLedgerStore
    from hastings.workspace.story_clue_commit import prepare_clue_joint
    from hastings.workspace.story_models import (
        CluePlanInput,
        ClueUse,
        CreateClueIdentity,
        UpsertCluePlan,
    )
    import hastings.workspace.story_store as story_store_module

    store = StoryStore(tmp_path / "state")
    change = StoryChangeSet(
        id="ledger-recovery",
        base_revision="empty",
        operations=[
            CreateClueIdentity(temp_id="c1", title="手套"),
            UpsertCluePlan(clue_id="c1", plan=CluePlanInput()),
            AddNode(
                node=StoryNode(
                    id="n1",
                    title="抵达",
                    action="进门",
                    clue_uses=[ClueUse(clue_id="c1", presentation="手套还在滴水")],
                )
            ),
        ],
    )
    real_write = story_store_module.atomic_write_json

    def fail_ledger(path: Path, *args: object, **kwargs: object) -> None:
        if str(path).endswith("clue-ledger.json"):
            raise OSError("模拟账本写入失败")
        real_write(path, *args, **kwargs)

    monkeypatch.setattr(story_store_module, "atomic_write_json", fail_ledger)
    with pytest.raises(WorkspaceError) as caught:
        store.commit_change(
            variant_id="main",
            change=change,
            request_key="ledger-recovery",
            prepare_joint=prepare_clue_joint(
                tmp_path / "state", variant_id="main", change=change
            ),
        )
    assert caught.value.code == "STORY_WRITE_FAILED"
    assert store.pending.exists()
    assert store.decision.exists()

    monkeypatch.setattr(story_store_module, "atomic_write_json", real_write)
    snapshot = store.read()
    assert snapshot.document is not None
    variant = snapshot.variant("main")
    assert variant is not None
    assert len(variant.clue_plans) == 1
    ledger = ClueLedgerStore(tmp_path / "state").read()
    assert ledger.ledger is not None
    assert len(ledger.ledger.clues) == 1
    receipt = snapshot.document.requests["ledger-recovery"]
    assert receipt.ledger_revision
    assert receipt.id_map["c1"].startswith("clue_")
