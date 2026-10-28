"""0.9 C1：整理流程（服务层）。

这一段验证的是「能不能真的产生一份可用的压缩记录」：摘要请求怎么发、失败怎么收场、
提交后下一轮的投影变不变。模型用固定输出的假 provider，不联网。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hastings.server.model_limits import ModelCapacity, resolve_model_limits
from hastings.server.provider_config import ProviderProfile
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.compaction import active_compaction
from hastings.workspace.conversation_context import replay_public_messages
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.service import TurnService
from hastings.workspace.state import ConversationStore, conversation_root
from tau_agent.messages import AssistantMessage, TextContent, message_text
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent

SUMMARY = json.dumps(
    {
        "summary": "作者在改雨夜这一章，决定保留第一人称。",
        "items": [
            {
                "kind": "author_decision",
                "text": "保留第一人称",
                "quote": "别改成第三人称",
                "sources": ["t1"],
            }
        ],
    },
    ensure_ascii=False,
)


class _SummaryProvider:
    def __init__(self, text: str = SUMMARY) -> None:
        self.text = text
        self.requests: list[dict[str, Any]] = []

    def stream_response(self, **kwargs: Any):
        self.requests.append(kwargs)
        text = self.text

        async def generator():
            yield AssistantStartEvent(partial=AssistantMessage())
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(content=[TextContent(text=text)], stop_reason="stop"),
            )

        return generator()

    async def aclose(self) -> None:
        return None


def _limits():
    profile = ProviderProfile(
        name="test",
        base_url="https://api.deepseek.com",
        api="openai-completions",
        default_model="fake-model",
        model_metadata={"fake-model": ModelCapacity(context_window_tokens=8_000)},
    )
    return resolve_model_limits(profile, "fake-model")


def _snapshot(provider: Any) -> ModelSnapshot:
    return ModelSnapshot(
        connection_id="test",
        display_name="测试连接",
        model="fake-model",
        provider=provider,
        limits=_limits(),
    )


def _store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(conversation_root(tmp_path / "state", "w1"))


def _seed(
    store: ConversationStore, turns: int = 4, *, context: dict[str, Any] | None = None
) -> str:
    """直接写公开轮次，跳过真正的写作流程。"""
    accepted = store.create_with_first_turn(
        text="写第一章" + "雨夜" * 400, request_key="k1", turn_id="t1", context=context
    )
    conversation_id = accepted.conversation_id
    store.append_batch(
        conversation_id,
        [
            {"type": "assistant_message", "turn_id": "t1", "text": "第一章写好了" + "雨" * 400},
            {
                "type": "run_finished",
                "turn_id": "t1",
                "status": "completed",
                "recording": "committed",
                "changed_paths": [],
            },
        ],
    )
    for index in range(2, turns + 1):
        store.append_batch(
            conversation_id,
            [
                {"type": "turn_started", "turn_id": f"t{index}", "text": "再写一段" * 200},
                {"type": "assistant_message", "turn_id": f"t{index}", "text": "写好了" * 200},
                {
                    "type": "run_finished",
                    "turn_id": f"t{index}",
                    "status": "completed",
                    "recording": "committed",
                    "changed_paths": [],
                },
            ],
        )
    return conversation_id


def _service(tmp_path: Path, provider: Any, store: ConversationStore) -> TurnService:
    return TurnService(store_root_workspace(tmp_path), store, lambda: _snapshot(provider))


def store_root_workspace(tmp_path: Path):
    from hastings.workspace.files import Workspace
    from hastings.workspace.git import GitRepo

    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    return workspace


def test_compact_appends_a_record_and_counts_the_summary_request(tmp_path: Path) -> None:
    provider = _SummaryProvider()
    store = _store(tmp_path)
    conversation_id = _seed(store)
    service = _service(tmp_path, provider, store)

    async def scenario():
        return await service.compact(conversation_id)

    record = asyncio.run(scenario())

    assert record is not None
    assert record.items[0].quote == "别改成第三人称"
    assert record.model == "test:fake-model"

    events = list(store.events(conversation_id))
    compacted = [event for event in events if event["type"] == "context_compacted"]
    assert len(compacted) == 1
    assert compacted[0]["record"]["first_kept_turn_id"] == record.first_kept_turn_id
    # 摘要请求本身也计入用量，但属于独立的 stage。
    finished = [
        event
        for event in events
        if event["type"] == "model_request_finished" and event["stage"] == "compaction"
    ]
    assert len(finished) == 1
    assert active_compaction(events) is not None


def test_compact_sends_no_tools_and_only_covered_turns(tmp_path: Path) -> None:
    provider = _SummaryProvider()
    store = _store(tmp_path)
    conversation_id = _seed(store)
    service = _service(tmp_path, provider, store)

    asyncio.run(service.compact(conversation_id))

    request = provider.requests[0]
    assert request["tools"] == []
    prompt = message_text(request["messages"][0])
    assert "[t1]" in prompt
    assert "[t4]" not in prompt


def test_compact_without_earlier_turns_is_a_noop(tmp_path: Path) -> None:
    provider = _SummaryProvider()
    store = _store(tmp_path)
    conversation_id = _seed(store, turns=1)
    service = _service(tmp_path, provider, store)

    result = asyncio.run(service.compact(conversation_id))

    assert result is None
    assert provider.requests == []


def test_bad_summary_is_rejected_without_touching_the_projection(tmp_path: Path) -> None:
    provider = _SummaryProvider("好的，我整理了一下：这段讨论主要在改雨夜。")
    store = _store(tmp_path)
    conversation_id = _seed(store)
    service = _service(tmp_path, provider, store)

    with pytest.raises(WorkspaceError) as caught:
        asyncio.run(service.compact(conversation_id))

    assert caught.value.code == "COMPACTION_FAILED"
    events = list(store.events(conversation_id))
    assert not [event for event in events if event["type"] == "context_compacted"]
    # 投影不变：仍是完整的公开历史。
    assert len(replay_public_messages(events, compaction=active_compaction(events))) == 8


def test_compacted_projection_is_shorter_and_starts_with_the_summary(tmp_path: Path) -> None:
    provider = _SummaryProvider()
    store = _store(tmp_path)
    conversation_id = _seed(store)
    service = _service(tmp_path, provider, store)

    asyncio.run(service.compact(conversation_id))

    events = list(store.events(conversation_id))
    messages = replay_public_messages(events, compaction=active_compaction(events))
    assert len(messages) == 5  # 摘要 + 保留的 2 轮（3 条公开消息）+ 1 条
    assert "较早对话摘要" in str(messages[0].content)


def test_second_compaction_links_to_the_first(tmp_path: Path) -> None:
    """多次整理要能回溯：第二条记录指向第一条，投影仍只有一份摘要。"""
    provider = _SummaryProvider()
    store = _store(tmp_path)
    conversation_id = _seed(store)
    service = _service(tmp_path, provider, store)

    async def scenario():
        first = await service.compact(conversation_id)
        # 中间要有新的对话，第二次整理才有新的可覆盖内容。
        store.append_batch(
            conversation_id,
            [
                {"type": "turn_started", "turn_id": "t5", "text": "再写一段" * 200},
                {"type": "assistant_message", "turn_id": "t5", "text": "写好了" * 200},
                {
                    "type": "run_finished",
                    "turn_id": "t5",
                    "status": "completed",
                    "recording": "committed",
                    "changed_paths": [],
                },
            ],
        )
        second = await service.compact(conversation_id)
        return first, second

    first, second = asyncio.run(scenario())

    assert first is not None and second is not None
    assert second.previous_id == first.compaction_id
    assert second.covered_through_seq >= first.covered_through_seq

    events = list(store.events(conversation_id))
    assert len([event for event in events if event["type"] == "context_compacted"]) == 2
    messages = replay_public_messages(events, compaction=active_compaction(events))
    assert sum("较早对话摘要" in message_text(message) for message in messages) == 1


def test_compact_without_new_material_is_a_noop(tmp_path: Path) -> None:
    """第二次整理没有新的可覆盖内容时不再提交记录，也不再调用模型。"""
    provider = _SummaryProvider()
    store = _store(tmp_path)
    conversation_id = _seed(store)
    service = _service(tmp_path, provider, store)

    async def scenario():
        first = await service.compact(conversation_id)
        second = await service.compact(conversation_id)
        return first, second

    first, second = asyncio.run(scenario())

    assert first is not None
    assert second is None
    assert provider.requests and len(provider.requests) == 1
    events = list(store.events(conversation_id))
    assert len([event for event in events if event["type"] == "context_compacted"]) == 1
