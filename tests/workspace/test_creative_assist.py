"""1.0.9 P3：目标化候选、停止、过期与增量语义索引。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.creative_assist import CreativeAssistService
from hastings.workspace.creative_candidates import (
    CandidateBase,
    CreativeCandidateStore,
    CreativeTarget,
)
from hastings.workspace.state import RecentWorkspaces
from hastings.workspace.story_index_jobs import IndexJob, IndexJobStore
from tau_agent.messages import AssistantMessage
from tau_agent.provider_events import AssistantDoneEvent, TextDeltaEvent


class _FakeProvider:
    def __init__(self, text: str, *, delay: float = 0.0) -> None:
        self.text = text
        self.delay = delay

    async def stream_response(self, **_: Any) -> AsyncIterator[Any]:
        for char in self.text:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield TextDeltaEvent(
                content_index=0,
                delta=char,
                partial=AssistantMessage(content=""),
            )
        yield AssistantDoneEvent(reason="stop", message=AssistantMessage(content=self.text))

    async def aclose(self) -> None:
        return None


class _FailingProvider:
    async def stream_response(self, **_: Any) -> AsyncIterator[Any]:
        raise RuntimeError("provider failed")
        yield  # pragma: no cover

    async def aclose(self) -> None:
        return None


class _Holder:
    def __init__(self) -> None:
        self.text = "候选正文"
        self.delay = 0.0
        self.fail = False
        self.raise_factory = False

    def __call__(self) -> ModelSnapshot:
        if self.raise_factory:
            raise RuntimeError("没有可用模型")
        provider = _FailingProvider() if self.fail else _FakeProvider(self.text, delay=self.delay)
        return ModelSnapshot(
            connection_id="test",
            display_name="测试连接",
            model="fake",
            provider=provider,
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


def _open(client: TestClient, tmp_path: Path) -> tuple[str, Path]:
    root = tmp_path / "book"
    response = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": not root.exists()}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["id"], Path(body["path"])


def _setup_chapter(client: TestClient, tmp_path: Path) -> dict[str, Any]:
    workspace_id, root = _open(client, tmp_path)
    base = f"/api/file-workspaces/{workspace_id}/story"
    intent = client.put(
        f"{base}/book-intent",
        json={
            "text": "暴雪封山的旅馆发生命案，林砚从湿手套开始怀疑管家。",
            "expected_plan_revision": "empty",
            "request_key": "book",
        },
    )
    assert intent.status_code == 200, intent.text
    plan_revision = intent.json()["plan_revision"]
    created = client.post(
        f"{base}/chapters",
        json={
            "title": "第一章",
            "after_chapter_id": None,
            "expected_plan_revision": plan_revision,
            "request_key": "chapter",
        },
    )
    assert created.status_code == 201, created.text
    chapter_id = created.json()["chapter_id"]
    chapter = client.get(f"{base}/workbench", params={"chapter_id": chapter_id}).json()
    chapter_revision = chapter["chapter"]["plan"]["revision"]
    plan_revision = chapter["plan_revision"]
    plan = client.put(
        f"{base}/chapters/{chapter_id}/plan",
        json={
            "title": "第一章",
            "intent": "封山、入住、管家说谎，湿手套最后出现。",
            "information_moves": None,
            "expected_chapter_revision": chapter_revision,
            "expected_plan_revision": plan_revision,
            "request_key": "plan",
        },
    )
    assert plan.status_code == 200, plan.text
    body = plan.json()
    chapter_revision = body["chapter_revision"]
    plan_revision = body["plan_revision"]
    started = client.post(
        f"{base}/chapters/{chapter_id}/start",
        json={
            "expected_story_revision": plan_revision,
            "expected_chapter_revision": chapter_revision,
            "attach_path": None,
            "attach_digest": None,
            "request_key": "start",
        },
    )
    assert started.status_code == 200, started.text
    started_body = started.json()
    return {
        "workspace_id": workspace_id,
        "root": root,
        "base": base,
        "chapter_id": chapter_id,
        "chapter_revision": chapter_revision,
        "plan_revision": started_body["plan_revision"],
        "started": started_body,
    }


def _wait_for(client: TestClient, url: str, predicate: Any, *, timeout: float = 4.0) -> Any:
    deadline = time.monotonic() + timeout
    latest: Any = None
    while time.monotonic() < deadline:
        response = client.get(url)
        assert response.status_code == 200, response.text
        latest = response.json()
        if predicate(latest):
            return latest
        time.sleep(0.05)
    raise AssertionError(f"等待超时：{latest}")


def test_assist_streams_and_is_idempotent(
    client: TestClient, tmp_path: Path, holder: _Holder
) -> None:
    setup = _setup_chapter(client, tmp_path)
    holder.text = "封山、入住，章末出现湿手套。"
    payload = {
        "kind": "chapter_plan",
        "target": {
            "kind": "chapter_plan",
            "workspace_id": setup["workspace_id"],
            "chapter_id": setup["chapter_id"],
        },
        "base": {
            "plan_revision": setup["plan_revision"],
            "chapter_revision": setup["chapter_revision"],
        },
        "author_note": "不要写凶手",
        "request_key": "assist:1",
    }
    response = client.post(
        f"/api/file-workspaces/{setup['workspace_id']}/creative-assists", json=payload
    )
    assert response.status_code == 202, response.text
    candidate = response.json()
    assert candidate["status"] == "streaming"
    url = (
        f"/api/file-workspaces/{setup['workspace_id']}/creative-assists/{candidate['candidate_id']}"
    )
    ready = _wait_for(client, url, lambda row: row["status"] == "ready")
    assert "湿手套" in ready["text"]

    again = client.post(
        f"/api/file-workspaces/{setup['workspace_id']}/creative-assists", json=payload
    )
    assert again.status_code == 202, again.text
    assert again.json()["candidate_id"] == candidate["candidate_id"]
    assert again.json()["status"] == "ready"


def test_assist_stale_base_and_unavailable_model(
    client: TestClient, tmp_path: Path, holder: _Holder
) -> None:
    setup = _setup_chapter(client, tmp_path)
    payload = {
        "kind": "chapter_plan",
        "target": {
            "kind": "chapter_plan",
            "workspace_id": setup["workspace_id"],
            "chapter_id": setup["chapter_id"],
        },
        "base": {"plan_revision": "rev_stale"},
        "request_key": "assist:stale",
    }
    stale = client.post(
        f"/api/file-workspaces/{setup['workspace_id']}/creative-assists", json=payload
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "ASSIST_TARGET_STALE"

    holder.raise_factory = True
    payload["base"]["plan_revision"] = setup["plan_revision"]
    payload["request_key"] = "assist:no-model"
    missing = client.post(
        f"/api/file-workspaces/{setup['workspace_id']}/creative-assists", json=payload
    )
    assert missing.status_code == 503, missing.text
    assert missing.json()["error"]["code"] == "MODEL_UNAVAILABLE"


def test_stop_preserves_candidate_state(
    client: TestClient, tmp_path: Path, holder: _Holder
) -> None:
    setup = _setup_chapter(client, tmp_path)
    holder.text = "这是一段足够长的候选正文，用来观察停止行为。"
    holder.delay = 0.2
    payload = {
        "kind": "chapter_plan",
        "target": {
            "kind": "chapter_plan",
            "workspace_id": setup["workspace_id"],
            "chapter_id": setup["chapter_id"],
        },
        "base": {
            "plan_revision": setup["plan_revision"],
            "chapter_revision": setup["chapter_revision"],
        },
        "request_key": "assist:stop",
    }
    response = client.post(
        f"/api/file-workspaces/{setup['workspace_id']}/creative-assists", json=payload
    )
    assert response.status_code == 202, response.text
    candidate_id = response.json()["candidate_id"]
    stopped = client.post(
        f"/api/file-workspaces/{setup['workspace_id']}/creative-assists/{candidate_id}/stop"
    )
    assert stopped.status_code == 200, stopped.text
    row = _wait_for(
        client,
        f"/api/file-workspaces/{setup['workspace_id']}/creative-assists/{candidate_id}",
        lambda item: item["status"] == "failed",
    )
    assert "停止" in (row["error"] or "")


def test_index_job_merges_candidates(client: TestClient, tmp_path: Path, holder: _Holder) -> None:
    setup = _setup_chapter(client, tmp_path)
    holder.text = '{"statement":"壁炉火钳上的蓝蜡","quote":"蓝蜡","kind":"observed"}'
    saved = client.put(
        f"{setup['base']}/chapters/{setup['chapter_id']}/prose",
        json={
            "text": "# 第一章\n\n林砚走进旅馆，壁炉火钳上有一小块蓝蜡。\n",
            "expected_digest": setup["started"]["prose_digest"],
            "expected_chapter_revision": setup["chapter_revision"],
            "draft_revision": "local_1",
            "candidate_id": None,
            "request_key": "save:index",
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["analysis_job_id"] is not None
    assert body["index"]["analysis"]["state"] == "queued"

    chapter = _wait_for(
        client,
        f"{setup['base']}/workbench?chapter_id={setup['chapter_id']}",
        lambda row: row["chapter"]["index"]["analysis"]["state"] == "ready",
    )
    items = chapter["chapter"]["index"]["items"]
    observed = [item for item in items if item["placement"] == "observed_candidate"]
    assert observed, items
    assert observed[0]["occurrences"][0]["state"] == "exact"


def test_index_failure_keeps_prose_saved(
    client: TestClient, tmp_path: Path, holder: _Holder
) -> None:
    setup = _setup_chapter(client, tmp_path)
    holder.fail = True
    saved = client.put(
        f"{setup['base']}/chapters/{setup['chapter_id']}/prose",
        json={
            "text": "# 第一章\n\n正文先保存。\n",
            "expected_digest": setup["started"]["prose_digest"],
            "expected_chapter_revision": setup["chapter_revision"],
            "draft_revision": "local_1",
            "candidate_id": None,
            "request_key": "save:fail-index",
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["analysis_job_id"] is not None

    chapter = _wait_for(
        client,
        f"{setup['base']}/workbench?chapter_id={setup['chapter_id']}",
        lambda row: row["chapter"]["index"]["analysis"]["state"] == "failed",
    )
    assert (setup["root"] / body["path"]).read_text(encoding="utf-8").endswith("正文先保存。\n")
    assert chapter["chapter"]["index"]["analysis"]["state"] == "failed"


def test_global_assist_works_without_workspace(client: TestClient, holder: _Holder) -> None:
    holder.text = "雪夜旅馆"
    payload = {
        "kind": "title",
        "target": {"kind": "new_workspace", "draft_id": "draft_1"},
        "draft_text": "一座暴雪封山的旅馆",
        "request_key": "assist:global",
    }
    response = client.post("/api/creative-assists", json=payload)
    assert response.status_code == 202, response.text
    candidate_id = response.json()["candidate_id"]
    ready = _wait_for(
        client,
        f"/api/creative-assists/{candidate_id}",
        lambda row: row["status"] == "ready",
    )
    assert "雪夜旅馆" in ready["text"]
    again = client.post("/api/creative-assists", json=payload)
    assert again.status_code == 202, again.text
    assert again.json()["candidate_id"] == candidate_id


def test_recovery_marks_interrupted_generation_and_index(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    candidates = CreativeCandidateStore(state_root)
    service = CreativeAssistService(
        state_root=state_root,
        workspace=None,
        candidates=candidates,
        provider_factory=lambda: ModelSnapshot(
            connection_id="", display_name="", model="", provider=_FakeProvider("")
        ),
        workspace_id="",
    )
    candidate = candidates.create(
        workspace_id=None,
        target=CreativeTarget(kind="new_workspace", draft_id="draft"),
        kind="title",
        base=CandidateBase(),
        request_key="recover:1",
    )
    recovered = service.recover(candidate)
    assert recovered.status == "failed"
    assert "中断" in (recovered.error or "")

    jobs = IndexJobStore(state_root)
    jobs.save(
        IndexJob(
            job_id="job_1",
            chapter_id="ch_recover",
            status="running",
            based_on_digest="digest",
            context_revision="ctx",
            plan_revision="rev",
            created_at="2026-11-02T00:00:00",
            updated_at="2026-11-02T00:00:00",
        )
    )
    job = jobs.read("ch_recover")
    assert job is not None
    assert job.status == "failed"
    assert "中断" in (job.error or "")


def test_prose_candidate_adoption_and_stale_rejection(
    client: TestClient, tmp_path: Path, holder: _Holder
) -> None:
    setup = _setup_chapter(client, tmp_path)
    holder.text = "林砚走进旅馆，柜台上有一双湿手套。\n"
    payload = {
        "kind": "prose_draft",
        "target": {
            "kind": "prose",
            "workspace_id": setup["workspace_id"],
            "chapter_id": setup["chapter_id"],
        },
        "base": {
            "plan_revision": setup["plan_revision"],
            "chapter_revision": setup["chapter_revision"],
            "prose_digest": setup["started"]["prose_digest"],
        },
        "request_key": "assist:prose",
    }
    response = client.post(
        f"/api/file-workspaces/{setup['workspace_id']}/creative-assists", json=payload
    )
    assert response.status_code == 202, response.text
    candidate_id = response.json()["candidate_id"]
    ready = _wait_for(
        client,
        f"/api/file-workspaces/{setup['workspace_id']}/creative-assists/{candidate_id}",
        lambda row: row["status"] == "ready",
    )
    assert ready["text"].startswith("林砚走进旅馆")

    saved = client.put(
        f"{setup['base']}/chapters/{setup['chapter_id']}/prose",
        json={
            "text": ready["text"],
            "expected_digest": setup["started"]["prose_digest"],
            "expected_chapter_revision": setup["chapter_revision"],
            "draft_revision": "local_candidate",
            "candidate_id": candidate_id,
            "request_key": "save:candidate",
        },
    )
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()

    chapter = client.get(
        f"{setup['base']}/workbench", params={"chapter_id": setup["chapter_id"]}
    ).json()
    plan_revision = chapter["plan_revision"]
    plan = client.put(
        f"{setup['base']}/chapters/{setup['chapter_id']}/plan",
        json={
            "title": "第一章",
            "intent": "封山、入住、管家说谎，湿手套最后出现。",
            "information_moves": None,
            "expected_chapter_revision": saved_body["chapter_revision"],
            "expected_plan_revision": plan_revision,
            "request_key": "plan:change-after-candidate",
        },
    )
    assert plan.status_code == 200, plan.text

    with_stale = client.put(
        f"{setup['base']}/chapters/{setup['chapter_id']}/prose",
        json={
            "text": ready["text"],
            "expected_digest": saved_body["prose_digest"],
            "expected_chapter_revision": plan.json()["chapter_revision"],
            "draft_revision": "local_stale",
            "candidate_id": candidate_id,
            "request_key": "save:stale-candidate",
        },
    )
    assert with_stale.status_code == 409, with_stale.text
    assert with_stale.json()["error"]["code"] == "ASSIST_TARGET_STALE"
