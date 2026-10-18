"""V03-5.3 修改稿快照：冻结已保存序号、选区与文本，不随副本前进变化。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app

CHAPTER = "侦探在雪夜抵达旅馆。\n\n前台声称只有一位客人。\n\n壁炉里的火快要灭了。"


def _client() -> tuple[TestClient, V2Runtime, str, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "快照"}).json()["project_id"]
    chapter_id = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={
            "title": "第一章",
            "text": CHAPTER,
            "order_key": "0001",
            "idempotency_key": "imp-1",
        },
    ).json()["chapter_id"]
    return client, runtime, pid, chapter_id


def _start(client: TestClient, pid: str) -> str:
    response = client.post(f"/api/v2/projects/{pid}/edit-sessions", json={})
    assert response.status_code == 200, response.text
    return response.json()["session"]["session_id"]


def _save(
    client: TestClient,
    pid: str,
    session_id: str,
    chapter_id: str,
    text: str,
    seq: int,
    request_id: str,
) -> dict:
    response = client.put(
        f"/api/v2/projects/{pid}/edit-sessions/{session_id}/chapters/{chapter_id}",
        json={"expected_save_seq": seq, "request_id": request_id, "text": text},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _snapshot(client: TestClient, pid: str, session_id: str, **body: object):
    return client.post(f"/api/v2/projects/{pid}/edit-sessions/{session_id}/snapshots", json=body)


def test_snapshot_freezes_saved_sequence_text_and_sources() -> None:
    client, _runtime, pid, chapter_id = _client()
    session = _start(client, pid)
    saved = _save(
        client,
        pid,
        session,
        chapter_id,
        "侦探在雪夜抵达旅馆。\n\n前台声称只有一位客人。",
        0,
        "save-1",
    )

    created = _snapshot(
        client,
        pid,
        session,
        chapter_id=chapter_id,
        save_seq=saved["save_seq"],
        text_hash=saved["content_hash"],
    )

    assert created.status_code == 201, created.text
    snapshot = created.json()
    assert snapshot["save_seq"] == 1
    assert snapshot["text"] == "侦探在雪夜抵达旅馆。\n\n前台声称只有一位客人。"
    assert snapshot["paragraph_sources"] == ["human", "human"]
    assert snapshot["selection"] is None
    assert snapshot["manuscript_ref"] == {
        "kind": "task_snapshot",
        "id": snapshot["snapshot_id"],
        "context_version": 1,
    }

    # 副本继续前进，快照保持冻结。
    _save(
        client,
        pid,
        session,
        chapter_id,
        "侦探在雪夜抵达旅馆。\n\n前台声称只有一位客人。\n\n楼梯尽头有光。",
        1,
        "save-2",
    )
    frozen = client.get(
        f"/api/v2/projects/{pid}/edit-sessions/{session}/snapshots/{snapshot['snapshot_id']}"
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["text"] == snapshot["text"]
    assert frozen.json()["save_seq"] == 1

    # 同一冻结点的重复请求回放同一个引用，不重复落盘。
    repeated = _snapshot(
        client, pid, session, chapter_id=chapter_id, save_seq=1, text_hash=saved["content_hash"]
    )
    assert repeated.status_code == 201
    assert repeated.json()["snapshot_id"] == snapshot["snapshot_id"]


def test_snapshot_rejects_stale_sequence_or_hash() -> None:
    client, _runtime, pid, chapter_id = _client()
    session = _start(client, pid)

    no_draft = _snapshot(client, pid, session, chapter_id=chapter_id, save_seq=1)
    assert no_draft.status_code == 409
    assert no_draft.json()["error"]["code"] == "EDIT_SESSION_CONFLICT"

    saved = _save(client, pid, session, chapter_id, "只有一段。", 0, "save-1")
    stale = _snapshot(client, pid, session, chapter_id=chapter_id, save_seq=saved["save_seq"] + 1)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "EDIT_SNAPSHOT_CONFLICT"
    assert stale.json()["error"]["details"]["current_save_seq"] == saved["save_seq"]

    wrong_hash = _snapshot(
        client,
        pid,
        session,
        chapter_id=chapter_id,
        save_seq=saved["save_seq"],
        text_hash="sha256:not-the-draft",
    )
    assert wrong_hash.status_code == 409
    assert wrong_hash.json()["error"]["code"] == "EDIT_SNAPSHOT_CONFLICT"
    assert wrong_hash.json()["error"]["details"]["current_text_hash"] == saved["content_hash"]


def test_snapshot_selection_freezes_only_selected_paragraphs() -> None:
    client, _runtime, pid, chapter_id = _client()
    session = _start(client, pid)
    saved = _save(client, pid, session, chapter_id, CHAPTER, 0, "save-1")

    selected = _snapshot(
        client,
        pid,
        session,
        chapter_id=chapter_id,
        save_seq=saved["save_seq"],
        start_paragraph=1,
        end_paragraph=2,
    )

    assert selected.status_code == 201, selected.text
    body = selected.json()
    assert body["text"] == "前台声称只有一位客人。"
    assert body["selection"] == {"start_paragraph": 1, "end_paragraph": 2}
    assert body["paragraph_sources"] == ["human"]

    out_of_range = _snapshot(
        client,
        pid,
        session,
        chapter_id=chapter_id,
        save_seq=saved["save_seq"],
        start_paragraph=1,
        end_paragraph=99,
    )
    assert out_of_range.status_code == 400
    assert out_of_range.json()["error"]["code"] == "MANUSCRIPT_INVALID_OPERATION"

    half_selection = _snapshot(
        client,
        pid,
        session,
        chapter_id=chapter_id,
        save_seq=saved["save_seq"],
        start_paragraph=1,
    )
    assert half_selection.status_code == 400
    assert half_selection.json()["error"]["code"] == "MANUSCRIPT_INVALID_OPERATION"


def test_snapshot_survives_finishing_the_session() -> None:
    client, _runtime, pid, chapter_id = _client()
    session = _start(client, pid)
    saved = _save(client, pid, session, chapter_id, "改后正文。", 0, "save-1")
    snapshot = _snapshot(
        client, pid, session, chapter_id=chapter_id, save_seq=saved["save_seq"]
    ).json()

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 200, finished.text

    frozen = client.get(
        f"/api/v2/projects/{pid}/edit-sessions/{session}/snapshots/{snapshot['snapshot_id']}"
    )
    assert frozen.status_code == 200
    assert frozen.json()["text"] == "改后正文。"

    missing = client.get(f"/api/v2/projects/{pid}/edit-sessions/{session}/snapshots/tks_missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "EDIT_SNAPSHOT_NOT_FOUND"
