"""执行循环 REST（V03-7.4 留白接线）：记账 → 读章 → 改稿 → 暂存 → 记覆盖。

验收口径（计划 M10 第 7/8/11 条）：模型调用必须真的计入任务预算；预算不足
先落等待、已完成部分保留；幂等重放不重复记账；发布仍走一次统一提交。
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from hastings.application.services.drafting_service import WrittenChapter
from hastings.server.v2 import V2Runtime, build_v2_app

MD = """# 雪山旅馆

## 第一章 雪夜

侦探在雪夜抵达旅馆，风雪封住了山路。

## 第二章 空房

走廊尽头有一扇门，门后没有声音。
"""


def _client() -> tuple[TestClient, V2Runtime, str]:
    runtime = V2Runtime.memory()
    calls: list[str] = []

    async def writer(*, system: str, prompt: str) -> WrittenChapter:
        calls.append(prompt)
        return WrittenChapter(text=f"改写稿 {len(calls)}：风雪更紧，门后没有声音。", model="fake")

    runtime.drafting._writer = writer  # type: ignore[assignment]
    runtime.drafting.calls = calls  # type: ignore[attr-defined]
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "执行循环"}).json()["project_id"]
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


def _start(client: TestClient, pid: str, *, budget: int) -> str:
    started = client.post(
        f"/api/v2/projects/{pid}/revision-tasks",
        json={"request_id": f"req-{budget}", "call_budget": budget},
    )
    assert started.status_code == 200, started.text
    return started.json()["task_id"]


def _chapters(client: TestClient, pid: str) -> list[dict[str, object]]:
    return client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]


def _run(client: TestClient, pid: str, task_id: str, *, request_id: str):
    return client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/run",
        json={"instruction": "把这一章写得更克制，保留全部事实。", "request_id": request_id},
    )


def _calls(runtime: V2Runtime) -> list[str]:
    return runtime.drafting.calls  # type: ignore[attr-defined]


def test_budget_stops_before_the_next_model_call() -> None:
    """预算 1 只能改一章：第二章**没有**调用模型，任务落等待且进度保留。"""
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid, budget=1)

    body = _run(client, pid, task_id, request_id="round-1").json()
    assert body["status"] == "stopped", body
    assert len(body["chapters"]) == 1
    assert body["stopped"]["reason"] == "budget_exhausted"
    assert len(_calls(runtime)) == 1, "预算不足时不能再调用模型"
    assert body["task"]["state"] == "waiting"
    assert body["task"]["waiting_reason"] == "budget_exhausted"
    assert body["task"]["budget"]["calls_used"] == 1

    # 暂存里有第一章的改写稿；正式正文还没变。
    staged = client.get(f"/api/v2/projects/{pid}/revision-tasks/{task_id}/staged").json()
    assert len(staged["chapters"]) == 1
    assert "改写稿" not in str(_chapters(client, pid))


def test_replay_does_not_charge_twice() -> None:
    """同一 request_id 重放：不重复记账、不重复调用模型。"""
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid, budget=1)
    first = _run(client, pid, task_id, request_id="round-1").json()
    replay = _run(client, pid, task_id, request_id="round-1").json()

    assert first["status"] == "stopped"
    assert replay["status"] == "replayed"
    assert replay["task"]["budget"]["calls_used"] == 1
    assert len(_calls(runtime)) == 1


def test_continue_then_finish_and_publish_once() -> None:
    """继续追加预算后跑完第二章；发布仍是一次提交，两章一起换版。"""
    client, _runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid, budget=1)
    _run(client, pid, task_id, request_id="round-1")
    head_before = client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]

    continued = client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/continue",
        json={"additional_calls": 1},
    )
    assert continued.status_code == 200, continued.text
    second = _run(client, pid, task_id, request_id="round-2").json()
    assert second["status"] == "executed", second
    assert len(second["chapters"]) == 1
    assert second["task"]["budget"]["calls_used"] == 2

    published = client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/publish",
        json={"operation_id": "op_exec_loop_1"},
    )
    assert published.status_code == 200, published.text
    body = published.json()
    assert len(body["changed_chapters"]) == 2
    texts = [
        client.get(f"/api/v2/projects/{pid}/chapters/{row['chapter_id']}").json()["text"]
        for row in _chapters(client, pid)
    ]
    assert all("改写稿" in text for text in texts)
    assert body["commit_id"] != head_before
    assert _run(client, pid, task_id, request_id="round-3").status_code == 400


def test_base_moved_blocks_run_instead_of_staging_old_text() -> None:
    """任务开始后正式版前进：执行直接拒绝，不用旧基础覆盖新版本。"""
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid, budget=4)
    added = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "extra", "title": "第三章 空椅", "text": "大厅里多了一把空椅子。"},
    )
    assert added.status_code == 200, added.text

    response = _run(client, pid, task_id, request_id="round-moved")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "REVISION_BASE_MOVED_CONFLICT"
    assert _calls(runtime) == []


def test_unknown_chapter_is_rejected_without_model_call() -> None:
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid, budget=4)
    response = client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/run",
        json={
            "instruction": "改这一章",
            "chapter_ids": ["ch_missing"],
            "request_id": "round-unknown",
        },
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "REVISION_STAGE_UNKNOWN_CHAPTER"
    assert _calls(runtime) == []


def test_stopped_task_requires_continue_before_running_again() -> None:
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid, budget=4)
    client.post(f"/api/v2/projects/{pid}/revision-tasks/{task_id}/stop")

    response = _run(client, pid, task_id, request_id="round-after-stop")
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "REVISION_TASK_NOT_RUNNING"
    assert _calls(runtime) == []


def test_default_scope_skips_checked_unchanged_chapters() -> None:
    """已确认无需修改的章不在默认范围：不重复消耗预算、不改它的正文。"""
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid, budget=4)
    chapters = _chapters(client, pid)
    second = str(chapters[1]["chapter_id"])

    read = client.post(
        f"/api/v2/projects/{pid}/revision-work/reads",
        json={"chapter_id": second, "kind": "full"},
    )
    assert read.status_code == 200, read.text
    confirmed = client.post(
        f"/api/v2/projects/{pid}/revision-work/coverage/{second}",
        json={"action": "confirm_unchanged"},
    )
    assert confirmed.status_code == 200, confirmed.text

    body = _run(client, pid, task_id, request_id="round-skip").json()
    assert body["status"] == "executed", body
    assert [row["chapter_id"] for row in body["chapters"]] == [str(chapters[0]["chapter_id"])]
    assert body["task"]["budget"]["calls_used"] == 1
    second_text = client.get(f"/api/v2/projects/{pid}/chapters/{second}").json()["text"]
    assert "改写稿" not in second_text
