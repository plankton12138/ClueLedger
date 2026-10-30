"""1.0.1 P3：结构进入模型输入、认知分栏与试读隔离，不调用模型。

这些用例对应验收表 D11（实际输入）、D12（长期连续性）、D13（试读隔离）中属于
装配层的部分。判断依据是「实际会进入请求的文本里有什么、没有什么」，不是模型写得怎样。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hastings.prompts.reading import READER_QUESTION
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import digest_bytes
from hastings.workspace.story_context import (
    StoryContextSnapshot,
    StoryRequest,
    assemble_reader_input,
    assemble_story_input,
    assert_reader_isolated,
    freeze_story_context,
    scoped_request,
    split_story_request,
    story_required_chars,
    verify_story_context,
)
from hastings.workspace.story_clue_catalog import StoryClueCatalog, StoryClueFact
from hastings.workspace.story_models import (
    AddNode,
    BindProse,
    CluePlanInput,
    ClueUse,
    Connect,
    KnowledgeChange,
    ProseBinding,
    StoryChangeSet,
    StoryEdge,
    StoryNode,
    StoryVariant,
    UpdateNode,
    UpsertCluePlan,
)
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore

_TITLES: dict[str, str] = {}
_counter = {"value": 0}


def change_id() -> str:
    _counter["value"] += 1
    return f"change_p3_{_counter['value']}"


@pytest.fixture
def store(tmp_path: Path) -> StoryStore:
    return StoryStore(tmp_path / "state" / "workspaces" / "ws1")


def commit(
    store: StoryStore,
    operations: list[Any],
    *,
    key: str,
    variant_id: str = MAIN_VARIANT_ID,
) -> Any:
    snapshot = store.read()
    base = "empty" if snapshot.document is None else snapshot.document.variants[variant_id].revision
    change = StoryChangeSet(id=change_id(), base_revision=base, operations=operations)
    return store.commit_change(variant_id=variant_id, change=change, request_key=key)


def node(node_id: str, title: str, action: str, **kwargs: Any) -> StoryNode:
    return StoryNode(id=node_id, title=title, action=action, **kwargs)


def add_clue(
    clue_id: str,
    title: str,
    truth: str | None,
    *,
    intention: str = "",
    resolution_intent: str = "undecided",
) -> UpsertCluePlan:
    _TITLES[clue_id] = title
    return UpsertCluePlan(
        clue_id=clue_id,
        plan=CluePlanInput(
            author_truth=truth,
            intention=intention,
            resolution_intent=resolution_intent,
        ),
    )


def catalog_for(variant: StoryVariant) -> StoryClueCatalog:
    ids = set(variant.clue_plans)
    ids.update(use.clue_id for node in variant.nodes.values() for use in node.clue_uses)
    return StoryClueCatalog(
        facts={
            clue_id: StoryClueFact(
                clue_id=clue_id,
                title=_TITLES.get(clue_id, clue_id),
                plan=variant.clue_plans.get(clue_id),
                has_identity=True,
            )
            for clue_id in ids
        }
    )


def replace_node(store: StoryStore, node_id: str, **fields: Any) -> UpdateNode:
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None
    target = variant.nodes[node_id]
    return UpdateNode(node_id=node_id, node=target.model_copy(update=fields))


def seed_long_distance(store: StoryStore) -> None:
    """第 1 个节点埋设线索，第 24 个节点回收；中间节点不参与。"""
    commit(
        store,
        [
            add_clue(
                "wet-gloves",
                "前台的手套",
                "管家在雪地里搬过尸体",
                intention="让读者以为手套是侦探留下的",
                resolution_intent="explain",
            ),
            AddNode(
                node=node(
                    "n1",
                    "抵达旅馆",
                    "侦探在前台看到一双手套，边缘还在滴水",
                    clue_uses=[
                        ClueUse(
                            clue_id="wet-gloves",
                            presentation="前台的手套还在滴水",
                            mode="narration",
                            expected_interpretation="读者会怀疑侦探自己",
                        )
                    ],
                )
            ),
            AddNode(node=node("n2", "走廊", "走廊尽头的灯忽明忽暗")),
            AddNode(
                node=node(
                    "n24",
                    "对质",
                    "侦探把手套放在管家面前，问他雪夜里搬了什么",
                    clue_uses=[
                        ClueUse(
                            clue_id="wet-gloves",
                            roles=["resolve"],
                            presentation="侦探用雪地里的痕迹说明手套的来历",
                            mode="narration",
                        )
                    ],
                )
            ),
        ],
        key="seed-nodes",
    )
    commit(
        store,
        [Connect(edge=StoryEdge(id="e1", from_id="n1", to_id="n2", kind="next"))],
        key="seed-edge-1",
    )
    commit(
        store,
        [Connect(edge=StoryEdge(id="e2", from_id="n2", to_id="n24", kind="next"))],
        key="seed-edge-2",
    )


def assemble(store: StoryStore, node_ids: list[str], **kwargs: Any) -> Any:
    frozen = freeze_story_context(
        store, StoryRequest(variant_id=MAIN_VARIANT_ID, node_ids=tuple(node_ids))
    )
    variant = verify_story_context(store, frozen)
    kwargs.setdefault("catalog", catalog_for(variant))
    return assemble_story_input(variant, frozen, **kwargs)


def test_blank_plan_still_sends_a_usable_context() -> None:
    """空白方案也要实际告诉模型现在能建立什么，不能只靠界面提示。"""
    variant = StoryVariant(id=MAIN_VARIANT_ID, name="主方案", revision="empty")
    frozen = StoryContextSnapshot(
        variant_id=MAIN_VARIANT_ID, revision="empty", purpose="design"
    )

    story = assemble_story_input(variant, frozen)

    assert story.empty is False
    assert story.coverage["empty_plan"] is True
    text = story.render()
    assert "当前方案为空" in text
    assert "未定" in text


def test_long_distance_clue_reaches_required_content(store: StoryStore) -> None:
    seed_long_distance(store)

    result = assemble(store, ["n24"])
    text = result.render()

    # 回收节点必须拿到线索真相、前面的出现方式与回收意图，否则只能靠猜。
    assert "管家在雪地里搬过尸体" in text
    assert "前台的手套还在滴水" in text
    assert "对质" in text
    keys = [section.key for section in result.sections]
    assert keys[:4] == ["macro", "truth", "plan", "prerequisites"]
    # 所有必需项都在，没有被容量逻辑悄悄丢掉。
    assert result.omitted == ()


def test_future_nodes_are_not_injected(store: StoryStore) -> None:
    seed_long_distance(store)
    commit(
        store,
        [
            add_clue(
                "later-reveal",
                "后文的解释",
                "管家还有同谋",
                resolution_intent="leave_open",
            ),
            AddNode(node=node("n30", "尾声", "同谋在站台等车")),
        ],
        key="seed-later",
    )

    result = assemble(store, ["n24"])
    text = result.render()

    # 后面才出现的节点动作不能进入当前这一轮的输入。
    assert "同谋在站台等车" not in text
    # 但它自己的回收目标（当前节点）仍然成立。
    assert "对质" in text


def test_other_variant_is_not_injected(store: StoryStore) -> None:
    seed_long_distance(store)
    snapshot = store.read()
    assert snapshot.document is not None
    receipt = store.create_variant(
        name="备选",
        source_variant_id=MAIN_VARIANT_ID,
        source_revision=snapshot.variant(MAIN_VARIANT_ID).revision,
        request_key="alt-key",
    )
    alt_id = str(receipt.result["variant_id"])
    commit(
        store,
        [
            add_clue("alt-clue", "备选线索", "备选方案里管家是无辜的"),
            AddNode(node=node("alt-n1", "另一条路", "侦探先去了车站")),
        ],
        key="seed-alt",
        variant_id=alt_id,
    )

    result = assemble(store, ["n24"])
    text = result.render()

    assert "备选方案里管家是无辜的" not in text
    assert "侦探先去了车站" not in text


def test_knowledge_uses_only_earlier_changes(store: StoryStore) -> None:
    seed_long_distance(store)
    commit(
        store,
        [
            replace_node(
                store,
                "n24",
                knowledge_changes=[
                    KnowledgeChange(
                        subject_id="detective",
                        claim="管家进过雪地",
                        source_node_ids=["n1"],
                        effective_event_node_id="n2",
                    )
                ],
            )
        ],
        key="seed-knowledge",
    )

    result = assemble(store, ["n24"])
    text = result.render()

    assert "detective 知道：管家进过雪地" in text


def test_capacity_refuses_to_truncate_required_content(store: StoryStore) -> None:
    seed_long_distance(store)
    frozen = freeze_story_context(
        store, StoryRequest(variant_id=MAIN_VARIANT_ID, node_ids=("n1", "n24"))
    )
    variant = verify_story_context(store, frozen)
    full = assemble_story_input(variant, frozen, catalog=catalog_for(variant))
    required = sum(len(section.text) for section in full.sections if section.required)

    with pytest.raises(WorkspaceError) as excinfo:
        assemble_story_input(variant, frozen, capacity=required - 1, catalog=catalog_for(variant))

    assert excinfo.value.code == "STORY_CONTEXT_INCOMPLETE"
    assert excinfo.value.details["required"] == required

    # 容量只够必需项时，可裁的创作目标被省略，并说明原因。
    roomy = assemble_story_input(variant, frozen, capacity=required + 10, catalog=catalog_for(variant))
    assert any(item["key"] == "judgement" for item in roomy.omitted)
    assert "管家在雪地里搬过尸体" in roomy.render()


def test_reader_preview_hides_truth_and_future_nodes(store: StoryStore) -> None:
    seed_long_distance(store)
    frozen = freeze_story_context(
        store,
        StoryRequest(
            variant_id=MAIN_VARIANT_ID,
            purpose="reader_preview",
            reader_cutoff="n2",
        ),
    )
    variant = verify_story_context(store, frozen)

    reader = assemble_reader_input(variant, "n2")
    text = reader.render()

    assert "前台的手套还在滴水" in text
    assert "管家在雪地里搬过尸体" not in text
    assert "对质" not in text
    assert "管家" not in text
    assert reader.reader is True
    # 自检通过：实际要发送的文本里没有隐藏内容。
    assert_reader_isolated(text, variant, "n2")

    with pytest.raises(WorkspaceError) as excinfo:
        assert_reader_isolated(text + "\n管家在雪地里搬过尸体", variant, "n2")
    assert excinfo.value.code == "STORY_READER_LEAK"


def test_frozen_revision_must_still_match(store: StoryStore) -> None:
    seed_long_distance(store)
    frozen = freeze_story_context(
        store, StoryRequest(variant_id=MAIN_VARIANT_ID, node_ids=("n24",))
    )

    commit(store, [replace_node(store, "n24", action="改过了")], key="seed-edit")

    with pytest.raises(WorkspaceError) as excinfo:
        verify_story_context(store, frozen)
    assert excinfo.value.code == "STORY_REVISION_CONFLICT"


def test_blank_structure_freezes_empty_main_but_rejects_named_variants(store: StoryStore) -> None:
    """空白构思可以进对话（模型用它建立第一份结构）；点名别的方案仍然拒绝。"""
    frozen = freeze_story_context(store, StoryRequest(purpose="design"))
    assert (frozen.variant_id, frozen.revision, frozen.node_ids) == (MAIN_VARIANT_ID, "empty", ())
    # 执行前复核在首轮（还没有文件）不算失败：返回同一份空主方案，模型用它建立结构。
    verified = verify_story_context(store, frozen)
    assert (verified.id, verified.revision) == (MAIN_VARIANT_ID, "empty")
    # 执行前复核在首轮（还没有文件）不算失败：返回同一份空主方案，模型用它建立结构。
    verified = verify_story_context(store, frozen)
    assert (verified.id, verified.revision) == (MAIN_VARIANT_ID, "empty")
    # 试读在空结构上没有意义：仍按无效处理。
    with pytest.raises(WorkspaceError) as excinfo:
        freeze_story_context(store, StoryRequest(purpose="reader_preview"))
    assert excinfo.value.code == "STORY_SCOPE_INVALID"

    with pytest.raises(WorkspaceError) as named:
        freeze_story_context(store, StoryRequest(variant_id="variant_alt"))
    assert named.value.code == "STORY_VARIANT_NOT_FOUND"

    with pytest.raises(WorkspaceError) as revised:
        freeze_story_context(store, StoryRequest(revision="rev_x"))
    assert revised.value.code == "STORY_VARIANT_NOT_FOUND"


def test_unknown_node_is_rejected_at_accept(store: StoryStore) -> None:
    seed_long_distance(store)

    with pytest.raises(WorkspaceError) as excinfo:
        freeze_story_context(store, StoryRequest(variant_id=MAIN_VARIANT_ID, node_ids=("n99",)))

    assert excinfo.value.code == "STORY_CONTEXT_INCOMPLETE"


def test_snapshot_roundtrips_for_request_identity() -> None:
    frozen = StoryContextSnapshot(
        variant_id="main", revision="r3", node_ids=("n1",), purpose="write"
    )

    again = StoryContextSnapshot.from_dict(frozen.to_dict())

    assert again == frozen
    assert StoryContextSnapshot.from_dict({"variant_id": ""}) is None


# -- 接线：结构真的进入这一轮的请求 -----------------------------------------


def _partial(text: str = "") -> Any:
    from tau_agent.messages import AssistantMessage, TextContent

    return AssistantMessage(content=[TextContent(text=text)] if text else [])


class CaptureProvider:
    """按脚本回复并保留实际收到的系统提示与消息。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def stream_response(self, **kwargs: Any) -> Any:
        from tau_agent.messages import AssistantMessage, TextContent
        from tau_agent.provider_events import (
            AssistantDoneEvent,
            AssistantStartEvent,
        )

        self.requests.append(
            {
                "system": kwargs.get("system") or "",
                "messages": list(kwargs.get("messages") or []),
                "tools": list(kwargs.get("tools") or []),
            }
        )
        index = len(self.requests) - 1

        async def generator() -> Any:
            yield AssistantStartEvent(partial=_partial())
            if index == 0:
                yield AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="写好了")], stop_reason="stop"
                    ),
                )

        return generator()


