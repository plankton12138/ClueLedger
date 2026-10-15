"""纯领域规划预演；同一检查用于模型纠正和作者接受。"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from hastings.application.workflows.change_support import reject
from hastings.domain.repository.ids import ArtifactId
from hastings.domain.repository.models import Snapshot
from hastings.domain.repository.requests import ArtifactChange, RelationChange
from hastings.domain.story.canon import commands as canon
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.narrative import commands as narrative
from hastings.domain.story.narrative.models import DisclosureSpec, StateChange
from hastings.domain.worker import RunCapability


def preview_story(
    snapshot: Snapshot, cap: RunCapability, output: dict[str, Any]
) -> tuple[tuple[ArtifactChange, ...], tuple[RelationChange, ...]]:
    catalog = StoryCatalog.from_snapshot(snapshot)
    pending = []
    edges = []
    for item in output["items"]:
        aid, kind, body = item["artifact_id"], item["kind"], dict(item["content"])
        if (
            kind not in cap.creation_kinds
            or not aid.startswith(cap.creation_prefix)
            or catalog.get(aid)
        ):
            reject(
                "RUN_SCOPE_VIOLATION",
                "planner.accept",
                "规划只能新建授权类型的候选对象",
                "保留 proposed，不能覆盖已有事实",
            )
        functions: dict[str, Callable[..., Any]] = {
            "story.character": canon.create_character,
            "story.clue": canon.plan_clue,
            "story.red_herring": canon.plan_red_herring,
            "story.mystery_truth": canon.define_mystery_truth,
            "narrative.beat": narrative.create_beat,
        }
        fn = functions.get(kind, narrative.create_node)
        for identity in ("schema_version", "node_id", "beat_id"):
            body.pop(identity, None)
        if kind.startswith("narrative.") and kind != "narrative.beat":
            body["kind"] = kind
        if kind == "narrative.beat":
            body["state_changes"] = tuple(
                StateChange.from_content(s) for s in body.get("state_changes", [])
            )
            body["reader_disclosures"] = tuple(
                DisclosureSpec.from_content(s) for s in body.get("reader_disclosures", [])
            )
        if not set(body) <= set(inspect.signature(fn).parameters) - {"catalog", "new_id"}:
            reject(
                "STORY_INVALID_COMMAND",
                "planner.accept",
                "候选包含不支持的命令字段",
                "按对应 Story 领域命令修正候选内容",
            )
        domain_change = fn(catalog, lambda unused, target=aid: target, **body)
        pending.extend(domain_change.artifact_changes)
        edges.extend(domain_change.relation_changes)
        if kind == "narrative.beat":
            participants = set(body.get("participants", ())) | {body.get("pov_character_id", "")}
            for character in sorted(participants - {""}):
                entry = catalog.get(character)
                if entry and entry.content.get("role") in (
                    "protagonist",
                    "antagonist",
                    "accomplice",
                ):
                    edges.append(
                        RelationChange("add", ArtifactId(character), "motivates", ArtifactId(aid))
                    )
        catalog = replace(
            catalog,
            entries=(
                *catalog.entries,
                *(
                    StoryEntry(c.artifact_id.value, c.kind or kind, "pending", c.content)
                    for c in domain_change.artifact_changes
                ),
            ),
        )
    return tuple(pending), tuple(edges)
