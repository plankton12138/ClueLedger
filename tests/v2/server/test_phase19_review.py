"""Phase 19 review regressions through the real HTTP/application boundaries."""

import asyncio
from typing import Any

import httpx
import pytest
from agents.scenario import ScenarioRunner

from hastings.application.services.conversation_service import ConversationService
from hastings.domain.conversation.task_drafts import ProjectView, missing_items
from hastings.infrastructure.conversations.runner import ProviderChatRunner
from hastings.infrastructure.conversations.store import MemoryConversationStore
from hastings.server.v2 import V2Runtime, build_v2_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def runtime() -> V2Runtime:
    runtime = V2Runtime.memory()
    runtime.chat.model_resolver = lambda: "review-model"
    runtime.chat.binding_resolver = None
    runtime.chat.bound_runner_factory = None
    runtime.model_binding_provider = lambda: {
        "profile": "review",
        "model": "review-model",
        "api": "openai-completions",
    }
    runtime.configure_agent_runner(ScenarioRunner(runtime.service))
    return runtime


async def setup(client: httpx.AsyncClient) -> tuple[str, str]:
    pid = (await client.post("/api/v2/projects", json={"name": "review"})).json()["project_id"]
    root = f"/api/v2/projects/{pid}"
    cid = (await client.post(root + "/conversations", json={})).json()["conversation_id"]
    return root, cid


def test_writer_without_selected_beats_is_visibly_incomplete() -> None:
    missing = missing_items(
        "chapter_draft",
        goal="new-chapter",
        target_ids=[],
        base_version="base",
        view=ProjectView(True, True, head="base"),
    )
    assert any(item["code"] == "TASK_NO_BEAT_TARGET" for item in missing)


@pytest.mark.anyio
async def test_empty_project_plans_and_persists_execution_card(runtime: V2Runtime) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_v2_app(runtime)), base_url="http://test"
    ) as client:
        root, cid = await setup(client)
        created = (
            await client.post(
                root + "/task-drafts",
                json={"conversation_id": cid, "task_kind": "plan", "instruction": "规划怀表谜案"},
            )
        ).json()
        draft = created["draft"]
        started = await client.post(
            root + f"/task-drafts/{draft['draft_id']}/start",
            json={"expected_revision": 1, "idempotency_key": "plan"},
        )
        assert started.status_code == 200, started.text
        jid = started.json()["job_ref"]
        messages = (await client.get(root + f"/conversations/{cid}/messages")).json()["messages"]
        execution = next(m for m in messages if m["kind"] == "task_execution")
        assert execution["message_id"] != created["message_id"]
        assert execution["payload"]["job_ref"] == jid
        assert execution["payload"]["model"] == "review-model"
        await client.post(root + f"/agent-jobs/{jid}/run")
        await runtime.task_projector.sync()
        messages = (await client.get(root + f"/conversations/{cid}/messages")).json()["messages"]
        assert (
            next(m for m in messages if m["kind"] == "task_execution")["payload"]["state"]
            == "waiting"
        )
        candidate = next(m for m in messages if m["kind"] == "candidate")
        assert candidate["payload"]["job_ref"] == jid


@pytest.mark.anyio
async def test_source_text_survives_task_preparation(runtime: V2Runtime) -> None:
    conv = await runtime.chat.create_conversation("p")
    from hastings.domain.conversation.models import ChatMessage

    await runtime.conversation_store.append_message(
        ChatMessage(
            "source", conv.conversation_id, None, "assistant", "text", "凶手改成姐姐，保留怀表线索"
        )
    )
    draft, _ = await runtime.task_prep.create_draft(
        "p", conv.conversation_id, "plan", source_message_id="source"
    )
    assert draft.instruction == "凶手改成姐姐，保留怀表线索"


@pytest.mark.anyio
async def test_saved_reference_supplies_content_and_rejects_stale_version(
    runtime: V2Runtime,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_v2_app(runtime)), base_url="http://test"
    ) as client:
        root, cid = await setup(client)
        imported = (
            await client.post(
                root + "/chapters/import",
                json={"idempotency_key": "import", "title": "怀表", "text": "姐姐藏起了怀表。"},
            )
        ).json()
        chapter = (await client.get(root + f"/chapters/{imported['chapter_id']}")).json()
        refs = [
            {
                "kind": "chapter",
                "target_id": imported["chapter_id"],
                "title": "怀表",
                "version": chapter["chapter_base"],
                "saved": True,
            }
        ]
        seen: list[Any] = []

        class Recorder:
            async def stream(self, run: Any, sink: Any) -> str:
                seen.append(run)
                await sink("讨论")
                return "讨论"

        runtime.chat.runner_factory = Recorder
        pid = root.rsplit("/", 1)[-1]
        await runtime.chat.send_turn(pid, cid, text="分析这章", refs=refs, idempotency_key="read")
        await asyncio.gather(*(r.task for r in list(runtime.chat._runs.values()) if r.task))
        assert "姐姐藏起了怀表。" in "\n".join(seen[0].refs)
        response = await client.post(
            root + f"/conversations/{cid}/turns",
            json={
                "text": "过期引用",
                "refs": [{**refs[0], "version": "obsolete"}],
                "idempotency_key": "stale",
            },
        )
        assert response.status_code == 409, response.text


