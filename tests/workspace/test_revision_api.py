"""1.2 修订学习 P1：正文保存生成修订、差异可回看、写作约定可读写。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.state import RecentWorkspaces

from .chapter_seed import seed_chapter


class _NoModel:
    def stream_response(self, **_: Any) -> Any:
        raise AssertionError("修订接口不应调用模型")


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


def _seed(client: TestClient, tmp_path: Path) -> tuple[str, str, str, dict[str, Any]]:
    root = tmp_path / "book"
    opened = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": not root.exists()}
    )
    workspace_id = opened.json()["id"]
    base = f"/api/file-workspaces/{workspace_id}/story"
    body = seed_chapter(
        tmp_path / "state", workspace_id, root, title="第七章", request_key="chapter:seed"
    )
    draft = "# 第七章\n\n林岚很紧张，她害怕许砚继续追问。\n\n窗外的雨没有停。\n"
    saved = client.put(
        f"{base}/chapters/{body['chapter_id']}/prose",
        json={
            "text": draft,
            "expected_digest": body["prose_digest"],
            "draft_revision": "draft:1",
            "candidate_id": None,
            "request_key": "save:first",
        },
    )
    assert saved.status_code == 200, saved.text
    return workspace_id, base, body["chapter_id"], saved.json()


def _save(
    client: TestClient,
    base: str,
    chapter_id: str,
    *,
    text: str,
    digest: str,
    key: str,
) -> dict[str, Any]:
    response = client.put(
        f"{base}/chapters/{chapter_id}/prose",
        json={
            "text": text,
            "expected_digest": digest,
            "draft_revision": key,
            "candidate_id": None,
            "request_key": key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_first_draft_is_not_a_revision(client: TestClient, tmp_path: Path) -> None:
    workspace_id, base, _, saved = _seed(client, tmp_path)
    created = seed_chapter(
        tmp_path / "state",
        workspace_id,
        tmp_path / "book",
        title="第八章",
        expected_plan_revision=saved["plan_revision"],
        request_key="chapter:8",
    )
    revisions = client.get(f"{base}/chapters/{created['chapter_id']}/revisions")

    assert revisions.status_code == 200, revisions.text
    assert revisions.json()["items"] == []


def test_author_edit_creates_analyzable_revision(client: TestClient, tmp_path: Path) -> None:
    _, base, chapter_id, saved = _seed(client, tmp_path)
    edited = "# 第七章\n\n林岚把杯沿转了半圈。「你刚才说到哪儿了？」\n\n窗外的雨没有停。\n"
    result = _save(
        client,
        base,
        chapter_id,
        text=edited,
        digest=saved["prose_digest"],
        key="save:edit-1",
    )

    assert result["revision_id"], result
    listed = client.get(f"{base}/chapters/{chapter_id}/revisions").json()
    assert [item["id"] for item in listed["items"]] == [result["revision_id"]]
    item = listed["items"][0]
    assert item["source"] == "author"
    assert item["analyzable"] is True
    assert item["active_hunk_count"] == 1
    assert item["hunk_count"] == 1

    detail = client.get(f"{base}/revisions/{result['revision_id']}").json()
    hunk = detail["hunks"][0]
    assert hunk["active"] is True
    assert hunk["before"].startswith("林岚很紧张")
    assert hunk["after"].startswith("林岚把杯沿转了半圈")
    assert any(span["kind"] == "insert" for span in hunk["inline"])


def test_revision_stays_analyzable_after_unrelated_edit(client: TestClient, tmp_path: Path) -> None:
    _, base, chapter_id, saved = _seed(client, tmp_path)
    edited = "# 第七章\n\n林岚把杯沿转了半圈。「你刚才说到哪儿了？」\n\n窗外的雨没有停。\n"
    first = _save(client, base, chapter_id, text=edited, digest=saved["prose_digest"], key="save:a")
    second_text = edited.replace("窗外的雨没有停。", "窗外的雨小了些，檐口还在滴水。")
    second = _save(
        client, base, chapter_id, text=second_text, digest=first["prose_digest"], key="save:b"
    )

    items = client.get(f"{base}/chapters/{chapter_id}/revisions").json()["items"]
    by_id = {item["id"]: item for item in items}

    assert by_id[first["revision_id"]]["analyzable"] is True
    # 第二次只改环境描写，本身也是一次修订；但第一次的对话改写仍然可以分析。
    assert by_id[second["revision_id"]]["active_hunk_count"] == 1


def test_reverting_the_edit_leaves_history_only(client: TestClient, tmp_path: Path) -> None:
    _, base, chapter_id, saved = _seed(client, tmp_path)
    original = (tmp_path / "book" / saved["path"]).read_text(encoding="utf-8")
    edited = "# 第七章\n\n林岚把杯沿转了半圈。「你刚才说到哪儿了？」\n\n窗外的雨没有停。\n"
    first = _save(client, base, chapter_id, text=edited, digest=saved["prose_digest"], key="save:a")
    _save(
        client,
        base,
        chapter_id,
        text=original,
        digest=first["prose_digest"],
        key="save:revert",
    )

    detail = client.get(f"{base}/revisions/{first['revision_id']}").json()

    assert detail["analyzable"] is False
    assert detail["hunks"][0]["active"] is False


def test_pure_append_is_not_a_revision(client: TestClient, tmp_path: Path) -> None:
    _, base, chapter_id, saved = _seed(client, tmp_path)
    appended = (
        "# 第七章\n\n林岚很紧张，她害怕许砚继续追问。\n\n窗外的雨没有停。\n\n"
        "许砚合上笔记本，起身去关窗。\n"
    )
    result = _save(
        client,
        base,
        chapter_id,
        text=appended,
        digest=saved["prose_digest"],
        key="save:append",
    )

    assert result["revision_id"] is None
    assert client.get(f"{base}/chapters/{chapter_id}/revisions").json()["items"] == []


def test_saving_same_request_key_returns_same_revision(client: TestClient, tmp_path: Path) -> None:
    _, base, chapter_id, saved = _seed(client, tmp_path)
    edited = "# 第七章\n\n林岚把杯沿转了半圈。「你刚才说到哪儿了？」\n\n窗外的雨没有停。\n"
    first = _save(
        client, base, chapter_id, text=edited, digest=saved["prose_digest"], key="save:same"
    )
    again = _save(
        client, base, chapter_id, text=edited, digest=saved["prose_digest"], key="save:same"
    )

    assert again["revision_id"] == first["revision_id"]
    assert len(client.get(f"{base}/chapters/{chapter_id}/revisions").json()["items"]) == 1


def test_style_document_round_trip_and_conflict(client: TestClient, tmp_path: Path) -> None:
    _, base, _, _ = _seed(client, tmp_path)
    empty = client.get(f"{base}/style").json()
    assert empty["exists"] is False
    assert empty["text"] == ""

    saved = client.put(
        f"{base}/style",
        json={
            "text": "写环境先写人物能感知的细节。\n",
            "expected_digest": None,
            "request_key": "style:1",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["digest"]
    assert (tmp_path / "book" / "STYLE.md").read_text(encoding="utf-8").startswith("写环境")

    replay = client.put(
        f"{base}/style",
        json={
            "text": "写环境先写人物能感知的细节。\n",
            "expected_digest": None,
            "request_key": "style:1",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True

    conflict = client.put(
        f"{base}/style",
        json={
            "text": "另一份约定。\n",
            "expected_digest": None,
            "request_key": "style:2",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "STYLE_DIGEST_MISMATCH"

    stale = client.put(
        f"{base}/style",
        json={
            "text": "另一份约定。\n",
            "expected_digest": "stale",
            "request_key": "style:3",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STYLE_DIGEST_MISMATCH"


def test_tool_revision_records_model_edit(tmp_path: Path) -> None:
    from hastings.workspace.chapter_write_policy import ChapterWritePolicy
    from hastings.workspace.chapter_writing import save_chapter_prose
    from hastings.workspace.files import Workspace
    from hastings.workspace.revision_store import RevisionStore, record_tool_revision
    from hastings.workspace.story_store import EMPTY_REVISION, StoryStore
    from hastings.workspace.writing_actions import create_chapter

    workspace = Workspace.create(tmp_path / "book")
    store = StoryStore(tmp_path / "state", workspace, format_version=5)
    workspace.chapter_policy = ChapterWritePolicy(store)
    created = create_chapter(
        store,
        workspace,
        title="第七章",
        after_chapter_id=None,
        expected_plan_revision=EMPTY_REVISION,
        request_key="chapter:tool",
    )
    draft = "# 第七章\n\n林岚很紧张，她害怕许砚继续追问。\n"
    saved = save_chapter_prose(
        store,
        workspace,
        chapter_id=created.chapter_id,
        text=draft,
        expected_digest=created.prose_digest,
        request_key="save:draft",
    )
    before = workspace.read(saved.path)
    edited = "# 第七章\n\n林岚把杯沿转了半圈。「你刚才说到哪儿了？」\n"
    after = workspace.write(saved.path, edited, expected_digest=before.digest)

    record = record_tool_revision(
        RevisionStore(store.root),
        store,
        path=after.path,
        before_text=before.text,
        after_text=after.text,
        before_digest=before.digest,
        after_digest=after.digest,
        operation_id="op_1",
        author_message="把这段改得更含蓄",
        conversation_id="conv_1",
    )

    assert record is not None
    assert record.source == "model"
    assert record.chapter_id == created.chapter_id
    assert record.author_message == "把这段改得更含蓄"
    detail = RevisionStore(store.root).detail(record.id, after.text)
    assert detail is not None
    assert detail.analyzable is True
    assert len(detail.hunks) == 1
