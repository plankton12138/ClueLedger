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
from hastings.workspace.story_sync import contract_digest


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


def test_marks_fall_back_to_anchor_without_ledger_contribution(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(
        tmp_path, "0001 雨夜.md", "# 第一章\n\n有人经过时木板没有响。\n"
    )
    root = conversation_root(tmp_path / "state", workspace_id)
    seed_clue_identity(root, clue_id="board", title="没有响的木板", key="anchor-mark-identity")
    store = StoryStore(root, Workspace(tmp_path / "book"))
    quote = "有人经过时木板没有响"
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="anchor-mark-node",
        change=StoryChangeSet(
            id="anchor-mark-node",
            base_revision="empty",
            operations=[
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "board",
                    "plan": {"intention": "让人怀疑有人熟悉走廊"},
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n1",
                        "title": "发现异样",
                        "action": "侦探发现木板没有响",
                        "clue_uses": [
                            {
                                "clue_id": "board",
                                "roles": ["plant"],
                                "presentation": quote,
                                "mode": "action",
                            }
                        ],
                    },
                },
            ],
        ),
    )
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None
    start = text.find(quote)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="anchor-mark-bind",
        change=StoryChangeSet(
            id="anchor-mark-bind",
            base_revision=variant.revision,
            operations=[
                {
                    "op": "bind_prose",
                    "binding": {
                        "id": "b1",
                        "node_id": "n1",
                        "document_id": "doc_1",
                        "path": path,
                        "digest": digest,
                        "span_digest": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                        "source": "manual",
                        "start": start,
                        "end": start + len(quote),
                        "quote": quote,
                        "contract_digest": contract_digest(variant, variant.nodes["n1"]),
                    },
                }
            ],
        ),
    )

    body = marks(client, workspace_id, path)

    assert body["state"] == "ready"
    assert len(body["marks"]) == 1
    mark = body["marks"][0]
    assert mark["clue_id"] == "board"
    assert mark["title"] == "没有响的木板"
    assert mark["role"] == "plant"
    assert mark["quote"] == quote
    assert mark["source"] == "anchor"


def test_anchor_marks_match_each_clue_to_its_own_segment(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(
        tmp_path,
        "0001 雨夜.md",
        "# 第一章\n\n颈侧有两个小孔。\n\n鞋底沾着白粉，没有湿泥。\n\n他身上的血似乎少得不寻常。\n",
    )
    root = conversation_root(tmp_path / "state", workspace_id)
    seed_clue_identity(root, clue_id="double", title="双孔与失血", key="anchor-mark-double")
    seed_clue_identity(root, clue_id="powder", title="鞋底白粉", key="anchor-mark-powder")
    store = StoryStore(root, Workspace(tmp_path / "book"))
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="anchor-mark-two-clues",
        change=StoryChangeSet(
            id="anchor-mark-two-clues",
            base_revision="empty",
            operations=[
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "double",
                    "plan": {"intention": "先保留死因疑问"},
                },
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "powder",
                    "plan": {"intention": "留下地点线索"},
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n1",
                        "title": "验尸",
                        "action": "两人检查尸体和鞋底",
                        "clue_uses": [
                            {
                                "clue_id": "double",
                                "roles": ["plant"],
                                "presentation": "颈侧小孔与失血",
                                "mode": "action",
                            },
                            {
                                "clue_id": "powder",
                                "roles": ["plant"],
                                "presentation": "鞋底白粉，没有湿泥",
                                "mode": "action",
                            },
                        ],
                    },
                },
            ],
        ),
    )
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None
    quotes = ["颈侧有两个小孔", "鞋底沾着白粉", "他身上的血似乎少得不寻常"]
    bindings = []
    for index, quote in enumerate(quotes, start=1):
        start = text.find(quote)
        bindings.append(
            {
                "op": "bind_prose",
                "binding": {
                    "id": f"b{index}",
                    "node_id": "n1",
                    "document_id": "doc_1",
                    "path": path,
                    "digest": digest,
                    "span_digest": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                    "source": "manual",
                    "start": start,
                    "end": start + len(quote),
                    "quote": quote,
                    "contract_digest": contract_digest(variant, variant.nodes["n1"]),
                },
            }
        )
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="anchor-mark-two-clues-bind",
        change=StoryChangeSet(
            id="anchor-mark-two-clues-bind",
            base_revision=variant.revision,
            operations=bindings,
        ),
    )

    body = marks(client, workspace_id, path)

    assert body["state"] == "ready"
    assert [(row["clue_id"], row["quote"]) for row in body["marks"]] == [
        ("double", "颈侧有两个小孔"),
        ("powder", "鞋底沾着白粉"),
        ("double", "他身上的血似乎少得不寻常"),
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
