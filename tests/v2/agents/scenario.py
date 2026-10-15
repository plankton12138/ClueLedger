"""只供测试的 V2 剧本：经真实 Tau 适配器，而非替代应用守卫。"""

from __future__ import annotations

import json

from hastings.infrastructure.agents.tau_runner import TauAgentRunner
from tau_agent.messages import AssistantMessage
from tau_ai import AssistantDoneEvent, FakeProvider


def planning_items(project):
    def item(key, kind, body):
        return {"artifact_id": f"{project}:{key}", "kind": kind, "content": body}

    items = [
        item(
            "helper", "story.character", {"name": "林岚", "role": "protagonist", "goal": "帮助侦探"}
        )
    ]
    previous = None
    for name in ("series", "book", "arc", "chapter", "scene"):
        body = {"title": name}
        if previous:
            body["parent_id"] = f"{project}:{previous}"
        items.append(item(name, f"narrative.{name}", body))
        previous = name
    items.append(
        item(
            "beat",
            "narrative.beat",
            {
                "parent_id": f"{project}:scene",
                "title": "递出怀表",
                "purpose": "林岚协助调查",
                "pov_character_id": f"{project}:helper",
                "participants": [f"{project}:helper"],
            },
        )
    )
    return items


def scenario_output(role, ctx):
    base = ctx["repository_commit_id"]
    if role == "planner":
        return {
            "kind": "story_proposal",
            "base_commit_id": base,
            "items": planning_items(ctx["project_id"]),
        }
    if role == "writer":
        return {
            "kind": "manuscript_patch",
            "base_commit_id": base,
            "chapter_id": ctx["evidence"]["chapter_id"],
            "text": "林岚非常缓慢地递出怀表。\n\n他仍然信任这位协助者。",
        }
    if role == "reviser":
        patches = []
        for item in ctx["selected_inputs"]:
            if item["artifact_id"] not in ctx["capability"]["writable_ids"]:
                continue
            body = dict(item["content"])
            if item["kind"] == "story.character":
                body["role"] = "antagonist"
            elif item["kind"] in ("narrative.beat", "story.plot_beat"):
                body["purpose"] = "林岚诱导调查方向"
            elif item["kind"] == "manuscript.text_unit":
                body["text"] = "他开始怀疑林岚递来的怀表。"
            else:
                continue
            patches.append(
                {
                    "artifact_id": item["artifact_id"],
                    "base_revision_id": item["revision_id"],
                    "content": body,
                }
            )
        return (
            {"kind": "manuscript_patch", "base_commit_id": base, "patches": patches}
            if patches
            else {"kind": "no_result", "reason": "本步骤无内容修订"}
        )
    if role == "auditor":
        unit = next(
            (i for i in ctx["selected_inputs"] if i["kind"] == "manuscript.text_unit"), None
        )
        return {
            "kind": "finding_set",
            "base_commit_id": base,
            "findings": []
            if unit is None
            else [
                {
                    "artifact_ids": [unit["artifact_id"]],
                    "quote": unit["content"]["text"],
                    "reason": "需核对人物动机与角色变化",
                    "suggestion": "保留为作者复核项",
                }
            ],
            "editorial_notes": ["叙事节奏留给作者判断"],
        }
    if role == "style_observer":
        return {
            "kind": "observation_set",
            "base_commit_id": base,
            "observations": [
                {
                    "unit_id": e["unit_id"],
                    "category": "style",
                    "statement": "作者倾向精简修饰语",
                    "confidence": 0.95,
                }
                for e in ctx["evidence"]["edits"]
            ],
        }
    return {
        "kind": "story_proposal",
        "base_commit_id": base,
        "intent_kind": "draft",
        "items": [],
        "rationale": "按已接受节拍调用草稿工作流",
    }


class ScenarioRunner:
    def __init__(self, reader, *, violate_repair_once=False):
        self.reader = reader
        self.violate_repair_once = violate_repair_once
        self.calls = []

    async def run(self, role, context_package):
        async def quiet(event):
            pass

        return await self.run_with_events(role, context_package, quiet)

    async def run_with_events(self, role, ctx, sink):
        output = scenario_output(role, ctx)
        turns = []
        if role == "reviser" and self.violate_repair_once:
            self.violate_repair_once = False
            bad = {
                "kind": "manuscript_patch",
                "base_commit_id": ctx["repository_commit_id"],
                "chapter_id": "outside",
                "text": "越权修改",
            }
            turns.append(
                [
                    AssistantDoneEvent(
                        reason="stop", message=AssistantMessage(content=json.dumps(bad))
                    )
                ]
            )
        turns.append(
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(content=json.dumps(output, ensure_ascii=False)),
                )
            ]
        )
        provider = FakeProvider(turns)
        result = await TauAgentRunner(lambda: provider, "fake", self.reader).run_with_events(
            role, ctx, sink
        )
        self.calls.append(
            {
                "role": role,
                "context_id": ctx["context_id"],
                "provider_calls": len(provider.calls),
                "output_kind": result["kind"],
            }
        )
        return result