@pytest.mark.anyio
async def test_retry_preserves_original_model() -> None:
    calls: list[str] = []

    class Recorder:
        async def stream(self, run: Any, sink: Any) -> str:
            calls.append(run.model)
            await sink("reply")
            return "reply"

    service = ConversationService(
        MemoryConversationStore(), runner_factory=Recorder, model_resolver=lambda: "A"
    )
    cid = (await service.create_conversation("p")).conversation_id
    opened = await service.send_turn("p", cid, text="question", idempotency_key="first")
    await asyncio.gather(*(r.task for r in list(service._runs.values()) if r.task))
    service.model_resolver = lambda: "B"
    await service.retry_turn("p", cid, opened.turn.turn_id)
    await asyncio.gather(*(r.task for r in list(service._runs.values()) if r.task))
    assert calls == ["A", "A"]


@pytest.mark.anyio
async def test_targeted_revision_opens_existing_change_workflow(runtime: V2Runtime) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_v2_app(runtime)), base_url="http://test"
    ) as client:
        root, cid = await setup(client)
        imported = (
            await client.post(
                root + "/chapters/import",
                json={"idempotency_key": "import", "title": "怀表", "text": "姐姐藏起了怀表。"},
            )
        ).json()
        draft = (
            await client.post(
                root + "/task-drafts",
                json={
                    "conversation_id": cid,
                    "task_kind": "targeted_revision",
                    "instruction": "精简本章叙述",
                    "target_ids": [imported["chapter_id"]],
                },
            )
        ).json()["draft"]
        started = await client.post(
            root + f"/task-drafts/{draft['draft_id']}/start",
            json={"expected_revision": 1, "idempotency_key": "revision"},
        )
        assert started.status_code == 200, started.text
        assert started.json()["job_ref"].startswith("change:")
        csid = started.json()["job_ref"].removeprefix("change:")
        detail = (await client.get(root + f"/changes/{csid}")).json()
        assert detail["change_set"]["intent"] == "精简本章叙述"
        assert detail["change_set"]["status"] == "opened"
        assert (await client.get(root + "/agent-jobs")).json()["jobs"] == []


@pytest.mark.anyio
async def test_same_project_runs_one_creation_job_at_a_time(runtime: V2Runtime) -> None:
    release = asyncio.Event()
    entered = asyncio.Event()
    active = 0
    peak = 0

    class BlockingRunner:
        async def run(self, task_kind: str, context: Any) -> dict[str, str]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            entered.set()
            try:
                await release.wait()
                return {"kind": "no_result", "reason": "合法空结果"}
            finally:
                active -= 1

    runtime.configure_agent_runner(BlockingRunner())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_v2_app(runtime)), base_url="http://test"
    ) as client:
        root, _cid = await setup(client)
        jobs = [
            (
                await client.post(
                    root + "/agent-jobs",
                    json={"role": "planner", "idempotency_key": key, "instruction": "plan"},
                )
            ).json()["job_id"]
            for key in ("first", "second")
        ]
        tasks = [asyncio.create_task(runtime._execute_job(jid)) for jid in jobs]
        await entered.wait()
        try:
            await asyncio.sleep(0.03)
            assert peak == 1
        finally:
            release.set()
            await asyncio.gather(*tasks)


@pytest.mark.anyio
async def test_cancel_closes_provider_while_waiting_for_first_delta() -> None:
    entered = asyncio.Event()
    closed = asyncio.Event()

    class StalledProvider:
        async def stream_response(self, **kwargs: Any) -> Any:
            entered.set()
            await asyncio.Event().wait()
            yield None

        async def aclose(self) -> None:
            closed.set()

    service = ConversationService(
        MemoryConversationStore(),
        runner_factory=lambda: ProviderChatRunner(StalledProvider, owns_provider=True),
        model_resolver=lambda: "A",
    )
    cid = (await service.create_conversation("p")).conversation_id
    opened = await service.send_turn("p", cid, text="question", idempotency_key="first")
    await entered.wait()
    try:
        await service.cancel_turn("p", cid, opened.turn.turn_id)
        async with asyncio.timeout(0.5):
            await closed.wait()
    finally:
        await service.shutdown()
