"""1.2 修订学习 P3：模型判断改动去向、写作约定建议与确认应用。"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.state import RecentWorkspaces
from tau_agent.messages import AssistantMessage
from tau_agent.provider_events import AssistantDoneEvent, TextDeltaEvent


class _FakeProvider:
    def __init__(self, text: str) -> None:
        self.text = text

    async def stream_response(self, **_: Any) -> AsyncIterator[Any]:
        yield TextDeltaEvent(
            content_index=0, delta=self.text, partial=AssistantMessage(content="")
        )
        yield AssistantDoneEvent(reason="stop", message=AssistantMessage(content=self.text))

    async def aclose(self) -> None:
        return None


class _Holder:
    def __init__(self) -> None:
        self.text = ""

    def __call__(self) -> ModelSnapshot:
        return ModelSnapshot(
            connection_id="test",
            display_name="测试连接",
            model="fake",
            provider=_FakeProvider(self.text),
        )


@pytest.fixture()
def holder() -> _Holder:
    return _Holder()


@pytest.fixture()
def client(tmp_path: Path, holder: _Holder) -> Iterator[TestClient]:
    book = RecentWorkspaces(tmp_path / "state")
    app = create_workspace_app(workspaces=book, provider_factory=holder)
    with TestClient(app) as opened:
        yield opened


def _setup_edited_chapter(client: TestClient, tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "book"
    opened = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": not root.exists()}
    ).json()
    workspace_id = opened["id"]
    base = f"/api/file-workspaces/{workspace_id}/story"
    created = client.post(
        f"{base}/chapters",
        json={
            "title": "第七章",
            "after_chapter_id": None,
            "expected_plan_revision": "empty",
            "request_key": "chapter:7",
        },
    ).json()
    chapter_id = created["chapter_id"]
    draft = "# 第七章\n\n林岚很紧张，她害怕许砚继续追问。\n\n窗外的雨没有停。\n"
    saved = client.put(
        f"{base}/chapters/{chapter_id}/prose",
        json={
            "text": draft,
            "expected_digest": created["prose_digest"],
            "draft_revision": "draft:1",
            "candidate_id": None,
            "request_key": "save:first",
        },
    )
    assert saved.status_code == 200, saved.text
    edited = "# 第七章\n\n林岚把杯沿转了半圈。「你刚才说到哪儿了？」\n\n窗外的雨没有停。\n"
    changed = client.put(
        f"{base}/chapters/{chapter_id}/prose",
        json={
            "text": edited,
            "expected_digest": saved.json()["prose_digest"],
            "draft_revision": "draft:2",
            "candidate_id": None,
            "request_key": "save:edit",
        },
    )
    assert changed.status_code == 200, changed.text
    return {
        "workspace_id": workspace_id,
        "base": base,
        "chapter_id": chapter_id,
        "revision_id": changed.json()["revision_id"],
    }


def _wait(client: TestClient, url: str, predicate: Any, *, timeout: float = 4.0) -> Any:
    deadline = time.monotonic() + timeout
    latest: Any = None
    while time.monotonic() < deadline:
        latest = client.get(url).json()
        if predicate(latest):
            return latest
        time.sleep(0.05)
    raise AssertionError(f"等待超时：{latest}")


def _analysis_payload(hunk_id: str) -> str:
    return json.dumps(
        {
            "summary": "这次改稿把直接写紧张换成了动作与对白。",
            "decisions": [
                {
                    "hunk_ids": [hunk_id],
                    "target": "style",
                    "reason": "作者把解释情绪的句子改成动作与对白，属于可复用的表达偏好。",
                    "evidence": "",
                    "proposal": "人物回避追问时，先用动作再开口。",
                    "target_label": "写作约定",
                },
                {
                    "hunk_ids": [hunk_id],
                    "target": "chapter_only",
                    "reason": "林岚此刻的紧张只服务这一场，不构成长期设定。",
                    "proposal": "",
                    "target_label": "本章",
                },
            ],
        },
        ensure_ascii=False,
    )


def test_analysis_returns_decisions_and_style_proposal(
    client: TestClient, tmp_path: Path, holder: _Holder
) -> None:
    setup = _setup_edited_chapter(client, tmp_path)
    detail = client.get(f"{setup['base']}/revisions/{setup['revision_id']}").json()
    hunk_id = detail["hunks"][0]["id"]
    holder.text = _analysis_payload(hunk_id)

    started = client.post(
        f"{setup['base']}/revisions/{setup['revision_id']}/analyses",
        json={
            "hunk_ids": [hunk_id],
            "conversation_id": "conv_1",
            "operation_id": "op_1",
            "request_key": "analysis:1",
        },
    )
    assert started.status_code == 200, started.text
    analysis_id = started.json()["id"]
    ready = _wait(
        client,
        f"{setup['base']}/revision-analyses/{analysis_id}",
        lambda row: row["status"] in ("ready", "failed"),
    )

    assert ready["status"] == "ready", ready
    assert len(ready["decisions"]) == 2
    assert ready["decisions"][0]["target"] == "style"
    assert ready["decisions"][1]["target"] == "chapter_only"
    assert "先用动作再开口" in ready["proposed_style_text"]
    assert ready["conversation_id"] == "conv_1"


def test_analysis_is_idempotent_for_same_request_key(
    client: TestClient, tmp_path: Path, holder: _Holder
) -> None:
    setup = _setup_edited_chapter(client, tmp_path)
    hunk_id = client.get(f"{setup['base']}/revisions/{setup['revision_id']}").json()["hunks"][0][
        "id"
    ]
    holder.text = _analysis_payload(hunk_id)
    payload = {
        "hunk_ids": [hunk_id],
        "conversation_id": "conv_1",
        "operation_id": "op_1",
        "request_key": "analysis:same",
    }

    first = client.post(
        f"{setup['base']}/revisions/{setup['revision_id']}/analyses", json=payload
    ).json()
    second = client.post(
        f"{setup['base']}/revisions/{setup['revision_id']}/analyses", json=payload
    ).json()

    assert first["id"] == second["id"]


def test_apply_style_decision_writes_style_file(
    client: TestClient, tmp_path: Path, holder: _Holder
) -> None:
    setup = _setup_edited_chapter(client, tmp_path)
    hunk_id = client.get(f"{setup['base']}/revisions/{setup['revision_id']}").json()["hunks"][0][
        "id"
    ]
    holder.text = _analysis_payload(hunk_id)
    analysis = client.post(
        f"{setup['base']}/revisions/{setup['revision_id']}/analyses",
        json={
            "hunk_ids": [hunk_id],
            "conversation_id": "conv_1",
            "operation_id": "op_1",
            "request_key": "analysis:apply",
        },
    ).json()
    ready = _wait(
        client,
        f"{setup['base']}/revision-analyses/{analysis['id']}",
        lambda row: row["status"] == "ready",
    )

    applied = client.post(
        f"{setup['base']}/revision-analyses/{analysis['id']}/applications",
        json={
            "text": ready["proposed_style_text"],
            "expected_digest": None,
            "decision_indexes": [0],
            "request_key": "apply:1",
        },
    )

    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["style"]["digest"]
    written = (tmp_path / "book" / "STYLE.md").read_text(encoding="utf-8")
    assert "人物回避追问时，先用动作再开口。" in written
    reloaded = client.get(f"{setup['base']}/revision-analyses/{analysis['id']}").json()
    assert reloaded["decisions"][0]["applied"] is True
    assert reloaded["decisions"][1]["applied"] is False


def test_analysis_on_reverted_revision_is_rejected(
    client: TestClient, tmp_path: Path, holder: _Holder
) -> None:
    setup = _setup_edited_chapter(client, tmp_path)
    original = "# 第七章\n\n林岚很紧张，她害怕许砚继续追问。\n\n窗外的雨没有停。\n"
    current = client.get(f"{setup['base']}/revisions/{setup['revision_id']}").json()
    client.put(
        f"{setup['base']}/chapters/{setup['chapter_id']}/prose",
        json={
            "text": original,
            "expected_digest": current["revision"]["after_digest"],
            "draft_revision": "draft:revert",
            "candidate_id": None,
            "request_key": "save:revert",
        },
    )

    response = client.post(
        f"{setup['base']}/revisions/{setup['revision_id']}/analyses",
        json={
            "hunk_ids": [],
            "conversation_id": "",
            "operation_id": "",
            "request_key": "analysis:reverted",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REVISION_NOT_ANALYZABLE"
