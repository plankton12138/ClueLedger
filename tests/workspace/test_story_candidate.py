"""候选流程使用脚本化事件验证，不调用本地或在线模型。"""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.materials import ContextRequest
from hastings.workspace.service import TurnService
from hastings.workspace.state import ConversationStore
from hastings.workspace.story_candidate import StoryCandidate
from hastings.workspace.story_context import StoryContextSnapshot, StoryRequest
from hastings.workspace.story_models import AddNode, StoryChangeSet, StoryNode
from hastings.workspace.story_store import StoryStore
from tau_agent.messages import AssistantMessage, TextContent, ToolCall
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent

PATH = "正文/0001 雨夜.md"
TEXT = "# 雨夜\n\n雨仍未停。"
NEW = "# 雨夜\n\n雨已经停了。"


def setup(tmp_path: Path) -> tuple[Workspace, StoryStore, StoryContextSnapshot]:
    workspace = Workspace.create(tmp_path / "book")
    workspace.write(PATH, TEXT, expected_digest=None)
    store = StoryStore(tmp_path / "state", workspace)
    receipt = store.commit_change(
        variant_id="main",
        request_key="seed",
        change=StoryChangeSet(
            id="seed",
            base_revision="empty",
            operations=[AddNode(node=StoryNode(id="n1", title="雨停", action="雨停了"))],
        ),
    )
    return (
        workspace,
        store,
        StoryContextSnapshot(
            variant_id="main", revision=receipt.revision, purpose="write", node_ids=("n1",)
        ),
    )


def candidate(tmp_path: Path) -> StoryCandidate:
    workspace, store, frozen = setup(tmp_path)
    return StoryCandidate(
        workspace,
        store.root,
        frozen,
        "op",
        1,
        lambda: None,
        lambda kind, payload: None,
        "改写第一章",
    )


def call(tool: Any, **args: Any) -> Any:
    return asyncio.run(tool.execute_fn("call", args))


def report(status: str = "pass", quote: str = "雨已经停了") -> dict[str, Any]:
    return {
        "checks": [
            {
                "id": f"scene:{PATH}",
                "status": status,
                "path": PATH,
                "quote": quote,
                "reason": "依据候选中的天气描写核对作者要求。",
            }
        ]
    }


