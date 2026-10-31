"""调查问题的回归：草稿零部分写入、局部修正、知识时点与来源确认。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hastings.library.modules import ModuleLibrary
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.module_assembly import ModuleRef
from hastings.workspace.story_authorization import validate_model_change
from hastings.workspace.story_constraint_sources import ConstraintSources
from hastings.workspace.story_context import StoryContextSnapshot, _knowledge_lines
from hastings.workspace.story_drafts import StoryDraftStore
from hastings.workspace.story_knowledge import effective_knowledge
from hastings.workspace.story_models import (
    Constraint,
    KnowledgeChange,
    StoryChangeSet,
    StoryNode,
    StoryVariant,
    validate_story_variant,
)
from hastings.workspace.story_store import StoryStore
from hastings.workspace.story_structure import structure_diagnostics
from hastings.workspace.story_tools import StoryToolScope, build_story_tools


def call(tools: list[Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    tool = next(t for t in tools if t.name == name)
    return dict(asyncio.run(tool.execute_fn("call", arguments)).details)


def scope(root: Path, **kwargs: Any) -> StoryToolScope:
    return StoryToolScope(
        state_root=root,
        frozen=StoryContextSnapshot(variant_id="main", revision="empty", purpose="design"),
        writable=True,
        operation_id="op-test",
        generation=1,
        author_text="创建构思",
        **kwargs,
    )


def add_node(id: str, **kwargs: Any) -> dict[str, Any]:
    return {"op": "add_node", "node": {"id": id, "title": id, "action": "局面变化", **kwargs}}


def test_draft_partial_plan_local_repair_atomic_commit_and_replay(tmp_path: Path) -> None:
    tools = build_story_tools(scope(tmp_path))
    rows = [
        {"op": "create_clue_identity", "temp_id": "glove", "title": "湿手套"},
        {"op": "upsert_clue_plan", "clue_id": "glove", "plan": {"resolution_intent": "explain"}},
    ]
    prepared = call(tools, "update_story", {"mode": "prepare", "operations": rows})
    assert prepared["pending_dependencies"]
    assert StoryStore(tmp_path).read().document is None
    draft = call(tools, "read_story", {"source": "draft"})
    assert draft["operations"][1]["operation"]["plan"]["resolution_intent"] == "explain"
    fixed = call(
        tools,
        "update_story",
        {
            "mode": "prepare",
            "draft_revision": prepared["draft_revision"],
            "operations": [
                add_node(
                    "n1",
                    clue_uses=[
                        {"clue_id": "glove", "roles": ["resolve"], "presentation": "查明手套来源"}
                    ],
                )
            ],
        },
    )
    assert not fixed["issues"]
    assert StoryStore(tmp_path).read().document is None
    result = call(
        tools, "update_story", {"mode": "commit", "draft_revision": fixed["draft_revision"]}
    )
    replay = call(
        build_story_tools(scope(tmp_path)),
        "update_story",
        {"mode": "commit", "draft_revision": fixed["draft_revision"]},
    )
    assert replay == result
    document = StoryStore(tmp_path).read().document
    assert document and len(document.changes) == 1
    assert len(document.variants["main"].nodes) == 1
    assert StoryDraftStore(tmp_path, "op-test", "main").read()["committed"]


def test_draft_preserves_strength_and_local_repair(tmp_path: Path) -> None:
    tools = build_story_tools(scope(tmp_path))
    required = {
        "op": "add_constraint",
        "constraint": {
            "id": "c1",
            "text": "不能有外来凶手",
            "strength": "required",
            "revision": "r1",
        },
    }
    prepared = call(
        tools, "update_story", {"mode": "prepare", "operations": [required, add_node("n1")]}
    )
    with pytest.raises(WorkspaceError, match="不能降低"):
        call(
            tools,
            "update_story",
            {
                "mode": "prepare",
                "draft_revision": prepared["draft_revision"],
                "replace_operations": [
                    {
                        "index": 0,
                        "operation": {
                            **required,
                            "constraint": {**required["constraint"], "strength": "preference"},
                        },
                    }
                ],
            },
        )
    with pytest.raises(WorkspaceError) as exc:
        call(
            tools, "update_story", {"mode": "commit", "draft_revision": prepared["draft_revision"]}
        )
    assert exc.value.code == "STORY_AUTHORIZATION_CONFLICT"
    fixed = call(
        tools,
        "update_story",
        {
            "mode": "prepare",
            "draft_revision": prepared["draft_revision"],
            "replace_operations": [{"index": 1, "operation": add_node("n1", action="新的变化")}],
        },
    )
    draft = call(tools, "read_story", {"source": "draft"})
    assert draft["operations"][0]["operation"]["constraint"]["strength"] == "required"
    assert fixed["draft_revision"] != prepared["draft_revision"]
    assert StoryStore(tmp_path).read().document is None


def test_malformed_arguments_and_all_field_errors_are_reported(tmp_path: Path) -> None:
    tools = build_story_tools(scope(tmp_path))
    with pytest.raises(WorkspaceError) as exc:
        call(tools, "update_story", {"_raw_arguments": '{"operations": [}'})
    assert exc.value.code == "STORY_ARGUMENTS_INVALID"
    assert exc.value.details["column"] == 17
    with pytest.raises(WorkspaceError) as exc:
        call(
            tools,
            "update_story",
            {"operations": [add_node("n1", title=""), add_node("n2", title="")]},
        )
    assert {item["operation_index"] for item in exc.value.details["issues"]} == {0, 1}
    assert StoryStore(tmp_path).read().document is None


def test_draft_revision_pause_and_resume(tmp_path: Path) -> None:
    tools = build_story_tools(scope(tmp_path))
    prepared = call(tools, "update_story", {"mode": "prepare", "operations": [add_node("n1")]})
    with pytest.raises(WorkspaceError) as exc:
        call(tools, "update_story", {"operations": [add_node("bypass")]})
    assert exc.value.code == "STORY_DRAFT_CONFLICT"
    with pytest.raises(WorkspaceError):
        call(tools, "update_story", {"mode": "prepare", "operations": [add_node("n2")]})

    def paused() -> None:
        raise WorkspaceError(
            code="OPERATION_PAUSED_CONFLICT", where="test", why="已暂停", fix="继续"
        )

    with pytest.raises(WorkspaceError, match="已暂停"):
        call(
            build_story_tools(scope(tmp_path, guard=paused)),
            "update_story",
            {"mode": "commit", "draft_revision": prepared["draft_revision"]},
        )
    assert StoryStore(tmp_path).read().document is None
    resumed_scope = scope(tmp_path)
    object.__setattr__(resumed_scope, "generation", 2)
    call(
        build_story_tools(resumed_scope),
        "update_story",
        {"mode": "commit", "draft_revision": prepared["draft_revision"]},
    )
    assert StoryStore(tmp_path).require_variant("main").nodes.keys() == {"n1"}


def test_read_node_roundtrip_keeps_ids_knowledge_and_edge_reason(tmp_path: Path) -> None:
    tools = build_story_tools(scope(tmp_path))
    rows = [
        {"op": "add_thread", "thread": {"id": "t", "title": "侦探", "order": 0}},
        add_node(
            "n1",
            thread_ids=["t"],
            primary_thread_id="t",
            knowledge_changes=[
                {
                    "subject_id": "hero",
                    "claim": "手套湿了",
                    "source_node_ids": ["n1"],
                    "effective_event_node_id": "n1",
                }
            ],
        ),
        add_node("n2"),
        {
            "op": "connect",
            "edge": {
                "id": "e1",
                "from_id": "n1",
                "to_id": "n2",
                "kind": "next",
                "reason": "湿手套促使询问",
            },
        },
    ]
    call(tools, "update_story", {"operations": rows})
    read = call(tools, "read_story", {"node_ids": ["n1"]})
    node = StoryNode.model_validate(read["nodes"][0])
    assert node.thread_ids == ["t"]
    assert node.knowledge_changes[0].effective_event_node_id == "n1"
    assert read["edges"][0]["reason"] == "湿手套促使询问"


def test_knowledge_uses_effective_event_not_storage_or_reveal_order() -> None:
    variant = StoryVariant(
        id="main",
        name="构思",
        revision="r1",
        nodes={id: StoryNode(id=id, title=id, action=id) for id in ("past", "present", "later")},
        event_times=[
            {"before_node_id": "past", "after_node_id": "present"},
            {"before_node_id": "present", "after_node_id": "later"},
        ],
    )
    variant.nodes["past"].knowledge_changes = [
        KnowledgeChange(
            subject_id="hero",
            claim="后来才知道",
            effective_event_node_id="later",
            source_node_ids=["past"],
            source_kind="inference",
        )
    ]
    validate_story_variant(variant)
    assert not effective_knowledge(variant, ["present"])
    assert not _knowledge_lines(
        variant, [variant.nodes["present"]], {"present": frozenset({"past", "later"})}
    )
    assert effective_knowledge(variant, ["later"])[0].claim == "后来才知道"
    assert "后来才知道" in "".join(_knowledge_lines(variant, [variant.nodes["later"]], {}))
    variant.nodes["past"].knowledge_changes[0].source_node_ids = ["later"]
    with pytest.raises(ValueError):
        # 重建字段模型仍拒绝同场推断。
        StoryVariant.model_validate(variant.model_dump())


def test_module_constraint_confirmation_cannot_be_forged_or_downgraded(tmp_path: Path) -> None:
    library = ModuleLibrary(tmp_path / "library")
    created = library.create_module(
        kind="reference",
        name="创作约束",
        entries=[{"entry_id": "rules", "title": "规则", "body": "不能有外来凶手。"}],
        request_key="create-rules",
    )
    module = created["module"]
    ref = ModuleRef(str(library.library_id), str(module["module_id"]), str(module["head"]))
    record = library.read_entry(
        module_id=ref.module_id, revision_id=ref.revision_id, entry_id="rules"
    )
    constraint = Constraint(
        id="rule",
        text="不能有外来凶手。",
        revision="r1",
        strength="required",
        source_ref={
            "library_id": ref.library_id,
            "module_id": ref.module_id,
            "revision_id": ref.revision_id,
            "entry_id": "rules",
            "digest": record["digest"],
        },
    )
    sources = ConstraintSources(tmp_path)
    variant = StoryVariant(id="main", name="构思", revision="empty")
    change = StoryChangeSet(
        id="c1",
        base_revision="empty",
        operations=[{"op": "add_constraint", "constraint": constraint.model_dump()}],
    )
    with pytest.raises(WorkspaceError):
        validate_model_change(variant, change, "创建构思")
    sources.confirm([constraint], library, (ref,))
    verified = sources.verified(library, (ref,))
    validate_model_change(variant, change, "创建构思", confirmed_sources=verified)
    assert change.operations[0].constraint.origin == "author_module"
    assert not sources.verified(library, ())
    edited = constraint.model_copy(update={"text": "可以有外来凶手"})
    with pytest.raises(WorkspaceError):
        sources.confirm([edited], library, (ref,))


def test_single_line_valid_and_multimembership_is_not_convergence() -> None:
    with pytest.raises(ValidationError):
        StoryNode(id="x", title="x", action="x", thread_ids=["a"], primary_thread_id="b")
    node = StoryNode(id="x", title="x", action="x", thread_ids=["a", "b"], primary_thread_id="a")
    assert not node.is_convergence
    variant = StoryVariant(
        id="main", name="单线", revision="r1", nodes={"x": StoryNode(id="x", title="x", action="x")}
    )
    validate_story_variant(variant)
    assert not structure_diagnostics(variant)


@pytest.mark.parametrize("with_source", [True, False])
def test_author_confirms_exact_module_constraints_through_api(
    tmp_path: Path, with_source: bool
) -> None:
    from fastapi.testclient import TestClient

    from hastings.server.file_app import create_workspace_app
    from hastings.workspace.module_assembly import AssemblyStore, Binding
    from hastings.workspace.operations import Operation
    from hastings.workspace.state import RecentWorkspaces, conversation_root

    library = ModuleLibrary(tmp_path / "library")
    created = library.create_module(
        kind="reference",
        name="规则",
        entries=[{"entry_id": "rules", "title": "规则", "body": "不能有外来凶手。"}],
        request_key="create-rules",
    )
    ref = ModuleRef(
        str(library.library_id), created["module"]["module_id"], created["module"]["head"]
    )
    _, digest = library.storage.read_entry(ref.module_id, ref.revision_id, "rules")
    constraint = Constraint(
        id="c1",
        text="不能有外来凶手。",
        revision="r1",
        source_ref={**ref.view(), "entry_id": "rules", "digest": digest} if with_source else None,
    )
    workspaces = RecentWorkspaces(tmp_path / "state")
    app = create_workspace_app(
        workspaces=workspaces,
        library=library,
        provider_factory=lambda: pytest.fail("确认来源不能调用模型"),
    )
    with TestClient(app) as client:
        book = client.post(
            "/api/file-workspaces", json={"path": str(tmp_path / "book"), "create": True}
        ).json()
        wid = book["id"]
        root = conversation_root(workspaces.root, wid)
        AssemblyStore(Path(book["path"])).replace(
            library_id=ref.library_id,
            bindings=[Binding(id="bind1", module=ref)],
            expected_digest=None,
            request_key="bind-rules",
        )
        operation = Operation(
            id="op-test",
            owner=f"workspace:{wid}",
            workspace_id=wid,
            conversation_id="c-test",
            turn_id="t-test",
            purpose="chat",
            status="needs_attention",
        )
        app.state.operations.create(operation)
        draft = StoryDraftStore(root, "op-test", "main").prepare(
            variant=StoryVariant(id="main", name="构思", revision="empty"),
            operation_id="op-test",
            generation=1,
            operations=[{"op": "add_constraint", "constraint": constraint.model_dump()}],
            replacements={},
            module_refs=(ref,),
        )
        base = f"/api/file-workspaces/{wid}/story/drafts/op-test"
        shown = client.get(base).json()["draft"]
        assert shown["constraints"][0]["text"] == constraint.text
        assert (
            client.post(base + "/confirm-constraints", json={"draft_revision": "stale"}).status_code
            == 409
        )
        confirmed = client.post(
            base + "/confirm-constraints", json={"draft_revision": draft["digest"]}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["confirmed"] == 1
        assert bool(ConstraintSources(root).verified(library, (ref,))) == with_source
        assert StoryDraftStore(root, "op-test", "main").read()["confirmed_constraints"]
        assert StoryStore(root).read().document is None
        tools = build_story_tools(scope(root, library=library, module_refs=(ref,)))
        assert call(
            tools,
            "update_story",
            {
                "mode": "commit",
                "draft_revision": draft["digest"],
            },
        )["ok"]


def test_draft_confirmation_is_lost_when_content_changes(tmp_path: Path) -> None:
    from hastings.workspace.story_constraint_sources import source_key

    tools = build_story_tools(scope(tmp_path))
    constraint = Constraint(id="c1", text="凶手不逃跑。", revision="r1")
    first = call(
        tools,
        "update_story",
        {
            "mode": "prepare",
            "operations": [
                {"op": "add_constraint", "constraint": constraint.model_dump(mode="json")},
                add_node("n1"),
            ],
        },
    )
    drafts = StoryDraftStore(tmp_path, "op-test", "main")
    drafts.confirm_constraints(first["draft_revision"], [source_key(constraint)])
    changed = call(
        tools,
        "update_story",
        {
            "mode": "prepare",
            "draft_revision": first["draft_revision"],
            "operations": [add_node("n2")],
        },
    )
    assert not drafts.read().get("confirmed_constraints")
    with pytest.raises(WorkspaceError):
        drafts.confirm_constraints(first["draft_revision"], [source_key(constraint)])
    with pytest.raises(WorkspaceError) as error:
        call(tools, "update_story", {"mode": "commit", "draft_revision": changed["draft_revision"]})
    assert error.value.code == "STORY_AUTHORIZATION_CONFLICT"
    assert StoryStore(tmp_path).read().document is None


def test_syntax_and_misplaced_fields_report_repairs_without_overwriting(tmp_path: Path) -> None:
    tools = build_story_tools(scope(tmp_path))
    first = call(tools, "update_story", {"mode": "prepare", "operations": [add_node("n1")]})
    with pytest.raises(WorkspaceError) as error:
        call(tools, "update_story", {"_raw_arguments": '{"operations":[]]'})
    assert error.value.code == "STORY_ARGUMENTS_INVALID"
    assert error.value.details["parser_message"] == "Expecting ',' delimiter"
    with pytest.raises(WorkspaceError) as error:
        call(
            tools,
            "update_story",
            {
                "mode": "prepare",
                "draft_revision": first["draft_revision"],
                "operations": [{**add_node("n2"), "clue_uses": []}],
            },
        )
    issue = error.value.details["issues"][0]
    assert issue["object_id"] == "n2" and "移入 node" in issue["suggestion"]
    assert StoryDraftStore(tmp_path, "op-test", "main").read()["digest"] == first["draft_revision"]


@pytest.mark.parametrize("kind", ["knowledge", "resolution"])
def test_local_repair_preserves_knowledge_time_and_planned_ending(
    tmp_path: Path, kind: str
) -> None:
    tools = build_story_tools(scope(tmp_path))
    if kind == "knowledge":
        knowledge = {
            "subject_id": "hero",
            "claim": "手套湿了",
            "effective_event_node_id": "n1",
            "source_node_ids": ["n1"],
        }
        original = add_node("n1", knowledge_changes=[knowledge])
        changed = add_node("n1", knowledge_changes=[{**knowledge, "effective_event_node_id": "n2"}])
    else:
        original = {
            "op": "upsert_clue_plan",
            "clue_id": "c1",
            "plan": {"resolution_intent": "leave_open"},
        }
        changed = {
            "op": "upsert_clue_plan",
            "clue_id": "c1",
            "plan": {"resolution_intent": "explain"},
        }
    prepared = call(tools, "update_story", {"mode": "prepare", "operations": [original]})
    with pytest.raises(WorkspaceError) as error:
        call(
            tools,
            "update_story",
            {
                "mode": "prepare",
                "draft_revision": prepared["draft_revision"],
                "replace_operations": [{"index": 0, "operation": changed}],
            },
        )
    assert error.value.code == "STORY_SEMANTIC_CONFLICT"
    assert (
        StoryDraftStore(tmp_path, "op-test", "main").read()["digest"] == prepared["draft_revision"]
    )
    assert StoryStore(tmp_path).read().document is None


def test_invalid_local_replacement_returns_field_errors_and_keeps_draft(tmp_path: Path) -> None:
    tools = build_story_tools(scope(tmp_path))
    prepared = call(tools, "update_story", {"mode": "prepare", "operations": [add_node("n1")]})
    with pytest.raises(WorkspaceError) as error:
        call(
            tools,
            "update_story",
            {
                "mode": "prepare",
                "draft_revision": prepared["draft_revision"],
                "replace_operations": [{"index": 0, "operation": {"op": "add_node"}}],
            },
        )
    assert error.value.code == "STORY_CHANGE_INVALID"
    assert error.value.details["issues"][0]["operation_index"] == 0
    assert (
        StoryDraftStore(tmp_path, "op-test", "main").read()["digest"] == prepared["draft_revision"]
    )


def test_resume_recovers_receipt_after_commit_before_draft_marker(tmp_path: Path) -> None:
    def lost_record(kind: str, payload: Any) -> None:
        if kind == "story_changes_committed":
            raise RuntimeError("模拟收据事件保存前中断")

    tools = build_story_tools(scope(tmp_path, record=lost_record))
    prepared = call(tools, "update_story", {"mode": "prepare", "operations": [add_node("n1")]})
    with pytest.raises(RuntimeError):
        call(
            tools, "update_story", {"mode": "commit", "draft_revision": prepared["draft_revision"]}
        )
    assert not StoryDraftStore(tmp_path, "op-test", "main").read()["committed"]
    recovered: list[str] = []
    tools = build_story_tools(scope(tmp_path, record=lambda kind, payload: recovered.append(kind)))
    result = call(
        tools, "update_story", {"mode": "commit", "draft_revision": prepared["draft_revision"]}
    )
    assert result["ok"] and recovered == ["story_changes_committed"]
    assert len(StoryStore(tmp_path).read().document.changes) == 1


def test_actual_tool_boundary_delivers_structured_diagnostics(tmp_path: Path) -> None:
    from tau_agent.loop import _run_tool
    from tau_agent.messages import ToolCall

    tool = next(t for t in build_story_tools(scope(tmp_path)) if t.name == "update_story")
    result, failed, _ = asyncio.run(
        _run_tool(
            tool,
            ToolCall(
                id="bad",
                name="update_story",
                arguments={"mode": "prepare", "operations": [{"op": "add_node", "id": "n1"}]},
            ),
            None,
        )
    )
    assert failed and "issues" in result.text and "operation_index" in result.text
    assert "node" in result.text


def test_append_errors_identify_batch_and_global_target_without_changing_draft(
    tmp_path: Path,
) -> None:
    tools = build_story_tools(scope(tmp_path))
    first = call(tools, "update_story", {"mode": "prepare", "operations": [add_node("n1")]})
    for bad in ({"op": "add_node", "node": {"id": "n2"}}, {"id": "n2"}):
        with pytest.raises(WorkspaceError) as error:
            call(
                tools,
                "update_story",
                {"mode": "prepare", "draft_revision": first["draft_revision"], "operations": [bad]},
            )
        issue = error.value.details["issues"][0]
        assert (issue["operation_index"], issue["batch_index"], issue["object_id"]) == (1, 0, "n2")
        assert (
            StoryDraftStore(tmp_path, "op-test", "main").read()["digest"] == first["draft_revision"]
        )


def test_wrong_target_and_duplicate_cannot_overwrite_valid_draft(tmp_path: Path) -> None:
    tools = build_story_tools(scope(tmp_path))
    first = call(
        tools,
        "update_story",
        {
            "mode": "prepare",
            "operations": [
                {"op": "create_clue_identity", "temp_id": "c1", "title": "线索"},
                add_node("n1"),
            ],
        },
    )
    for arguments in (
        {"replace_operations": [{"index": 0, "operation": add_node("n1")}]},
        {"operations": [add_node("n1")]},
    ):
        with pytest.raises(WorkspaceError):
            call(
                tools,
                "update_story",
                {"mode": "prepare", "draft_revision": first["draft_revision"], **arguments},
            )
        assert (
            StoryDraftStore(tmp_path, "op-test", "main").read()["digest"] == first["draft_revision"]
        )
    lookup = call(tools, "read_story", {"source": "draft", "target_id": "n1"})
    assert lookup["operations"][0]["index"] == 1
    patched = call(
        tools,
        "update_story",
        {
            "mode": "prepare",
            "draft_revision": first["draft_revision"],
            "node_patches": [{"node_id": "n1", "fields": {"change": "改变调查方向"}}],
        },
    )
    assert patched["draft_revision"] != first["draft_revision"]
