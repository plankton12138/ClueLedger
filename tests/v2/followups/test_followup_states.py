"""操作后的后续状态（V03-2.6）。

契约：操作查询附带两个确定性状态——摘要（missing/stale/fresh）与账本建议
（not_started）。只读，不创建 Job、不调用模型；无变化的操作不会伪造状态。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.domain.manuscript.evidence import KIND_CHAPTER_SUMMARY
from hastings.domain.repository.ids import Actor, ArtifactId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.server.v2 import V2Runtime, build_v2_app

FIRST = "侦探在雨夜抵达旅馆。\n\n前台声称只有一位客人。"
CHANGED = "侦探在雪夜抵达旅馆。\n\n前台声称只有一位客人。"


def _client() -> tuple[TestClient, V2Runtime, str, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "后续状态"}).json()["project_id"]
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


def _seed_summary(runtime: V2Runtime, pid: str, chapter_id: str, status: str) -> None:
    repo = runtime.service
    branch = repo.get_branch(ProjectId(pid), "main")
    assert branch is not None and branch.head_commit_id is not None
    head = branch.head_commit_id
    snapshot = repo.get_snapshot(ProjectId(pid), head)
    chapter = snapshot.artifacts[ArtifactId(chapter_id)]
    assert chapter.revision is not None
    repo.commit(
        CommitRequest(
            project_id=ProjectId(pid),
            branch_id=branch.id,
            expected_head=head,
            actor=Actor(kind="human", id="author"),
            idempotency_key=f"seed-summary-{status}",
            summary="预置摘要",
            changes=(
                ArtifactChange(
                    artifact_id=ArtifactId("art_sum_1"),
                    content={
                        "summary_id": "sum_1",
                        "chapter_id": chapter_id,
                        "source_hash": chapter.revision.content_hash.value,
                        "status": status,
                    },
                    kind=KIND_CHAPTER_SUMMARY,
                ),
            ),
        )
    )


def test_operation_reports_missing_summary_and_unstarted_suggestions() -> None:
    client, _runtime, pid, chapter_id = _client()
    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    operation = client.get(f"/api/v2/projects/{pid}/operations/{saved['operation_id']}").json()
    assert operation["summary_state"] == "missing"
    assert operation["ledger_suggestions_state"] == "not_started"
    assert operation["result_url"].endswith(f"/operations/{saved['operation_id']}")


def test_summary_becomes_stale_after_change() -> None:
    client, runtime, pid, chapter_id = _client()
    _seed_summary(runtime, pid, chapter_id, "fresh")
    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    operation = client.get(f"/api/v2/projects/{pid}/operations/{saved['operation_id']}").json()
    assert operation["summary_state"] == "stale"


def test_no_change_operation_keeps_fresh_summary() -> None:
    """无变化不造空版本：状态仍按现有摘要回答，不伪造「待更新」。"""
    client, runtime, pid, chapter_id = _client()
    _seed_summary(runtime, pid, chapter_id, "fresh")
    saved = _save(client, pid, chapter_id, FIRST, "save-same")
    assert saved["status"] == "no_change"
    operation = client.get(f"/api/v2/projects/{pid}/operations/{saved['operation_id']}").json()
    assert operation["summary_state"] == "fresh"
    assert operation["changed_chapter_ids"] == []


def test_suggestions_state_reads_the_real_candidate_group() -> None:
    """建议存在候选组的辅助表里：状态必须读那份数据。

    复现路径：整理出建议后查操作结果——早先按 ``ledger.suggestion_group``
    Artifact 判断，而建议从来只写辅助表，于是「已有四条建议」仍回报
    ``not_started``，作者会重复启动整理。现在按候选组的 pending 条目回答：
    not_started → available → handled。
    """
    import json

    from fastapi.testclient import TestClient as _Client

    runtime = V2Runtime.memory()

    async def proposer(*, system: str, prompt: str) -> tuple[str, str]:
        return (
            json.dumps(
                {
                    "suggestions": [
                        {
                            "kind": "story.character",
                            "fields": {"name": "前台职员", "identity": "旅馆接待"},
                            "quote": "前台声称只有一位客人",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            "test-model",
        )

    runtime.suggestions._proposer = proposer  # type: ignore[assignment]
    client = _Client(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "建议状态"}).json()["project_id"]
    chapter_id = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={
            "title": "第一章",
            "text": FIRST,
            "order_key": "0001",
            "idempotency_key": "imp-1",
        },
    ).json()["chapter_id"]

    saved = _save(client, pid, chapter_id, CHANGED, "save-1")
    operation = client.get(f"/api/v2/projects/{pid}/operations/{saved['operation_id']}").json()
    assert operation["ledger_suggestions_state"] == "not_started"

    started = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/suggestions",
        json={"request_id": "sug-1"},
    )
    assert started.status_code == 200, started.text

    # 同一条操作结果，现在如实反映「有建议可查看」。
    operation = client.get(f"/api/v2/projects/{pid}/operations/{saved['operation_id']}").json()
    assert operation["ledger_suggestions_state"] == "available"

    group = client.get(f"/api/v2/projects/{pid}/suggestions").json()
    accepted = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [group["items"][0]["item_id"]],
            "expected_revision": group["revision"],
            "request_id": "acc-1",
        },
    )
    assert accepted.status_code == 200, accepted.text

    # 全部决定之后不再提示「可查看」，但也不谎称「尚未整理」。
    operation = client.get(f"/api/v2/projects/{pid}/operations/{saved['operation_id']}").json()
    assert operation["ledger_suggestions_state"] == "handled"
