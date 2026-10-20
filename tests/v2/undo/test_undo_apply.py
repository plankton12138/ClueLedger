"""基础关联撤销（V03-4.6 后半）：一次提交恢复多章，冲突时零写入。

覆盖：可撤销的操作真的恢复正文、之后有修改时拒绝、副本打开时拒绝、
重复撤销幂等、多章操作一次提交同时恢复。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import save_full_text
from hastings.domain.repository.ids import ProjectId
from hastings.server.v2 import V2Runtime, build_v2_app

ORIGINAL = "侦探在雪夜抵达旅馆，前台没有人。"
CHANGED = "侦探在雪夜抵达旅馆，前台站着一个女人。"
OTHER = "二楼走廊尽头有一扇没有门牌的门。"
OTHER_CHANGED = "二楼走廊尽头那扇门开着，里面亮着灯。"


def _client() -> tuple[TestClient, str, str, V2Runtime]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "撤销写入"}).json()["project_id"]
    client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp1", "title": "第一章 雪夜", "text": ORIGINAL},
    )
    chapter_id = client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"][0][
        "chapter_id"
    ]
    return client, pid, chapter_id, runtime


def _save(client: TestClient, pid: str, chapter_id: str, text: str, key: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/save",
        json={"idempotency_key": key, "full_text": text},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _text(client: TestClient, pid: str, chapter_id: str) -> str:
    return client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()["text"]


def _undo(client: TestClient, pid: str, operation_id: str) -> Any:
    return client.post(f"/api/v2/projects/{pid}/operations/{operation_id}/undo")


def test_apply_restores_the_chapter_in_a_new_version() -> None:
    client, pid, chapter_id, _ = _client()
    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    head_after_save = client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]

    response = _undo(client, pid, saved["operation_id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "committed"
    assert body["undo_of"] == saved["operation_id"]
    assert body["operation_id"] != saved["operation_id"]
    assert body["restored_chapter_ids"] == [chapter_id]
    assert _text(client, pid, chapter_id) == ORIGINAL
    # 撤销本身是一次新版本，不改写历史。
    assert client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"] != head_after_save


def test_apply_is_idempotent_and_does_not_fight_its_own_result() -> None:
    client, pid, chapter_id, _ = _client()
    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    first = _undo(client, pid, saved["operation_id"]).json()

    second = _undo(client, pid, saved["operation_id"]).json()

    assert second["commit_id"] == first["commit_id"]
    assert second["replayed"] is True
    assert _text(client, pid, chapter_id) == ORIGINAL


def test_later_save_blocks_undo_and_changes_nothing() -> None:
    client, pid, chapter_id, _ = _client()
    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    latest = _save(client, pid, chapter_id, CHANGED + "她抬起头。", "save-2")
    head_before = client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]

    blocked = _undo(client, pid, saved["operation_id"])

    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "UNDO_DEPENDENCY_CONFLICT"
    assert _text(client, pid, chapter_id).endswith("她抬起头。")
    assert client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"] == head_before
    assert latest["commit_id"] == head_before


def test_open_edit_session_blocks_undo() -> None:
    client, pid, chapter_id, _ = _client()
    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    started = client.post(f"/api/v2/projects/{pid}/edit-sessions")
    assert started.status_code in (200, 201), started.text

    blocked = _undo(client, pid, saved["operation_id"])

    assert blocked.status_code == 409, blocked.text
    assert _text(client, pid, chapter_id) == CHANGED


def test_multi_chapter_operation_restores_both_in_one_commit() -> None:
    client, pid, chapter_id, runtime = _client()
    client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp2", "title": "第二章 门", "text": OTHER},
    )
    chapters = client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]
    # 按标题取身份：两章的 order_key 相同时列表顺序不稳定，下标不可靠。
    by_title = {chapter["title"]: chapter["chapter_id"] for chapter in chapters}
    assert set(by_title) == {"第一章 雪夜", "第二章 门"}
    second_id = by_title["第二章 门"]
    branch = runtime.service.get_branch(ProjectId(pid), "main")
    assert branch is not None and branch.head_commit_id is not None
    catalog = ManuscriptCatalog.from_snapshot(
        runtime.service.get_snapshot(ProjectId(pid), branch.head_commit_id)
    )
    commands = tuple(
        manuscript_command(
            save_full_text,
            chapter_id=cid,
            text=text,
            expected_chapter_base=catalog.chapter_base(cid),
        )
        for cid, text in ((chapter_id, CHANGED), (second_id, OTHER_CHANGED))
    )
    published = runtime.manuscript_publisher.publish_commands(
        project_id=ProjectId(pid),
        commands=commands,
        operation_id="op_two_chapters",
        kind="chapter.save",
        summary="一次改两章",
    )
    assert published.envelope.status == "committed"
    assert _text(client, pid, chapter_id) == CHANGED
    assert _text(client, pid, second_id) == OTHER_CHANGED

    restored = _undo(client, pid, "op_two_chapters")

    assert restored.status_code == 200, restored.text
    assert set(restored.json()["restored_chapter_ids"]) == {chapter_id, second_id}
    # 两章在同一次提交里恢复：head 只前进一次，且内容都回到原样。
    assert _text(client, pid, chapter_id) == ORIGINAL
    assert _text(client, pid, second_id) == OTHER