def test_proposal_is_persisted_without_changing_prose(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    item.propose(PATH, item.workspace.read(PATH).digest, NEW)
    assert item.workspace.read(PATH).text == TEXT
    assert json.loads(item.file.read_text(encoding="utf-8"))["documents"][PATH] == NEW
    with pytest.raises(WorkspaceError):
        item.change()


def test_candidate_proposes_to_variant_copy(tmp_path: Path) -> None:
    workspace, store, frozen = setup(tmp_path)
    fork = store.create_variant(
        name="备选",
        source_variant_id="main",
        source_revision=frozen.revision,
        request_key="fork",
    )
    variant_id = str(fork.result["variant_id"])
    variant = store.require_variant(variant_id)
    item = StoryCandidate(
        workspace,
        store.root,
        StoryContextSnapshot(
            variant_id=variant.id, revision=variant.revision, purpose="write", node_ids=("n1",)
        ),
        "op",
        1,
        lambda: None,
        lambda kind, payload: None,
        "改写第一章",
    )
    digest, _, inherited = store.prose_state(PATH, variant_id)
    # 创建备选时已复制正文基线；候选改的是受管副本，不是主方案文件。
    assert inherited is False
    item.propose(PATH, digest, NEW)
    assert workspace.read(PATH).text == TEXT
    item.approved = True
    item.status = "reviewing"
    store.commit_change(
        variant_id=variant_id, change=item.change(), request_key="candidate-variant"
    )
    assert store.read_prose(PATH, variant_id)[1] == NEW
    assert workspace.read(PATH).text == TEXT


@pytest.mark.parametrize(
    "bad", [{"checks": []}, report(quote="不存在的原文"), {"checks": [report()["checks"][0]] * 2}]
)
def test_review_requires_full_unique_coverage_and_real_quote(tmp_path: Path, bad: dict) -> None:
    item = candidate(tmp_path)
    item.propose(PATH, item.workspace.read(PATH).digest, NEW)
    item.requirements()
    item.status = "reviewing"
    with pytest.raises(WorkspaceError):
        call(item.review_tool(), **bad)
    assert not item.approved


def test_candidate_commits_structure_with_prose(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    item.propose(PATH, item.workspace.read(PATH).digest, NEW)
    item.propose_structure(
        [{"op": "add_node", "node": {"id": "n2", "title": "翌日", "action": "天亮后雨停了"}}]
    )
    item.requirements()
    assert item.review is not None
    assert any(check.requirement_id.startswith("structure:") for check in item.review.checks)
    item.status = "reviewing"
    rows = []
    for check in item.review.checks:
        row: dict[str, Any] = {
            "id": check.requirement_id,
            "status": "pass",
            "path": "",
            "quote": "",
            "reason": "按候选内容逐项核对。",
        }
        if check.requirement_id.startswith("scene:"):
            row["path"] = PATH
            row["quote"] = "雨已经停了"
        rows.append(row)
    call(item.review_tool(), checks=rows)
    assert item.approved
    receipt = StoryStore(item.state_root, item.workspace).commit_change(
        variant_id="main", change=item.change(), request_key=f"candidate:{item.id}"
    )
    assert item.workspace.read(PATH).text == NEW
    variant = StoryStore(item.state_root, item.workspace).require_variant("main")
    assert "n2" in variant.nodes
    record = StoryStore(item.state_root, item.workspace).read().document.changes[receipt.change_id]
    assert {item.op for item in record.operations} == {"add_node"}


def test_candidate_structure_needs_author_object_scope(tmp_path: Path) -> None:
    workspace, store, frozen = setup(tmp_path)
    unselected = StoryCandidate(
        workspace,
        store.root,
        replace(frozen, node_ids=()),
        "op",
        1,
        lambda: None,
        lambda kind, payload: None,
        "改写第一章",
    )
    node = store.require_variant("main").nodes["n1"].model_dump(mode="json")
    node["action"] = "雨停了，但屋檐还在滴水"
    with pytest.raises(WorkspaceError) as caught:
        unselected.propose_structure([{"op": "update_node", "node_id": "n1", "node": node}])
    assert caught.value.code == "STORY_AUTHORIZATION_CONFLICT"


def test_one_valid_report_cannot_be_replaced(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    item.propose(PATH, item.workspace.read(PATH).digest, NEW)
    item.requirements()
    item.status = "reviewing"
    call(item.review_tool(), **report("fail"))
    with pytest.raises(WorkspaceError):
        call(item.review_tool(), **report())
    assert not item.approved


def test_repair_is_local_and_single_use(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    item.propose(PATH, item.workspace.read(PATH).digest, TEXT)
    item.status = "repairing"
    patch = [{"path": PATH, "expected": "雨仍未停", "replacement": "雨已经停了"}]
    call(item.repair_tool(), patches=patch)
    assert item.documents[PATH] == NEW
    assert item.workspace.read(PATH).text == TEXT
    with pytest.raises(WorkspaceError):
        call(item.repair_tool(), patches=patch)


class Provider:
    def __init__(self, digest: str, outcome: str) -> None:
        self.digest, self.outcome, self.index = digest, outcome, 0
        self.tool_lists: list[set[str]] = []

    def stream_response(self, **kwargs: Any) -> Any:
        index = self.index
        self.index += 1
        self.tool_lists.append({tool.name for tool in kwargs.get("tools", [])})
        calls: dict[int, tuple[str, dict[str, Any]]] = {
            0: (
                "set_task_scope",
                {
                    "mode": "edit",
                    "summary": "改写第一章",
                    "authorization": "改写第一章",
                    "grants": [
                        {"action": "create" if self.digest == "absent" else "update", "path": PATH}
                    ],
                },
            ),
            1: (("list_files", {}) if self.digest == "absent" else ("read_file", {"path": PATH})),
            2: (
                "propose_story_scene",
                {
                    "path": PATH,
                    "base_digest": self.digest,
                    "text": NEW if self.outcome in ("pass", "create", "recheck") else TEXT,
                },
            ),
            4: (
                "submit_story_candidate_review",
                report("pass" if self.outcome in ("pass", "create") else "fail"),
            ),
            6: (
                "repair_story_candidate",
                {"patches": [{"path": PATH, "expected": "雨仍未停", "replacement": "雨已经停了"}]},
            ),
            8: (
                "submit_story_candidate_review",
                report("fail" if self.outcome == "fail" else "pass"),
            ),
        }
        if self.outcome == "recheck":
            # 核对报告误判时，修正阶段可不修改已经满足要求的正文。
            calls.pop(6)
            calls[7] = calls.pop(8)

        async def generate() -> Any:
            yield AssistantStartEvent(partial=AssistantMessage())
            if index in calls:
                name, arguments = calls[index]
                message = AssistantMessage(
                    content=[ToolCall(id=f"call{index}", name=name, arguments=arguments)],
                    stop_reason="toolUse",
                )
                yield AssistantDoneEvent(reason="toolUse", message=message)
            else:
                yield AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="候选已交给应用处理。")], stop_reason="stop"
                    ),
                )

        return generate()


