"""构思结构的模型工具：读取方案、提议变更并提交（1.0.1 P4）。

工具只做两件事，边界都很窄：

- ``read_story``：读当前方案的节点、线索、关系与约定。返回的是精确 ID 与修订，
  模型不需要（也不允许）靠标题猜目标。
- ``update_story``：提交一组语义变更。基础修订由应用填，``operation_id`` 与代次由
  应用填，模型提交的字段只描述「改什么」。提交前先过代次资格检查：暂停之后旧代次
  即使还在跑工具，也拿不到写权限。

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

from hastings.library.modules import ModuleLibrary
from hastings.prompts import tools as prompt_tools
from hastings.prompts.tools import (
    READ_STORY_DESCRIPTION,
    READ_STORY_GUIDELINES,
    UPDATE_STORY_DESCRIPTION,
    UPDATE_STORY_GUIDELINES,
)
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import ModuleRef
from hastings.workspace.story_authorization import (
    authorize_change,
    blocks_writes,
    build_authority,
    validate_model_change,
)
from hastings.workspace.story_clue_catalog import StoryClueCatalog
from hastings.workspace.story_clue_commit import prepare_clue_joint
from hastings.workspace.story_constraint_sources import ConstraintSources
from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_drafts import (
    StoryDraftStore,
    candidate_data,
    check_candidate,
    validation_issues,
)
from hastings.workspace.story_models import (
    StoryChangeSet,
    StoryVariant,
)
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore, new_id
from hastings.workspace.story_structure import structure_diagnostics
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
    #: 任务状态检查：提问后或模块应用后的收尾阶段，结构工具也不再写入。
    write_guard: Callable[[str], None] | None = None
    #: 提交决定与暂停共用操作锁。
    decision_guard: Callable[[], AbstractContextManager[None]] | None = None
    #: 事件出口：提交结果写进本轮记录。
    record: Callable[[str, Mapping[str, Any]], None] | None = None
    #: 本轮作者原话：对象授权与采用范围的唯一来源，模型不能自报权限。
    author_text: str = ""
    #: 作者确认的上一轮候选原文：只扩展可修改对象，不授予真相或约定修改。
    candidate_text: str = ""
    #: 统一操作的标识与代次：应用填进变更集，模型看不到也不需要知道。
    operation_id: str = ""
    generation: int = 0
    workspace: Workspace | None = None
    library: ModuleLibrary | None = None
    module_refs: tuple[ModuleRef, ...] = ()
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


def _catalog(scope: StoryToolScope, variant: StoryVariant) -> StoryClueCatalog:
    return StoryClueCatalog.from_root(variant, scope.state_root)


def _node_summary(variant: StoryVariant, node_id: str) -> dict[str, Any] | None:
    node = variant.nodes.get(node_id)
    if node is None:
        return None
    return node.model_dump(mode="json")


_KIND_LABELS = {
    "stage": "阶段",
    "node": "节点",
    "clue": "线索",
    "clue_plan": "线索计划",
    "thread": "故事线",
    "constraint": "约定",
    "edge": "关系",
    "binding": "正文关联",
    "assessment": "核对记录",
    "time": "时间",
}


def _object_label(
    variant: StoryVariant,
    kind: str,
    object_id: str,
    catalog: StoryClueCatalog | None = None,
) -> str:
    """收据里的对象标签：作者看到标题，不看到内部标识。"""
    if kind == "stage" and object_id in variant.stages:
        return f"阶段「{variant.stages[object_id].title}」"
    if kind == "node" and object_id in variant.nodes:
        return f"节点「{variant.nodes[object_id].title}」"
    if kind in ("clue", "clue_plan"):
        if catalog is not None:
            return f"线索「{catalog.title(object_id)}」"
        return f"线索「{object_id}」"
    if kind == "thread" and object_id in variant.threads:
        return f"故事线「{variant.threads[object_id].title}」"
    if kind == "constraint" and object_id in variant.constraints:
        text = variant.constraints[object_id].text.strip()
        return f"约定「{text[:20]}」" if text else "约定"
    if kind == "edge" and object_id in variant.edges:
        edge = variant.edges[object_id]
        return f"关系 {edge.from_id} → {edge.to_id}"
    if kind == "time" and ">" in object_id:
        before, _, after = object_id.partition(">")
        return f"时间 {before} → {after}"
    return f"{_KIND_LABELS.get(kind, kind)} {object_id}"


def _changed_labels(
    variant: StoryVariant, keys: list[str], catalog: StoryClueCatalog | None = None
) -> list[str]:
    labels: list[str] = []
    for key in keys:
        kind, _, object_id = key.partition(":")
        labels.append(_object_label(variant, kind, object_id, catalog))
    return labels[:20]


def _require_write_state(scope: StoryToolScope, where: str) -> None:
    if scope.write_guard is not None:
        scope.write_guard(where)
    if scope.guard is not None:
        # 先查资格再读结构：暂停之后旧代次不能靠一次工具调用拿到写入。
        scope.guard()


def build_story_tools(scope: StoryToolScope) -> list[AgentTool]:
    """按当前冻结方案给出读取与变更工具；只读用途不给提交工具。"""

    #: 本轮实际读过的对象与修订；no_change 只能用这里出现过的对象作依据。
    read_state: dict[str, Any] = {"revision": "", "objects": set()}
    drafts = StoryDraftStore(scope.state_root, scope.operation_id, scope.frozen.variant_id)

    async def read_story(
        tool_call_id: str,
        arguments: Mapping[str, Any],
        signal: Any = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        if arguments.get("source") == "draft":
            draft = drafts.read()
            if draft is None:
                return _json({"draft": None, "note": "本任务尚无草稿"})
            variant = _variant(scope)
            start = arguments.get("operation_start", 0)
            if type(start) is not int or start < 0:
                raise WorkspaceError(
                    code="STORY_SCOPE_INVALID",
                    where="story.read_story",
                    why="操作起点不是非负整数",
                    fix="使用 operation_start 指定草稿操作索引",
                )
            target_id = arguments.get("target_id")
            if target_id:
                matches = [
                    i
                    for i, row in enumerate(draft["operations"])
                    if any(isinstance(v, dict) and v.get("id") == target_id for v in row.values())
                    or row.get("clue_id") == target_id
                    or row.get("temp_id") == target_id
                ]
                return _json(
                    {
                        "draft_revision": draft["digest"],
                        "operations": [
                            {"index": i, "operation": draft["operations"][i]} for i in matches
                        ],
                        "coverage": {"target_id": target_id, "complete": True},
                    }
                )
            operations = draft["operations"][start : start + 12]
            draft_payload: dict[str, Any] = {
                "draft_revision": draft["digest"],
                "base_revision": draft["base_revision"],
                "author_confirmed_constraint_count": len(draft.get("confirmed_constraints", [])),
                "operations": [
                    {"index": start + i, "operation": row} for i, row in enumerate(operations)
                ],
                "operation_start": start,
                "operation_total": len(draft["operations"]),
                "next_operation_start": start + len(operations)
                if start + len(operations) < len(draft["operations"])
                else None,
                "coverage": {
                    "operations_complete": start == 0
                    and len(operations) == len(draft["operations"])
                },
                "note": "这是任务草稿，尚未整体提交；按操作 index 用 replace_operations 局部替换。",
            }
            if draft["committed"]:
                return _json({"committed": True, "receipt": draft["receipt"]})
            if draft["base_revision"] != variant.revision:
                return _json(
                    {**draft_payload, "conflict": True, "note": "正式结构已变化，草稿尚未提交"}
                )
            change = StoryChangeSet(
                id=draft["change_id"],
                base_revision=variant.revision,
                operations=draft["operations"],
            )
            try:
                data = candidate_data(variant, change)
            except WorkspaceError as exc:
                return _json(
                    {
                        **draft_payload,
                        "issues": [{"code": exc.code, "message": exc.why, **exc.details}],
                    }
                )
            wanted = arguments.get("node_ids") or []
            shown = (
                {key: value for key, value in data["nodes"].items() if key in wanted}
                if wanted
                else {}
            )
            draft_payload.update(
                {
                    "candidate": {**data, "nodes": shown},
                    "node_index": [
                        {
                            "id": n["id"],
                            "title": n["title"],
                            "stage_id": n["stage_id"],
                            "primary_thread_id": n["primary_thread_id"],
                        }
                        for n in data["nodes"].values()
                    ],
                    "issues": check_candidate(data),
                }
            )
            draft_payload["coverage"]["node_ids"] = list(shown)
            draft_payload["coverage"]["nodes_complete"] = len(shown) == len(data["nodes"])
            return _json(draft_payload)

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
        if read_state["revision"] != variant.revision:
            read_state["revision"] = variant.revision
            read_state["objects"] = set()
        read_objects = cast(set[tuple[str, str]], read_state["objects"])
        # 返回内容里始终带有全部线索与约定；它们也算实际读到。
        catalog = _catalog(scope, variant)
        read_objects.update(("clue", fact.clue_id) for fact in catalog)
        read_objects.update(("constraint", constraint_id) for constraint_id in variant.constraints)
        nodes = [item for item in (_node_summary(variant, node_id) for node_id in node_ids) if item]
        document = scope.store.read().document
        payload: dict[str, Any] = {
            "variant_id": variant.id,
            "revision": variant.revision,
            "stages": [
                {
                    "id": stage.id,
                    "title": stage.title,
                    "order": stage.order,
                    "summary": stage.summary,
                }
                for stage in sorted(variant.stages.values(), key=lambda item: (item.order, item.id))
            ],
            "threads": [
                {
                    "id": thread.id,
                    "title": thread.title,
                    "order": thread.order,
                    "goal": thread.goal,
                    "obstacle": thread.obstacle,
                    "intended_resolution": thread.intended_resolution,
                }
                for thread in sorted(
                    variant.threads.values(), key=lambda item: (item.order, item.id)
                )
            ],
            "nodes": nodes,
            "event_times": [item.model_dump(mode="json") for item in variant.event_times],
            "coverage": {
                "node_ids": node_ids,
                "total": len(variant.nodes),
                "complete": len(node_ids) == len(variant.nodes),
            },
            "variants": [
                {"id": item.id, "name": item.name, "revision": item.revision}
                for item in (document.variants.values() if document else ())
            ],
            "clues": [
                {
                    "id": fact.clue_id,
                    "title": fact.title,
                    "has_identity": fact.has_identity,
                    "author_truth": fact.plan.author_truth if fact.plan else None,
                    "intention": fact.plan.intention if fact.plan else "",
                    "resolution_intent": (
                        fact.plan.resolution_intent if fact.plan else "undecided"
                    ),
                }
                for fact in catalog
            ],
            "confirmed_module_constraints": list(
                ConstraintSources(scope.state_root)
                .verified(scope.library, scope.module_refs)
                .values()
            ),
            "constraints": [
                constraint.model_dump(mode="json") for constraint in variant.constraints.values()
            ],
            "edges": [edge.model_dump(mode="json") for edge in variant.edges.values()],
            "note": (
                "这些是当前方案的全部结构；改结构用 update_story，正文用 write/edit。"
                "不要凭标题猜 ID，也不要把 author_truth 写进角色的台词。"
            ),
        }
        if not wanted and len(json.dumps(payload, ensure_ascii=False)) > _READ_LIMIT:
            saved_nodes: list[dict[str, Any]] = []
            used = 0
            for node in nodes:
                size = len(json.dumps(node, ensure_ascii=False))
                if saved_nodes and used + size > _READ_LIMIT:
                    break
                saved_nodes.append(node)
                used += size
            payload["nodes"] = saved_nodes
            payload["truncated"] = len(saved_nodes) < len(nodes)
            payload["coverage"] = {
                "node_ids": [n["id"] for n in saved_nodes],
                "total": len(variant.nodes),
                "complete": len(saved_nodes) == len(variant.nodes),
            }
            payload["remaining_node_ids"] = [n["id"] for n in nodes[len(saved_nodes) :]]
            payload["note"] = (
                "节点按完整对象分批返回；用 node_ids 精确读取剩余节点。"
                "不要把 author_truth 写进角色台词。"
            )
        read_objects.update(("node", n["id"]) for n in payload["nodes"])
        return _json(payload)

    def record_no_change(raw: Any) -> AgentToolResult:
        """记录一次经核验的「无需改动」：目标存在、本轮读过、在当前授权范围内。"""
        if not isinstance(raw, Mapping):
            raise WorkspaceError(
                code="STORY_CHANGE_INVALID",
                where="story.update",
                why="no_change 不是一个对象",
                fix="给出 objects 与 reason：列出本轮读过的目标对象并说明为何无需修改",
            )
        rows = raw.get("objects")
        reason = str(raw.get("reason") or "").strip()
        if not isinstance(rows, list) or not rows:
            raise WorkspaceError(
                code="STORY_CHANGE_INVALID",
                where="story.update",
                why="no_change 没有列出目标对象",
                fix="列出本轮实际读过的节点、线索或约定；没有读过就先 read_story",
            )
        if not reason:
            raise WorkspaceError(
                code="STORY_CHANGE_INVALID",
                where="story.update",
                why="no_change 没有说明无需改动的理由",
                fix="说明作者要求已经如何得到满足，不能只写「已存在」",
            )
        variant = _variant(scope)
        if read_state["revision"] != variant.revision:
            raise WorkspaceError(
                code="STORY_CONTEXT_INCOMPLETE",
                where="story.update",
                why="no_change 依据的修订不是本轮实际读到的修订",
                fix="先用 read_story 读取当前方案，再说明哪些对象已经满足要求",
            )
        read_objects = cast(set[tuple[str, str]], read_state["objects"])
        catalog = _catalog(scope, variant)
        authority = build_authority(
            variant,
            scope.frozen,
            scope.author_text,
            catalog=catalog,
            candidate_text=scope.candidate_text,
        )
        objects: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise WorkspaceError(
                    code="STORY_CHANGE_INVALID",
                    where="story.update",
                    why="no_change.objects 里有一项不是对象",
                    fix="每项写成 {kind, id}，kind 为 node、clue 或 constraint",
                )
            kind = str(row.get("kind") or "")
            object_id = str(row.get("id") or "")
            if kind not in ("node", "clue", "constraint") or not object_id:
                raise WorkspaceError(
                    code="STORY_CHANGE_INVALID",
                    where="story.update",
                    why=f"no_change 的对象不合法：{kind or '缺少 kind'} {object_id}".strip(),
                    fix="只列本方案里已有的节点、线索或约定",
                )
            existing = (
                (kind == "node" and object_id in variant.nodes)
                or (kind == "clue" and catalog.get(object_id) is not None)
                or (kind == "constraint" and object_id in variant.constraints)
            )
            if not existing:
                raise WorkspaceError(
                    code="STORY_CHANGE_INVALID",
                    where="story.update",
                    why=f"no_change 指向的对象不存在：{kind} {object_id}",
                    fix="先用 read_story 确认现有对象；不存在的对象不能作为无需改动的依据",
                )
            if (kind, object_id) not in read_objects:
                raise WorkspaceError(
                    code="STORY_CONTEXT_INCOMPLETE",
                    where="story.update",
                    why=f"no_change 指向的对象本轮还没有读到：{kind} {object_id}",
                    fix="先用 read_story 读取这个对象，再说明它已经满足要求",
                )
            if not authority.covers(kind, object_id):
                raise WorkspaceError(
                    code="STORY_AUTHORIZATION_CONFLICT",
                    where="story.update",
                    why=f"作者本轮没有点到{_KIND_LABELS[kind]}「{object_id}」，不能作为无需改动的依据",
                    fix="只列作者明确点到或界面选中的对象",
                )
            if (kind, object_id) in seen:
                continue
            seen.add((kind, object_id))
            objects.append(
                {
                    "kind": kind,
                    "id": object_id,
                    "label": _object_label(variant, kind, object_id, catalog),
                }
            )
        payload = {
            "variant_id": variant.id,
            "revision": variant.revision,
            "base_revision": variant.revision,
            "objects": objects,
            "reason": reason,
            "operation_id": scope.operation_id,
            "generation": scope.generation,
        }
        if scope.record is not None:
            scope.record("story_change_noop", payload)
        return _json(
            {
                "ok": True,
                "no_change": True,
                "variant_id": variant.id,
                "revision": variant.revision,
                "objects": objects,
                "note": "已记录这次无需改动及其依据；没有提交新的结构变更。",
            }
        )

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
        _require_write_state(scope, "story.update")
        if "_raw_arguments" in arguments:
            raw = str(arguments["_raw_arguments"])
            try:
                json.loads(raw)
            except json.JSONDecodeError as exc:
                raise WorkspaceError(
                    code="STORY_ARGUMENTS_INVALID",
                    where=f"构思提交参数第 {exc.colno} 列",
                    why=f"JSON 语法无效（{exc.msg}）",
                    fix="核对该位置的逗号、引号与括号后重新提交当前批次；保留草稿，减少单批操作",
                    details={
                        "offset": exc.pos,
                        "line": exc.lineno,
                        "column": exc.colno,
                        "parser_message": exc.msg,
                        "argument_chars": len(raw),
                        "context": raw[max(0, exc.pos - 60) : exc.pos + 60],
                    },
                ) from exc
            raise WorkspaceError(
                code="STORY_ARGUMENTS_INVALID",
                where="story.update",
                why="工具参数没有解析成对象",
                fix="按 schema 提交一个 JSON 对象",
            )
        mode = arguments.get("mode", "commit")
        if mode not in ("prepare", "commit"):
            raise WorkspaceError(
                code="STORY_CHANGE_INVALID",
                where="story.update.mode",
                why="不支持的提交模式",
                fix="使用 prepare 或 commit",
            )
        draft = drafts.read()
        if mode == "prepare":
            if not scope.operation_id:
                raise WorkspaceError(
                    code="STORY_DRAFT_INVALID",
                    where="story.draft",
                    why="本轮缺少任务标识",
                    fix="从构思界面重新发起任务",
                )
            raw_rows = arguments.get("operations", [])
            replacements_raw = arguments.get("replace_operations", [])
            if not isinstance(raw_rows, list) or not isinstance(replacements_raw, list):
                raise WorkspaceError(
                    code="STORY_CHANGE_INVALID",
                    where="story.draft",
                    why="草稿操作不是数组",
                    fix="按 operations 与 replace_operations 数组提交",
                )
            replacements: dict[int, Any] = {}
            for row in replacements_raw:
                if (
                    not isinstance(row, dict)
                    or type(row.get("index")) is not int
                    or not isinstance(row.get("operation"), dict)
                    or row["index"] in replacements
                ):
                    raise WorkspaceError(
                        code="STORY_CHANGE_INVALID",
                        where="story.draft.replace_operations",
                        why="替换项缺少唯一整数 index 或 operation",
                        fix="每项指定一个已有操作索引及完整替换操作",
                    )
                replacements[row["index"]] = row["operation"]
            for patch in arguments.get("node_patches", []):
                node_id = patch.get("node_id")
                fields = patch.get("fields", {})
                if not isinstance(fields, dict) or "id" in fields:
                    raise WorkspaceError(
                        code="STORY_CHANGE_INVALID",
                        where="story.draft",
                        why="节点补丁不能修改身份",
                        fix="只提交待修改字段",
                    )
                matches = [
                    (i, row)
                    for i, row in enumerate((draft or {}).get("operations", []))
                    if isinstance(row.get("node"), dict) and row["node"]["id"] == node_id
                ]
                if len(matches) != 1:
                    raise WorkspaceError(
                        code="STORY_DRAFT_TARGET_CONFLICT",
                        where="story.draft",
                        why="节点补丁没有唯一草稿目标",
                        fix="用 target_id 读取目标后核对",
                    )
                index, original = matches[0]
                if index in replacements:
                    raise WorkspaceError(
                        code="STORY_CHANGE_INVALID",
                        where="story.draft",
                        why="同一对象重复修正",
                        fix="每批只给同一节点一个补丁",
                    )
                replacements[index] = {**original, "node": {**original["node"], **fields}}
            if not raw_rows and not replacements:
                raise WorkspaceError(
                    code="STORY_CHANGE_INVALID",
                    where="story.draft",
                    why="没有草稿变更",
                    fix="提供新增 operations 或局部 replace_operations",
                )
            with scope.decision_guard() if scope.decision_guard else nullcontext():
                _require_write_state(scope, "story.draft")
                try:
                    draft = drafts.prepare(
                        variant=_variant(scope),
                        operation_id=scope.operation_id,
                        generation=scope.generation,
                        operations=raw_rows,
                        replacements=replacements,
                        expected=str(arguments.get("draft_revision") or ""),
                        module_refs=scope.module_refs,
                    )
                except ValidationError as exc:
                    issues = validation_issues(exc, raw_rows)
                    raise WorkspaceError(
                        code="STORY_CHANGE_INVALID",
                        where="story.draft",
                        why="草稿字段校验未通过",
                        fix="按问题列表局部修正，已有草稿保留",
                        details={"issues": issues},
                    ) from exc
            if scope.record:
                scope.record(
                    "story_draft_prepared",
                    {"draft_revision": draft["digest"], "operations": len(draft["operations"])},
                )
            change = StoryChangeSet(
                id=draft["change_id"],
                base_revision=draft["base_revision"],
                operations=draft["operations"],
            )
            data = candidate_data(_variant(scope), change)
            issues = check_candidate(data)
            return _json(
                {
                    "prepared": True,
                    "committed": False,
                    "draft_revision": draft["digest"],
                    "operations": len(change.operations),
                    "issues": [],
                    "pending_dependencies": issues,
                    "next_step": "先补齐事件节点"
                    if not data.get("nodes")
                    else "补齐真实推进关系"
                    if len(data["nodes"]) > 1 and not data.get("edges")
                    else "集中补齐待办后检查并提交"
                    if issues
                    else "核对并整体提交",
                    "note": (
                        "本批已准备；pending_dependencies 为尚待补齐的安排，不是本批字段错误。"
                        "正式提交会执行完整校验。"
                    ),
                }
            )
        use_draft = arguments.get("draft_revision") is not None
        if use_draft:
            if draft is None or arguments["draft_revision"] != draft["digest"]:
                raise WorkspaceError(
                    code="STORY_DRAFT_CONFLICT",
                    where="story.draft",
                    why="草稿版本不匹配",
                    fix="先 read_story(source=draft) 读取当前草稿",
                )
            if (
                arguments.get("operations")
                or arguments.get("replace_operations")
                or arguments.get("node_patches")
                or arguments.get("no_change")
            ):
                raise WorkspaceError(
                    code="STORY_CHANGE_INVALID",
                    where="story.draft",
                    why="草稿提交混入了其他操作",
                    fix="先 prepare 修改草稿，提交时只传 draft_revision 与 mode",
                )
            if draft.get("committed"):
                return _json(draft["receipt"])
            if draft.get("module_refs", []) != [m.view() for m in scope.module_refs]:
                raise WorkspaceError(
                    code="STORY_SOURCE_CONFLICT",
                    where="story.draft",
                    why="草稿的冻结模块版本与本轮不一致",
                    fix="保留草稿，核对来源后重新发起任务",
                )
            snapshot = scope.store.read()
            previous = (
                snapshot.document.changes.get(draft["change_id"]) if snapshot.document else None
            )
            if previous and previous.request_key == f"draft:{draft['change_id']}:{draft['digest']}":
                result = {
                    "ok": True,
                    "variant_id": draft["variant_id"],
                    "revision": previous.revision,
                    "change_id": previous.change_id,
                    "changed": sorted(previous.touched),
                    "note": "草稿已经提交",
                }
                if scope.record is not None:
                    scope.record(
                        "story_changes_committed",
                        {
                            "variant_id": draft["variant_id"],
                            "base_revision": previous.base_revision,
                            "revision": previous.revision,
                            "change_id": previous.change_id,
                            "operations": len(previous.operations),
                            "operation_types": [op.op for op in previous.operations],
                            "changed": sorted(previous.touched),
                            "labels": [],
                            "operation_id": scope.operation_id,
                            "generation": scope.generation,
                        },
                    )
                drafts.mark_committed(draft["digest"], result)
                return _json(result)
            if draft["base_revision"] != _variant(scope).revision:
                raise WorkspaceError(
                    code="STORY_DRAFT_CONFLICT",
                    where="story.draft",
                    why="正式结构在准备草稿后已变化",
                    fix="读取正式结构，在新任务中核对修改",
                )
        elif draft and not draft.get("committed"):
            raise WorkspaceError(
                code="STORY_DRAFT_CONFLICT",
                where="story.draft",
                why="任务已有未提交草稿",
                fix="先 prepare 局部修正，再用 draft_revision 整体提交，不能绕过草稿单独写入",
            )
        raw_operations = draft["operations"] if use_draft and draft else arguments.get("operations")
        raw_no_change = arguments.get("no_change")
        if raw_no_change is not None:
            if isinstance(raw_operations, list) and raw_operations:
                raise WorkspaceError(
                    code="STORY_CHANGE_INVALID",
                    where="story.update",
                    why="同一调用不能既提交变更又声明无需改动",
                    fix="只保留其中一种：有改动就提交 operations，确实无需改动才用 no_change",
                )
            return record_no_change(raw_no_change)
        if not isinstance(raw_operations, list) or not raw_operations:
            raise WorkspaceError(
                code="STORY_CHANGE_INVALID",
                where="story.update",
                why="没有给出要做的结构变更",
                fix="用 operations 描述至少一项变更；确实无需改动时用 no_change 列出依据",
            )
        variant = _variant(scope)
        try:
            change = StoryChangeSet(
                id=draft["change_id"] if use_draft and draft else new_id("agent"),
                base_revision=variant.revision,
                operations=raw_operations,
                requested_scope=(scope.author_text or "")[:500],
                operation_id=scope.operation_id,
                generation=scope.generation,
            )
        except ValidationError as exc:
            issues = validation_issues(exc, raw_operations)
            first = issues[0]
            raise WorkspaceError(
                code="STORY_CHANGE_INVALID",
                where=f"story.update:{first['field']}",
                why=first["message"],
                fix=first["suggestion"],
                details={"issues": issues},
            ) from exc

        data = candidate_data(variant, change)
        issues = check_candidate(data)
        if issues:
            raise WorkspaceError(
                code="STORY_GRAPH_INVALID",
                where="story.change",
                why=issues[0]["message"],
                fix=issues[0]["suggestion"],
                details={"issues": issues},
            )

        # 先按作者本轮的对象范围收口，再检查真相、约定与裁决这些不能由模型自行扩大的部分。
        # 作者确认的候选可以扩展可修改对象，但它里面的命令不构成真相授权。
        catalog = _catalog(scope, variant)
        authorize_change(
            variant,
            scope.frozen,
            scope.author_text,
            change,
            catalog=catalog,
            candidate_text=scope.candidate_text,
        )

        for operation in change.operations:
            constraint = getattr(operation, "constraint", None)
            if constraint is not None and constraint.source_ref is not None:
                ConstraintSources.verify(constraint, scope.library, scope.module_refs)
        validate_model_change(
            variant,
            change,
            scope.author_text,
            catalog=catalog,
            confirmed_sources=ConstraintSources(scope.state_root).verified(
                scope.library, scope.module_refs
            ),
            confirmed_constraints=frozenset(draft.get("confirmed_constraints", []))
            if use_draft and draft
            else frozenset(),
        )

        guard = scope.guard
        if guard is not None:
            guard()

        def require_eligible(operation_id: str, generation: int) -> None:
            if guard is not None:
                guard()
            if use_draft and draft:
                latest = drafts.read()
                if latest is None or latest["digest"] != draft["digest"]:
                    raise WorkspaceError(
                        code="STORY_DRAFT_CONFLICT",
                        where="story.commit",
                        why="草稿在提交前已变化",
                        fix="读取最新草稿并重新核对",
                    )

        ledger_revision = ClueLedgerStore(scope.state_root).read().revision
        receipt = scope.store.commit_change(
            variant_id=variant.id,
            change=change,
            request_key=f"draft:{change.id}:{draft['digest']}"
            if use_draft and draft
            else f"agent:{change.id}:{variant.revision}",
            # 提交决定落在锁内再查一次：读结构到写盘之间可能刚好暂停。
            require_eligible=require_eligible if guard is not None or use_draft else None,
            decision_guard=scope.decision_guard,
            prepare_joint=prepare_clue_joint(
                scope.state_root,
                variant_id=variant.id,
                change=change,
                expected_ledger_revision=ledger_revision,
            ),
        )
        after = scope.store.read()
        record = after.document.changes.get(receipt.change_id) if after.document else None
        after_variant = after.variant(variant.id) if after.document else None
        changed = sorted(record.touched) if record is not None else []
        after_catalog = _catalog(scope, after_variant or variant)
        labels = _changed_labels(after_variant or variant, changed, after_catalog)
        result = {
            "ok": True,
            "variant_id": variant.id,
            "revision": receipt.revision,
            "change_id": receipt.change_id,
            "changed": changed,
            "labels": labels,
            "structure_issues": structure_diagnostics(after_variant) if after_variant else [],
            "note": "结构已经保存；正文改动仍要另行写入正文文件。",
        }
        if scope.record is not None:
            scope.record(
                "story_changes_committed",
                {
                    "variant_id": variant.id,
                    "base_revision": (
                        record.base_revision if record is not None else variant.revision
                    ),
                    "revision": receipt.revision,
                    "change_id": receipt.change_id,
                    "operations": len(change.operations),
                    "operation_types": [operation.op for operation in change.operations],
                    "changed": changed,
                    "labels": labels,
                    "operation_id": scope.operation_id,
                    "generation": scope.generation,
                },
            )
        if use_draft and draft:
            drafts.mark_committed(draft["digest"], result)
        return _json(result)

    tools = [
        AgentTool(
            name="read_story",
            label="读取构思",
            description=READ_STORY_DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "target_id": {
                        "type": "string",
                        "description": "草稿中按稳定对象 ID 精确读取操作及索引，不必遍历分页",
                    },
                    "operation_start": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "草稿操作分页起点；每页 12 项，返回原始 index",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["saved", "draft"],
                        "description": "saved 正式构思；draft 本任务草稿及操作索引",
                    },
                    "node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (prompt_tools.READ_STORY_NODE_IDS_PARAM_DESCRIPTION),
                    },
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
        and not blocks_writes(scope.author_text)
    ):
        tools.append(
            AgentTool(
                name="update_story",
                label="修改构思",
                description=UPDATE_STORY_DESCRIPTION,
                parameters={
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["prepare", "commit"]},
                        "draft_revision": {
                            "type": "string",
                            "description": "已有草稿的精确版本；prepare 和 commit 均需携带",
                        },
                        "node_patches": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "node_id": {"type": "string"},
                                    "fields": {
                                        "type": "object",
                                        "description": "待修改节点字段，例如 stage_id；不可修改 id",
                                    },
                                },
                                "required": ["node_id", "fields"],
                                "additionalProperties": False,
                            },
                        },
                        "replace_operations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "index": {"type": "integer", "minimum": 0},
                                    "operation": {"type": "object"},
                                },
                                "required": ["index", "operation"],
                                "additionalProperties": False,
                            },
                        },
                        "operations": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": (prompt_tools.UPDATE_STORY_OPERATIONS_PARAM_DESCRIPTION),
                        },
                        "no_change": {
                            "type": "object",
                            "description": prompt_tools.UPDATE_STORY_NO_CHANGE_PARAM_DESCRIPTION,
                            "properties": {
                                "objects": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "kind": {
                                                "type": "string",
                                                "enum": ["node", "clue", "constraint"],
                                            },
                                            "id": {"type": "string"},
                                        },
                                        "required": ["kind", "id"],
                                        "additionalProperties": False,
                                    },
                                },
                                "reason": {"type": "string"},
                            },
                            "required": ["objects", "reason"],
                            "additionalProperties": False,
                        },
                        "note": {
                            "type": "string",
                            "description": prompt_tools.UPDATE_STORY_NOTE_PARAM_DESCRIPTION,
                        },
                    },
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
            parameters["properties"]["replace_operations"]["items"]["properties"]["operation"] = (
                schema["properties"]["operations"]["items"]
            )
    from dataclasses import replace

    def with_diagnostics(tool: AgentTool) -> AgentTool:
        execute = tool.execute_fn

        async def wrapped(*args: Any, **kwargs: Any) -> AgentToolResult:
            try:
                return await execute(*args, **kwargs)
            except WorkspaceError as exc:

                class DiagnosticError(WorkspaceError):
                    def __str__(self) -> str:
                        return json.dumps(
                            {
                                "ok": False,
                                "code": self.code,
                                "summary": self.message,
                                **self.details,
                            },
                            ensure_ascii=False,
                            default=str,
                        )

                raise DiagnosticError(
                    code=exc.code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
                ) from exc

        return replace(tool, execute_fn=wrapped)

    return [with_diagnostics(tool) for tool in tools]
