"""构思结构的模型工具：读取方案、提议变更并提交（1.0.1 P4）。

工具只做三件事，边界都很窄：

- ``read_story``：读当前方案的节点、线索、关系与约定。返回的是精确 ID 与修订，
  模型不需要（也不允许）靠标题猜目标。
- ``update_story``：提交一组语义变更。基础修订由应用填，``operation_id`` 与代次由
  应用填，模型提交的字段只描述「改什么」。提交前先过代次资格检查：暂停之后旧代次
  即使还在跑工具，也拿不到写权限。
- 不做的事：不合并方案、不采用备选、不改正文。那些需要作者明确授权与差异计算，
  属于后续阶段的独立操作。

工具失败一律抛带错误码的异常，由执行层转成一次工具失败；模型能看到原因并重试，
但拿不到「已成功」的假回执。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from hastings.prompts import tools as prompt_tools
from hastings.prompts.tools import (
    READ_STORY_DESCRIPTION,
    READ_STORY_GUIDELINES,
    UPDATE_STORY_DESCRIPTION,
    UPDATE_STORY_GUIDELINES,
    variant_tool_description,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_authorization import authorize_change, validate_model_change
from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_models import (
    StoryChangeSet,
    StoryVariant,
)
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore, new_id
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult

__all__ = ["StoryToolScope", "build_story_tools"]

#: 一次读取返回的字符上限；超过时只给概览与继续读取的入口。
_READ_LIMIT = 8_000


@dataclass(frozen=True, slots=True)
class StoryToolScope:
    """结构工具能用到的应用侧事实：冻结方案、写权限与代次资格。"""

    state_root: Path
    frozen: StoryContextSnapshot
    writable: bool = False
    #: 代次资格检查：暂停后由服务层抛错，工具不再写入。
    guard: Callable[[], None] | None = None
    #: 提交决定与暂停共用操作锁。
    decision_guard: Callable[[], AbstractContextManager[None]] | None = None
    #: 事件出口：提交结果写进本轮记录。
    record: Callable[[str, Mapping[str, Any]], None] | None = None
    #: 本轮作者原话：只记录授权来源，不参与判断。
    author_text: str = ""
    #: 统一操作的标识与代次：应用填进变更集，模型看不到也不需要知道。
    operation_id: str = ""
    generation: int = 0
    workspace: Workspace | None = None
    store: StoryStore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "store", StoryStore(self.state_root, self.workspace))


def _json(payload: Mapping[str, Any]) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text=json.dumps(payload, ensure_ascii=False, indent=2))],
        details=dict(payload),
    )


def _variant(scope: StoryToolScope) -> StoryVariant:
    store = scope.store
    snapshot = store.read()
    if snapshot.document is None:
        # 空白构思的第一轮：冻结的是 empty 主方案，第一份结构由本轮的 update_story 建立。
        if scope.frozen.revision == EMPTY_REVISION and scope.frozen.variant_id == MAIN_VARIANT_ID:
            return StoryVariant(id=MAIN_VARIANT_ID, name="主方案", revision=EMPTY_REVISION)
        raise WorkspaceError(
            code="STORY_VARIANT_NOT_FOUND",
            where="story.tool",
            why="这本作品还没有构思结构",
            fix="先在构思里建立节点与线索，或把这一轮改成普通讨论",
            details={"variant_id": scope.frozen.variant_id},
        )
    variant = snapshot.variant(scope.frozen.variant_id)
    if variant is None:
        raise WorkspaceError(
            code="STORY_VARIANT_NOT_FOUND",
            where="story.tool",
            why=f"方案已经不存在：{scope.frozen.variant_id}",
            fix="重新打开构思后再改；这一轮的结构目标已经失效",
            details={"variant_id": scope.frozen.variant_id},
        )
    return variant


def _node_summary(variant: StoryVariant, node_id: str) -> dict[str, Any] | None:
    node = variant.nodes.get(node_id)
    if node is None:
        return None
    return {
        "id": node.id,
        "title": node.title,
        "action": node.action,
        "threads": [
            variant.threads[item].title for item in node.thread_ids if item in variant.threads
        ],
        "clues": [use.clue_id for use in node.clue_uses],
        "time_note": node.time_note,
    }


def build_story_tools(scope: StoryToolScope) -> list[AgentTool]:
    """按当前冻结方案给出读取与变更工具；只读用途不给提交工具。"""

    async def read_story(
        tool_call_id: str,
        arguments: Mapping[str, Any],
        signal: Any = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        variant = _variant(scope)
        wanted = arguments.get("node_ids") or []
        if not isinstance(wanted, list):
            raise WorkspaceError(
                code="STORY_SCOPE_INVALID",
                where="story.read_story",
                why="node_ids 不是数组",
                fix="传一个节点标识数组，或省略它读取整个方案",
            )
        node_ids = [str(item) for item in wanted] if wanted else list(variant.nodes)
        unknown = [node_id for node_id in node_ids if node_id not in variant.nodes]
        if unknown:
            raise WorkspaceError(
                code="STORY_CONTEXT_INCOMPLETE",
                where="story.read_story",
                why=f"没有这个节点：{unknown[0]}",
                fix="先不带参数读一次当前方案，确认节点标识",
                details={"node_id": unknown[0]},
            )
        nodes = [item for item in (_node_summary(variant, node_id) for node_id in node_ids) if item]
        document = scope.store.read().document
        payload: dict[str, Any] = {
            "variant_id": variant.id,
            "revision": variant.revision,
            "nodes": nodes,
            "variants": [
                {"id": item.id, "name": item.name, "revision": item.revision}
                for item in (document.variants.values() if document else ())
            ],
            "clues": [
                {
                    "id": clue.id,
                    "title": clue.title,
                    "author_truth": clue.author_truth,
                    "intention": clue.intention,
                    "resolution": {
                        "intent": clue.resolution.intent,
                        "node_ids": list(clue.resolution.node_ids),
                    },
                }
                for clue in variant.clues.values()
            ],
            "constraints": [
                {
                    "id": constraint.id,
                    "text": constraint.text,
                    "strength": constraint.strength,
                    "scope": {
                        "kind": constraint.scope.kind,
                        "target_id": constraint.scope.target_id,
                    },
                }
                for constraint in variant.constraints.values()
            ],
            "edges": [
                {"id": edge.id, "from_id": edge.from_id, "to_id": edge.to_id, "kind": edge.kind}
                for edge in variant.edges.values()
            ],
            "note": (
                "这些是当前方案的全部结构；改结构用 update_story，正文用 write/edit。"
                "不要凭标题猜 ID，也不要把 author_truth 写进角色的台词。"
            ),
        }
        size = len(json.dumps(payload, ensure_ascii=False))
        if size > _READ_LIMIT:
            payload["nodes"] = nodes[:20]
            payload["truncated"] = True
            payload["note"] = "节点较多，这里只列前 20 个；用 node_ids 指定要看的节点再看其余部分。"
        return _json(payload)

    async def update_story(
        tool_call_id: str,
        arguments: Mapping[str, Any],
        signal: Any = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        if not scope.writable:
            raise WorkspaceError(
                code="STORY_WRITE_FORBIDDEN",
                where="story.update",
                why="这一轮是讨论或核对，不能修改构思结构",
                fix="作者明确要求修改时，从构思里选定节点再发起一轮",
            )
        if scope.guard is not None:
            # 先查资格再读结构：暂停之后旧代次不能靠一次工具调用拿到写入。
            scope.guard()
        raw_operations = arguments.get("operations")
        if not isinstance(raw_operations, list) or not raw_operations:
            raise WorkspaceError(
                code="STORY_CHANGE_INVALID",
                where="story.update",
                why="没有给出要做的结构变更",
                fix="用 operations 描述至少一项变更，例如新增节点或连接关系",
            )
        variant = _variant(scope)
        try:
            change = StoryChangeSet(
                id=new_id("agent"),
                base_revision=variant.revision,
                operations=raw_operations,
                requested_scope=(scope.author_text or "")[:500],
                operation_id=scope.operation_id,
                generation=scope.generation,
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first.get("loc", ()))
            raise WorkspaceError(
                code="STORY_CHANGE_INVALID",
                where=f"story.update:{where}",
                why=str(first.get("msg") or "变更字段不合法"),
                fix="按工具参数的字段名与类型重新提交；先用 read_story 确认现有 ID",
                details={"field": where},
            ) from exc

        # 先按作者本轮的对象范围收口，再检查真相、约定与裁决这些不能由模型自行扩大的部分。
        authorize_change(variant, scope.frozen, scope.author_text, change)

        validate_model_change(variant, change, scope.author_text)

        guard = scope.guard
        if guard is not None:
            guard()

        def require_eligible(operation_id: str, generation: int) -> None:
            if guard is not None:
                guard()

        receipt = scope.store.commit_change(
            variant_id=variant.id,
            change=change,
            request_key=f"agent:{change.id}:{variant.revision}",
            # 提交决定落在锁内再查一次：读结构到写盘之间可能刚好暂停。
            require_eligible=require_eligible if guard is not None else None,
            decision_guard=scope.decision_guard,
        )
        after = scope.store.read()
        record = after.document.changes.get(receipt.change_id) if after.document else None
        result = {
            "ok": True,
            "variant_id": variant.id,
            "revision": receipt.revision,
            "change_id": receipt.change_id,
            "changed": sorted(record.touched) if record is not None else [],
            "note": "结构已经保存；正文改动仍要另行写入正文文件。",
        }
        if scope.record is not None:
            scope.record(
                "story_changes_committed",
                {
                    "variant_id": variant.id,
                    "revision": receipt.revision,
                    "change_id": receipt.change_id,
                    "operations": len(change.operations),
                },
            )
        return _json(result)

    async def fork_story(
        tool_call_id: str, arguments: Mapping[str, Any], signal: Any = None, on_update: Any = None
    ) -> AgentToolResult:
        del signal, on_update
        if not any(word in scope.author_text for word in ("备选", "另一方案", "另一个方案")):
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.fork",
                why="作者没有要求创建备选方案",
                fix="先讨论当前方案，待作者明确要求备选后创建",
            )
        variant = _variant(scope)
        with scope.decision_guard() if scope.decision_guard else nullcontext():
            if scope.guard:
                scope.guard()
            receipt = scope.store.create_variant(
                name=str(arguments.get("name") or "备选"),
                source_variant_id=variant.id,
                source_revision=variant.revision,
                request_key=f"{scope.operation_id}:fork:{tool_call_id}",
            )
        return _json(
            {
                "variant_id": receipt.result["variant_id"],
                "revision": receipt.revision,
                "copied_counts": {
                    "nodes": len(variant.nodes),
                    "clues": len(variant.clues),
                    "constraints": len(variant.constraints),
                },
                "note": "备选已经从当前方案完整复制，并非空白；主方案没有变化。"
                "修改备选需在对应方案语境发起后续任务。",
            }
        )

    async def adopt_story(
        tool_call_id: str, arguments: Mapping[str, Any], signal: Any = None, on_update: Any = None
    ) -> AgentToolResult:
        del signal, on_update
        if "采用" not in scope.author_text or any(
            word in scope.author_text for word in ("不要采用", "只讨论", "不保存")
        ):
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.adopt",
                why="作者没有明确要求采用方案",
                fix="先说明差异，等待作者决定采用范围",
            )
        target = _variant(scope)
        source = scope.store.require_variant(str(arguments.get("source_variant_id") or ""))
        selected = tuple(str(item) for item in arguments.get("node_ids", []))
        if scope.frozen.node_ids and not set(selected) <= set(scope.frozen.node_ids):
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.adopt",
                why="采用范围超过本轮选中的节点",
                fix="只采用所选节点，其他范围由作者另行指定",
            )
        with scope.decision_guard() if scope.decision_guard else nullcontext():
            if scope.guard:
                scope.guard()
            receipt = scope.store.adopt(
                source_variant_id=source.id,
                source_revision=source.revision,
                target_variant_id=target.id,
                target_revision=target.revision,
                node_ids=selected,
                replace=True,
                request_key=f"{scope.operation_id}:adopt:{tool_call_id}",
                mode="exact",
            )
        return _json(
            {
                "revision": receipt.revision,
                "change_id": receipt.change_id,
                "plan": receipt.result.get("adoption_plan"),
                "note": "结构采用已保存；正文尚未调整。",
            }
        )

    tools = [
        AgentTool(
            name="read_story",
            label="读取构思",
            description=READ_STORY_DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            prompt_tools.READ_STORY_NODE_IDS_PARAM_DESCRIPTION
                        ),
                    }
                },
                "additionalProperties": False,
            },
            execute_fn=read_story,
            prompt_guidelines=READ_STORY_GUIDELINES,
            execution_mode="sequential",
        )
    ]
    if (
        scope.writable
        and not scope.frozen.structure_readonly
        and not any(
            word in scope.author_text
            for word in ("只讨论", "不保存", "只分析", "不要修改", "不执行修改")
        )
    ):
        for name, label, execute, properties, required in (
            (
                "create_story_variant",
                "创建备选",
                fork_story,
                {"name": {"type": "string"}},
                ["name"],
            ),
            (
                "adopt_story_variant",
                "采用备选结构",
                adopt_story,
                {
                    "source_variant_id": {"type": "string"},
                    "node_ids": {"type": "array", "items": {"type": "string"}},
                },
                ["source_variant_id"],
            ),
        ):
            tools.append(
                AgentTool(
                    name=name,
                    label=label,
                    description=variant_tool_description(label),
                    parameters={
                        "type": "object",
                        "properties": cast(Any, properties),
                        "required": cast(Any, required),
                        "additionalProperties": False,
                    },
                    execute_fn=execute,
                    execution_mode="sequential",
                )
            )
        tools.append(
            AgentTool(
                name="update_story",
                label="修改构思",
                description=UPDATE_STORY_DESCRIPTION,
                parameters={
                    "type": "object",
                    "properties": {
                        "operations": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": (
                                prompt_tools.UPDATE_STORY_OPERATIONS_PARAM_DESCRIPTION
                            ),
                        },
                        "note": {
                            "type": "string",
                            "description": prompt_tools.UPDATE_STORY_NOTE_PARAM_DESCRIPTION,
                        },
                    },
                    "required": ["operations"],
                    "additionalProperties": False,
                },
                execute_fn=update_story,
                prompt_guidelines=UPDATE_STORY_GUIDELINES,
                execution_mode="sequential",
            )
        )
    schema = StoryChangeSet.model_json_schema()
    for tool in tools:
        if tool.name == "update_story":
            parameters = cast(dict[str, Any], tool.parameters)
            parameters["$defs"] = schema["$defs"]
            parameters["properties"]["operations"] = schema["properties"]["operations"]
    return tools
