"""构思结构的模型工具：读取方案、提议变更并提交（1.0.1 P4）。

工具只做三件事，边界都很窄：

- ``read_story``：读当前方案的节点、线索、关系与约定。返回的是精确 ID 与修订，
  模型不需要（也不允许）靠标题猜目标。
- ``update_story``：提交一组语义变更。基础修订由应用填，``operation_id`` 与代次由
  应用填，模型提交的字段只描述「改什么」。提交前先过代次资格检查：暂停之后旧代次
  即使还在跑工具，也拿不到写权限。
- ``create_story_variant`` / ``adopt_story_variant``：只在作者明确要求时创建备选或
  采用；实际采用范围由冻结选择与作者原话决定，采用产生的变更走同一套对象、真相与
  约定检查，不绕过普通修改的权限。

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
    ADOPT_STORY_DESCRIPTION,
    ADOPT_STORY_NODE_IDS_PARAM_DESCRIPTION,
    READ_STORY_DESCRIPTION,
    READ_STORY_GUIDELINES,
    UPDATE_STORY_DESCRIPTION,
    UPDATE_STORY_GUIDELINES,
    variant_tool_description,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_adoption import AdoptionPlan
from hastings.workspace.story_authorization import (
    authorize_change,
    blocks_writes,
    build_authority,
    validate_model_change,
)
from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_models import (
    StoryChangeSet,
    StoryOperation,
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


_KIND_LABELS = {
    "node": "节点",
    "clue": "线索",
    "thread": "分支",
    "constraint": "约定",
    "edge": "关系",
    "binding": "正文关联",
    "assessment": "核对记录",
    "time": "时间",
}


def _object_label(variant: StoryVariant, kind: str, object_id: str) -> str:
    """收据里的对象标签：作者看到标题，不看到内部标识。"""
    if kind == "node" and object_id in variant.nodes:
        return f"节点「{variant.nodes[object_id].title}」"
    if kind == "clue" and object_id in variant.clues:
        return f"线索「{variant.clues[object_id].title}」"
    if kind == "thread" and object_id in variant.threads:
        return f"分支「{variant.threads[object_id].title}」"
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


def _changed_labels(variant: StoryVariant, keys: list[str]) -> list[str]:
    labels: list[str] = []
    for key in keys:
        kind, _, object_id = key.partition(":")
        labels.append(_object_label(variant, kind, object_id))
    return labels[:20]


def _require_write_state(scope: StoryToolScope, where: str) -> None:
    if scope.write_guard is not None:
        scope.write_guard(where)
    if scope.guard is not None:
        # 先查资格再读结构：暂停之后旧代次不能靠一次工具调用拿到写入。
        scope.guard()


#: 作者明确要求整案采用时的说法；没有这些说法就不能把空选择当成整案。
_WHOLE_ADOPTION = (
    "整案",
    "整份",
    "整个方案",
    "整份方案",
    "整个备选",
    "整份备选",
    "整个结构",
    "全部采用",
    "采用全部",
    "采用整个",
    "全部内容",
)


def _whole_requested(author_text: str) -> bool:
    """作者是否明确要求整案，而不是只采用其中一部分或明确不要整案。"""
    if not any(word in author_text for word in _WHOLE_ADOPTION):
        return False
    narrowing = ("只采用", "仅采用", "局部", "一部分", "其中", "不要整案", "不整案", "别整案")
    return not any(word in author_text for word in narrowing)


def _adoption_scope(
    scope: StoryToolScope, selected: tuple[str, ...]
) -> tuple[tuple[str, ...], bool]:
    """由应用确定实际采用范围；模型传的节点只能收窄，不能借空值扩大。

    返回 ``(范围, 是否整案)``。界面选中了节点时，范围就是选中的节点；没有选中时，
    只有作者明确要求整案才采用整个方案，否则要求作者说明要采用哪一部分。
    """
    frozen = tuple(dict.fromkeys(scope.frozen.node_ids))
    if frozen:
        if selected and not set(selected) <= set(frozen):
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.adopt",
                why="采用范围超过本轮选中的节点",
                fix="只采用所选节点，其他范围由作者另行指定",
            )
        return (selected or frozen), False
    if selected:
        return selected, False
    if _whole_requested(scope.author_text):
        return (), True
    raise WorkspaceError(
        code="STORY_AUTHORIZATION_CONFLICT",
        where="story.adopt",
        why="作者没有指定采用哪些内容，也没有要求整案采用",
        fix="只采用界面选中的节点，或请作者明确说明要整案采用",
    )


def _prepare_adoption(
    scope: StoryToolScope, *, whole: bool
) -> Callable[[AdoptionPlan, StoryVariant, StoryVariant], list[StoryOperation]]:
    """采用产生的变更走与普通修改相同的对象、真相与约定检查。"""

    def prepare(
        plan: AdoptionPlan, source: StoryVariant, target: StoryVariant
    ) -> list[StoryOperation]:
        del source
        if not plan.operations:
            # 没有可复制的对象：不构造空变更，交给存储登记一次无改动收据。
            return []
        change = StoryChangeSet(
            id=new_id("adopt"),
            base_revision=target.revision,
            operations=list(plan.operations),
            requested_scope=(scope.author_text or "")[:500],
            operation_id=scope.operation_id,
            generation=scope.generation,
        )
        authorize_change(
            target, scope.frozen, scope.author_text, change, design_wide=whole
        )
        validate_model_change(target, change, scope.author_text)
        return list(change.operations)

    return prepare


def build_story_tools(scope: StoryToolScope) -> list[AgentTool]:
    """按当前冻结方案给出读取与变更工具；只读用途不给提交工具。"""

    #: 本轮实际读过的对象与修订；no_change 只能用这里出现过的对象作依据。
    read_state: dict[str, Any] = {"revision": "", "objects": set()}

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
        if read_state["revision"] != variant.revision:
            read_state["revision"] = variant.revision
            read_state["objects"] = set()
        read_objects = cast(set[tuple[str, str]], read_state["objects"])
        read_objects.update(("node", node_id) for node_id in node_ids)
        # 返回内容里始终带有全部线索与约定；它们也算实际读到。
        read_objects.update(("clue", clue_id) for clue_id in variant.clues)
        read_objects.update(("constraint", constraint_id) for constraint_id in variant.constraints)
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
        authority = build_authority(
            variant, scope.frozen, scope.author_text, candidate_text=scope.candidate_text
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
                or (kind == "clue" and object_id in variant.clues)
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
                    "label": _object_label(variant, kind, object_id),
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
        raw_operations = arguments.get("operations")
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
        # 作者确认的候选可以扩展可修改对象，但它里面的命令不构成真相授权。
        authorize_change(
            variant,
            scope.frozen,
            scope.author_text,
            change,
            candidate_text=scope.candidate_text,
        )

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
        after_variant = after.variant(variant.id) if after.document else None
        changed = sorted(record.touched) if record is not None else []
        labels = _changed_labels(after_variant or variant, changed)
        result = {
            "ok": True,
            "variant_id": variant.id,
            "revision": receipt.revision,
            "change_id": receipt.change_id,
            "changed": changed,
            "labels": labels,
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
        return _json(result)

    async def fork_story(
        tool_call_id: str, arguments: Mapping[str, Any], signal: Any = None, on_update: Any = None
    ) -> AgentToolResult:
        del signal, on_update
        if blocks_writes(scope.author_text) or not any(
            word in scope.author_text for word in ("备选", "另一方案", "另一个方案")
        ):
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.fork",
                why="作者没有要求创建备选方案",
                fix="先讨论当前方案，待作者明确要求备选后创建",
            )
        if scope.frozen.structure_readonly:
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.fork",
                why="本轮结构只读，不能创建备选",
                fix="只调整正文；需要备选时另起一轮",
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
        if "采用" not in scope.author_text or blocks_writes(scope.author_text) or any(
            word in scope.author_text for word in ("不要采用", "不采用", "别采用", "不用采用")
        ):
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.adopt",
                why="作者没有明确要求采用方案",
                fix="先说明差异，等待作者决定采用范围",
            )
        if scope.frozen.structure_readonly:
            raise WorkspaceError(
                code="STORY_AUTHORIZATION_CONFLICT",
                where="story.adopt",
                why="本轮结构只读，不能采用备选",
                fix="只调整正文；需要采用时另起一轮",
            )
        target = _variant(scope)
        source = scope.store.require_variant(str(arguments.get("source_variant_id") or ""))
        raw_selected = arguments.get("node_ids") or []
        if not isinstance(raw_selected, list):
            raise WorkspaceError(
                code="STORY_SCOPE_INVALID",
                where="story.adopt",
                why="node_ids 不是数组",
                fix="传一个节点标识数组，或省略它采用本轮选中的节点",
            )
        selected = tuple(str(item) for item in raw_selected)
        actual, whole = _adoption_scope(scope, selected)
        prepare = _prepare_adoption(scope, whole=whole)

        def require_eligible(operation_id: str, generation: int) -> None:
            del operation_id, generation
            if scope.guard is not None:
                scope.guard()

        with scope.decision_guard() if scope.decision_guard else nullcontext():
            if scope.guard:
                scope.guard()
            receipt = scope.store.adopt(
                source_variant_id=source.id,
                source_revision=source.revision,
                target_variant_id=target.id,
                target_revision=target.revision,
                node_ids=actual,
                replace=True,
                request_key=f"{scope.operation_id}:adopt:{tool_call_id}",
                mode="exact",
                prepare=prepare,
                require_eligible=require_eligible if scope.guard is not None else None,
                decision_guard=scope.decision_guard,
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
        and not blocks_writes(scope.author_text)
    ):
        for name, label, execute, properties, required, description in (
            (
                "create_story_variant",
                "创建备选",
                fork_story,
                {"name": {"type": "string"}},
                ["name"],
                variant_tool_description("创建备选"),
            ),
            (
                "adopt_story_variant",
                "采用备选结构",
                adopt_story,
                {
                    "source_variant_id": {"type": "string"},
                    "node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ADOPT_STORY_NODE_IDS_PARAM_DESCRIPTION,
                    },
                },
                ["source_variant_id"],
                ADOPT_STORY_DESCRIPTION,
            ),
        ):
            tools.append(
                AgentTool(
                    name=name,
                    label=label,
                    description=description,
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
    return tools
