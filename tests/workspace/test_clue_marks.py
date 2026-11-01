"""1.0.6 P3：正文线索下划线只来自已提交的账本证据。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.clue_models import ClueCoverage, ClueEntry
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.files import Workspace
from hastings.workspace.state import RecentWorkspaces, conversation_root


class _NoModel:
    def stream_response(self, **_: Any) -> Any:
        raise AssertionError("线索标注不应调用模型")


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    book = RecentWorkspaces(tmp_path / "state")
    app = create_workspace_app(
        workspaces=book,
        provider_factory=lambda: ModelSnapshot(
            connection_id="test", display_name="测试连接", model="fake", provider=_NoModel()
        ),
    )
    with TestClient(app) as opened:
        yield opened


def open_workspace(client: TestClient, tmp_path: Path) -> str:
    root = tmp_path / "book"
    response = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": not root.exists()}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def write_chapter(tmp_path: Path, name: str, text: str) -> tuple[str, str, str]:
    path = f"正文/{name}"
    target = tmp_path / "book" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    view = Workspace(tmp_path / "book").read(path)
    return path, view.digest, view.text


def evidence(path: str, digest: str, text: str, quote: str) -> ObservedEvidence:
    start = text.find(quote)
    assert start >= 0
    return ObservedEvidence(
        path=path, digest=digest, start=start, end=start + len(quote), quote=quote
    )


def coverage(path: str, digest: str, chapter: int) -> ClueCoverage:
    return ClueCoverage(
        path=path,
        digest=digest,
        outcome="found",
        task_id="marks",
        at="2026-11-01T00:00:00+08:00",
        chapter=chapter,
    )


def marks(client: TestClient, workspace_id: str, path: str) -> dict[str, Any]:
    response = client.get(
        f"/api/file-workspaces/{workspace_id}/clue-marks",
        params={"chapter_path": path},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_marks_report_plant_mention_and_resolve(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path1, digest1, text1 = write_chapter(
        tmp_path, "0001 雨夜.md", "# 第一章\n\n有人经过时木板没有响。\n"
    )
    path2, digest2, text2 = write_chapter(
        tmp_path,
        "0002 走廊.md",
        "# 第二章\n\n他想起木板没有响，又看见第二具尸体。\n",
    )
    ledger = ClueLedgerStore(conversation_root(tmp_path / "state", workspace_id))
    first = evidence(path1, digest1, text1, "有人经过时木板没有响")
    ledger.publish(
        [
            ClueEntry(
                clue="没有响的木板",
                planted_chapter=1,
                due_chapter=None,
                status="open",
                supports="",
                evidence=first,
                source_chapter=1,
                new_identity=True,
                mode="action",
            )
        ],
        source_chapter=1,
        expected_revision=ledger.read().revision,
        request_key="marks-plant",
        coverage=coverage(path1, digest1, 1),
        task_id="marks",
    )
    clue_id = next(iter(ledger.read().ledger.clues))
    ledger.publish(
        [
            ClueEntry(
                clue="没有响的木板",
                planted_chapter=1,
                due_chapter=None,
                status="resolved",
                supports="",
                evidence=first,
                source_chapter=2,
                clue_id=clue_id,
                mention_evidence=evidence(path2, digest2, text2, "木板没有响"),
                resolution_chapter=2,
                resolution_evidence=evidence(path2, digest2, text2, "第二具尸体"),
                mode="testimony",
            )
        ],
        source_chapter=2,
        expected_revision=ledger.read().revision,
        request_key="marks-mention",
        coverage=coverage(path2, digest2, 2),
        task_id="marks",
    )

    first_marks = marks(client, workspace_id, path1)
    assert first_marks["state"] == "ready"
    assert [(row["role"], row["quote"]) for row in first_marks["marks"]] == [
        ("plant", "有人经过时木板没有响")
    ]

    second_marks = marks(client, workspace_id, path2)
    assert second_marks["state"] == "ready"
    assert sorted(row["role"] for row in second_marks["marks"]) == ["mention", "resolve"]
    assert {row["title"] for row in second_marks["marks"]} == {"没有响的木板"}


def test_marks_are_hidden_when_prose_digest_changed(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(
        tmp_path, "0001 雨夜.md", "# 第一章\n\n有人经过时木板没有响。\n"
    )
    ledger = ClueLedgerStore(conversation_root(tmp_path / "state", workspace_id))
    ledger.publish(
        [
            ClueEntry(
                clue="没有响的木板",
                planted_chapter=1,
                due_chapter=None,
                status="open",
                supports="",
                evidence=evidence(path, digest, text, "有人经过时木板没有响"),
                source_chapter=1,
                new_identity=True,
                mode="action",
            )
        ],
        source_chapter=1,
        expected_revision=ledger.read().revision,
        request_key="marks-stale",
        coverage=coverage(path, digest, 1),
        task_id="marks",
    )

    (tmp_path / "book" / path).write_text(
        "# 第一章\n\n有人经过时木板没有响。后来，门外传来脚步声。\n",
        encoding="utf-8",
    )
    body = marks(client, workspace_id, path)

    assert body["state"] == "stale"
    assert body["marks"] == []


def test_marks_report_unrecorded_and_empty(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, _ = write_chapter(tmp_path, "0001 雨夜.md", "# 第一章\n\n没有线索。\n")

    unrecorded = marks(client, workspace_id, path)
    assert unrecorded["state"] == "unrecorded"

    ledger = ClueLedgerStore(conversation_root(tmp_path / "state", workspace_id))
    ledger.publish(
        [],
        source_chapter=1,
        expected_revision=ledger.read().revision,
        request_key="marks-empty",
        coverage=ClueCoverage(
            path=path,
            digest=digest,
            outcome="empty",
            task_id="marks",
            at="2026-11-01T00:00:00+08:00",
            chapter=1,
        ),
        task_id="marks",
    )
    assert marks(client, workspace_id, path)["state"] == "empty"
