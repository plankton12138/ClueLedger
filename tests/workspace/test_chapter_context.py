"""v1.0.8 M1：正文随章聚合读取。

只验证投影事实：本章实际出现、明确计划、失效引用分开；空结果合法；游标与文件版本绑定。
不调用模型，也不写入任何章节副本。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

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
        raise AssertionError("随章读取不应调用模型")


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


def seed_clue(root: Path, clue_id: str, title: str) -> None:
    ledger = ClueLedgerStore(root)

    def apply(store: Any) -> dict[str, str]:
        store.clues[clue_id] = ClueIdentity(
            clue_id=clue_id,
            title=title,
            clue_revision=f"rev_{uuid4().hex}",
            created_at="2026-11-02T00:00:00+08:00",
            created_by="author",
        )
        return {"clue_id": clue_id}

    ledger.mutate(
        expected_revision=ledger.read().revision,
        request_key=f"seed-{clue_id}",
        kind="test_identity",
        payload={},
        apply=apply,
    )


def binding_row(
    binding_id: str,
    path: str,
    digest: str,
    text: str,
    quote: str,
    *,
    node_id: str | None = None,
    clue_id: str | None = None,
) -> dict[str, Any]:
    start = text.find(quote)
    assert start >= 0
    return {
        "op": "bind_prose",
        "binding": {
            "id": binding_id,
            "node_id": node_id,
            "clue_id": clue_id,
            "document_id": "doc_1",
            "path": path,
            "digest": digest,
            "span_digest": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
            "start": start,
            "end": start + len(quote),
            "quote": quote,
        },
    }


def chapter_context(
    client: TestClient, workspace_id: str, path: str, **params: Any
) -> dict[str, Any]:
    response = client.get(
        f"/api/file-workspaces/{workspace_id}/story/chapter-context",
        params={"chapter_path": path, **params},
    )
    return (
        response.json()
        if response.status_code == 200
        else {"status": response.status_code, **response.json()}
    )


def test_empty_chapter_is_a_legal_empty_result(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, _, _ = write_chapter(tmp_path, "0001 空章.md", "# 第一章\n\n还没有内容。\n")

    body = chapter_context(client, workspace_id, path)

    assert body["chapter_path"] == path
    assert body["variant_id"] is None
    assert body["story_revision"] is None
    assert body["items"] == []
    assert body["truncated"] is False


def test_observed_planned_and_unresolved_are_separated(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(
        tmp_path, "0001 雨夜.md", "# 第一章\n\n木板没有响。他看见第二具尸体。\n"
    )
    root = conversation_root(tmp_path / "state", workspace_id)
    seed_clue(root, "board", "没有响的木板")
    store = StoryStore(root, Workspace(tmp_path / "book"))
    operations: list[dict[str, Any]] = [
        {
            "op": "add_node",
            "node": {"id": "n1", "title": "抵达", "action": "侦探到达"},
        },
        {
            "op": "add_node",
            "node": {"id": "n2", "title": "暗门", "action": "他推开暗门"},
        },
        {
            "op": "add_node",
            "node": {
                "id": "n3",
                "title": "旧信",
                "action": "信件被找出",
                "planned_chapter_number": 1,
            },
        },
        binding_row("b1", path, digest, text, "木板没有响", node_id="n1"),
        binding_row("b2", path, digest, text, "第二具尸体", node_id="n2"),
        binding_row("b3", path, digest, text, "木板没有响", clue_id="board"),
        {"op": "upsert_clue_plan", "clue_id": "board", "plan": {"kind": "truth"}},
    ]
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="chapter-context",
        change=StoryChangeSet(id="chapter-context", base_revision="empty", operations=operations),
    )

    body = chapter_context(client, workspace_id, path)

    placements = {row["object"]["id"]: row["placement"] for row in body["items"]}
    assert placements == {"n1": "observed", "board": "observed", "n3": "planned", "n2": "observed"}

    # 把第二章引文改成正文里不存在的内容：该引用变成本章失效项。
    target = tmp_path / "book" / path
    target.write_text("# 第一章\n\n木板没有响。他转身走了。\n", encoding="utf-8")
    changed = chapter_context(client, workspace_id, path)
    placements = {row["object"]["id"]: row["placement"] for row in changed["items"]}
    assert placements["n2"] == "unresolved"
    node = next(row for row in changed["items"] if row["object"]["id"] == "n2")
    assert node["occurrences"][0]["state"] == "missing"
    assert node["occurrences"][0]["start"] is None


def test_same_clue_multiple_occurrences_are_not_merged(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(
        tmp_path, "0001 雨夜.md", "# 第一章\n\n门开了。他看见门后没有人。\n"
    )
    root = conversation_root(tmp_path / "state", workspace_id)
    seed_clue(root, "door", "打开的门")
    store = StoryStore(root, Workspace(tmp_path / "book"))
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="multi-occurrence",
        change=StoryChangeSet(
            id="multi-occurrence",
            base_revision="empty",
            operations=[
                binding_row("b1", path, digest, text, "门开了", clue_id="door"),
                binding_row("b2", path, digest, text, "门后没有人", clue_id="door"),
            ],
        ),
    )

    body = chapter_context(client, workspace_id, path)

    item = body["items"][0]
    assert item["object"] == {"kind": "clue", "id": "door"}
    assert [row["quote"] for row in item["occurrences"]] == ["门开了", "门后没有人"]
    assert all(row["state"] == "exact" for row in item["occurrences"])
    assert [len(row["binding_ids"]) for row in item["occurrences"]] == [1, 1]


def test_ledger_only_clue_is_visible_with_evidence_id(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(tmp_path, "0001 雨夜.md", "# 第一章\n\n钟停在23:00。\n")
    root = conversation_root(tmp_path / "state", workspace_id)
    ledger = ClueLedgerStore(root)
    quote = "钟停在23:00"
    start = text.find(quote)
    proof = ObservedEvidence(
        path=path, digest=digest, start=start, end=start + len(quote), quote=quote
    )
    ledger.publish(
        [
            ClueEntry(
                clue="停住的钟",
                planted_chapter=1,
                due_chapter=None,
                status="open",
                supports="",
                evidence=proof,
                source_chapter=1,
                new_identity=True,
                mode="narration",
            )
        ],
        source_chapter=1,
        expected_revision=ledger.read().revision,
        request_key="ledger-only",
        coverage=ClueCoverage(
            path=path,
            digest=digest,
            outcome="found",
            task_id="context",
            at="2026-11-02T00:00:00+08:00",
            chapter=1,
        ),
        task_id="context",
    )

    body = chapter_context(client, workspace_id, path)

    item = body["items"][0]
    assert item["title"] == "停住的钟"
    assert item["placement"] == "observed"
    assert item["occurrences"][0]["evidence_ids"]
    assert item["occurrences"][0]["start"] == start


def test_cursor_is_bound_to_chapter_digest(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    path, digest, text = write_chapter(tmp_path, "0001 雨夜.md", "# 第一章\n\n甲。乙。丙。\n")
    root = conversation_root(tmp_path / "state", workspace_id)
    store = StoryStore(root, Workspace(tmp_path / "book"))
    operations: list[dict[str, Any]] = [
        {"op": "add_node", "node": {"id": f"n{index}", "title": title, "action": "行动"}}
        for index, title in enumerate(("甲", "乙", "丙"), start=1)
    ]
    operations += [
        binding_row(f"b{index}", path, digest, text, quote, node_id=f"n{index}")
        for index, quote in enumerate(("甲", "乙", "丙"), start=1)
    ]
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="cursor",
        change=StoryChangeSet(id="cursor", base_revision="empty", operations=operations),
    )

    first = chapter_context(client, workspace_id, path, limit=2)
    assert len(first["items"]) == 2
    assert first["truncated"] is True
    second = chapter_context(client, workspace_id, path, limit=2, cursor=first["next_cursor"])
    assert [row["object"]["id"] for row in second["items"]] == ["n3"]
    assert second["truncated"] is False

    target = tmp_path / "book" / path
    target.write_text("# 第一章\n\n甲。乙。丙。丁。\n", encoding="utf-8")
    stale = chapter_context(client, workspace_id, path, limit=2, cursor=first["next_cursor"])
    assert stale["status"] == 409
    assert stale["error"]["code"] == "CHAPTER_CONTEXT_CURSOR_CONFLICT"


def test_non_chapter_path_is_rejected(client: TestClient, tmp_path: Path) -> None:
    workspace_id = open_workspace(client, tmp_path)
    write_chapter(tmp_path, "0001 雨夜.md", "# 第一章\n\n内容。\n")

    body = chapter_context(client, workspace_id, "设定/人物.md")

    assert body["status"] == 404
    assert body["error"]["code"] == "PROSE_CHAPTER_NOT_FOUND"
