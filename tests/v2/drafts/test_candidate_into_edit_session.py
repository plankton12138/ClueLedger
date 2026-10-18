"""V03-5.3 候选采用进副本：只写副本、标记生成来源、与正式采用分开。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.application.services.drafting_service import WrittenChapter
from hastings.server.v2 import V2Runtime, build_v2_app

BASE = "侦探在雪夜抵达旅馆。\n\n前台声称只有一位客人。\n\n壁炉里的火快要灭了。"
GENERATED = "前台补充说，他听见楼梯上有脚步声。"


def _runtime(generated: str = GENERATED) -> V2Runtime:
    runtime = V2Runtime.memory()

    async def writer(*, system: str, prompt: str) -> WrittenChapter:
        del system, prompt
        return WrittenChapter(text=generated, model="test-model")

    runtime.drafting._writer = writer  # type: ignore[assignment]
    return runtime


def _client(runtime: V2Runtime) -> tuple[TestClient, str, str]:
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "候选进副本"}).json()["project_id"]
    chapter_id = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={
            "title": "第一章",
            "text": BASE,
            "order_key": "0001",
            "idempotency_key": "imp-1",
        },
    ).json()["chapter_id"]
    return client, pid, chapter_id


def _start(client: TestClient, pid: str) -> str:
    return client.post(f"/api/v2/projects/{pid}/edit-sessions", json={}).json()["session"][
        "session_id"
    ]


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


def _candidate(client: TestClient, pid: str, *, target: str = "") -> dict:
    body: dict[str, object] = {"instruction": "改写这一章的前台段落"}
    if target:
        body["target"] = target
    response = client.post(f"/api/v2/projects/{pid}/drafts", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _apply(
    client: TestClient,
    pid: str,
    candidate: dict,
    *,
    session_id: str,
    expected_save_seq: int,
    request_id: str = "apply-1",
    preview_hash: str | None = None,
    **extra: object,
):
    body: dict[str, object] = {
        "session_id": session_id,
        "expected_save_seq": expected_save_seq,
        "request_id": request_id,
        "preview_hash": preview_hash or candidate["preview_hash"],
        **extra,
    }
    return client.post(
        f"/api/v2/projects/{pid}/drafts/{candidate['candidate_id']}/apply-to-edit-session",
        json=body,
    )


def _draft(client: TestClient, pid: str, session_id: str, chapter_id: str) -> dict:
    view = client.get(f"/api/v2/projects/{pid}/edit-sessions/{session_id}").json()
    return next(item for item in view["drafts"] if item["chapter_id"] == chapter_id)


def test_candidate_replaces_selection_and_marks_generated_paragraph() -> None:
    runtime = _runtime()
    client, pid, chapter_id = _client(runtime)
    session = _start(client, pid)
    _save(client, pid, session, chapter_id, BASE, 0, "save-1")
    candidate = _candidate(client, pid, target=chapter_id)

    applied = _apply(
        client,
        pid,
        candidate,
        session_id=session,
        expected_save_seq=1,
        start_paragraph=1,
        end_paragraph=2,
    )

    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["status"] == "applied"
    draft = body["draft"]
    assert draft["save_seq"] == 2
    assert draft["text"] == (
        "侦探在雪夜抵达旅馆。\n\n前台补充说，他听见楼梯上有脚步声。\n\n壁炉里的火快要灭了。"
    )
    # 选区外的人工段落来源不变，写入的一段是生成来源。
    assert draft["paragraph_sources"] == ["human", "agent", "human"]
    # 正式正文仍是原稿：进副本不等于发布。
    assert client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()["text"] == BASE

    stored = client.get(f"/api/v2/projects/{pid}/drafts/{candidate['candidate_id']}").json()
    assert stored["status"] == "applied"
    assert stored["applied_session_id"] == session
    assert stored["applied_chapter_id"] == chapter_id
    assert stored["applied_save_seq"] == 2


def test_later_save_keeps_generated_paragraph_until_author_edits_it() -> None:
    runtime = _runtime()
    client, pid, chapter_id = _client(runtime)
    session = _start(client, pid)
    _save(client, pid, session, chapter_id, BASE, 0, "save-1")
    candidate = _candidate(client, pid, target=chapter_id)
    _apply(
        client,
        pid,
        candidate,
        session_id=session,
        expected_save_seq=1,
        start_paragraph=1,
        end_paragraph=2,
    )

    # 作者改了第一段，生成的第二段一个字没动：来源不应被冲成人工。
    saved = _save(
        client,
        pid,
        session,
        chapter_id,
        "侦探在清晨抵达旅馆。\n\n前台补充说，他听见楼梯上有脚步声。\n\n壁炉里的火快要灭了。",
        2,
        "save-2",
    )
    assert saved["paragraph_sources"] == ["human", "agent", "human"]

    # 作者动手改生成段：它就不再是生成文字。
    edited = _save(
        client,
        pid,
        session,
        chapter_id,
        "侦探在清晨抵达旅馆。\n\n前台改口说，他什么也没听见。\n\n壁炉里的火快要灭了。",
        3,
        "save-3",
    )
    assert edited["paragraph_sources"] == ["human", "human", "human"]


def test_apply_requires_current_preview_and_save_sequence() -> None:
    runtime = _runtime()
    client, pid, chapter_id = _client(runtime)
    session = _start(client, pid)
    _save(client, pid, session, chapter_id, BASE, 0, "save-1")
    candidate = _candidate(client, pid, target=chapter_id)

    stale = _apply(
        client,
        pid,
        candidate,
        session_id=session,
        expected_save_seq=0,
        request_id="apply-stale",
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "EDIT_SAVE_SEQ_CONFLICT"

    wrong_preview = _apply(
        client,
        pid,
        candidate,
        session_id=session,
        expected_save_seq=1,
        request_id="apply-hash",
        preview_hash="sha256:not-the-preview",
    )
    assert wrong_preview.status_code == 409
    assert wrong_preview.json()["error"]["code"] == "DRAFT_PREVIEW_CONFLICT"

    # 失败请求没有推进副本。
    assert _draft(client, pid, session, chapter_id)["save_seq"] == 1


def test_apply_is_idempotent_and_consumes_the_candidate() -> None:
    runtime = _runtime()
    client, pid, chapter_id = _client(runtime)
    session = _start(client, pid)
    _save(client, pid, session, chapter_id, BASE, 0, "save-1")
    candidate = _candidate(client, pid, target=chapter_id)

    first = _apply(
        client, pid, candidate, session_id=session, expected_save_seq=1, request_id="apply-1"
    )
    assert first.status_code == 200, first.text
    replay = _apply(
        client, pid, candidate, session_id=session, expected_save_seq=1, request_id="apply-1"
    )
    assert replay.status_code == 200
    assert replay.json()["draft"]["save_seq"] == 2

    repeated = _apply(
        client, pid, candidate, session_id=session, expected_save_seq=2, request_id="apply-2"
    )
    assert repeated.status_code == 400
    assert repeated.json()["error"]["code"] == "DRAFT_CANDIDATE_NOT_ADOPTABLE"

    # 已用进副本的候选不能再单独正式采用、丢弃或继续调整。
    adopted = client.post(
        f"/api/v2/projects/{pid}/drafts/{candidate['candidate_id']}/adopt",
        json={"preview_hash": candidate["preview_hash"]},
    )
    assert adopted.status_code == 400
    assert adopted.json()["error"]["code"] == "DRAFT_CANDIDATE_NOT_ADOPTABLE"
    discarded = client.post(f"/api/v2/projects/{pid}/drafts/{candidate['candidate_id']}/discard")
    assert discarded.status_code == 400
    revised = client.post(
        f"/api/v2/projects/{pid}/drafts/{candidate['candidate_id']}/revise",
        json={"instruction": "再改一次"},
    )
    assert revised.status_code == 400
    # 正式正文没有被单独采用一次。
    assert client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()["text"] == BASE


def test_apply_without_selection_replaces_whole_draft() -> None:
    runtime = _runtime()
    client, pid, chapter_id = _client(runtime)
    session = _start(client, pid)
    candidate = _candidate(client, pid, target=chapter_id)

    applied = _apply(
        client, pid, candidate, session_id=session, expected_save_seq=0, request_id="apply-full"
    )

    assert applied.status_code == 200, applied.text
    draft = applied.json()["draft"]
    assert draft["save_seq"] == 1
    assert draft["text"] == GENERATED
    assert draft["paragraph_sources"] == ["agent"]


def test_new_chapter_candidate_cannot_enter_edit_session() -> None:
    runtime = _runtime()
    client, pid, _chapter_id = _client(runtime)
    session = _start(client, pid)
    candidate = _candidate(client, pid)

    applied = _apply(
        client, pid, candidate, session_id=session, expected_save_seq=0, request_id="apply-new"
    )

    assert applied.status_code == 409
    assert applied.json()["error"]["code"] == "DRAFT_TARGET_CONFLICT"


def test_selection_without_draft_is_rejected() -> None:
    runtime = _runtime()
    client, pid, chapter_id = _client(runtime)
    session = _start(client, pid)
    candidate = _candidate(client, pid, target=chapter_id)

    applied = _apply(
        client,
        pid,
        candidate,
        session_id=session,
        expected_save_seq=0,
        request_id="apply-selection",
        start_paragraph=0,
        end_paragraph=1,
    )

    assert applied.status_code == 400
    assert applied.json()["error"]["code"] == "MANUSCRIPT_INVALID_OPERATION"
    view = client.get(f"/api/v2/projects/{pid}/edit-sessions/{session}").json()
    assert view["drafts"] == []


def test_finish_keeps_mixed_sources_in_operation_refs() -> None:
    runtime = _runtime()
    client, pid, chapter_id = _client(runtime)
    session = _start(client, pid)
    _save(client, pid, session, chapter_id, BASE, 0, "save-1")
    candidate = _candidate(client, pid, target=chapter_id)
    _apply(
        client,
        pid,
        candidate,
        session_id=session,
        expected_save_seq=1,
        start_paragraph=1,
        end_paragraph=2,
    )

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 200, finished.text
    result = finished.json()
    assert result["status"] == "committed"

    operation = client.get(f"/api/v2/projects/{pid}/operations/{result['operation_id']}").json()
    runs = operation["refs"]["paragraph_sources"][chapter_id]
    assert runs == [
        {"start": 0, "end": 1, "source": "human"},
        {"start": 1, "end": 2, "source": "agent"},
        {"start": 2, "end": 3, "source": "human"},
    ]
    assert client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()["text"] == (
        "侦探在雪夜抵达旅馆。\n\n前台补充说，他听见楼梯上有脚步声。\n\n壁炉里的火快要灭了。"
    )


def test_merged_chapter_provenance_is_unknown_not_human() -> None:
    runtime = _runtime()
    client, pid, chapter_id = _client(runtime)
    session = _start(client, pid)
    # 作者只改第一段；副本打开后正式稿改了第二段——完成时干净合并。
    _save(
        client,
        pid,
        session,
        chapter_id,
        "侦探在清晨抵达旅馆。\n\n前台声称只有一位客人。\n\n壁炉里的火快要灭了。",
        0,
        "save-1",
    )
    advanced = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/save",
        json={
            "idempotency_key": "formal-1",
            "full_text": "侦探在雪夜抵达旅馆。\n\n前台空无一人。\n\n壁炉里的火快要灭了。",
        },
    )
    assert advanced.status_code == 200, advanced.text

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 200, finished.text
    result = finished.json()
    assert chapter_id in result["merged_chapters"]

    operation = client.get(f"/api/v2/projects/{pid}/operations/{result['operation_id']}").json()
    runs = operation["refs"]["paragraph_sources"][chapter_id]
    # 合并稿混入正式稿内容，逐段来源无法再精确判断：保守标 unknown，
    # 绝不能把可能来自生成的内容算成人工编辑。
    assert runs == [{"start": 0, "end": 3, "source": "unknown"}]
