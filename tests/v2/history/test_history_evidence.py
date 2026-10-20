"""章版本历史与证据重锚（V03-2.5）。

契约：历史只读、按章基础判重、当前版本在首位；恢复走统一发布（摘要失效、
操作记录可查），且被未结束的修改副本拦住；证据可人工重新关联，但引用片段
必须在当前正文中真实存在。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.domain.manuscript.evidence import (
    KIND_CHAPTER_SUMMARY,
    KIND_EVIDENCE_LINK,
    EvidenceLink,
)
from hastings.domain.repository.ids import Actor, ArtifactId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.domain.repository.serialization import content_hash
from hastings.server.v2 import V2Runtime, build_v2_app

FIRST = "侦探在雨夜抵达旅馆。\n\n前台声称只有一位客人。"
CHANGED = "侦探在雪夜抵达旅馆。\n\n前台声称只有一位客人。"


def _client() -> tuple[TestClient, V2Runtime, str, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "历史走查"}).json()["project_id"]
    chapter_id = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={
            "title": "第一章",
            "text": FIRST,
            "order_key": "0001",
            "idempotency_key": "imp-1",
        },
    ).json()["chapter_id"]
    return client, runtime, pid, chapter_id


def _save(client: TestClient, pid: str, chapter_id: str, text: str, key: str) -> dict:
    response = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/save",
        json={"idempotency_key": key, "full_text": text},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _history(client: TestClient, pid: str, chapter_id: str) -> list[dict]:
    response = client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}/history")
    assert response.status_code == 200, response.text
    return response.json()["versions"]


def _seed_evidence(runtime: V2Runtime, pid: str, chapter_id: str, quote: str) -> None:
    """预置一条证据与一份章摘要，指向该章当前版本。"""
    repo = runtime.service
    branch = repo.get_branch(ProjectId(pid), "main")
    assert branch is not None and branch.head_commit_id is not None
    head = branch.head_commit_id
    snapshot = repo.get_snapshot(ProjectId(pid), head)
    chapter = snapshot.artifacts[ArtifactId(chapter_id)]
    assert chapter.revision is not None
    link = EvidenceLink(
        evidence_id="evd_1",
        anchor_kind="setting",
        target_id="art_ledger_hotel",
        chapter_id=chapter_id,
        quote=quote,
        source_hash=content_hash(FIRST),
    )
    summary = {
        "summary_id": "sum_1",
        "chapter_id": chapter_id,
        "source_hash": chapter.revision.content_hash.value,
        "status": "fresh",
    }
    repo.commit(
        CommitRequest(
            project_id=ProjectId(pid),
            branch_id=branch.id,
            expected_head=head,
            actor=Actor(kind="human", id="author"),
            idempotency_key="seed-evidence",
            summary="预置证据与摘要",
            changes=(
                ArtifactChange(
                    artifact_id=ArtifactId("art_evd_1"),
                    content=link.to_wire(),
                    kind=KIND_EVIDENCE_LINK,
                ),
                ArtifactChange(
                    artifact_id=ArtifactId("art_sum_1"),
                    content=summary,
                    kind=KIND_CHAPTER_SUMMARY,
                ),
            ),
        )
    )


def _read_artifact(runtime: V2Runtime, pid: str, artifact_id: str) -> dict:
    head = runtime.service.get_branch(ProjectId(pid), "main").head_commit_id
    assert head is not None
    view = runtime.service.get_artifact(ArtifactId(artifact_id), head)
    assert view is not None and view.revision is not None
    return dict(view.revision.content)


# -- 历史 -------------------------------------------------------------------


def test_history_lists_versions_newest_first() -> None:
    client, _runtime, pid, chapter_id = _client()
    _save(client, pid, chapter_id, CHANGED, "save-1")
    _save(client, pid, chapter_id, "第三版正文。", "save-2")

    versions = _history(client, pid, chapter_id)
    assert len(versions) == 3
    assert versions[0]["is_current"] is True
    assert "第三版正文。" in versions[0]["preview"]
    assert all(not v["is_current"] for v in versions[1:])
    # 当前版本与旧版本的基础不同（判重按章基础，不按提交数）
    assert len({v["chapter_base"] for v in versions}) == 3


def test_restore_goes_through_unified_publish() -> None:
    """恢复后摘要失效、操作可查：证明它走的是统一发布而不是旁路提交。"""
    client, runtime, pid, chapter_id = _client()
    _seed_evidence(runtime, pid, chapter_id, "雨夜抵达旅馆")
    first = _history(client, pid, chapter_id)[0]["commit_id"]

    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    assert saved["status"] == "committed"
    assert _read_artifact(runtime, pid, "art_sum_1")["status"] == "stale"

    restored = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/restore",
        json={"idempotency_key": "restore-1", "source_commit_id": first},
    )
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["status"] == "committed"
    assert body["operation_id"].startswith("op_")
    assert body["commit_id"] not in (None, first)
    # 操作记录可查，且影响章节就是本章
    operation = client.get(
        f"/api/v2/projects/{pid}/operations/{body['operation_id']}"
    ).json()
    assert operation["changed_chapter_ids"] == [chapter_id]
    # 恢复后正文回到首版
    after = client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()
    assert "雨夜抵达旅馆" in after["text"]


def test_restore_blocked_by_active_edit_session() -> None:
    client, _runtime, pid, chapter_id = _client()
    first = _history(client, pid, chapter_id)[0]["commit_id"]
    session = client.post(f"/api/v2/projects/{pid}/edit-sessions").json()["session"][
        "session_id"
    ]
    client.put(
        f"/api/v2/projects/{pid}/edit-sessions/{session}/chapters/{chapter_id}",
        json={"expected_save_seq": 0, "request_id": "req-1", "text": "副本里的修改。"},
    )

    blocked = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/restore",
        json={"idempotency_key": "restore-1", "source_commit_id": first},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "EDIT_SESSION_CONFLICT"
    # 副本仍在，正式正文未变
    current = client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()
    assert current["session"]["session_id"] == session


# -- 证据 -------------------------------------------------------------------


def test_evidence_becomes_needs_check_then_reanchors() -> None:
    client, runtime, pid, chapter_id = _client()
    _seed_evidence(runtime, pid, chapter_id, "雨夜抵达旅馆")

    _save(client, pid, chapter_id, CHANGED, "save-1")
    listed = client.get(
        f"/api/v2/projects/{pid}/evidence-links", params={"chapter_id": chapter_id}
    ).json()["evidence"]
    assert listed[0]["status"] == "needs_check"
    assert listed[0]["previous"]["status"] == "valid"

    reanchored = client.post(
        f"/api/v2/projects/{pid}/evidence-links/evd_1/reanchor",
        json={"quote": "前台声称只有一位客人"},
    )
    assert reanchored.status_code == 200, reanchored.text
    body = reanchored.json()
    assert body["evidence"]["status"] == "valid"
    assert body["evidence"]["quote"] == "前台声称只有一位客人"
    assert body["evidence"]["previous"]["quote"] == "雨夜抵达旅馆"
    assert body["operation_id"].startswith("op_")


def test_reanchor_rejects_quote_not_in_text() -> None:
    client, runtime, pid, chapter_id = _client()
    _seed_evidence(runtime, pid, chapter_id, "雨夜抵达旅馆")
    response = client.post(
        f"/api/v2/projects/{pid}/evidence-links/evd_1/reanchor",
        json={"quote": "正文里没有这句话"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EVIDENCE_QUOTE_MISSING"


def test_reanchor_unknown_evidence_returns_404() -> None:
    client, _runtime, pid, _chapter_id = _client()
    response = client.post(
        f"/api/v2/projects/{pid}/evidence-links/evd_missing/reanchor", json={}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVIDENCE_NOT_FOUND"


def test_evidence_filters_by_target_id() -> None:
    """按账本对象过滤证据：从图谱节点定位它在正文中的依据（V03-6.4 来源定位）。

    不传过滤时行为不变（返回全部证据），过滤只收窄、不改语义。
    """
    client, runtime, pid, chapter_id = _client()
    _seed_evidence(runtime, pid, chapter_id, "雨夜抵达旅馆")

    hit = client.get(
        f"/api/v2/projects/{pid}/evidence-links",
        params={"target_id": "art_ledger_hotel"},
    ).json()["evidence"]
    assert [item["evidence_id"] for item in hit] == ["evd_1"]

    miss = client.get(
        f"/api/v2/projects/{pid}/evidence-links",
        params={"target_id": "art_other"},
    ).json()["evidence"]
    assert miss == []

    # 不传过滤：返回全部证据（既有行为不变）。
    everything = client.get(f"/api/v2/projects/{pid}/evidence-links").json()["evidence"]
    assert [item["evidence_id"] for item in everything] == ["evd_1"]
