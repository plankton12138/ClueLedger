"""有限执行任务 REST（V03-7.3）：预算、等待、停止与继续。

验收口径（计划 M10 第 7/8/11 条 / 阶段指南 §11.2）：到上限先落等待、内部进度
保留；继续由作者显式触发并追加预算；同一作品不能靠新建任务绕开预算。
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

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
    pid = client.post("/api/v2/projects", json={"name": "有限执行"}).json()["project_id"]
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


def _start(client: TestClient, pid: str, *, request_id: str = "req-1", budget: int | None = None):
    body: dict[str, object] = {"request_id": request_id}
    if budget is not None:
        body["call_budget"] = budget
    return client.post(f"/api/v2/projects/{pid}/revision-tasks", json=body)


def _chapters(client: TestClient, pid: str) -> list[str]:
    rows = client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]
    return [row["chapter_id"] for row in rows]


def test_start_without_manuscript_reports_next_step() -> None:
    client, _runtime, pid = _client()
    response = _start(client, pid)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "REVISION_WORK_EMPTY_PROJECT"


def test_start_declares_budget_and_pending_publication() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    view = _start(client, pid).json()
    assert view["state"] == "queued"
    assert view["attempt"] == 1
    assert view["grant_version"] == 1
    assert view["budget"] == {
        "call_limit": 12,
        "calls_used": 0,
        "remaining": 12,
        "repair_rounds": 0,
        "max_repair_rounds": 2,
        "exhausted": False,
    }
    assert view["coverage"]["complete"] is False
    assert view["coverage"]["total_count"] == 2
    assert view["publication"]["ready"] is False
    codes = [b["code"] for b in view["publication"]["blockers"]]
    assert "REVISION_COVERAGE_INCOMPLETE_CONFLICT" in codes


def test_start_with_smaller_budget_and_replay_same_request() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    first = _start(client, pid, budget=5).json()
    assert first["budget"]["call_limit"] == 5
    again = _start(client, pid, budget=5).json()
    assert again["task_id"] == first["task_id"]
    assert again["budget"]["calls_used"] == 0


def test_second_task_while_active_is_rejected() -> None:
    """不能靠新建任务绕预算：进行中的任务必须先继续或停止。"""
    client, _runtime, pid = _client()
    _import(client, pid)
    _start(client, pid, request_id="req-1")
    conflict = _start(client, pid, request_id="req-2")
    assert conflict.status_code == 409, conflict.text
    error = conflict.json()["error"]
    assert error["code"] == "REVISION_TASK_ACTIVE_CONFLICT"
    assert "继续" in error["fix"]


def test_budget_above_default_is_rejected() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    response = _start(client, pid, budget=13)
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "REVISION_BUDGET_INVALID"
    assert "12" in error["fix"]


def test_stop_then_continue_adds_budget_and_attempt() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    task = _start(client, pid, budget=4).json()
    task_id = task["task_id"]

    stopped = client.post(f"/api/v2/projects/{pid}/revision-tasks/{task_id}/stop").json()
    assert stopped["state"] == "cancelled"

    resumed = client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/continue",
        json={"additional_calls": 3},
    ).json()
    assert resumed["state"] == "running"
    assert resumed["attempt"] == 2
    assert resumed["grant_version"] == 2
    assert resumed["budget"]["call_limit"] == 7
    assert resumed["budget"]["calls_used"] == 0
    assert resumed["requires_revalidation"] is True


def test_budget_exhaustion_pauses_and_preserves_progress() -> None:
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid, budget=3).json()["task_id"]
    runtime.revision_tasks.begin(pid, task_id)

    runtime.revision_tasks.consume_calls(pid, task_id, calls=3)
    with pytest.raises(CoverageViolation) as exc:
        runtime.revision_tasks.consume_calls(pid, task_id)
    assert exc.value.code == "REVISION_BUDGET_EXHAUSTED_CONFLICT"

    paused = client.get(f"/api/v2/projects/{pid}/revision-tasks/{task_id}").json()
    assert paused["state"] == "waiting"
    assert paused["waiting_reason"] == "budget_exhausted"
    assert paused["budget"]["calls_used"] == 3  # 已用进度保留
    assert paused["publication"]["ready"] is False
    codes = [b["code"] for b in paused["publication"]["blockers"]]
    assert "REVISION_BUDGET_EXHAUSTED_CONFLICT" in codes

    # 等待中再开新任务仍然被拒——预算只能靠「继续」追加。
    assert _start(client, pid, request_id="req-2").status_code == 409

    resumed = client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/continue",
        json={"additional_calls": 2},
    ).json()
    assert resumed["state"] == "running"
    assert resumed["budget"]["call_limit"] == 5
    assert resumed["budget"]["calls_used"] == 3
    assert resumed["budget"]["remaining"] == 2


def test_repair_limit_pauses_task() -> None:
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid).json()["task_id"]
    runtime.revision_tasks.begin(pid, task_id)
    runtime.revision_tasks.start_repair(pid, task_id)
    runtime.revision_tasks.start_repair(pid, task_id)
    with pytest.raises(CoverageViolation) as exc:
        runtime.revision_tasks.start_repair(pid, task_id)
    assert exc.value.code == "REVISION_REPAIR_LIMIT_CONFLICT"

    view = client.get(f"/api/v2/projects/{pid}/revision-tasks/{task_id}").json()
    assert view["state"] == "waiting"
    assert view["waiting_reason"] == "repair_limit"
    assert view["budget"]["repair_rounds"] == 2


def test_publication_ready_after_every_chapter_is_covered() -> None:
    client, runtime, pid = _client()
    _import(client, pid)
    task_id = _start(client, pid).json()["task_id"]
    runtime.revision_tasks.begin(pid, task_id)
    for chapter_id in _chapters(client, pid):
        response = client.post(
            f"/api/v2/projects/{pid}/revision-work/coverage/{chapter_id}",
            json={"action": "changed"},
        )
        assert response.status_code == 200, response.text

    view = client.get(f"/api/v2/projects/{pid}/revision-tasks/{task_id}").json()
    assert view["coverage"]["complete"] is True
    assert view["publication"] == {
        "ready": True,
        "blockers": [],
        "head_commit_id": view["base_commit_id"],
    }


def test_new_task_after_stop_opens_fresh_work_version() -> None:
    """新任务是一份新授权：不继承上一轮用旧预算读出的覆盖进度。"""
    client, _runtime, pid = _client()
    _import(client, pid)
    first = _start(client, pid, request_id="req-1").json()
    chapter = _chapters(client, pid)[0]
    read = client.post(
        f"/api/v2/projects/{pid}/revision-work/reads",
        json={"chapter_id": chapter, "kind": "full"},
    )
    assert read.status_code == 200, read.text
    client.post(f"/api/v2/projects/{pid}/revision-tasks/{first['task_id']}/stop")

    second = _start(client, pid, request_id="req-2").json()
    assert second["work_id"] != first["work_id"]
    assert second["coverage"]["covered_count"] == 0
    assert second["coverage"]["total_count"] == 2
    assert second["budget"]["call_limit"] == 12

    old = client.get(f"/api/v2/projects/{pid}/revision-tasks/{first['task_id']}").json()
    assert old["state"] == "cancelled"
    assert old["task_id"] == first["task_id"]


def test_current_and_unknown_task() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    assert client.get(f"/api/v2/projects/{pid}/revision-tasks/current").json() == {
        "task_id": None,
        "opened": False,
    }
    task_id = _start(client, pid).json()["task_id"]
    current = client.get(f"/api/v2/projects/{pid}/revision-tasks/current").json()
    assert current["task_id"] == task_id

    missing = client.get(f"/api/v2/projects/{pid}/revision-tasks/rtk_missing")
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "REVISION_TASK_NOT_FOUND"
