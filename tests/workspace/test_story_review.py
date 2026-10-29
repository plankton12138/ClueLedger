"""1.0.1 P4：候选正文的结构核对要求与程序发现，不调用模型。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.story_context import (
    StoryRequest,
    freeze_story_context,
    verify_story_context,
)
from hastings.workspace.story_models import (
    AddClue,
    AddNode,
    Clue,
    ClueResolution,
    ClueUse,
    KnowledgeChange,
    StoryChangeSet,
    StoryNode,
    UpdateNode,
)
from hastings.workspace.story_review import build_story_review
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore

TRUTH = "管家在雪地里搬过尸体"
PRESENTATION = "前台的手套还在滴水"


@pytest.fixture
def store(tmp_path: Path) -> StoryStore:
    return StoryStore(tmp_path / "state")


def _commit(store: StoryStore, operations: list[object], key: str) -> None:
    snapshot = store.read()
    base = (
        "empty"
        if snapshot.document is None
        else snapshot.document.variants[MAIN_VARIANT_ID].revision
    )
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(id=f"change_{key}", base_revision=base, operations=operations),
        request_key=key,
    )


def seed(store: StoryStore) -> None:
    _commit(
        store,
        [
            AddClue(
                clue=Clue(
                    id="wet-gloves",
                    title="前台的手套",
                    author_truth=TRUTH,
                    resolution=ClueResolution(intent="explain", node_ids=["n2"]),
                )
            ),
            AddNode(
                node=StoryNode(
                    id="n1",
                    title="抵达",
                    action="侦探看到一双手套",
                    clue_uses=[
                        ClueUse(
                            clue_id="wet-gloves",
                            presentation=PRESENTATION,
                            mode="narration",
                        )
                    ],
                )
            ),
            AddNode(node=StoryNode(id="n2", title="对质", action="侦探把手套放在桌上")),
        ],
        "seed",
    )
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None
    node = variant.nodes["n1"]
    _commit(
        store,
        [
            UpdateNode(
                node_id="n1",
                node=node.model_copy(
                    update={
                        "knowledge_changes": [
                            KnowledgeChange(
                                subject_id="detective",
                                claim="手套的主人进过雪地",
                                effective_event_node_id="n1",
                            )
                        ]
                    }
                ),
            )
        ],
        "seed-knowledge",
    )


def review(store: StoryStore, *, documents: dict[str, str]) -> object:
    frozen = freeze_story_context(
        store, StoryRequest(variant_id=MAIN_VARIANT_ID, node_ids=("n1",))
    )
    variant = verify_story_context(store, frozen)
    return build_story_review(variant, frozen, documents=documents)


def test_requirements_are_generated_from_structure(store: StoryStore) -> None:
    seed(store)

    result = review(store, documents={"正文/0001.md": "侦探看到一双手套，还在滴水。"})

    ids = [check.requirement_id for check in result.checks]
    assert "present:n1:wet-gloves" in ids
    assert "hidden:wet-gloves" in ids
    assert "knowledge:n1:detective" in ids
    # 要求写进了核对提示，并声明不能改。
    assert "不能改写或跳过" in result.text
    assert "present:n1:wet-gloves" in result.text
    # 呈现没有原样出现：给一条可说明的提示，而不是直接判失败。
    assert any(item["severity"] == "advisory" for item in result.findings)


def test_written_truth_is_a_blocking_finding_with_position(store: StoryStore) -> None:
    seed(store)
    text = f"侦探把杯子放下。\n{TRUTH}。\n他什么也没说。"
    path = "正文/0002.md"

    result = review(store, documents={path: text})

    assert any(item["severity"] == "blocking" for item in result.findings)
    finding = next(item for item in result.findings if item["severity"] == "blocking")
    assert finding["path"] == path
    assert text[finding["start"] : finding["end"]] == TRUTH
    assert finding["quote"] == TRUTH
    # 位置会被写进提示，核对阶段可以直接引用。
    assert f"{path}:{finding['start']}-{finding['end']}" in result.text


def test_present_text_and_no_truth_leak_has_no_findings(store: StoryStore) -> None:
    seed(store)

    result = review(
        store,
        documents={"正文/0001.md": f"前台的人说：{PRESENTATION}。侦探没有多问。"},
    )

    assert result.findings == ()
    assert "present:n1:wet-gloves" in result.text


def test_empty_selection_produces_nothing(store: StoryStore) -> None:
    seed(store)
    frozen = freeze_story_context(store, StoryRequest(variant_id=MAIN_VARIANT_ID))
    variant = verify_story_context(store, frozen)

    result = build_story_review(variant, frozen, documents={"正文/0001.md": TRUTH})

    assert result.empty is True


def test_service_review_block_is_built_from_the_frozen_structure(tmp_path: Path) -> None:
    """核对阶段拿到的要求来自冻结结构，而不是模型自己的报告。"""
    from hastings.server.provider_runtime import ModelSnapshot
    from hastings.workspace.files import Workspace
    from hastings.workspace.materials import ContextSnapshot
    from hastings.workspace.service import TurnInfo, TurnService
    from hastings.workspace.state import ConversationStore

    store = StoryStore(tmp_path / "state")
    seed(store)
    workspace = Workspace.create(tmp_path / "book")
    conversations = ConversationStore(tmp_path / "state")
    service = TurnService(
        workspace,
        conversations,
        lambda: ModelSnapshot(
            connection_id="test", display_name="测试连接", model="fake", provider=object()
        ),
    )
    chapter = "正文/0001 雨夜.md"
    (workspace.root / "正文").mkdir(parents=True, exist_ok=True)
    (workspace.root / chapter).write_text(f"雪停了。\n{TRUTH}。\n", encoding="utf-8")

    frozen = freeze_story_context(
        store, StoryRequest(variant_id=MAIN_VARIANT_ID, node_ids=("n1",))
    )
    info = TurnInfo(
        turn_id="t1",
        conversation_id="c1",
        started_at="",
        context=ContextSnapshot(
            selected_path=None,
            references=(),
            observed_digests={},
            story=frozen,
        ),
    )

    block = service._story_review_block(info, [chapter])

    assert "present:n1:wet-gloves" in block
    assert "hidden:wet-gloves" in block
    assert "blocking" in block
    assert f"{chapter}:" in block

    # 同一轮次的结构没有变化时，重复生成的要求一致（不依赖当前界面）。
    assert service._story_review_block(info, [chapter]) == block