def _service(tmp_path: Path, provider: Any) -> tuple[Any, Any, Any]:
    from hastings.server.provider_runtime import ModelSnapshot
    from hastings.workspace.files import Workspace
    from hastings.workspace.service import TurnService
    from hastings.workspace.state import ConversationStore

    workspace = Workspace.create(tmp_path / "book")
    store = ConversationStore(tmp_path / "state")
    service = TurnService(
        workspace,
        store,
        lambda: ModelSnapshot(
            connection_id="test", display_name="测试连接", model="fake", provider=provider
        ),
    )
    return workspace, store, service


def test_turn_sends_required_structure_to_the_model(tmp_path: Path) -> None:
    import asyncio

    from hastings.workspace.materials import ContextRequest

    story = StoryStore(tmp_path / "state")
    seed_long_distance(story)
    provider = CaptureProvider()
    _, store, service = _service(tmp_path, provider)

    async def scenario() -> Any:
        info = await service.accept(
            "写对质这一场",
            "story-turn-1",
            context=ContextRequest(
                story=StoryRequest(variant_id=MAIN_VARIANT_ID, node_ids=("n24",))
            ),
        )
        await service.wait(info.turn_id)
        return info

    info = asyncio.run(scenario())

    assert provider.requests, "这一轮没有请求模型"
    system = provider.requests[0]["system"]
    assert "构思结构" in system
    assert "管家在雪地里搬过尸体" in system
    assert "前台的手套还在滴水" in system
    # 实际装进去的内容与覆盖范围落在事件里，界面与日志读同一份结果。
    events = list(store.events(info.conversation_id))
    story_events = [event for event in events if event["type"] == "story_context"]
    assert len(story_events) == 1
    assert story_events[0]["coverage"]["nodes"] == ["n24"]
    assert story_events[0]["revision"]


