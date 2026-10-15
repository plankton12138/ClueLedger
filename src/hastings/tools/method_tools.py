"""method_lock / method_unlock：诡计手法的冻结与解冻（P2 T4）。

locked→drafted 这条边只归本模块的 unlock 工具；bible_upsert 对 locked
手法拦截内容修改（见 bible_tools._reject_locked_method_edit）。两次操作
都追加 Method.history（章节+理由+时间戳），审计链随账本落盘——状态为何
变化对模型与读者同等可见，是事件契约的一部分。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from hastings.bible.models import Method, MethodHistoryEntry
from hastings.bible.store import StoryWorkspace, load_ledger, save_ledger
from hastings.tools.architecture_gate import is_v2, refuse_tool, v2_write_rejection
from hastings.tools.bible_tools import _require_str, reject_intake_write
from hastings.tools.errors import ToolInputError
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_methods(workspace: StoryWorkspace) -> list[Method]:
    return load_ledger(workspace.method_path, Method)


def _find_method(methods: list[Method], name: str) -> Method:
    found = next((m for m in methods if m.name == name), None)
    if found is None:
        raise ToolInputError(
            f"手法「{name}」不存在。"
            "为什么错：method 账本中没有同名条目。"
            "怎样算对：先 bible_query(entity_type='method', name=...) 核对名称，"
            "或用 bible_upsert(entity_type='method') 创建后再操作。"
        )
    return found


def _save_methods(workspace: StoryWorkspace, methods: list[Method]) -> None:
    save_ledger(workspace.method_path, methods)


def _lock_render_call(arguments: Mapping[str, JSONValue]) -> str | None:
    name = arguments.get("name")
    return f"锁定手法 {name}" if isinstance(name, str) else "锁定手法"


def _unlock_render_call(arguments: Mapping[str, JSONValue]) -> str | None:
    name = arguments.get("name")
    return f"解锁手法 {name}" if isinstance(name, str) else "解锁手法"


def create_method_tools(
    workspace: StoryWorkspace, *, architecture_version: str = "v1"
) -> list[AgentTool]:
    """创建绑定 workspace 的 method_lock / method_unlock 工具。

    ``architecture_version="v2"`` 时两者都被替换为拒绝桩（T17.6）：手法冻结是
    V1 账本状态，V2 项目的手法以 MysteryTruth 领域对象表达，不经工具层写盘。
    """

    async def lock_execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        reject_intake_write(workspace)  # L2 数据面写门：intake 阶段拒绝锁手法
        name = _require_str(arguments, "name")
        chapter = _require_str(arguments, "chapter")
        reason = _require_str(arguments, "reason")
        methods = _load_methods(workspace)
        method = _find_method(methods, name)

        if method.status == "locked":
            raise ToolInputError(
                f"手法「{name}」已是 locked。为什么错：重复锁定没有效果。"
                "怎样算对：要改内容先 method_unlock；要结案用 bible_query 取回全量数据、"
                "把 data.status 设为 'revealed' 后整体重交。"
            )
        if method.status == "revealed":
            raise ToolInputError(
                f"手法「{name}」已 revealed（终态）。为什么错：终态不可再迁移。"
                "怎样算对：如需新手法，用 bible_upsert 新建一条（status=drafted）。"
            )

        method.status = "locked"
        method.history.append(
            MethodHistoryEntry(
                action="locked", chapter=chapter, reason=reason, timestamp=_now_iso()
            )
        )
        _save_methods(workspace, methods)
        return AgentToolResult(
            content=[TextContent(text=f"手法「{name}」已锁定（章节 {chapter}）：{reason}")],
            details={
                "name": name,
                "status": "locked",
                "chapter": chapter,
                "history_count": len(method.history),
            },
        )

    async def unlock_execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        reject_intake_write(workspace)  # L2 数据面写门：intake 阶段拒绝解手法
        name = _require_str(arguments, "name")
        reason = _require_str(arguments, "reason")
        methods = _load_methods(workspace)
        method = _find_method(methods, name)

        if method.status == "drafted":
            raise ToolInputError(
                f"手法「{name}」未锁定（drafted）。为什么错：解锁只对 locked 状态有意义。"
                "怎样算对：drafted 状态可直接用 bible_upsert 修改，无需解锁。"
            )
        if method.status == "revealed":
            raise ToolInputError(
                f"手法「{name}」已 revealed（终态）。为什么错：终态不可解锁回退。"
                "怎样算对：如需改写已揭晓手法，新建一条手法。"
            )

        method.status = "drafted"
        method.history.append(
            MethodHistoryEntry(action="unlocked", reason=reason, timestamp=_now_iso())
        )
        _save_methods(workspace, methods)
        return AgentToolResult(
            content=[TextContent(text=f"手法「{name}」已解锁：{reason}")],
            details={
                "name": name,
                "status": "drafted",
                "history_count": len(method.history),
            },
        )

    lock = AgentTool(
        name="method_lock",
        label="锁定诡计手法",
        description=(
            "把一条 drafted 手法冻结为 locked：锁定后 bible_upsert 不能再改其任何字段"
            "（含退回 drafted），操作与理由记入该手法的 history。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "手法名（method 账本主键）"},
                "chapter": {"type": "string", "description": "锁定发生的章节，如 ch01"},
                "reason": {"type": "string", "description": "锁定理由（定稿依据），记入审计历史"},
            },
            "required": ["name", "chapter", "reason"],
            "additionalProperties": False,
        },
        execute_fn=lock_execute,
        prompt_guidelines=(
            "手法内容定稿后立即 method_lock；之后想改任何字段必须先 method_unlock。",
        ),
        render_call=_lock_render_call,
    )
    unlock = AgentTool(
        name="method_unlock",
        label="解锁诡计手法",
        description=(
            "把 locked 手法退回 drafted（唯一能走这条边的工具），解锁理由记入该手法的 history。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "手法名（method 账本主键）"},
                "reason": {"type": "string", "description": "解锁理由，记入审计历史"},
            },
            "required": ["name", "reason"],
            "additionalProperties": False,
        },
        execute_fn=unlock_execute,
        prompt_guidelines=(
            "解锁须给出理由；解锁后 bible_upsert 恢复可写，改完记得重新 method_lock。",
        ),
        render_call=_unlock_render_call,
    )
    tools = [lock, unlock]
    if is_v2(architecture_version):
        return [
            refuse_tool(
                tool,
                rejection=v2_write_rejection(
                    tool.name,
                    alternative="手法内容请在 V2 项目内以 MysteryTruth 领域对象提交"
                    "（真凶/手法/时间窗为结构化字段），状态变更随 Commit 版本化。",
                ),
            )
            for tool in tools
        ]
    return tools


__all__ = ["create_method_tools"]
