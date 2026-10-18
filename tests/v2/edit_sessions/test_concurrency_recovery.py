"""V03-5.5 并发与收口恢复：多标签 CAS、并发完成、关闭失败后的重启幂等。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app

FIRST = "原一。\n\n原二。"
TAB_A = "标签 A 改的。\n\n原二。"
TAB_B = "标签 B 改的。\n\n原二。"


def _runtime(db_path: str | None = None) -> V2Runtime:
    return V2Runtime.memory() if db_path is None else V2Runtime(db_path=db_path)


def _setup(runtime: V2Runtime) -> tuple[TestClient, str, str, str]:
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "并发走查"}).json()["project_id"]
    chapter_id = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={
            "title": "第一章",
            "text": FIRST,
            "order_key": "0001",
            "idempotency_key": "imp-1",
        },
    ).json()["chapter_id"]
    session = client.post(f"/api/v2/projects/{pid}/edit-sessions", json={}).json()["session"][
        "session_id"
    ]
    return client, pid, chapter_id, session


def _save(
    client: TestClient,
    pid: str,
    session: str,
    chapter_id: str,
    text: str,
    seq: int,
    request_id: str,
) -> Any:
    return client.put(
        f"/api/v2/projects/{pid}/edit-sessions/{session}/chapters/{chapter_id}",
        json={"expected_save_seq": seq, "request_id": request_id, "text": text},
    )


def _finish(client: TestClient, pid: str, session: str) -> Any:
    return client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})


def _head(client: TestClient, pid: str) -> str:
    return client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]


def _current(client: TestClient, pid: str) -> dict[str, Any]:
    return client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()


def test_two_tabs_one_sequence_only_one_save_wins() -> None:
    runtime = _runtime()
    client, pid, chapter_id, session = _setup(runtime)

    first = _save(client, pid, session, chapter_id, TAB_A, 0, "tab-a")
    second = _save(client, pid, session, chapter_id, TAB_B, 0, "tab-b")

    assert first.status_code == 200, first.text
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "EDIT_SAVE_SEQ_CONFLICT"
    draft = _current(client, pid)["drafts"][0]
    assert draft["save_seq"] == 1
    assert draft["text"] == TAB_A

    # 后到的标签重新读序号后再保存，仍可继续，不是永久失效。
    retried = _save(client, pid, session, chapter_id, TAB_B, 1, "tab-b-2")
    assert retried.status_code == 200, retried.text
    assert retried.json()["save_seq"] == 2


def test_concurrent_saves_have_exactly_one_winner(tmp_path) -> None:
    runtime = _runtime(str(tmp_path / "tabs.db"))
    _client, pid, chapter_id, session = _setup(runtime)
    first_client = TestClient(build_v2_app(runtime))
    second_client = TestClient(build_v2_app(runtime))
    barrier = threading.Barrier(2)

    def attempt(client: TestClient, text: str, request_id: str) -> Any:
        barrier.wait(timeout=5)
        return _save(client, pid, session, chapter_id, text, 0, request_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(attempt, first_client, TAB_A, "race-a"),
            pool.submit(attempt, second_client, TAB_B, "race-b"),
        ]
        responses = [future.result(timeout=20) for future in results]

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 409]
    draft = _current(first_client, pid)["drafts"][0]
    assert draft["save_seq"] == 1
    assert draft["text"] in (TAB_A, TAB_B)


def test_concurrent_finish_returns_the_same_commit(tmp_path) -> None:
    runtime = _runtime(str(tmp_path / "finish-race.db"))
    setup_client, pid, chapter_id, session = _setup(runtime)
    assert _save(setup_client, pid, session, chapter_id, TAB_A, 0, "save-1").status_code == 200
    head_before = _head(setup_client, pid)
    first_client = TestClient(build_v2_app(runtime))
    second_client = TestClient(build_v2_app(runtime))
    barrier = threading.Barrier(2)

    def attempt(client: TestClient) -> Any:
        barrier.wait(timeout=5)
        return _finish(client, pid, session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(attempt, first_client), pool.submit(attempt, second_client)]
        responses = [future.result(timeout=20) for future in results]

    assert [response.status_code for response in responses] == [200, 200]
    bodies = [response.json() for response in responses]
    assert bodies[0]["operation_id"] == bodies[1]["operation_id"]
    assert bodies[0]["commit_id"] == bodies[1]["commit_id"]
    # 只产生一个正式版本：head 从保存推进到完成，而不是完成两次。
    assert _head(setup_client, pid) != head_before
    assert _current(setup_client, pid)["session"] is None


def test_finish_close_failure_replays_after_restart(tmp_path) -> None:
    db = str(tmp_path / "close-failure.db")
    runtime = _runtime(db)
    client, pid, chapter_id, session = _setup(runtime)
    assert _save(client, pid, session, chapter_id, TAB_A, 0, "save-1").status_code == 200
    head_before = _head(client, pid)

    original = runtime.service.aux_mutate

    def flaky(
        project_id: Any,
        bucket: str,
        key: str,
        mutate: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    ) -> Any:
        if bucket == "edit_session_current":
            raise RuntimeError("模拟关闭副本失败")
        return original(project_id, bucket, key, mutate)

    runtime.service.aux_mutate = flaky  # type: ignore[method-assign]
    broken = TestClient(build_v2_app(runtime), raise_server_exceptions=False)
    failed = broken.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert failed.status_code == 500
    # 正式版本已经提交，但副本没关掉——这是需要恢复的窗口。
    head_after_failure = _head(client, pid)
    assert head_after_failure != head_before
    assert _current(client, pid)["session"]["status"] == "open"

    runtime.service.aux_mutate = original  # type: ignore[method-assign]
    runtime._repo.close()  # type: ignore[attr-defined]

    restarted = _runtime(db)
    resumed = TestClient(build_v2_app(restarted))
    assert _current(resumed, pid)["session"]["session_id"] == session
    retried = _finish(resumed, pid, session)

    assert retried.status_code == 200, retried.text
    assert retried.json()["commit_id"] == head_after_failure
    # 重试是回放已提交的操作，不再发布第二个版本。
    assert _head(resumed, pid) == head_after_failure
    assert _current(resumed, pid)["session"] is None
    assert resumed.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()["text"] == TAB_A


def test_closed_session_archive_failure_self_heals_on_next_start() -> None:
    runtime = _runtime()
    client, pid, chapter_id, session = _setup(runtime)
    assert _save(client, pid, session, chapter_id, TAB_A, 0, "save-1").status_code == 200
    head_before = _head(client, pid)

    original = runtime.service.aux_mutate

    def flaky(
        project_id: Any,
        bucket: str,
        key: str,
        mutate: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    ) -> Any:
        if bucket == "edit_session":
            raise RuntimeError("模拟归档失败")
        return original(project_id, bucket, key, mutate)

    runtime.service.aux_mutate = flaky  # type: ignore[method-assign]
    broken = TestClient(build_v2_app(runtime), raise_server_exceptions=False)
    failed = broken.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert failed.status_code == 500
    # 当前指针已经写成 closed，只是归档没落盘。
    assert _current(client, pid)["session"]["status"] == "closed"
    assert _head(client, pid) != head_before

    runtime.service.aux_mutate = original  # type: ignore[method-assign]
    retried = _finish(client, pid, session)
    assert retried.status_code == 200, retried.text
    assert retried.json()["commit_id"] == _head(client, pid)

    # 下一次「开始修改」补完归档，再开新副本；旧会话仍可读。
    started = client.post(f"/api/v2/projects/{pid}/edit-sessions", json={}).json()
    assert started["session"]["session_id"] != session
    archived = client.get(f"/api/v2/projects/{pid}/edit-sessions/{session}").json()
    assert archived["session"]["status"] == "closed"
    assert archived["session"]["chapters"] == [chapter_id]
