"""每个角色独立回放成功、空结果、Schema 错误、越权及环境故障。"""

import asyncio
import json

import pytest
from changes.test_branch_executor import _seed_chain

from hastings.application.contexts import ContextBuilder
from hastings.application.errors import EnvironmentFailure
from hastings.infrastructure.agents.tau_runner import TauAgentRunner
from tau_agent.messages import AssistantMessage
from tau_agent.provider_events import AssistantErrorEvent
from tau_ai import AssistantDoneEvent, FakeProvider

ROLES = ("coordinator", "planner", "writer", "reviser", "auditor", "style_observer")


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("case", ("success", "empty", "schema", "scope", "environment"))
def test_role_contract_matrix(changes_env, role, case):
    env = changes_env
    project, base = _seed_chain(env)
    ctx = ContextBuilder(env.service).build(
        role,
        project.id,
        base,
        ("tu_x",),
        writable_ids=("tu_x", "new_chapter"),
        evidence={"edits": [{"unit_id": "tu_x", "before": "旧稿", "after": "新稿"}]},
    )
    selected = next(i for i in ctx.selected_inputs if i.artifact_id == "tu_x")
    success = {
        "coordinator": {
            "kind": "story_proposal",
            "base_commit_id": base,
            "items": [],
            "intent_kind": "query",
        },
        "planner": {"kind": "story_proposal", "base_commit_id": base, "items": []},
        "writer": {
            "kind": "manuscript_patch",
            "base_commit_id": base,
            "chapter_id": "new_chapter",
            "text": "怀表停了。",
        },
        "reviser": {
            "kind": "manuscript_patch",
            "base_commit_id": base,
            "patches": [
                {
                    "artifact_id": "tu_x",
                    "base_revision_id": selected.revision_id,
                    "content": {**selected.content, "text": "他递出怀表。"},
                }
            ],
        },
        "auditor": {
            "kind": "finding_set",
            "base_commit_id": base,
            "findings": [
                {"artifact_ids": ["tu_x"], "quote": selected.content["text"], "reason": "核对动机"}
            ],
        },
        "style_observer": {
            "kind": "observation_set",
            "base_commit_id": base,
            "observations": [
                {"unit_id": "tu_x", "category": "style", "statement": "精简", "confidence": 0.9}
            ],
        },
    }[role]
    if case == "environment":
        turns = [
            [
                AssistantErrorEvent(
                    reason="error",
                    error=AssistantMessage(stop_reason="error", error_message="网络中断"),
                )
            ]
        ]
    else:
        output = success
        if case == "empty":
            output = {"kind": "no_result", "reason": "没有需要修改的内容"}
        if case == "schema":
            output = {"kind": "not_a_contract"}
        if case == "scope":
            if role in ("coordinator", "planner"):
                output = {
                    "kind": "story_proposal",
                    "base_commit_id": base,
                    "items": [
                        {
                            "artifact_id": "outside",
                            "kind": "story.character",
                            "content": {"name": "越权"},
                        }
                    ],
                }
            elif role in ("writer", "reviser"):
                output = {
                    "kind": "manuscript_patch",
                    "base_commit_id": base,
                    "chapter_id": "outside",
                    "text": "越权正文",
                }
            elif role == "auditor":
                output = {
                    **success,
                    "findings": [
                        {"artifact_ids": ["outside"], "quote": "不存在", "reason": "伪造证据"}
                    ],
                }
            else:
                output = {
                    **success,
                    "observations": [
                        {
                            "unit_id": "outside",
                            "category": "style",
                            "statement": "无依据",
                            "confidence": 1.0,
                        }
                    ],
                }
        turn = [
            AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(content=json.dumps(output, ensure_ascii=False)),
            )
        ]
        turns = [turn, turn] if case in ("schema", "scope") else [turn]
    fake = FakeProvider(turns)
    runner = TauAgentRunner(lambda: fake, "fake", env.service)
    if case == "environment":
        with pytest.raises(EnvironmentFailure):
            asyncio.run(runner.run(role, ctx.model_dump()))
        return
    output = asyncio.run(runner.run(role, ctx.model_dump()))
    if case in ("schema", "scope"):
        assert output["kind"] == "worker_failure" and len(output["raw_outputs"]) == 2
        assert len(fake.calls) == 2
    else:
        assert output["kind"] == ("no_result" if case == "empty" else success["kind"])
    assert {t.name for t in fake.calls[0][3]} == set(ctx.capability.allowed_tools)
    from tau_agent.tools import AgentToolResult

    for tool in fake.calls[0][3]:
        assert tool.render_call({}) == f"V2 {tool.name}"
        assert (
            tool.render_result(AgentToolResult(content=[]), False)
            == "候选/查询结果（未证明项目提交）"
        )


def test_proposal_tool_terminates_as_candidate_and_emits_event(changes_env):
    from tau_agent.messages import ToolCall

    env = changes_env
    project, base = _seed_chain(env)
    ctx = ContextBuilder(env.service).build(
        "writer", project.id, base, ("art_b",), writable_ids=("new_chapter",)
    )
    proposal = {
        "kind": "manuscript_patch",
        "base_commit_id": base,
        "chapter_id": "new_chapter",
        "text": "怀表停了。",
    }
    fake = FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(
                        content=[
                            ToolCall(
                                id="proposal1",
                                name="manuscript_patch_propose",
                                arguments={"output": proposal},
                            )
                        ],
                        stop_reason="toolUse",
                    ),
                )
            ]
        ]
    )
    events = []

    async def sink(event):
        events.append(event)

    output = asyncio.run(
        TauAgentRunner(lambda: fake, "fake", env.service).run_with_events(
            "writer", ctx.model_dump(), sink
        )
    )
    assert output["kind"] == "manuscript_patch"
    assert any(e["type"] == "agent.output.proposed" for e in events)
    assert len(fake.calls) == 1
