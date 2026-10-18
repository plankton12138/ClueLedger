"""V03-5.4 关联撤销：三方逆向、冲突选择与正文/账本一次恢复。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import save_full_text
from hastings.domain.repository.ids import ArtifactId, ProjectId, RevisionId
from hastings.domain.repository.requests import ArtifactChange
from hastings.domain.story.catalog import StoryCatalog
from hastings.server.v2 import V2Runtime, build_v2_app

BEFORE = "原一。\n\n原二。"
CHANGED = "改一。\n\n原二。"
LATER = "改一。\n\n改二。"


def _text_client() -> tuple[TestClient, V2Runtime, str, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "撤销选择"}).json()["project_id"]
    client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp1", "title": "第一章", "text": BEFORE},
    )
    chapter_id = client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"][0][
        "chapter_id"
    ]
    return client, runtime, pid, chapter_id


def _save(client: TestClient, pid: str, chapter_id: str, text: str, key: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/save",
        json={"idempotency_key": key, "full_text": text},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _text(client: TestClient, pid: str, chapter_id: str) -> str:
    return client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()["text"]


def _head(client: TestClient, pid: str) -> str:
    return client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]


def _preview(client: TestClient, pid: str, operation_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v2/projects/{pid}/operations/{operation_id}/undo")
    assert response.status_code == 200, response.text
    return response.json()


def _undo(
    client: TestClient, pid: str, operation_id: str, body: dict[str, Any] | None = None
) -> Any:
    return client.post(f"/api/v2/projects/{pid}/operations/{operation_id}/undo", json=body or {})


def _ledger_entry(runtime: V2Runtime, pid: str, entry_id: str) -> StoryCatalog:
    branch = runtime.service.get_branch(ProjectId(pid), "main")
    assert branch is not None and branch.head_commit_id is not None
    return StoryCatalog.from_snapshot(
        runtime.service.get_snapshot(ProjectId(pid), branch.head_commit_id)
    )


def _seed_ledger(runtime: V2Runtime, pid: str, content: dict[str, str]) -> str:
    runtime.operations.submit(
        project_id=pid,
        operation_id="op_seed_ledger",
        kind="test.seed",
        changes=(
            ArtifactChange(
                artifact_id=ArtifactId("art_char"),
                content=content,
                old_revision_id=None,
                kind="story.character",
            ),
        ),
        summary="预置账本",
    )
    return "art_char"


def _mixed_operation(
    runtime: V2Runtime, pid: str, chapter_id: str, text: str, ledger: dict[str, str]
) -> None:
    """一次提交同时改正文与账本：撤销要恢复两者。"""
    branch = runtime.service.get_branch(ProjectId(pid), "main")
    assert branch is not None and branch.head_commit_id is not None
    catalog = ManuscriptCatalog.from_snapshot(
        runtime.service.get_snapshot(ProjectId(pid), branch.head_commit_id)
    )
    entry = _ledger_entry(runtime, pid, "art_char").get("art_char")
    assert entry is not None
    result = runtime.manuscript_publisher.publish_commands(
        project_id=ProjectId(pid),
        commands=(
            manuscript_command(
                save_full_text,
                chapter_id=chapter_id,
                text=text,
                expected_chapter_base=catalog.chapter_base(chapter_id),
            ),
        ),
        operation_id="op_mixed",
        kind="chapter.save",
        summary="同时改正文与账本",
        extra_changes=(
            ArtifactChange(
                artifact_id=ArtifactId("art_char"),
                content=ledger,
                kind="story.character",
                old_revision_id=RevisionId(entry.revision_id),
            ),
        ),
    )
    assert result.envelope.status == "committed", result.envelope


def _set_ledger(runtime: V2Runtime, pid: str, content: dict[str, str], op: str) -> None:
    runtime.operations.submit(
        project_id=pid,
        operation_id=op,
        kind="test.ledger",
        changes=(
            ArtifactChange(
                artifact_id=ArtifactId("art_char"),
                content=content,
                old_revision_id=None,
                kind="story.character",
            ),
        ),
        summary="独立改账本",
    )


def test_later_non_overlapping_text_is_preserved_by_undo() -> None:
    client, _runtime, pid, chapter_id = _text_client()
    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    _save(client, pid, chapter_id, LATER, "save-2")

    preview = _preview(client, pid, saved["operation_id"])

    assert preview["chapters"][0]["status"] == "mergeable"
    assert preview["requires_choices"] is False
    assert preview["can_undo"] is True

    undone = _undo(client, pid, saved["operation_id"])
    assert undone.status_code == 200, undone.text
    # 操作前的第一段恢复，后来的第二段保留。
    assert _text(client, pid, chapter_id) == "原一。\n\n改二。"


def test_overlapping_text_needs_choice_and_keep_preserves_current() -> None:
    client, _runtime, pid, chapter_id = _text_client()
    saved = _save(client, pid, chapter_id, "改一。", "save-1")
    _save(client, pid, chapter_id, "再改一。", "save-2")
    head = _head(client, pid)

    preview = _preview(client, pid, saved["operation_id"])
    decision_id = preview["decisions"][0]["id"]
    assert preview["chapters"][0]["status"] == "conflict"
    assert preview["can_undo"] is False
    assert preview["requires_choices"] is True

    missing = _undo(client, pid, saved["operation_id"])
    assert missing.status_code == 409
    assert missing.json()["error"]["details"]["required_choices"] == [decision_id]
    assert _head(client, pid) == head

    kept = _undo(
        client,
        pid,
        saved["operation_id"],
        {"choices": {decision_id: "keep"}, "preview_token": preview["preview_token"]},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["status"] == "no_change"
    assert _text(client, pid, chapter_id) == "再改一。"


def test_overlapping_text_explicit_restore_writes_before_text() -> None:
    client, _runtime, pid, chapter_id = _text_client()
    saved = _save(client, pid, chapter_id, "改一。", "save-1")
    _save(client, pid, chapter_id, "再改一。", "save-2")
    preview = _preview(client, pid, saved["operation_id"])
    decision_id = preview["decisions"][0]["id"]

    restored = _undo(client, pid, saved["operation_id"], {"choices": {decision_id: "restore"}})

    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "committed"
    assert _text(client, pid, chapter_id) == BEFORE


def test_stale_preview_token_is_rejected_without_writing() -> None:
    client, _runtime, pid, chapter_id = _text_client()
    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    preview = _preview(client, pid, saved["operation_id"])
    # 让预览之后的事实发生变化。
    _save(client, pid, chapter_id, LATER + "\n\n第三段。", "save-2")
    head = _head(client, pid)

    stale = _undo(
        client,
        pid,
        saved["operation_id"],
        {"preview_token": preview["preview_token"]},
    )

    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_preview_token"]
    assert _head(client, pid) == head


def test_undo_restores_text_and_associated_ledger_in_one_commit() -> None:
    client, runtime, pid, chapter_id = _text_client()
    _seed_ledger(runtime, pid, {"name": "姐姐", "identity": "九点"})
    _mixed_operation(
        runtime, pid, chapter_id, "八点。\n\n原二。", {"name": "姐姐", "identity": "八点"}
    )

    preview = _preview(client, pid, "op_mixed")
    assert preview["chapters"][0]["status"] == "ok"
    assert preview["ledger"][0]["status"] == "ok"
    assert preview["can_undo"] is True

    undone = _undo(client, pid, "op_mixed")
    assert undone.status_code == 200, undone.text
    body = undone.json()
    assert body["restored_chapter_ids"] == [chapter_id]
    assert body["restored_ledger_ids"] == ["art_char"]
    assert _text(client, pid, chapter_id) == BEFORE
    entry = client.get(f"/api/v2/projects/{pid}/ledger/entries/art_char").json()
    assert entry["content"]["identity"] == "九点"


def test_later_ledger_change_needs_choice_and_keep_preserves_it() -> None:
    client, runtime, pid, chapter_id = _text_client()
    _seed_ledger(runtime, pid, {"name": "姐姐", "identity": "九点"})
    _mixed_operation(
        runtime, pid, chapter_id, "八点。\n\n原二。", {"name": "姐姐", "identity": "八点"}
    )
    _set_ledger(runtime, pid, {"name": "姐姐", "identity": "八点半"}, "op_later_ledger")

    preview = _preview(client, pid, "op_mixed")
    decision_id = preview["decisions"][0]["id"]
    assert preview["ledger"][0]["status"] == "conflict"
    assert decision_id == "ledger:art_char"
    assert preview["can_undo"] is False

    missing = _undo(client, pid, "op_mixed")
    assert missing.status_code == 409
    assert missing.json()["error"]["details"]["required_choices"] == [decision_id]

    kept = _undo(client, pid, "op_mixed", {"choices": {decision_id: "keep"}})
    assert kept.status_code == 200, kept.text
    assert kept.json()["kept"] == [
        {"id": "ledger:art_char", "kind": "ledger", "target_id": "art_char"}
    ]
    entry = client.get(f"/api/v2/projects/{pid}/ledger/entries/art_char").json()
    assert entry["content"]["identity"] == "八点半"
    assert _text(client, pid, chapter_id) == BEFORE


def test_later_ledger_change_can_be_explicitly_restored() -> None:
    client, runtime, pid, chapter_id = _text_client()
    _seed_ledger(runtime, pid, {"name": "姐姐", "identity": "九点"})
    _mixed_operation(
        runtime, pid, chapter_id, "八点。\n\n原二。", {"name": "姐姐", "identity": "八点"}
    )
    _set_ledger(runtime, pid, {"name": "姐姐", "identity": "八点半"}, "op_later_ledger")
    preview = _preview(client, pid, "op_mixed")
    decision_id = preview["decisions"][0]["id"]

    restored = _undo(client, pid, "op_mixed", {"choices": {decision_id: "restore"}})

    assert restored.status_code == 200, restored.text
    entry = client.get(f"/api/v2/projects/{pid}/ledger/entries/art_char").json()
    assert entry["content"]["identity"] == "九点"


def test_new_reference_is_reported_and_not_deleted() -> None:
    client, runtime, pid, chapter_id = _text_client()
    _seed_ledger(runtime, pid, {"name": "姐姐", "identity": "九点"})
    _mixed_operation(
        runtime, pid, chapter_id, "八点。\n\n原二。", {"name": "姐姐", "identity": "八点"}
    )
    runtime.operations.submit(
        project_id=pid,
        operation_id="op_new_reference",
        kind="test.reference",
        changes=(
            ArtifactChange(
                artifact_id=ArtifactId("art_sister"),
                content={"name": "妹妹", "knows": "art_char"},
                old_revision_id=None,
                kind="story.character",
            ),
        ),
        summary="新增引用",
    )

    preview = _preview(client, pid, "op_mixed")

    assert preview["references"] == [
        {
            "kind": "ledger_entry",
            "id": "art_sister",
            "refers_to": ["art_char"],
            "status": "kept",
        }
    ]
    # 撤销后新引用仍在；只是恢复被引用的旧内容。
    undone = _undo(client, pid, "op_mixed")
    assert undone.status_code == 200, undone.text
    assert client.get(f"/api/v2/projects/{pid}/ledger/entries/art_sister").status_code == 200