def test_new_conversation_gets_the_same_structure_without_history(tmp_path: Path) -> None:
    import asyncio

    from hastings.workspace.materials import ContextRequest

    story = StoryStore(tmp_path / "state")
    seed_long_distance(story)
    provider = CaptureProvider()
    _, _, service = _service(tmp_path, provider)

    async def scenario() -> None:
        for index, key in enumerate(("story-a", "story-b")):
            info = await service.accept(
                f"写第 {index + 1} 次",
                key,
                context=ContextRequest(
                    story=StoryRequest(variant_id=MAIN_VARIANT_ID, node_ids=("n24",))
                ),
            )
            await service.wait(info.turn_id)

    asyncio.run(scenario())

    assert len(provider.requests) == 2
    first, second = provider.requests
    # 第二个会话没有更早的讨论，也必须拿到同一份必需结构。
    assert "管家在雪地里搬过尸体" in second["system"]
    assert first["system"].count("构思结构") == second["system"].count("构思结构")


def test_stale_structure_revision_is_rejected_at_accept(tmp_path: Path) -> None:
    import asyncio

    from hastings.workspace.materials import ContextRequest

    story = StoryStore(tmp_path / "state")
    seed_long_distance(story)
    provider = CaptureProvider()
    _, _, service = _service(tmp_path, provider)

    async def scenario() -> None:
        await service.accept(
            "写对质这一场",
            "stale-1",
            context=ContextRequest(
                story=StoryRequest(
                    variant_id=MAIN_VARIANT_ID,
                    revision="rev_does_not_exist",
                    node_ids=("n24",),
                )
            ),
        )

    with pytest.raises(WorkspaceError) as excinfo:
        asyncio.run(scenario())

    assert excinfo.value.code == "STORY_REVISION_CONFLICT"
    assert provider.requests == []


