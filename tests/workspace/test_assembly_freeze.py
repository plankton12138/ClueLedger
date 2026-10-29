"""受理时冻结装配，运行中拒绝装卸（0.8 F2）。

- 受理后作者改装配，本轮提示仍用受理时的精确版本与摘要；
- 写作轮次运行中，装配接口返回 409，不改任何绑定；结束后恢复。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import AssemblyStore
from hastings.workspace.state import RecentWorkspaces
from tau_agent.messages import AssistantMessage
from tau_agent.provider_events import AssistantStartEvent


class HangingProvider:
    """只发出响应壳并一直等待取消；用来看「运行中」状态。"""

    def __init__(self) -> None:
        self.calls = 0
        self.systems: list[str] = []

    def stream_response(
        self, *, model: str = "", system: str = "", **_rest: Any
    ) -> AsyncIterator[Any]:
        self.calls += 1
        self.systems.append(system)
        signal = _rest.get("signal")

        async def generator() -> AsyncIterator[Any]:
            yield AssistantStartEvent(partial=AssistantMessage())
            while signal is None or not signal.is_cancelled():
                await asyncio.sleep(0.02)

        return generator()


def _snapshot(provider: Any) -> ModelSnapshot:
    return ModelSnapshot(
        connection_id="test", display_name="测试连接", model="fake-model", provider=provider
    )


def _client(
    tmp_path: Path, provider: HangingProvider
) -> tuple[TestClient, ModuleLibrary, str, Workspace]:
    library = ModuleLibrary(tmp_path / "模块库", cache_file=tmp_path / "cache" / "catalog.json")
    root = tmp_path / "雪山旅馆"
    workspace = Workspace.create(root)
    created = library.create_module(
        kind="characters",
        name="主角团",
        entries=[EntryDraft("lin", "character", "林静", "法医。", (), {})],
        request_key="create-1",
    )
    module = created["module"]  # type: ignore[index]
    AssemblyService(library, workspace).bind(
        module_id=str(module["module_id"]),
        revision_id=str(module["head"]),
        label="主角团",
        expected_digest=None,
        request_key="bind-key-1",
    )
    book = RecentWorkspaces(tmp_path / "state")
    client = TestClient(
        create_workspace_app(
            workspaces=book,
            provider_factory=lambda: _snapshot(provider),
            library=library,
        )
    )
    client.__enter__()
    response = client.post("/api/file-workspaces", json={"path": str(root)})
    assert response.status_code == 200, response.text
    return client, library, str(response.json()["id"]), workspace


def _accept(client: TestClient, workspace_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "写第一章", "request_key": f"k_{time.monotonic_ns()}"},
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


def _wait_for(condition: Any, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("等待条件超时")


def test_assembly_mutations_are_rejected_while_a_turn_is_running(tmp_path: Path) -> None:
    provider = HangingProvider()
    client, _, workspace_id, _ = _client(tmp_path, provider)
    try:
        accepted = _accept(client, workspace_id)
        _wait_for(lambda: provider.calls > 0)

        current = client.get(f"/api/file-workspaces/{workspace_id}/assembly").json()
        response = client.put(
            f"/api/file-workspaces/{workspace_id}/assembly",
            json={
                "bindings": [],
                "links": [],
                "expected_digest": current["digest"],
                "request_key": "busy-put",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ASSEMBLY_BUSY_CONFLICT"
        assert client.get(f"/api/file-workspaces/{workspace_id}/assembly").json()["bindings"]

        client.post(
            f"/api/operations/{accepted['operation_id']}/pause",
            json={"request_key": "pause-1"},
        )
        _wait_for(
            lambda: any(
                event["type"] == "run_finished"
                for event in client.get(
                    f"/api/file-workspaces/{workspace_id}/conversations/"
                    f"{accepted['conversation_id']}/messages"
                ).json()["events"]
            )
        )

        after = client.put(
            f"/api/file-workspaces/{workspace_id}/assembly",
            json={
                "bindings": [],
                "links": [],
                "expected_digest": current["digest"],
                "request_key": "idle-put",
            },
        )
        assert after.status_code == 200, after.text
    finally:
        client.__exit__(None, None, None)


def test_turn_keeps_the_assembly_frozen_at_acceptance(tmp_path: Path) -> None:
    provider = HangingProvider()
    client, library, workspace_id, workspace = _client(tmp_path, provider)
    try:
        accepted = _accept(client, workspace_id)
        _wait_for(lambda: provider.calls > 0)
        before = AssemblyStore(workspace.root).read()
        assert "主角团" in provider.systems[0]
        assert before.bindings[0].module.revision_id in provider.systems[0]

        # 绕过接口直接改磁盘：本轮已经受理，提示里不能出现新状态。
        store = AssemblyStore(workspace.root)
        store.replace(
            library_id=library.library_id,
            bindings=(),
            expected_digest=store.digest(),
            request_key="direct-change",
        )
        # 磁盘已经变了，但本轮实际发给模型的仍是受理时的版本。
        assert "主角团" in provider.systems[0]
        assert before.bindings[0].module.revision_id in provider.systems[0]

        events = client.get(
            f"/api/file-workspaces/{workspace_id}/conversations/"
            f"{accepted['conversation_id']}/messages"
        ).json()["events"]
        injected = next(event for event in events if event["type"] == "state_injected")
        assert injected["frozen"]["bindings"][0]["module"]["revision_id"] == (
            before.bindings[0].module.revision_id
        )

        client.post(
            f"/api/operations/{accepted['operation_id']}/pause",
            json={"request_key": "pause-1"},
        )
    finally:
        client.__exit__(None, None, None)