@pytest.mark.parametrize("outcome", ["pass", "repair", "fail", "create", "recheck"])
def test_service_candidate_review_repair_and_commit(tmp_path: Path, outcome: str) -> None:
    workspace, store, frozen = setup(tmp_path)
    if outcome == "create":
        workspace.delete(PATH, expected_digest=workspace.read(PATH).digest)
    GitRepo(workspace.root).init()
    provider = Provider("absent" if outcome == "create" else workspace.read(PATH).digest, outcome)
    conversations = ConversationStore(store.root)
    service = TurnService(
        workspace,
        conversations,
        lambda: ModelSnapshot(
            connection_id="test", display_name="test", model="script", provider=provider
        ),
    )

    async def run() -> Any:
        info = await service.accept(
            "改写第一章",
            "request",
            context=ContextRequest(
                story=StoryRequest(variant_id="main", purpose="write", node_ids=("n1",))
            ),
        )
        await service.wait(info.turn_id)
        return info

    info = asyncio.run(run())
    events = list(conversations.events(info.conversation_id))
    errors = [e for e in events if e["type"] == "error"]
    assert not errors, errors
    failures = [e for e in events if e["type"] == "tool_finished" and not e.get("ok")]
    assert not failures, failures
    if outcome == "fail":
        assert workspace.read(PATH).text == TEXT
        assert any(e["type"] == "author_input_requested" for e in events)
        assert not any(e["type"] == "story_candidate_committed" for e in events)
    else:
        assert workspace.read(PATH).text == NEW
        assert any(e["type"] == "story_candidate_committed" for e in events)
        assert not any(e["type"] == "author_input_requested" for e in events)
    assert "write_file" not in provider.tool_lists[0]
    assert "update_story" not in provider.tool_lists[0]
    assert provider.tool_lists[4] == {"submit_story_candidate_review"}
    if outcome not in ("pass", "create"):
        assert provider.tool_lists[6] == {"repair_story_candidate"}
    saved = list((store.dir / "candidates").glob("*.json"))
    assert len(saved) == 1
    state = json.loads(saved[0].read_text(encoding="utf-8"))
    assert state["status"] == ("needs_input" if outcome == "fail" else "committed")


def test_program_blocking_finding_cannot_be_overridden(tmp_path: Path) -> None:
    from hastings.workspace.story_review import StoryCheck, StoryReview

    item = candidate(tmp_path)
    item.propose(PATH, item.workspace.read(PATH).digest, NEW)
    item.review = StoryReview(
        checks=(StoryCheck(f"scene:{PATH}", "核对", "semantic"),),
        findings=({"severity": "blocking", "note": "泄露真相"},),
    )
    item.status = "reviewing"
    call(item.review_tool(), **report())
    assert not item.approved
    with pytest.raises(WorkspaceError):
        item.change()