# -- 试读：独立执行入口 ------------------------------------------------------


def test_reader_preview_runs_without_tools_history_or_catalog(tmp_path: Path) -> None:
    import asyncio

    from tau_agent.messages import message_text

    story = StoryStore(tmp_path / "state")
    seed_long_distance(story)
    provider = CaptureProvider()
    workspace, store, service = _service(tmp_path, provider)
    workspace.write("正文/0001 城门.md", "# 城门\n\n雪夜。\n", expected_digest=None)

    async def scenario() -> Any:
        info = await service.accept_reader("reader-turn-1", variant_id=MAIN_VARIANT_ID, cutoff="n2")
        await service.wait(info.turn_id)
        return info

    info = asyncio.run(scenario())

    request = provider.requests[0]
    # 读者阶段没有工具、没有作者历史，问题由应用固定。
    assert request["tools"] == []
    assert len(request["messages"]) == 1
    assert message_text(request["messages"][0]) == READER_QUESTION
    system = request["system"]
    # 截止节点及其前驱的呈现稿进入请求。
    assert "前台的手套还在滴水" in system
    assert "抵达旅馆" not in system
    # 真相、未来节点、线索标题、正文目录与正文原文都不进入试读。
    assert "管家在雪地里搬过尸体" not in system
    assert "对质" not in system
    assert "城门" not in system
    assert "雪夜" not in system

    events = list(store.events(info.conversation_id))
    started = [event for event in events if event["type"] == "turn_started"][0]
    assert started["origin"] == "reader"
    story_events = [event for event in events if event["type"] == "story_context"]
    assert story_events[0]["reader"] is True
    assert story_events[0]["coverage"]["nodes"] == ["n1", "n2"]


