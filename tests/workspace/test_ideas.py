"""灵感本：原子修订、冲突、删除恢复与权限边界（0.8 C）。

契约：一条灵感一个文件；编辑、删除、恢复都追加修订；冲突时保留双方内容；
灵感不进作品 Git、不在受管路径里，普通文件工具与目录都看不到它。
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path
from hastings.workspace.git import GitRepo
from hastings.workspace.ideas import IDEAS_DIR, IdeaStore, IdeaUseDraft


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    workspace.write("正文/0001 雨夜.md", "第一章 雨夜\n\n雪压住了山路。", expected_digest=None)
    return workspace


def _store(tmp_path: Path) -> tuple[Workspace, IdeaStore]:
    workspace = _workspace(tmp_path)
    return workspace, IdeaStore(workspace)


def _create(store: IdeaStore, *, key: str = "idea-key-1", body: str = "老板娘认识死者。") -> dict:
    return store.create(title="老板娘", body=body, tags=["人物"], request_key=key)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_create_writes_one_json_with_a_first_revision(tmp_path: Path) -> None:
    workspace, store = _store(tmp_path)
    view = _create(store)

    path = workspace.root / IDEAS_DIR / f"{view['idea_id']}.json"
    assert path.is_file()
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["head_revision_id"] == view["revision_id"]
    assert document["revisions"][0]["operation"] == "create"
    assert view["digest"] == _digest(path)
    assert set(document) >= {"schema_version", "idea_id", "revisions", "origin", "uses"}


def test_create_is_idempotent_and_refuses_key_reuse(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    first = _create(store)
    second = _create(store)

    assert second["idea_id"] == first["idea_id"]
    assert second["revision_id"] == first["revision_id"]
    assert len(list((store.root).glob("*.json"))) == 1

    with pytest.raises(WorkspaceError) as excinfo:
        store.create(title="另一条", body="不同内容。", request_key="idea-key-1")
    assert excinfo.value.code == "REQUEST_KEY_CONFLICT"


def test_quick_captures_in_one_second_keep_creation_order(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    moments = [datetime(2026, 10, 25, 10, 0, 0, n, tzinfo=UTC) for n in (100, 200, 300)]
    with patch("hastings.workspace.ideas.datetime") as clock:
        clock.now.side_effect = moments
        for number in range(3):
            store.create(body=f"连续灵感{number}", request_key=f"quick-capture-{number}")
    page = store.list(order="created")
    assert [item["excerpt"] for item in page["items"]] == ["连续灵感2", "连续灵感1", "连续灵感0"]


def test_edit_appends_a_revision_and_keeps_history(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    created = _create(store)

    edited = store.update(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        expected_revision=str(created["revision_id"]),
        body="老板娘一直在保护死者的女儿。",
        request_key="idea-key-2",
    )

    assert edited["revision_id"] != created["revision_id"]
    assert edited["parent"] == created["revision_id"]
    assert edited["body"] == "老板娘一直在保护死者的女儿。"
    history = store.revisions(str(created["idea_id"]))
    assert [item["operation"] for item in history["items"]] == ["edit", "create"]
    assert store.revision(str(created["idea_id"]), str(created["revision_id"]))["body"] == (
        "老板娘认识死者。"
    )


def test_stale_edit_keeps_the_newer_content(tmp_path: Path) -> None:
    workspace, store = _store(tmp_path)
    created = _create(store)
    path = workspace.root / IDEAS_DIR / f"{created['idea_id']}.json"
    before = path.read_bytes()

    with pytest.raises(WorkspaceError) as excinfo:
        store.update(
            str(created["idea_id"]),
            expected_digest="过期指纹",
            expected_revision=str(created["revision_id"]),
            body="旧页面上的修改。",
            request_key="idea-key-2",
        )
    assert excinfo.value.code == "IDEA_STALE_CONFLICT"
    assert path.read_bytes() == before

    with pytest.raises(WorkspaceError):
        store.update(
            str(created["idea_id"]),
            expected_digest=_digest(path),
            expected_revision="irev_0000000000000000",
            body="基于旧修订的修改。",
            request_key="idea-key-3",
        )
    assert path.read_bytes() == before


def test_concurrent_edits_let_only_one_through(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    created = _create(store)
    outcomes: list[tuple[str, str]] = []

    def attempt(index: int) -> None:
        try:
            view = store.update(
                str(created["idea_id"]),
                expected_digest=str(created["digest"]),
                expected_revision=str(created["revision_id"]),
                body=f"并发第 {index} 版。",
                request_key=f"idea-concurrent-{index}",
            )
            outcomes.append(("ok", str(view["body"])))
        except WorkspaceError as exc:
            outcomes.append(("err", exc.code))

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(kind for kind, _ in outcomes) == ["err", "ok"]
    assert next(value for kind, value in outcomes if kind == "err") == "IDEA_STALE_CONFLICT"
    stored = store.get(str(created["idea_id"]))
    assert stored["body"] == next(value for kind, value in outcomes if kind == "ok")


def test_trash_and_restore_append_revisions(tmp_path: Path) -> None:
    workspace, store = _store(tmp_path)
    created = _create(store)
    idea_id = str(created["idea_id"])
    chapter_before = (workspace.root / "正文/0001 雨夜.md").read_bytes()

    trashed = store.trash(
        idea_id,
        expected_digest=str(created["digest"]),
        expected_revision=str(created["revision_id"]),
        request_key="idea-trash-1",
    )
    assert trashed["deleted"] is True
    assert store.list()["total"] == 0
    assert store.list(deleted=True)["total"] == 1
    assert store.revision(idea_id, str(created["revision_id"]))["body"] == "老板娘认识死者。"

    restored = store.restore(
        idea_id,
        target_revision=str(created["revision_id"]),
        expected_digest=str(trashed["digest"]),
        request_key="idea-restore-1",
    )
    assert restored["deleted"] is False
    assert restored["body"] == "老板娘认识死者。"
    assert restored["restored_from"] == created["revision_id"]
    assert store.list()["total"] == 1
    # 恢复灵感不改任何正文文件。
    assert (workspace.root / "正文/0001 雨夜.md").read_bytes() == chapter_before


def test_restoring_a_deletion_revision_keeps_it_deleted(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    created = _create(store)
    idea_id = str(created["idea_id"])
    trashed = store.trash(
        idea_id,
        expected_digest=str(created["digest"]),
        expected_revision=str(created["revision_id"]),
        request_key="idea-trash-1",
    )

    restored = store.restore(
        idea_id,
        target_revision=str(trashed["revision_id"]),
        expected_digest=str(trashed["digest"]),
        request_key="idea-restore-2",
    )
    assert restored["deleted"] is True
    assert restored["restored_from"] == trashed["revision_id"]


def test_editing_a_deleted_idea_is_rejected(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    created = _create(store)
    trashed = store.trash(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        expected_revision=str(created["revision_id"]),
        request_key="idea-trash-1",
    )
    with pytest.raises(WorkspaceError) as excinfo:
        store.update(
            str(created["idea_id"]),
            expected_digest=str(trashed["digest"]),
            expected_revision=str(trashed["revision_id"]),
            body="删了还改。",
            request_key="idea-edit-2",
        )
    assert excinfo.value.code == "IDEA_DELETED_CONFLICT"


def test_content_limits_reject_instead_of_truncating(tmp_path: Path) -> None:
    workspace, store = _store(tmp_path)
    with pytest.raises(WorkspaceError) as excinfo:
        store.create(body="字" * 20_001, request_key="idea-too-long")
    assert excinfo.value.code == "IDEA_CONTENT_TOO_LARGE"
    assert not (workspace.root / IDEAS_DIR).exists() or not list(store.root.glob("*.json"))

    with pytest.raises(WorkspaceError) as excinfo:
        store.create(body="   ", request_key="idea-empty")
    assert excinfo.value.code == "IDEA_CONTENT_INVALID"


def test_corrupt_file_is_preserved_and_listed_as_invalid(tmp_path: Path) -> None:
    workspace, store = _store(tmp_path)
    created = _create(store)
    path = workspace.root / IDEAS_DIR / f"{created['idea_id']}.json"
    path.write_text("{ 不是 JSON", encoding="utf-8")
    broken = path.read_bytes()

    with pytest.raises(WorkspaceError) as excinfo:
        store.get(str(created["idea_id"]))
    assert excinfo.value.code == "IDEA_STORE_INVALID"
    assert path.read_bytes() == broken
    assert store.list()["invalid"] == 1


def test_pagination_and_cursor_binding(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    first = _create(store, key="idea-key-1")
    store.create(title="第二条", body="另一条想法。", request_key="idea-key-2")

    page = store.list(limit=1)
    assert page["next_cursor"]
    with pytest.raises(WorkspaceError) as excinfo:
        store.list(q="老板", limit=1, cursor=str(page["next_cursor"]))
    assert excinfo.value.code == "QUERY_CURSOR_STALE_CONFLICT"

    second = store.list(limit=1, cursor=str(page["next_cursor"]))
    assert len(second["items"]) == 1
    # 同秒创建时顺序由 ID 打破；两页只需要覆盖不同条目，不假设谁在前。
    assert second["items"][0]["idea_id"] != page["items"][0]["idea_id"]
    all_ids = {item["idea_id"] for item in store.list()["items"]}
    assert {page["items"][0]["idea_id"], second["items"][0]["idea_id"]} == all_ids

    revisions = store.revisions(str(first["idea_id"]))
    assert revisions["total"] == 1


def test_ideas_are_outside_managed_paths_and_git(tmp_path: Path) -> None:
    workspace, store = _store(tmp_path)
    repo = GitRepo(workspace.root)
    repo.init()
    head = repo.head()

    created = _create(store)

    assert not is_managed_path(f"{IDEAS_DIR}/{created['idea_id']}.json")
    with pytest.raises(WorkspaceError) as excinfo:
        workspace.read(f"{IDEAS_DIR}/{created['idea_id']}.json")
    assert excinfo.value.code == "PATH_NOT_MANAGED"
    assert all(not item.path.startswith(IDEAS_DIR) for item in workspace.catalog().chapters)
    assert repo.head() == head
    assert all(not change.path.startswith(f"{IDEAS_DIR}/") for change in repo.status().changes)


def test_origin_survives_missing_source(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    created = store.create(
        body="他提到过一把伞。",
        origin={"role": "assistant", "conversation_id": "conv-1", "message_seq": 7, "quote": "伞"},
        request_key="idea-origin-1",
    )
    assert created["origin"]["conversation_id"] == "conv-1"
    # 来源会话已经不在了，笔记本身仍然可读。
    assert store.get(str(created["idea_id"]))["body"] == "他提到过一把伞。"


def test_link_use_verifies_the_written_range(tmp_path: Path) -> None:
    workspace, store = _store(tmp_path)
    created = _create(store)
    chapter = workspace.read("正文/0001 雨夜.md")
    quote = "雪压住了山路。"
    start = chapter.text.index(quote)

    linked = store.add_use(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        use=IdeaUseDraft(
            idea_revision_id=str(created["revision_id"]),
            kind="applied",
            target_path=chapter.path,
            target_digest=chapter.digest,
            quote=quote,
            start=start,
            end=start + len(quote),
            turn_id="turn-1",
        ),
        request_key="idea-link-1",
    )
    assert linked["uses"][0]["kind"] == "applied"
    assert linked["uses"][0]["target_digest"] == chapter.digest

    with pytest.raises(WorkspaceError) as excinfo:
        store.add_use(
            str(created["idea_id"]),
            expected_digest=str(linked["digest"]),
            use=IdeaUseDraft(
                idea_revision_id=str(created["revision_id"]),
                kind="applied",
                target_path=chapter.path,
                target_digest=chapter.digest,
                quote="不存在的引文",
                start=0,
                end=4,
            ),
            request_key="idea-link-2",
        )
    assert excinfo.value.code == "IDEA_USE_UNVERIFIED"


def test_author_link_does_not_need_a_target(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    created = _create(store)
    linked = store.add_use(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        use=IdeaUseDraft(
            idea_revision_id=str(created["revision_id"]), kind="author_link", target_record_id=""
        ),
        request_key="idea-link-3",
    )
    assert linked["uses"][0]["kind"] == "author_link"


def test_link_use_replays_by_request_key(tmp_path: Path) -> None:
    """补记是独立命令：同一个键重试回到同一条关联，不新增、也不报冲突。"""
    workspace, store = _store(tmp_path)
    created = _create(store)
    chapter = workspace.read("正文/0001 雨夜.md")
    quote = "雪压住了山路。"
    start = chapter.text.index(quote)
    draft = IdeaUseDraft(
        idea_revision_id=str(created["revision_id"]),
        kind="applied",
        target_path=chapter.path,
        target_digest=chapter.digest,
        quote=quote,
        start=start,
        end=start + len(quote),
        turn_id="turn-1",
        commit="c1",
    )

    first = store.add_use(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        use=draft,
        request_key="idea-link-9",
    )
    again = store.add_use(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        use=draft,
        request_key="idea-link-9",
    )

    assert len(first["uses"]) == 1
    assert again["uses"] == first["uses"]
    assert again["uses"][0]["commit"] == "c1"


def test_link_use_rejects_a_reused_key_for_another_target(tmp_path: Path) -> None:
    workspace, store = _store(tmp_path)
    created = _create(store)
    chapter = workspace.read("正文/0001 雨夜.md")
    quote = "雪压住了山路。"
    start = chapter.text.index(quote)
    store.add_use(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        use=IdeaUseDraft(
            idea_revision_id=str(created["revision_id"]),
            kind="applied",
            target_path=chapter.path,
            target_digest=chapter.digest,
            quote=quote,
            start=start,
            end=start + len(quote),
            turn_id="turn-1",
        ),
        request_key="idea-link-10",
    )

    with pytest.raises(WorkspaceError) as excinfo:
        store.add_use(
            str(created["idea_id"]),
            expected_digest=str(store.get(str(created["idea_id"]))["digest"]),
            use=IdeaUseDraft(
                idea_revision_id=str(created["revision_id"]),
                kind="applied",
                target_path=chapter.path,
                target_digest=chapter.digest,
                quote=quote,
                start=start,
                end=start + len(quote),
                turn_id="turn-2",
            ),
            request_key="idea-link-10",
        )
    assert excinfo.value.code == "REQUEST_KEY_CONFLICT"


def test_rewriting_the_same_range_refreshes_the_recorded_commit(tmp_path: Path) -> None:
    """同一位置再次写入时记录换成新提交，避免一直挂着已经被撤销的旧提交。"""
    workspace, store = _store(tmp_path)
    created = _create(store)
    chapter = workspace.read("正文/0001 雨夜.md")
    quote = "雪压住了山路。"
    start = chapter.text.index(quote)

    def link(commit: str, key: str) -> None:
        store.add_use(
            str(created["idea_id"]),
            expected_digest=str(store.get(str(created["idea_id"]))["digest"]),
            use=IdeaUseDraft(
                idea_revision_id=str(created["revision_id"]),
                kind="applied",
                target_path=chapter.path,
                target_digest=chapter.digest,
                quote=quote,
                start=start,
                end=start + len(quote),
                turn_id="turn-1",
                commit=commit,
            ),
            request_key=key,
        )

    link("c1", "idea-link-11")
    link("c2", "idea-link-12")
    uses = store.get(str(created["idea_id"]))["uses"]
    assert len(uses) == 1
    assert uses[0]["commit"] == "c2"

    # 不带提交的登记（例如作者手动补充）不能把已有依据抹掉。
    store.add_use(
        str(created["idea_id"]),
        expected_digest=str(store.get(str(created["idea_id"]))["digest"]),
        use=IdeaUseDraft(
            idea_revision_id=str(created["revision_id"]),
            kind="applied",
            target_path=chapter.path,
            target_digest=chapter.digest,
            quote=quote,
            start=start,
            end=start + len(quote),
            turn_id="turn-1",
        ),
        request_key="idea-link-13",
    )
    assert store.get(str(created["idea_id"]))["uses"][0]["commit"] == "c2"


def test_body_is_saved_verbatim_without_trimming(tmp_path: Path) -> None:
    """正文按原文保存：首尾空白、缩进与表情都不改写，长度按原文计算。"""
    _, store = _store(tmp_path)
    body = "  开头两个空格\n\t缩进与表情 🎭\n结尾保留  \n"
    created = store.create(body=body, request_key="idea-verbatim-1")
    assert created["body"] == body
    assert store.get(str(created["idea_id"]))["body"] == body

    edited = store.update(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        expected_revision=str(created["revision_id"]),
        body=body + "追加",
        request_key="idea-verbatim-2",
    )
    assert edited["body"] == body + "追加"


def test_length_limit_counts_the_original_text(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    at_limit = " " + "字" * 19_998 + " "
    assert len(at_limit) == 20_000
    created = store.create(body=at_limit, request_key="idea-limit-1")
    assert len(str(created["body"])) == 20_000

    with pytest.raises(WorkspaceError) as excinfo:
        store.create(body=" " + "字" * 20_000 + " ", request_key="idea-limit-2")
    assert excinfo.value.code == "IDEA_CONTENT_TOO_LARGE"


def test_update_with_identical_body_does_not_append_a_revision(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    created = _create(store)
    same = store.update(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        expected_revision=str(created["revision_id"]),
        body=str(created["body"]),
        request_key="idea-key-same",
    )
    assert same["revision_id"] == created["revision_id"]
    assert store.revisions(str(created["idea_id"]))["total"] == 1

    # 同键重试仍返回同一修订，不因为文件指纹变化而失败。
    retry = store.update(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        expected_revision=str(created["revision_id"]),
        body=str(created["body"]),
        request_key="idea-key-same",
    )
    assert retry["revision_id"] == created["revision_id"]


def test_list_order_separates_created_from_updated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hastings.workspace.ideas as ideas_module

    stamp = {"value": 0}

    def clock() -> str:
        stamp["value"] += 1
        return f"2026-10-25T00:00:{stamp['value']:02d}+00:00"

    monkeypatch.setattr(ideas_module, "_now", clock)
    _, store = _store(tmp_path)
    first = store.create(body="第一条。", request_key="idea-order-1")
    second = store.create(body="第二条。", request_key="idea-order-2")

    before = [item["idea_id"] for item in store.list(order="created")["items"]]
    assert before == [second["idea_id"], first["idea_id"]]

    store.update(
        str(first["idea_id"]),
        expected_digest=str(first["digest"]),
        expected_revision=str(first["revision_id"]),
        body="第一条改。",
        request_key="idea-order-3",
    )
    updated = [item["idea_id"] for item in store.list(order="updated")["items"]]
    created = [item["idea_id"] for item in store.list(order="created")["items"]]
    assert updated == [first["idea_id"], second["idea_id"]]
    assert created == before


def test_summary_reports_length_and_truncation(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    short = store.create(body="很短的一条。", request_key="idea-summary-1")
    long = store.create(body="字" * 200, request_key="idea-summary-2")
    rows = {item["idea_id"]: item for item in store.list()["items"]}
    assert rows[str(short["idea_id"])]["body_chars"] == len("很短的一条。")
    assert rows[str(short["idea_id"])]["excerpt_truncated"] is False
    assert rows[str(long["idea_id"])]["body_chars"] == 200
    assert rows[str(long["idea_id"])]["excerpt_truncated"] is True
    assert len(str(rows[str(long["idea_id"])]["excerpt"])) == 120
    assert rows[str(short["idea_id"])]["created_at"]


def test_resolve_reference_rejects_deleted_entry_and_delete_revision(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    created = _create(store)
    resolved = store.resolve_reference(str(created["idea_id"]), str(created["revision_id"]))
    assert resolved["body"] == "老板娘认识死者。"

    deleted = store.trash(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        expected_revision=str(created["revision_id"]),
        request_key="idea-ref-1",
    )
    with pytest.raises(WorkspaceError) as excinfo:
        store.resolve_reference(str(created["idea_id"]), str(created["revision_id"]))
    assert excinfo.value.code == "IDEA_REFERENCE_UNAVAILABLE"

    # 恢复后条目重新可读，但删除修订本身仍然不能被引用。
    restored = store.restore(
        str(created["idea_id"]),
        target_revision=str(created["revision_id"]),
        expected_digest=str(deleted["digest"]),
        request_key="idea-ref-2",
    )
    assert store.resolve_reference(str(created["idea_id"]), str(restored["revision_id"]))
    with pytest.raises(WorkspaceError) as excinfo:
        store.resolve_reference(str(created["idea_id"]), str(deleted["revision_id"]))
    assert excinfo.value.code == "IDEA_REFERENCE_UNAVAILABLE"


def test_resolve_reference_rejects_unknown_revision(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    created = _create(store)
    with pytest.raises(WorkspaceError) as excinfo:
        store.resolve_reference(str(created["idea_id"]), "irev_0000000000000000")
    assert excinfo.value.code == "IDEA_REFERENCE_UNAVAILABLE"