def test_revoked_generation_cannot_change_candidate(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    item.propose(PATH, item.workspace.read(PATH).digest, NEW)
    before = item.file.read_bytes()

    def revoked() -> None:
        raise WorkspaceError(
            code="OPERATION_PAUSED_CONFLICT", where="test", why="已暂停", fix="继续后重试"
        )

    item.guard = revoked
    with pytest.raises(WorkspaceError):
        item.propose(PATH, item.workspace.read(PATH).digest, TEXT)
    assert item.file.read_bytes() == before
    assert item.workspace.read(PATH).text == TEXT


def test_candidate_supports_new_chapter_without_touching_disk(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    path = "正文/0002 次日.md"
    item.propose(path, "absent", "# 次日\n\n雨已经停了。")
    assert item.originals[path] == ("absent", "")
    with pytest.raises(WorkspaceError, match="FILE_NOT_FOUND"):
        item.workspace.read(path)


def test_changed_structure_prevents_candidate_review(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    item.propose(PATH, item.workspace.read(PATH).digest, NEW)
    store = StoryStore(item.state_root, item.workspace)
    store.commit_change(
        variant_id="main",
        request_key="later",
        change=StoryChangeSet(
            id="later",
            base_revision=item.frozen.revision,
            operations=[AddNode(node=StoryNode(id="n2", title="后来", action="发生变化"))],
        ),
    )
    with pytest.raises(WorkspaceError):
        item.requirements()
    assert item.workspace.read(PATH).text == TEXT


def test_pause_during_review_blocks_late_report_and_commit(tmp_path: Path) -> None:
    workspace, store, _ = setup(tmp_path)
    GitRepo(workspace.root).init()
    conversations = ConversationStore(store.root)

    async def run() -> list[dict[str, Any]]:
        started, release = asyncio.Event(), asyncio.Event()

        class PausingProvider(Provider):
            def stream_response(self, **kwargs: Any) -> Any:
                response = super().stream_response(**kwargs)
                if self.index != 5:
                    return response

                async def delayed() -> Any:
                    started.set()
                    await release.wait()
                    async for event in response:
                        yield event

                return delayed()

        provider = PausingProvider(workspace.read(PATH).digest, "pass")
        service = TurnService(
            workspace,
            conversations,
            lambda: ModelSnapshot(
                connection_id="test", display_name="test", model="script", provider=provider
            ),
        )
        info = await service.accept(
            "改写第一章",
            "request",
            context=ContextRequest(
                story=StoryRequest(variant_id="main", purpose="write", node_ids=("n1",))
            ),
        )
        await asyncio.wait_for(started.wait(), timeout=10)
        await service.pause_operation(info.operation_id, "pause")
        release.set()
        await service.wait(info.turn_id)
        return list(conversations.events(info.conversation_id))

    events = asyncio.run(run())
    assert workspace.read(PATH).text == TEXT
    assert not any(e["type"] == "story_candidate_committed" for e in events)
    assert events[-1]["status"] == "stopped"
    saved = next((store.dir / "candidates").glob("*.json"))
    assert json.loads(saved.read_text(encoding="utf-8"))["status"] == "paused"


def test_continue_reads_only_uncommitted_prior_generation(tmp_path: Path) -> None:
    first = candidate(tmp_path)
    first.propose(PATH, first.workspace.read(PATH).digest, NEW)
    first.status = "paused"
    first.save()
    later = StoryCandidate(
        first.workspace,
        first.state_root,
        first.frozen,
        "op",
        2,
        lambda: None,
        lambda kind, payload: None,
        "继续",
    )
    prior = later.previous()
    assert prior and prior["candidate_id"] == first.id and prior["documents"][PATH] == NEW
    assert not later.documents
    assert first.workspace.read(PATH).text == TEXT
    first.workspace.write(
        PATH, "# 雨夜\n\n作者修改。", expected_digest=first.workspace.read(PATH).digest
    )
    with pytest.raises(WorkspaceError, match="STORY_CANDIDATE_CONFLICT"):
        later.previous()


def test_question_restores_story_context_and_candidate_identity(tmp_path: Path) -> None:
    from hastings.workspace.service import _context_from_question, _context_is_empty
    from hastings.workspace.story_context import StoryRequest

    _, _, frozen = setup(tmp_path)
    restored = _context_from_question({"story": frozen.to_dict(), "candidate_id": "candidate_x"})
    assert restored.story is not None
    assert restored.story.variant_id == frozen.variant_id
    assert restored.story.revision == frozen.revision
    assert restored.story.node_ids == frozen.node_ids
    assert not _context_is_empty(ContextRequest(story=StoryRequest(variant_id="main")))


def test_explicit_candidate_does_not_read_other_operation(tmp_path: Path) -> None:
    first = candidate(tmp_path)
    first.propose(PATH, first.workspace.read(PATH).digest, NEW)
    later = StoryCandidate(
        first.workspace,
        first.state_root,
        first.frozen,
        "other",
        1,
        lambda: None,
        lambda kind, payload: None,
        "回答",
        source_candidate_id=first.id,
    )
    assert later.previous()["candidate_id"] == first.id
    later.source_candidate_id = "candidate_" + "0" * 32
    with pytest.raises(WorkspaceError):
        later.previous()


def test_committed_candidate_is_not_replayed(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    item.propose(PATH, item.workspace.read(PATH).digest, NEW)
    item.requirements()
    item.status = "reviewing"
    call(item.review_tool(), **report())
    StoryStore(item.state_root, item.workspace).commit_change(
        variant_id="main", change=item.change(), request_key=f"candidate:{item.id}"
    )
    later = StoryCandidate(
        item.workspace,
        item.state_root,
        item.frozen,
        "op",
        2,
        lambda: None,
        lambda kind, payload: None,
        "继续",
    )
    assert later.previous() is None


def test_request_key_checks_story_identity() -> None:
    from hastings.workspace.service import _same_materials
    from hastings.workspace.story_context import StoryRequest

    prior = {
        "story": {
            "variant_id": "main",
            "revision": "r1",
            "node_ids": ["n1"],
            "purpose": "write",
            "reader_cutoff": None,
        },
        "selected_path": None,
        "reference_paths": [],
        "observed_digests": {},
        "idea_references": [],
        "clue_references": [],
    }

    def same(request: ContextRequest) -> bool:
        return _same_materials(prior, request, "chat", (), "chat", ())

    assert same(ContextRequest(story=StoryRequest(variant_id="main", node_ids=("n1",))))
    assert not same(ContextRequest(story=StoryRequest(variant_id="main", node_ids=("n2",))))
    assert not same(ContextRequest())


def test_answer_question_reuses_candidate_with_new_authorization(tmp_path: Path) -> None:
    workspace, store, _ = setup(tmp_path)
    GitRepo(workspace.root).init()
    conversations = ConversationStore(store.root)
    digest = workspace.read(PATH).digest

    class AnswerProvider(Provider):
        def stream_response(self, **kwargs: Any) -> Any:
            index = self.index
            self.index += 1
            self.tool_lists.append({tool.name for tool in kwargs.get("tools", [])})
            calls = {
                0: ("read_previous_story_candidate", {}),
                1: (
                    "set_task_scope",
                    {
                        "mode": "edit",
                        "summary": "调整雨停",
                        "authorization": "调整雨停",
                        "grants": [{"action": "update", "path": PATH}],
                    },
                ),
                2: ("read_file", {"path": PATH}),
                3: ("propose_story_scene", {"path": PATH, "base_digest": digest, "text": NEW}),
                5: ("submit_story_candidate_review", report()),
            }

            async def generate() -> Any:
                yield AssistantStartEvent(partial=AssistantMessage())
                if index in calls:
                    name, args = calls[index]
                    yield AssistantDoneEvent(
                        reason="toolUse",
                        message=AssistantMessage(
                            content=[ToolCall(id=f"answer{index}", name=name, arguments=args)],
                            stop_reason="toolUse",
                        ),
                    )
                else:
                    yield AssistantDoneEvent(
                        reason="stop",
                        message=AssistantMessage(
                            content=[TextContent(text="候选已交给应用处理。")], stop_reason="stop"
                        ),
                    )

            return generate()

    first_provider, answer_provider = Provider(digest, "fail"), AnswerProvider(digest, "pass")
    providers = iter([first_provider, answer_provider])
    service = TurnService(
        workspace,
        conversations,
        lambda: ModelSnapshot(
            connection_id="test", display_name="test", model="script", provider=next(providers)
        ),
    )

    async def run() -> tuple[Any, Any]:
        first = await service.accept(
            "改写第一章",
            "first",
            context=ContextRequest(
                story=StoryRequest(variant_id="main", purpose="write", node_ids=("n1",))
            ),
        )
        await service.wait(first.turn_id)
        event = next(
            e
            for e in conversations.events(first.conversation_id)
            if e["type"] == "author_input_requested"
        )
        answer = await service.accept(
            "调整雨停",
            "answer",
            conversation_id=first.conversation_id,
            reply_to_question=event["question_id"],
        )
        await service.wait(answer.turn_id)
        return first, answer

    first, answer = asyncio.run(run())
    events = list(conversations.events(first.conversation_id))
    assert answer.context and answer.context.story
    assert "read_previous_story_candidate" in answer_provider.tool_lists[0]
    assert any(
        e["type"] == "story_candidate_committed" and e["turn_id"] == answer.turn_id for e in events
    ), [e for e in events if e["type"] in ("error", "tool_finished")]
    assert workspace.read(PATH).text == NEW


@pytest.mark.parametrize(
    "operation",
    [
        {
            "op": "add_constraint",
            "constraint": {
                "id": "hard",
                "text": "禁止提前披露",
                "strength": "required",
                "revision": "r1",
            },
        },
    ],
)
def test_candidate_rejects_protected_structure(tmp_path: Path, operation: dict) -> None:
    item = candidate(tmp_path)
    with pytest.raises(WorkspaceError):
        item.propose_structure([operation])
    assert item.operations == []


def test_candidate_cannot_change_truth_or_remove_required_constraint(tmp_path: Path) -> None:
    workspace, store, frozen = setup(tmp_path)
    node = store.require_variant("main").nodes["n1"].model_dump(mode="json")
    node["clue_uses"] = [{"clue_id": "glove", "presentation": "湿手套", "mode": "narration"}]
    receipt = store.commit_change(
        variant_id="main",
        request_key="protected",
        change=StoryChangeSet(
            id="protected",
            base_revision=frozen.revision,
            operations=[
                {
                    "op": "add_clue",
                    "clue": {"id": "glove", "title": "手套", "author_truth": "管家所留"},
                },
                {"op": "update_node", "node_id": "n1", "node": node},
                {
                    "op": "add_constraint",
                    "constraint": {
                        "id": "hard",
                        "text": "保持雨停",
                        "strength": "required",
                        "scope": {"kind": "node", "target_id": "n1"},
                        "revision": "r1",
                    },
                },
            ],
        ),
    )
    item = StoryCandidate(
        workspace,
        store.root,
        replace(frozen, revision=receipt.revision),
        "op",
        1,
        lambda: None,
        lambda *args: None,
        "只写天气，不改真相和约定",
    )
    clue = store.require_variant("main").clues["glove"].model_dump(mode="json")
    clue["author_truth"] = "客人所留"
    for operation in [
        {"op": "update_clue", "clue_id": "glove", "clue": clue},
        {"op": "remove_constraint", "constraint_id": "hard"},
    ]:
        with pytest.raises(WorkspaceError, match="STORY_AUTHORIZATION_CONFLICT"):
            item.propose_structure([operation])
    assert item.operations == []
    assert store.require_variant("main").clues["glove"].author_truth == "管家所留"
    assert "hard" in store.require_variant("main").constraints


def test_candidate_does_not_accept_forged_author_provenance(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    item.propose_structure(
        [
            {
                "op": "add_clue",
                "clue": {
                    "id": "glove",
                    "title": "手套",
                    "provenance": "author_request",
                },
            }
        ]
    )
    operation = item.operations[0]
    assert operation.op == "add_clue"
    assert operation.clue.provenance == "authorized_creation"


def test_future_book_constraint_checks_non_contradiction(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    store = StoryStore(item.state_root, item.workspace)
    receipt = store.commit_change(
        variant_id="main",
        request_key="future",
        change=StoryChangeSet(
            id="future",
            base_revision=item.frozen.revision,
            operations=[
                {
                    "op": "add_constraint",
                    "constraint": {
                        "id": "ending",
                        "text": "结局保留搬尸事实",
                        "strength": "required",
                        "revision": "r1",
                    },
                }
            ],
        ),
    )
    item.frozen = replace(item.frozen, revision=receipt.revision)
    item.propose(PATH, item.workspace.read(PATH).digest, NEW)
    requirements = item.requirements()
    future = next(
        check for check in requirements.checks if check.requirement_id == "constraint:ending"
    )
    assert "不要求在当前场景提前实现或披露" in future.text
    assert "本次变更没有违背" in future.text


def test_assist_candidate_cannot_rewrite_adopted_structure(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    item.frozen = replace(item.frozen, structure_readonly=True)
    node = (
        StoryStore(item.state_root, item.workspace)
        .require_variant("main")
        .nodes["n1"]
        .model_dump(mode="json")
    )
    node["action"] = "模型想改掉采用的安排"
    with pytest.raises(WorkspaceError, match="STORY_AUTHORIZATION_CONFLICT"):
        item.propose_structure([{"op": "update_node", "node_id": "n1", "node": node}])
    assert item.operations == []


def test_single_scene_commit_creates_real_source_binding(tmp_path: Path) -> None:
    item = candidate(tmp_path)
    item.propose(PATH, item.workspace.read(PATH).digest, NEW)
    item.requirements()
    item.status = "reviewing"
    call(item.review_tool(), **report())
    store = StoryStore(item.state_root, item.workspace)
    change = item.change()
    assert change.model_dump() == item.change().model_dump()
    receipt = store.commit_change(variant_id="main", change=change, request_key="bound-scene")
    variant = store.require_variant("main")
    binding = next(iter(variant.bindings.values()))
    assert binding.node_id == "n1" and binding.path == PATH
    assert binding.quote == NEW[binding.start : binding.end]
    assert binding.digest == item.workspace.read(PATH).digest
    store.undo(change_id=receipt.change_id, request_key="undo-bound")
    assert store.require_variant("main").bindings == {}
