"""账本只读投影（V03-3.3）：给试写提供可引用条目，且不把节拍列为依据。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.application.queries.ledger import entry_view
from hastings.domain.story.catalog import StoryEntry
from hastings.server.v2 import V2Runtime, build_v2_app


def test_entry_view_labels_and_summarizes() -> None:
    entry = StoryEntry(
        artifact_id="chr_1",
        kind="story.character",
        revision_id="rev_1",
        content={
            "name": "林昭",
            "identity": "私家侦探",
            "goal": "查清姐姐失踪的真相",
            "secret": "她其实是委托人",
            "schema_version": 3,
        },
    )
    view = entry_view(entry)
    assert view["id"] == "chr_1"
    assert view["kind_label"] == "人物"
    assert view["title"] == "林昭"
    assert "私家侦探" in view["summary"]
    assert "她其实是委托人" in view["summary"]


def test_empty_project_has_no_ledger_entries() -> None:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "空作品"}).json()["project_id"]
    payload = client.get(f"/api/v2/projects/{pid}/ledger").json()
    assert payload == {"entries": []}
