"""基础关联撤销（V03-4.6）：预览与冲突判定，只读。

覆盖：可选撤销的正文操作、之后又被修改的冲突、同时改账本的阻断、
首次提交无旧版本、未知操作、以及「预览不写版本」。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from hastings.domain.repository.ids import ArtifactId
from hastings.domain.repository.requests import ArtifactChange
from hastings.server.v2 import V2Runtime, build_v2_app

ORIGINAL = "侦探在雪夜抵达旅馆，前台没有人。"
CHANGED = "侦探在雪夜抵达旅馆，前台站着一个女人。"


def _client() -> tuple[TestClient, str, str, V2Runtime]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "撤销测试"}).json()["project_id"]
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


def _preview(client: TestClient, pid: str, operation_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v2/projects/{pid}/operations/{operation_id}/undo")
    assert response.status_code == 200, response.text
    return response.json()


def test_preview_reports_the_chapter_and_allows_undo() -> None:
    client, pid, chapter_id, _ = _client()
    saved = _save(client, pid, chapter_id, CHANGED, "save-1")

    preview = _preview(client, pid, saved["operation_id"])

    assert preview["can_undo"] is True
    assert preview["blocked_reason"] == ""
    assert preview["before_commit_id"] != preview["after_commit_id"]
    row = preview["chapters"][0]
    assert row["chapter_id"] == chapter_id
    assert row["status"] == "ok"
    assert row["chars_before"] == len(ORIGINAL)
    assert row["chars_after"] == len(CHANGED)
    assert row["before_base"] != row["after_base"]
    assert preview["ledger"] == []


def test_a_later_save_is_a_conflict_and_requires_choices() -> None:
    client, pid, chapter_id, _ = _client()
    first = _save(client, pid, chapter_id, CHANGED, "save-1")
    _save(client, pid, chapter_id, CHANGED + "她在等一张字条。", "save-2")

    preview = _preview(client, pid, first["operation_id"])

    assert preview["can_undo"] is False
    assert preview["requires_choices"] is True
    assert preview["hard_blocked"] is False
    assert preview["chapters"][0]["status"] == "conflict"
    assert preview["decisions"][0]["kind"] == "text"
    assert preview["conflicts"][0].startswith("这一处正文")
    assert "需要选择" in preview["blocked_reason"]


def test_operation_that_created_a_ledger_entry_is_blocked() -> None:
    client, pid, _chapter, runtime = _client()
    envelope = runtime.operations.submit(
        project_id=pid,
        operation_id="op_ledger_mix",
        kind="test.mixed",
        changes=(
            ArtifactChange(
                artifact_id=ArtifactId("art_character_x"),
                content={"name": "姐姐", "identity": "旅馆经营者"},
                old_revision_id=None,
                kind="story.character",
            ),
        ),
        summary="新增人物与正文无关的测试提交",
        changed_chapter_ids=(),
    )
    assert envelope.commit_id

    preview = _preview(client, pid, "op_ledger_mix")

    assert preview["can_undo"] is False
    assert [row["artifact_id"] for row in preview["ledger"]] == ["art_character_x"]
    assert preview["ledger"][0]["status"] == "created"
    assert "新建了账本条目" in preview["blocked_reason"]


def test_first_commit_has_no_previous_version() -> None:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "首次提交"}).json()["project_id"]
    runtime.operations.submit(
        project_id=pid,
        operation_id="op_first",
        kind="test.first",
        changes=(
            ArtifactChange(
                artifact_id=ArtifactId("art_character_first"),
                content={"name": "侦探"},
                old_revision_id=None,
                kind="story.character",
            ),
        ),
        summary="作品首次提交",
    )

    preview = _preview(client, pid, "op_first")

    assert preview["can_undo"] is False
    assert "首次提交" in preview["blocked_reason"]


def test_unknown_operation_is_not_found() -> None:
    client, pid, _chapter, _ = _client()
    response = client.get(f"/api/v2/projects/{pid}/operations/op_missing/undo")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "OPERATION_NOT_FOUND"


def test_preview_never_writes() -> None:
    client, pid, chapter_id, _ = _client()
    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    head_before = client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]

    _preview(client, pid, saved["operation_id"])
    _preview(client, pid, saved["operation_id"])

    assert client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"] == head_before


def test_open_edit_session_blocks_undo() -> None:
    client, pid, chapter_id, _ = _client()
    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    started = client.post(f"/api/v2/projects/{pid}/edit-sessions")
    assert started.status_code in (200, 201), started.text

    preview = _preview(client, pid, saved["operation_id"])

    assert preview["can_undo"] is False
    assert "未结束的手工修改副本" in preview["blocked_reason"]