def test_reader_preview_rejects_free_question_and_author_materials(tmp_path: Path) -> None:
    import asyncio

    from hastings.workspace.materials import ContextRequest

    story = StoryStore(tmp_path / "state")
    seed_long_distance(story)
    provider = CaptureProvider()
    workspace, _, service = _service(tmp_path, provider)
    workspace.write("正文/0001 城门.md", "# 城门\n", expected_digest=None)

    async def free_question() -> None:
        await service.accept(
            "作者自己的问题",
            "reader-free",
            context=ContextRequest(
                story=StoryRequest(purpose="reader_preview", reader_cutoff="n2")
            ),
        )

    with pytest.raises(WorkspaceError) as excinfo:
        asyncio.run(free_question())
    assert excinfo.value.code == "STORY_SCOPE_INVALID"
    assert provider.requests == []

    async def with_materials() -> None:
        await service.accept(
            "reader-materials",
            "reader-materials-key",
            context=ContextRequest(
                selected_path="正文/0001 城门.md",
                story=StoryRequest(purpose="reader_preview", reader_cutoff="n2"),
            ),
        )

    with pytest.raises(WorkspaceError) as excinfo2:
        asyncio.run(with_materials())
    assert excinfo2.value.code == "STORY_SCOPE_INVALID"
    assert provider.requests == []


# -- 容量：按节点拆组 --------------------------------------------------------


