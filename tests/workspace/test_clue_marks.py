"""1.0.6 P3：正文线索下划线优先来自账本证据，缺失时回退到段落锚点。"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.clue_models import ClueCoverage, ClueEntry, ClueIdentity
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.files import Workspace
from hastings.workspace.state import RecentWorkspaces, conversation_root
from hastings.workspace.story_models import StoryChangeSet
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore


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
    assert [(row["quote"], row["sources"]) for row in first_marks["marks"]] == [
        ("有人经过时木板没有响", ["ledger"])
    ]

    second_marks = marks(client, workspace_id, path2)
    assert second_marks["state"] == "ready"
    assert sorted(row["quote"] for row in second_marks["marks"]) == ["木板没有响", "第二具尸体"]
    assert {row["title"] for row in second_marks["marks"]} == {"没有响的木板"}
    # 没有作者计划时用途未定，标记仍是普通线索样式。
    assert {row["kind"] for row in second_marks["marks"]} == {None}


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


def seed_clue_identity(root: Path, *, clue_id: str, title: str, key: str) -> None:
    ledger = ClueLedgerStore(root)

    def apply(store: Any) -> dict[str, str]:
        store.clues[clue_id] = ClueIdentity(
            clue_id=clue_id,
            title=title,
            clue_revision="rev_board",
            created_at="2026-11-01T00:00:00+08:00",
            created_by="author",
        )
        return {"clue_id": clue_id}

    ledger.mutate(
        expected_revision=ledger.read().revision,
        request_key=key,
        kind="test_identity",
        payload={},
        apply=apply,
    )


def test_marks_come_from_explicit_clue_bindings(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(
        tmp_path, "0001 雨夜.md", "# 第一章\n\n有人经过时木板没有响。\n"
    )
    root = conversation_root(tmp_path / "state", workspace_id)
    seed_clue_identity(root, clue_id="board", title="没有响的木板", key="binding-mark-identity")
    store = StoryStore(root, Workspace(tmp_path / "book"))
    quote = "有人经过时木板没有响"
    start_offset = text.find(quote)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="binding-mark",
        change=StoryChangeSet(
            id="binding-mark",
            base_revision="empty",
            operations=[
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "board",
                    "plan": {"kind": "mislead", "intention": "让人以为走廊里没人"},
                },
                {
                    "op": "bind_prose",
                    "binding": {
                        "id": "b1",
                        "clue_id": "board",
                        "document_id": "doc_1",
                        "path": path,
                        "digest": digest,
                        "span_digest": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                        "start": start_offset,
                        "end": start_offset + len(quote),
                        "quote": quote,
                    },
                },
            ],
        ),
    )

    body = marks(client, workspace_id, path)

    assert body["state"] == "ready"
    assert body["invalid"] == []
    mark = body["marks"][0]
    assert (mark["clue_id"], mark["kind"], mark["title"]) == ("board", "mislead", "没有响的木板")
    assert (mark["start"], mark["end"], mark["quote"]) == (
        start_offset,
        start_offset + len(quote),
        quote,
    )
    assert mark["sources"] == ["binding"]


def test_overlapping_clue_marks_are_kept_separately(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(
        tmp_path, "0001 雨夜.md", "# 第一章\n\n钟停在23:00。他没有抬头。\n"
    )
    root = conversation_root(tmp_path / "state", workspace_id)
    seed_clue_identity(root, clue_id="clock", title="停住的钟", key="overlap-clock")
    seed_clue_identity(root, clue_id="silence", title="反常的沉默", key="overlap-silence")
    store = StoryStore(root, Workspace(tmp_path / "book"))
    spans = {"clock": "钟停在23:00。", "silence": "钟停在23:00。他没有抬头。"}
    operations: list[dict[str, Any]] = [
        {"op": "upsert_clue_plan", "clue_id": "clock", "plan": {"kind": "truth"}},
        {"op": "upsert_clue_plan", "clue_id": "silence", "plan": {"kind": "mislead"}},
    ]
    for index, (clue_id, quote) in enumerate(spans.items(), start=1):
        offset = text.find(quote)
        operations.append(
            {
                "op": "bind_prose",
                "binding": {
                    "id": f"b{index}",
                    "clue_id": clue_id,
                    "document_id": "doc_1",
                    "path": path,
                    "digest": digest,
                    "span_digest": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                    "start": offset,
                    "end": offset + len(quote),
                    "quote": quote,
                },
            }
        )
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="overlap-marks",
        change=StoryChangeSet(id="overlap-marks", base_revision="empty", operations=operations),
    )

    body = marks(client, workspace_id, path)

    assert [(row["clue_id"], row["kind"]) for row in body["marks"]] == [
        ("clock", "truth"),
        ("silence", "mislead"),
    ]


def test_lost_binding_quote_is_reported_instead_of_guessed(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(
        tmp_path, "0001 雨夜.md", "# 第一章\n\n有人经过时木板没有响。\n"
    )
    root = conversation_root(tmp_path / "state", workspace_id)
    seed_clue_identity(root, clue_id="board", title="没有响的木板", key="lost-quote-identity")
    store = StoryStore(root, Workspace(tmp_path / "book"))
    quote = "有人经过时木板没有响"
    offset = text.find(quote)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="lost-quote",
        change=StoryChangeSet(
            id="lost-quote",
            base_revision="empty",
            operations=[
                {
                    "op": "bind_prose",
                    "binding": {
                        "id": "b1",
                        "clue_id": "board",
                        "document_id": "doc_1",
                        "path": path,
                        "digest": digest,
                        "span_digest": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                        "start": offset,
                        "end": offset + len(quote),
                        "quote": quote,
                    },
                }
            ],
        ),
    )
    (tmp_path / "book" / path).write_text("# 第一章\n\n门外的脚步声停了。\n", encoding="utf-8")

    body = marks(client, workspace_id, path)

    assert body["marks"] == []
    assert body["invalid"] == [
        {"clue_id": "board", "title": "没有响的木板", "quote": quote, "reason": "missing"}
    ]


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


def test_marking_a_selection_saves_identity_kind_and_precise_span(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(
        tmp_path, "0001 雨夜.md", "# 第一章\n\n有人经过时木板没有响。\n"
    )
    quote = "有人经过时木板没有响"
    offset = text.find(quote)
    created = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json={
            "variant_id": MAIN_VARIANT_ID,
            "expected_revision": "empty",
            "request_key": "mark-selection-01",
            "operations": [
                {"op": "create_clue_identity", "temp_id": "new-board", "title": "没有响的木板"},
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "new-board",
                    "plan": {"kind": "truth", "intention": "读者开始怀疑走廊是否有人"},
                },
                {
                    "op": "bind_prose",
                    "binding": {
                        "id": "b-mark",
                        "clue_id": "new-board",
                        "document_id": "",
                        "path": path,
                        "digest": digest,
                        "start": offset,
                        "end": offset + len(quote),
                        "quote": quote,
                    },
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    stable = created.json()["id_map"]["new-board"]
    store = StoryStore(conversation_root(tmp_path / "state", workspace_id))
    binding = next(iter(store.require_variant(MAIN_VARIANT_ID).bindings.values()))
    assert binding.clue_id == stable
    assert binding.document_id.startswith("doc_")

    body = marks(client, workspace_id, path)
    assert [(row["clue_id"], row["kind"], row["quote"]) for row in body["marks"]] == [
        (stable, "truth", quote)
    ]


def test_stale_selection_is_rejected_without_writing(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(
        tmp_path, "0001 雨夜.md", "# 第一章\n\n有人经过时木板没有响。\n"
    )
    quote = "有人经过时木板没有响"
    offset = text.find(quote)
    (tmp_path / "book" / path).write_text(
        "# 第一章\n\n有人经过时木板没有响。后来门外有脚步声。\n", encoding="utf-8"
    )
    rejected = client.post(
        f"/api/file-workspaces/{workspace_id}/story/changes",
        json={
            "variant_id": MAIN_VARIANT_ID,
            "expected_revision": "empty",
            "request_key": "mark-stale",
            "operations": [
                {"op": "create_clue_identity", "temp_id": "new-board", "title": "没有响的木板"},
                {
                    "op": "bind_prose",
                    "binding": {
                        "id": "b-mark",
                        "clue_id": "new-board",
                        "document_id": "",
                        "path": path,
                        "digest": digest,
                        "start": offset,
                        "end": offset + len(quote),
                        "quote": quote,
                    },
                },
            ],
        },
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["error"]["code"] == "PROSE_SOURCE_STALE"
    assert not StoryStore(conversation_root(tmp_path / "state", workspace_id)).read().document
