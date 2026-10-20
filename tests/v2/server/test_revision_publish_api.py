"""内部工作版本暂存与统一发布 REST（V03-7.4）。

验收口径（计划 M10 第 9/10 条 / 阶段指南 §11.3）：正文与账本进**一个** Commit；
执行期间新建副本、正式基础前进、未覆盖与预算耗尽都整体拒绝，稳定版不出现半套。
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.revision.errors import CoverageViolation
from hastings.server.v2 import V2Runtime, build_v2_app

MD = """# 雪山旅馆

## 第一章 雪夜

侦探在雪夜抵达旅馆，风雪封住了山路。

## 第二章 空房

走廊尽头有一扇门，门后没有声音。
"""


def _client() -> tuple[TestClient, V2Runtime, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "统一发布"}).json()["project_id"]
    return client, runtime, pid


def _import(client: TestClient, pid: str) -> None:
    preview = client.post(
        f"/api/v2/projects/{pid}/imports/previews",
        json={
            "filename": "雪山旅馆.md",
            "content_base64": base64.b64encode(MD.encode("utf-8")).decode("ascii"),
        },
    ).json()
    response = client.post(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}/confirm",
        json={"preview_hash": preview["preview_hash"]},
    )
    assert response.status_code == 200, response.text


def _chapters(client: TestClient, pid: str) -> list[dict]:
    return client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]


def _start(client: TestClient, pid: str, *, budget: int | None = None) -> str:
    body: dict[str, object] = {"request_id": "req-1"}
    if budget is not None:
        body["call_budget"] = budget
    response = client.post(f"/api/v2/projects/{pid}/revision-tasks", json=body)
    assert response.status_code == 200, response.text
    return response.json()["task_id"]


def _stage(client: TestClient, pid: str, task_id: str, chapter_id: str, text: str):
    chapter = client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()
    return client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/staged-chapters",
        json={
            "chapter_id": chapter_id,
            "text": text,
            "base_revision_id": chapter["chapter_base"],
        },
    )


def _stage_all(client: TestClient, pid: str, task_id: str) -> list[str]:
    ids = [row["chapter_id"] for row in _chapters(client, pid)]
    for index, chapter_id in enumerate(ids):
        response = _stage(client, pid, task_id, chapter_id, f"改写后的第 {index + 1} 章正文。")
        assert response.status_code == 200, response.text
    return ids


def _publish(client: TestClient, pid: str, task_id: str, operation_id: str = "op_pub1"):
    return client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/publish",
        json={"operation_id": operation_id},
    )


def test_publish_without_staging_is_rejected() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid)
    response = _publish(client, pid, task_id)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "REVISION_PUBLISH_EMPTY"


def test_staged_chapters_publish_in_one_commit() -> None:
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid)
    ids = _stage_all(client, pid, task_id)
    before = runtime.service.get_branch(ProjectId(pid), "main").head_commit_id
    assert before is not None

    result = _publish(client, pid, task_id)
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["status"] == "committed"
    assert payload["changed_chapters"] == ids
    assert payload["task"]["state"] == "completed"
    assert payload["task"]["published_operation_id"] == "op_pub1"

    chapter = client.get(f"/api/v2/projects/{pid}/chapters/{ids[0]}").json()
    assert chapter["text"] == "改写后的第 1 章正文。"

    # 一个 Commit：新 head 的唯一父提交就是发布前 head。
    new_head = CommitId(payload["commit_id"])
    commits = runtime.service.commit_history(ProjectId(pid), new_head)
    published = next(c for c in commits if c.id.value == new_head.value)
    assert [p.value for p in published.parent_ids] == [before.value]


def test_uncovered_chapter_blocks_until_staged() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid)
    first = _chapters(client, pid)[0]["chapter_id"]
    assert _stage(client, pid, task_id, first, "只改第一章。").status_code == 200

    blocked = _publish(client, pid, task_id)
    assert blocked.status_code == 409, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "REVISION_COVERAGE_INCOMPLETE_CONFLICT"
    assert any(b["code"] == error["code"] for b in error["details"]["blockers"])

    second = _chapters(client, pid)[1]["chapter_id"]
    assert _stage(client, pid, task_id, second, "再改第二章。").status_code == 200
    assert _publish(client, pid, task_id).status_code == 200


def test_active_edit_session_blocks_publish() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid)
    _stage_all(client, pid, task_id)
    session = client.post(f"/api/v2/projects/{pid}/edit-sessions")
    assert session.status_code == 200, session.text

    blocked = _publish(client, pid, task_id)
    assert blocked.status_code == 409, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "REVISION_EDIT_SESSION_ACTIVE_CONFLICT"
    assert "完成或放弃" in error["fix"]


def test_head_moved_after_task_start_blocks_publish() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid)
    ids = _stage_all(client, pid, task_id)
    chapter = client.get(f"/api/v2/projects/{pid}/chapters/{ids[0]}").json()
    saved = client.post(
        f"/api/v2/projects/{pid}/chapters/{ids[0]}/save",
        json={
            "idempotency_key": "save-during-task",
            "full_text": "任务开始后别人又改了一版。",
            "expected_chapter_base": chapter["chapter_base"],
        },
    )
    assert saved.status_code == 200, saved.text

    blocked = _publish(client, pid, task_id)
    assert blocked.status_code == 409, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "REVISION_BASE_MOVED_CONFLICT"
    assert "重新核对" in error["fix"]


def test_staging_with_stale_chapter_base_is_rejected() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid)
    chapter_id = _chapters(client, pid)[0]["chapter_id"]
    response = client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/staged-chapters",
        json={"chapter_id": chapter_id, "text": "正文", "base_revision_id": "rev_stale"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "REVISION_STAGE_CHAPTER_CONFLICT"


def test_chapter_and_ledger_publish_together() -> None:
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid)
    _stage_all(client, pid, task_id)

    staged = client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/staged-ledger",
        json={"kind": "story.character", "fields": {"name": "姐夫"}},
    )
    assert staged.status_code == 200, staged.text
    assert staged.json()["ledger_count"] == 1

    result = _publish(client, pid, task_id)
    assert result.status_code == 200, result.text
    payload = result.json()

    ledger = client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]
    assert any(entry["title"] == "姐夫" for entry in ledger)

    # 同一 Commit 里同时含正文与账本条目。
    snapshot = runtime.service.get_snapshot(ProjectId(pid), CommitId(payload["commit_id"]))
    kinds = {view.artifact.kind for view in snapshot.artifacts.values()}
    assert "story.character" in kinds
    assert "manuscript.chapter" in kinds


def test_staged_ledger_revision_requires_current_revision() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    created = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={"kind": "story.character", "fields": {"name": "姐姐"}, "request_id": "req-char"},
    )
    assert created.status_code == 201, created.text
    entry = created.json()["entry"]
    task_id = _start(client, pid)

    stale = client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/staged-ledger",
        json={
            "entry_id": entry["id"],
            "fields": {"name": "姐姐（改）"},
            "expected_revision": "rev_stale",
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "LEDGER_ENTRY_CONFLICT"

    ok = client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/staged-ledger",
        json={
            "entry_id": entry["id"],
            "fields": {"name": "姐姐（改）"},
            "expected_revision": entry["revision_id"],
        },
    )
    assert ok.status_code == 200, ok.text


def test_publish_replays_same_operation_and_rejects_new_one() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid)
    _stage_all(client, pid, task_id)
    first = _publish(client, pid, task_id).json()

    replay = _publish(client, pid, task_id)
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "replayed"
    assert replay.json()["commit_id"] == first["commit_id"]

    different = _publish(client, pid, task_id, operation_id="op_pub2")
    assert different.status_code == 400, different.text
    assert different.json()["error"]["code"] == "REVISION_TASK_COMPLETED"


def test_budget_exhaustion_blocks_publish() -> None:
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid, budget=1)
    _stage_all(client, pid, task_id)
    runtime.revision_tasks.consume_calls(pid, task_id)  # 用掉唯一一次
    with pytest.raises(CoverageViolation):
        # 再要一次：整笔拒绝并把任务落到 waiting/budget_exhausted
        runtime.revision_tasks.consume_calls(pid, task_id)

    blocked = _publish(client, pid, task_id)
    # 因预算停下 → 任务转 waiting，发布先被「不是运行中」拦下；任务视图给出预算原因。
    assert blocked.status_code == 400, blocked.text
    assert blocked.json()["error"]["code"] == "REVISION_TASK_NOT_RUNNING"
    view = client.get(f"/api/v2/projects/{pid}/revision-tasks/{task_id}").json()
    assert view["state"] == "waiting"
    assert view["waiting_reason"] == "budget_exhausted"
    assert "继续" in blocked.json()["error"]["fix"]


def test_unstage_removes_item() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid)
    chapter_id = _chapters(client, pid)[0]["chapter_id"]
    assert _stage(client, pid, task_id, chapter_id, "暂存后又撤销。").status_code == 200

    removed = client.delete(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/staged-chapters/{chapter_id}"
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["chapter_count"] == 0
    assert removed.json()["empty"] is True