def test_split_groups_fitting_nodes_and_stops_dependents(store: StoryStore) -> None:
    commit(
        store,
        [
            add_clue(
                "clue-a",
                "线索甲",
                "甲是真的",
                resolution_intent="leave_open",
            ),
            add_clue(
                "clue-b",
                "线索乙",
                "乙是真的",
                resolution_intent="explain",
            ),
            AddNode(
                node=node(
                    "n_a",
                    "开端",
                    "开头一句",
                    clue_uses=[
                        ClueUse(clue_id="clue-a", presentation="甲被提起", mode="narration")
                    ],
                )
            ),
            AddNode(
                node=node(
                    "n_b",
                    "转折",
                    "转折一句",
                    clue_uses=[
                        ClueUse(
                            clue_id="clue-b",
                            roles=["resolve"],
                            presentation="乙在这里得到解释",
                            mode="narration",
                        )
                    ],
                )
            ),
        ],
        key="seed-split",
    )
    commit(
        store,
        [Connect(edge=StoryEdge(id="e_ab", from_id="n_a", to_id="n_b", kind="next"))],
        key="seed-split-edge",
    )
    frozen = freeze_story_context(
        store, StoryRequest(variant_id=MAIN_VARIANT_ID, node_ids=("n_a", "n_b"))
    )
    variant = verify_story_context(store, frozen)
    alone_a = story_required_chars(variant, scoped_request(frozen, ("n_a",)))
    alone_b = story_required_chars(variant, scoped_request(frozen, ("n_b",)))
    both = story_required_chars(variant, frozen)
    assert both > alone_b >= alone_a

    fit = split_story_request(variant, frozen, capacity=alone_b)
    assert [batch.node_ids for batch in fit.batches] == [("n_a",), ("n_b",)]
    assert fit.stopped == ()

    # 单个节点仍装不下：它和依赖它的后继一起停下，其余节点照常执行。
    stopped = split_story_request(variant, frozen, capacity=alone_a)
    assert [batch.node_ids for batch in stopped.batches] == [("n_a",)]
    assert stopped.stopped == ("n_b",)
    assert "n_b" in stopped.note()

    with pytest.raises(WorkspaceError) as excinfo:
        split_story_request(variant, frozen, capacity=alone_a - 1)
    assert excinfo.value.code == "STORY_CONTEXT_INCOMPLETE"


def test_over_capacity_turn_sends_first_batch_and_records_split(tmp_path: Path) -> None:
    import asyncio

    from hastings.workspace.materials import ContextRequest

    story = StoryStore(tmp_path / "state")
    seed_long_distance(story)
    n24 = story.require_variant(MAIN_VARIANT_ID).nodes["n24"]
    commit(
        story,
        [
            add_clue(
                "long-truth",
                "长线索",
                "LONGTRUTH-" * 1_500,
                resolution_intent="explain",
            ),
            UpdateNode(
                node_id="n24",
                node=n24.model_copy(
                    update={
                        "clue_uses": [
                            *n24.clue_uses,
                            ClueUse(
                                clue_id="long-truth",
                                roles=["resolve"],
                                presentation="长线索在这里解释",
                                mode="narration",
                            ),
                        ]
                    }
                ),
            ),
        ],
        key="seed-long-truth",
    )
    provider = CaptureProvider()
    _, store, service = _service(tmp_path, provider)

    async def scenario() -> Any:
        info = await service.accept(
            "把这两场一起写出来",
            "split-turn-1",
            context=ContextRequest(
                story=StoryRequest(variant_id=MAIN_VARIANT_ID, node_ids=("n1", "n24"))
            ),
        )
        await service.wait(info.turn_id)
        return info

    info = asyncio.run(scenario())

    system = provider.requests[0]["system"]
    # 只装第 1 组：小节点进入请求，装不下的节点的动作与它的长真相都不进入。
    assert "抵达旅馆" in system
    assert "侦探把手套放在管家面前" not in system
    assert "LONGTRUTH" not in system
    assert "已拆成" in system
    events = list(store.events(info.conversation_id))
    story_event = [event for event in events if event["type"] == "story_context"][0]
    assert story_event["coverage"]["nodes"] == ["n1"]
    assert story_event["split"]["stopped"] == ["n24"]
    assert story_event["split"]["batches"][0]["node_ids"] == ["n1"]
    finished = [event for event in events if event["type"] == "run_finished"][0]
    assert finished["status"] == "completed"


