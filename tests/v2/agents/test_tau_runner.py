import asyncio
import json

import pytest
from changes.test_branch_executor import _seed_chain

from hastings.application.contexts import ContextBuilder
from hastings.infrastructure.agents.tau_runner import TauAgentRunner
from tau_agent.messages import AssistantMessage
from tau_ai import AssistantDoneEvent, FakeProvider


@pytest.mark.parametrize(
    "role", ["coordinator", "planner", "writer", "reviser", "auditor", "style_observer"]
)
def test_tool_json_references_resolve_at_parameter_root(changes_env, role):
    project, base = _seed_chain(changes_env)
    ctx = ContextBuilder(changes_env.service).build(role, project.id, base, ("art_b",))
    fake = FakeProvider([response({"kind": "no_result", "reason": "本轮无候选"})])
    asyncio.run(
        TauAgentRunner(lambda: fake, "fake", changes_env.service).run(role, ctx.model_dump())
    )

    def check(value, definitions):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].rsplit("/", 1)[-1] in definitions
            for child in value.values():
                check(child, definitions)
        elif isinstance(value, list):
            for child in value:
                check(child, definitions)

    for tool in fake.calls[0][3]:
        check(tool.parameters, tool.parameters.get("$defs", {}))


def response(body):
    return [
        AssistantDoneEvent(
            reason="stop", message=AssistantMessage(content=json.dumps(body, ensure_ascii=False))
        )
    ]


def test_schema_correction_is_bounded_and_retains_context(changes_env):
    project, base = _seed_chain(changes_env)
    ctx = ContextBuilder(changes_env.service).build("writer", project.id, base, ("art_b",))
    fake = FakeProvider(
        [response({"kind": "bad"}), response({"kind": "no_result", "reason": "缺少已确认节拍"})]
    )
    events = []

    async def sink(event):
        events.append(event)

    runner = TauAgentRunner(lambda: fake, "fake", changes_env.service)
    output = asyncio.run(runner.run_with_events("writer", ctx.model_dump(), sink))
    assert output["kind"] == "no_result"
    assert len(fake.calls) == 2
    assert any(e["type"] == "agent.schema_correction" for e in events)
    assert all(e.get("commit_id") is None for e in events)
    assert not {"bible_upsert", "write_chapter", "story_stage", "approval_grant"} & {
        t.name for t in fake.calls[0][3]
    }


def test_second_bad_output_is_terminal_with_evidence(changes_env):
    project, base = _seed_chain(changes_env)
    ctx = ContextBuilder(changes_env.service).build("auditor", project.id, base, ("art_b",))
    ctx.capability.allowed_tools.append("manuscript_patch_propose")
    fake = FakeProvider(
        [
            response({"kind": "bad"}),
            response({"kind": "still_bad"}),
            response({"kind": "no_result", "reason": "第三次不可到达"}),
        ]
    )
    output = asyncio.run(
        TauAgentRunner(lambda: fake, "fake", changes_env.service).run("auditor", ctx.model_dump())
    )
    assert output["kind"] == "worker_failure"
    assert len(output["raw_outputs"]) == 2
    assert len(fake.calls) == 2
    assert "manuscript_patch_propose" not in {t.name for t in fake.calls[0][3]}


def test_nested_domain_shape_error_receives_one_correction(changes_env):
    project, base = _seed_chain(changes_env)
    ctx = ContextBuilder(changes_env.service).build(
        "planner", project.id, base, (), creation_kinds=("narrative.beat",)
    )
    bad = {
        "kind": "story_proposal",
        "base_commit_id": base,
        "items": [
            {
                "artifact_id": f"{project.id.value}:beat",
                "kind": "narrative.beat",
                "content": {"parent_id": "art_b", "state_changes": ["wrong shape"]},
            }
        ],
    }
    fake = FakeProvider(
        [response(bad), response({"kind": "no_result", "reason": "需要重新规划状态变化"})]
    )
    output = asyncio.run(
        TauAgentRunner(lambda: fake, "fake", changes_env.service).run("planner", ctx.model_dump())
    )
    assert output["kind"] == "no_result" and len(fake.calls) == 2
