"""总览更新沿用一个作品轮次，每个目标使用独立模型上下文。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from hastings.workspace.disclosure import check_evidence
from hastings.workspace.material_tasks import _story_event
from hastings.workspace.materials import covers, merge_spans
from hastings.workspace.module_assembly import AssemblyStore
from hastings.workspace.module_facets import check_quote, module_projection, overview_error
from hastings.workspace.overview_models import LinkResult, ModuleSource
from hastings.workspace.story_index import ChapterProjection, StoryIndexStore, validate_projection
from hastings.workspace.story_overview import StoryOverview
from tau_agent.harness import AgentHarness, AgentHarnessConfig
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult

if TYPE_CHECKING:
    from hastings.server.provider_runtime import ModelSnapshot
    from hastings.workspace.agent import RunContext
    from hastings.workspace.service import TurnInfo, TurnService


def freeze_overview(service: TurnService, request: dict[str, Any]) -> dict[str, Any]:
    overview = StoryOverview(
        service.workspace, service.store.root, service.library, request["as_of_chapter"]
    )
    overview.require_generation(request["observed_generation"])
    targets = [target for target in overview.targets if not overview.complete(target)]
    return {
        "domain": "overview",
        "request": request,
        "as_of_chapter": overview.as_of,
        "assembly_digest": overview.assembly_digest,
        "targets": targets,
        "sources": overview.module_targets + overview.chapter_targets,
    }


def verify_frozen(run: RunContext, scope: dict[str, Any], target: dict[str, Any]) -> None:
    if AssemblyStore(run.workspace.root).digest() != scope["assembly_digest"]:
        raise overview_error("OVERVIEW_SOURCE_CHANGED_CONFLICT", "作品挂载的模块已改变")
    if target["kind"] != "module" and run.workspace.read(target["path"]).digest != target["digest"]:
        raise overview_error("OVERVIEW_SOURCE_CHANGED_CONFLICT", "章节在任务受理后已改变")
    if target["kind"] == "module":
        source = target["source"]
        if run.library is None or run.library.library_id != source["library_id"]:
            raise overview_error("OVERVIEW_REF_UNAVAILABLE", "本轮模块库不可用")
        # 库读接口复核不可变版本清单和原文指纹。
        entry = run.library.read_entry(
            module_id=source["module_id"],
            revision_id=source["revision_id"],
            entry_id=source["entry_id"],
            end=0,
        )
        if entry["digest"] != source["digest"]:
            raise overview_error("OVERVIEW_SOURCE_CHANGED_CONFLICT", "模块原文指纹已改变")


INSTRUCTIONS = """你正在为线簿整理故事总览。只能读取冻结目标并发布派生结果，不能修改作者内容。
必须先 read_overview_source 分段读完目标原文。字符位置为原始文本的 Unicode 码点半开区间。
模块原文中可有多个人物、线索，也可以完全没有；不能按标题或旧类型猜内容。
当前 target.kind=module 时，任务是提取作者设定中的人物与线索，不是检查它们是否已经写入正文。
模块可以只是一个普通段落，不需要任何小标题。出现明确人物或关键物件时要提取。
原文夹有未来计划也不能忽略已经明确的人物和物件；计划在 summary 中说明其性质。
只有全文确实没有人物或线索时才返回 empty；拿不准则返回 insufficient。
人物关系必须有明确肯定的原文依据；否定、假设、猜测不能变成肯定关系。
关系端点仅用当前提交 facets 的下标。
章节事件保留 narration/testimony/recollection/hypothesis/dream/unknown 表达方式。
证词不能升级成客观事实。
仅在明确日期时规范化日期，不猜先后关系。事件每章最多30条，摘要最多500字。
关联只用给定的完整 ref 和已发布 event_id；同名不代表同一对象，依据不足不要关联。
作者设定和计划不是正文事实。没有发现返回 empty；没有读完或无法确认返回 insufficient。
完成后调用 publish_overview_result；不能以文字声明替代发布。"""


def overview_tools(
    run: RunContext, scope: dict[str, Any], target: dict[str, Any], overview: StoryOverview
) -> list[AgentTool]:
    spans: list[tuple[int, int]] = []
    published = False
    read_chars = 0

    def source_text() -> str:
        verify_frozen(run, scope, target)
        if target["kind"] == "module":
            source = target["source"]
            assert run.library is not None
            text, digest = run.library.storage.read_entry(
                source["module_id"], source["revision_id"], source["entry_id"]
            )
            if digest != source["digest"]:
                raise overview_error("OVERVIEW_SOURCE_CHANGED_CONFLICT", "模块内容指纹已改变")
            return text
        return run.workspace.read(target["path"]).text

    def output(payload: dict[str, Any]) -> AgentToolResult:
        return AgentToolResult(content=[TextContent(text=json.dumps(payload, ensure_ascii=False))])

    async def read_source(
        tool_call_id: str, arguments: Any, signal: Any = None, on_update: Any = None
    ) -> AgentToolResult:
        nonlocal read_chars
        del tool_call_id, signal, on_update
        text = source_text()
        try:
            start = int(arguments.get("start", 0))
        except (ValueError, TypeError):
            raise overview_error("OVERVIEW_EVIDENCE_INVALID", "读取起点必须是整数") from None
        if start < 0 or start > len(text):
            raise overview_error("OVERVIEW_EVIDENCE_INVALID", "读取起点不在原文内")
        end = min(start + 6000, len(text))
        if read_chars + end - start > 24000:
            return output(
                {
                    "budget_exhausted": True,
                    "length": len(text),
                    "note": "本批原文读取预算已用完；未完整读取时只能返回 insufficient。",
                }
            )
        read_chars += end - start
        spans.append((start, end))
        return output(
            {
                "text": text[start:end],
                "start": start,
                "end": end,
                "length": len(text),
                "next_start": end if end < len(text) else None,
            }
        )

    async def publish(
        tool_call_id: str, arguments: Any, signal: Any = None, on_update: Any = None
    ) -> AgentToolResult:
        nonlocal published
        del tool_call_id, signal, on_update
        if published:
            raise overview_error("OVERVIEW_EVIDENCE_INVALID", "这个目标已经发布过结果")
        text = source_text()
        raw = arguments["result"]
        if not isinstance(raw, dict):
            raise overview_error("OVERVIEW_RESULT_INVALID", "整理结果必须是对象")
        outcome = raw.get("outcome", "found")
        if outcome != "insufficient" and not covers(merge_spans(spans), 0, len(text)):
            raise overview_error("OVERVIEW_EVIDENCE_INVALID", "尚未完整读取目标原文，不能声明完成")
        if target["kind"] == "module":
            result = module_projection(ModuleSource.model_validate(target["source"]), raw, text)
            overview.store.publish(target, result, run.turn_id)
        elif target["kind"] == "chapter":
            if outcome == "insufficient":
                return output({"outcome": "insufficient", "published": False})
            if (
                outcome != "found"
                or not isinstance(raw.get("summary"), str)
                or not isinstance(raw.get("events"), list)
                or not all(isinstance(item, dict) for item in raw["events"])
            ):
                raise overview_error(
                    "OVERVIEW_RESULT_INVALID", "章节整理需要摘要、事件数组和 found 覆盖状态"
                )
            if any(
                any(item.get(key) for key in ("speaker", "related", "author_event"))
                for item in raw.get("events", [])
            ):
                raise overview_error(
                    "OVERVIEW_REF_UNAVAILABLE", "章节整理不能提交未经对象关联核对的模块引用"
                )
            events = tuple(_story_event(item, target["chapter"]) for item in raw.get("events", []))
            for event in events:
                for evidence in event.evidence:
                    if evidence.path != target["path"] or evidence.digest != target["digest"]:
                        raise overview_error("OVERVIEW_EVIDENCE_INVALID", "章节依据超出当前目标")
                    check_evidence(run.workspace, evidence, chapter=target["chapter"])
            projection = ChapterProjection(
                path=target["path"],
                chapter=target["chapter"],
                digest=target["digest"],
                summary=raw["summary"],
                events=events,
            )
            validate_projection(run.workspace, projection)
            StoryIndexStore(overview.root).publish(projection, expected_digest=target["digest"])
        else:
            try:
                link_result = LinkResult.model_validate(raw)
            except ValidationError as exc:
                raise overview_error(
                    "OVERVIEW_RESULT_INVALID", "正文关联的字段或覆盖状态不符合要求"
                ) from exc
            known = [facet["ref"] for facet in overview.facets()]
            link_projection = overview.index.get(target["path"])
            linked_events = (
                {event.id: event for event in link_projection.events} if link_projection else {}
            )
            for link in link_result.links:
                if link.ref.model_dump() not in known or link.event_id not in linked_events:
                    raise overview_error(
                        "OVERVIEW_REF_UNAVAILABLE", "关联对象或事件不属于本轮有效来源"
                    )
                check_quote(text, link.evidence)
            for relation in link_result.relations:
                refs = (relation.left.model_dump(), relation.right.model_dump())
                if (
                    any(ref not in known for ref in refs)
                    or refs[0] == refs[1]
                    or relation.event_id not in linked_events
                ):
                    raise overview_error(
                        "OVERVIEW_REF_UNAVAILABLE", "正文关系端点或事件不属于本轮来源"
                    )
                characters = [f["ref"] for f in overview.facets() if f["kind"] == "character"]
                if any(ref not in characters for ref in refs):
                    raise overview_error("OVERVIEW_REF_UNAVAILABLE", "正文人物关系只能引用人物对象")
                check_quote(text, relation.evidence)
            overview.store.publish(target, link_result.model_dump(), run.turn_id)
        published = True
        run.record("overview_target_published", {"target_id": target["id"], "outcome": outcome})
        return output({"published": True, "outcome": outcome})

    evidence_schema: Any = {
        "type": "object",
        "properties": {
            "start": {"type": "integer"},
            "end": {"type": "integer"},
            "quote": {"type": "string"},
        },
        "required": ["start", "end", "quote"],
    }
    if target["kind"] == "module":
        schema: Any = {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": ["found", "empty", "insufficient"]},
                "facets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": ["character", "clue"]},
                            "name": {"type": "string"},
                            "summary": {"type": "string"},
                            "evidence": evidence_schema,
                        },
                        "required": ["kind", "name", "summary", "evidence"],
                    },
                },
                "relations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "left": {"type": "integer"},
                            "right": {"type": "integer"},
                            "label": {"type": "string"},
                            "evidence": evidence_schema,
                        },
                        "required": ["left", "right", "label", "evidence"],
                    },
                },
            },
            "required": ["outcome", "facets", "relations"],
        }
    elif target["kind"] == "links":
        schema = LinkResult.model_json_schema()
        definitions = schema.pop("$defs", {})

        def inline(value: Any) -> Any:
            if isinstance(value, dict):
                if "$ref" in value:
                    return inline(definitions[value["$ref"].rsplit("/", 1)[-1]])
                return {key: inline(item) for key, item in value.items()}
            if isinstance(value, list):
                return [inline(item) for item in value]
            return value

        schema = inline(schema)
    else:
        schema = {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": ["found", "insufficient"]},
                "summary": {"type": "string"},
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "mode": {
                                "type": "string",
                                "enum": [
                                    "narration",
                                    "testimony",
                                    "recollection",
                                    "hypothesis",
                                    "dream",
                                    "unknown",
                                ],
                            },
                            "evidence": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        **evidence_schema["properties"],
                                        "path": {"type": "string"},
                                        "digest": {"type": "string"},
                                    },
                                    "required": ["path", "digest", "start", "end", "quote"],
                                },
                            },
                            "story_time": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "normalized": {"type": "string"},
                                },
                            },
                        },
                        "required": ["text", "mode", "evidence"],
                    },
                },
            },
            "required": ["outcome", "summary", "events"],
        }
    return [
        AgentTool(
            name="read_overview_source",
            label="读取总览原文",
            description="只读本批冻结目标，每次最多6000码点，可用 next_start 继续。",
            parameters={
                "type": "object",
                "properties": {"start": {"type": "integer", "minimum": 0}},
            },
            execute_fn=read_source,
        ),
        AgentTool(
            name="publish_overview_result",
            label="发布总览结果",
            description="只发布当前目标的派生结果，必须有原文依据。",
            parameters={"type": "object", "properties": {"result": schema}, "required": ["result"]},
            execute_fn=publish,
            execution_mode="sequential",
        ),
    ]


async def execute_overview(
    service: TurnService, info: TurnInfo, run: RunContext, snapshot: ModelSnapshot
) -> str:
    from hastings.workspace.model_activity import RecordedProvider

    scope = cast(dict[str, Any], dict(info.material_scope or {}))
    done: set[str] = set()
    total = len(scope["targets"])
    truncated = False
    for frozen_target in scope["targets"]:
        if info.turn_id in service._stopped:
            return "stopped"
        overview = StoryOverview(
            run.workspace, service.store.root, run.library, scope["as_of_chapter"]
        )
        target = next((t for t in overview.targets if t["id"] == frozen_target["id"]), None)
        if target is None:
            raise overview_error("OVERVIEW_SOURCE_CHANGED_CONFLICT", "冻结目标当前不可用")
        verify_frozen(run, scope, frozen_target)
        # 模块或章节未完成时不能把无依赖结果记为空关联。
        if target["kind"] == "links" and not all(
            overview.complete(t)
            for t in overview.module_targets
            + [t for t in overview.chapter_targets if t["path"] == target["path"]]
        ):
            continue
        prompt: dict[str, Any] = {"target": target}
        if target["kind"] == "links":
            prompt["facets"] = overview.facets()
            prompt["events"] = overview.index[target["path"]].to_dict()["events"]
            # 关联依赖过大时保持未完成，不把无限增长的上下文交给模型。
            if len(json.dumps(prompt, ensure_ascii=False)) > 24000:
                continue
        harness = AgentHarness(
            AgentHarnessConfig(
                provider=RecordedProvider(
                    snapshot.provider, service.activity, run.record, stage="overview"
                ),
                model=snapshot.model,
                system=INSTRUCTIONS,
                tools=overview_tools(run, scope, target, overview),
                max_turns=min(service.max_turns, 16),
            )
        )
        service._harnesses[info.turn_id] = harness
        if info.turn_id in service._stopped:
            return "stopped"
        truncated = (
            await service._consume(
                harness, info, json.dumps(prompt, ensure_ascii=False), turns_used=[0]
            )
            or truncated
        )
        current = StoryOverview(
            run.workspace, service.store.root, run.library, scope["as_of_chapter"]
        )
        current_target = next((t for t in current.targets if t["id"] == target["id"]), None)
        if current_target and current.complete(current_target):
            done.add(target["id"])
        run.record("overview_progress", {"completed": len(done), "total": total})
    # 最后核对全部冻结来源；已发布贡献保留，由读取端标记失效。
    for source_target in scope["sources"]:
        verify_frozen(run, scope, source_target)
    run.record(
        "overview_finished",
        {
            "completed": len(done),
            "total": total,
            "remaining_target_ids": [t["id"] for t in scope["targets"] if t["id"] not in done],
        },
    )
    if info.turn_id in service._stopped:
        return "stopped"
    return "completed" if len(done) == total and not truncated else "partial"