def test_next_story_batch_survives_refresh_and_deduplicates(tmp_path: Path) -> None:
    import asyncio

    from hastings.workspace.operations import Operation

    story = StoryStore(tmp_path / "state")
    seed_long_distance(story)
    provider = CaptureProvider()
    _, _, service = _service(tmp_path, provider)

    async def scenario() -> None:
        first = await service.accept("只讨论场景，不保存", "batch-parent")
        await service.wait(first.turn_id)

        def defer(operation: Operation) -> None:
            operation.pending_units = ["n1"]
            operation.recovery_request["story_batches"] = {
                "variant_id": "main",
                "purpose": "discuss",
                "remaining": [["n1"]],
                "stopped": [],
            }

        service.operations.update(first.operation_id, defer)
        next_info = await service.next_story_batch(first.operation_id)
        await service.wait(next_info.turn_id)
        replay = await service.next_story_batch(first.operation_id)
        assert replay.turn_id == next_info.turn_id
        assert replay.operation_id == next_info.operation_id
        assert len(provider.requests) == 2
        assert next_info.context and next_info.context.story
        assert next_info.context.story.node_ids == ("n1",)

    asyncio.run(scenario())


def test_reader_excludes_author_node_labels(store: StoryStore) -> None:
    seed_long_distance(store)
    variant = store.require_variant("main")
    node = variant.nodes["n1"].model_copy(
        update={"title": "AUTHOR_SECRET_LABEL", "action": "AUTHOR_SECRET_ACTION"}
    )
    variant = variant.model_copy(update={"nodes": {**variant.nodes, "n1": node}})
    text = assemble_reader_input(variant, "n1").render()
    assert "AUTHOR_SECRET" not in text
    assert "前台的手套还在滴水" in text


def test_reader_allows_truth_already_published_in_verified_prose(store: StoryStore) -> None:
    truth = "凶手是一直守在门口的管家"
    text = f"侦探说：{truth}。"
    path = "正文/0001 揭示.md"
    digest = digest_bytes(text.encode("utf-8"))
    commit(
        store,
        [
            add_clue("glove", "手套", truth),
            AddNode(
                node=node(
                    "n1",
                    "揭示",
                    "侦探公开结论",
                    clue_uses=[
                        ClueUse(clue_id="glove", presentation=text, mode="narration")
                    ],
                )
            ),
            BindProse(
                binding=ProseBinding(
                    id="revealed",
                    node_id="n1",
                    document_id="document-1",
                    path=path,
                    digest=digest,
                    start=0,
                    end=len(text),
                    quote=text,
                )
            ),
        ],
        key="reader-revealed",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)

    reader = assemble_reader_input(variant, "n1", prose={path: (digest, text)})
    actual = reader.render()

    assert truth in actual
    assert reader.published == text
    assert reader.coverage["published_chars"] == len(text)
    # 同一句真相已经由截止前经核验的正文公开，可以正常发送。
    assert_reader_isolated(actual, variant, "n1", published=reader.published)
    # 没有已公开正文作为依据时仍然隔离：不能靠关闭自检来放行。
    with pytest.raises(WorkspaceError) as caught:
        assert_reader_isolated(actual, variant, "n1")
    assert caught.value.code == "STORY_READER_LEAK"


def test_reader_still_rejects_stale_prose_binding(store: StoryStore) -> None:
    truth = "凶手是一直守在门口的管家"
    text = f"侦探说：{truth}。"
    path = "正文/0001 揭示.md"
    digest = digest_bytes(text.encode("utf-8"))
    commit(
        store,
        [
            add_clue("glove", "手套", truth),
            AddNode(node=node("n1", "揭示", "侦探公开结论")),
            BindProse(
                binding=ProseBinding(
                    id="revealed",
                    node_id="n1",
                    document_id="document-1",
                    path=path,
                    digest=digest,
                    start=0,
                    end=len(text),
                    quote=text,
                )
            ),
        ],
        key="reader-stale",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)

    with pytest.raises(WorkspaceError) as caught:
        assemble_reader_input(variant, "n1", prose={path: ("stale", text)})

    assert caught.value.code == "STORY_READER_SOURCE_CONFLICT"
