"""直接修改文件的写作 Agent（0.4 M03）。

一个 Agent，一组通用文件工具，没有规划者/写作者/审阅者的固定多角色流程。工具只在
文件层之上做两件事：把参数整理成可理解的错误，以及在第一次真正写文件之前通知记录层
准备 Git 基线——读操作永远不会触发提交。

工具成功只表示「已经写到磁盘」：``write_file`` / ``edit_file`` 返回的 ``digest`` 是
落盘后的实际指纹，模型可以据此继续改。指纹不匹配、路径不受管、文件不存在都按失败
返回给模型，由它重新读取，而不是由应用替它猜。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hastings.workspace.context import build_system_prompt
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession
from tau_agent.harness import AgentHarness, AgentHarnessConfig
from tau_agent.messages import TextContent
from tau_agent.provider import ModelProvider
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

__all__ = ["MAX_TURNS", "RunContext", "build_harness", "build_tools"]

#: 单轮最多的模型回合数；到顶就结束并如实记录，不无限循环。
MAX_TURNS = 24

#: 单次工具输出上限，避免把整本书塞回模型。
_MAX_DIFF_CHARS = 20_000
_READ_DEFAULT_LINES = 200


@dataclass
class RunContext:
    """一轮写入的共享状态：文件工作区、记录会话与「首次写前准备基线」。"""

    workspace: Workspace
    session: WriteSession

    def before_write(self) -> None:
        if not self.session.prepared:
            self.session.prepare()


def build_tools(workspace: Workspace, run: RunContext) -> list[AgentTool]:
    """本轮可用的文件工具；Git 提交与撤销不暴露给模型。"""
    repo = GitRepo(workspace.root)

    async def list_files(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        catalog = workspace.catalog()
        return _json(
            {
                "chapters": [
                    {"path": entry.path, "title": entry.title, "size": entry.size}
                    for entry in catalog.chapters
                ],
                "settings": [
                    {"path": entry.path, "title": entry.title, "exists": entry.exists}
                    for entry in catalog.settings
                ],
            }
        )

    async def read_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        path = _text_argument(arguments, "path")
        offset = _int_argument(arguments, "offset", default=1, minimum=1)
        limit = _int_argument(arguments, "limit", default=_READ_DEFAULT_LINES, minimum=1)
        view = workspace.read(path)
        lines = view.text.split("\n")
        start = min(offset - 1, len(lines))
        chunk = lines[start : start + limit]
        return _json(
            {
                "path": view.path,
                "digest": view.digest,
                "total_lines": len(lines),
                "offset": start + 1,
                "truncated": start + len(chunk) < len(lines),
                "text": "\n".join(chunk),
            }
        )

    async def search_text(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        query = _text_argument(arguments, "query")
        limit = _int_argument(arguments, "limit", default=20, minimum=1)
        result = workspace.search(query, limit=limit)
        return _json(
            {
                "hits": [
                    {"path": hit.path, "line": hit.line, "text": hit.text} for hit in result.hits
                ],
                "unreadable": list(result.unreadable),
                "truncated": result.truncated,
            }
        )

    async def write_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        path = _text_argument(arguments, "path")
        text = _text_argument(arguments, "text", allow_empty=True)
        digest = _digest_argument(arguments)
        run.before_write()
        view = workspace.write(path, text, expected_digest=digest)
        return _json({"path": view.path, "digest": view.digest, "size": view.size})

    async def edit_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        path = _text_argument(arguments, "path")
        old_text = _text_argument(arguments, "old_text")
        new_text = _text_argument(arguments, "new_text", allow_empty=True)
        digest = _digest_argument(arguments)
        view = workspace.read(path)
        occurrences = view.text.count(old_text)
        if occurrences == 0:
            raise WorkspaceError(
                code="EDIT_MATCH_NOT_FOUND",
                where=f"tools.edit_file:{path}",
                why="要替换的原文没有出现在当前文件里",
                fix="重新 read_file 确认当前内容，再复制一段完全一致的原文",
                details={"path": path},
            )
        if occurrences > 1:
            raise WorkspaceError(
                code="EDIT_MATCH_AMBIGUOUS",
                where=f"tools.edit_file:{path}",
                why=f"要替换的原文出现了 {occurrences} 次，无法确定改哪一处",
                fix="多带一些上下文让原文唯一，或改用 write_file 重写整章",
                details={"path": path, "occurrences": occurrences},
            )
        updated = view.text.replace(old_text, new_text, 1)
        run.before_write()
        written = workspace.write(path, updated, expected_digest=digest)
        return _json({"path": written.path, "digest": written.digest, "size": written.size})

    async def move_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        source = _text_argument(arguments, "source")
        target = _text_argument(arguments, "target")
        digest = _required_digest(arguments)
        run.before_write()
        view = workspace.move(source, target, expected_digest=digest)
        return _json({"path": view.path, "digest": view.digest})

    async def delete_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        path = _text_argument(arguments, "path")
        digest = _required_digest(arguments)
        run.before_write()
        workspace.delete(path, expected_digest=digest)
        return _json({"path": path, "deleted": True})

    async def git_diff(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        diff = repo.diff()
        if len(diff) > _MAX_DIFF_CHARS:
            diff = diff[:_MAX_DIFF_CHARS] + "\n…（差异过长，已截断）"
        return _json({"diff": diff})

    async def git_log(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        limit = _int_argument(arguments, "limit", default=10, minimum=1)
        return _json(
            {
                "commits": [
                    {"commit": commit.sha, "at": commit.committed_at, "subject": commit.subject}
                    for commit in repo.log(limit=limit)
                ]
            }
        )

    return [
        AgentTool(
            name="list_files",
            label="文件目录",
            description="列出正文各章与三份设定文件的路径、标题与大小（只读）。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            execute_fn=list_files,
            prompt_guidelines=("不确定有哪些文件时先调用；无参数。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="read_file",
            label="读取文件",
            description=(
                "读取受管文件的文本，返回内容、总行数与 digest。digest 是后续写入的凭据。"
                "长文用 offset/limit 分页。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "如 正文/0001 雨夜.md"},
                    "offset": {"type": "integer", "minimum": 1, "description": "起始行号，默认 1"},
                    "limit": {"type": "integer", "minimum": 1, "description": "读取行数，默认 200"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            execute_fn=read_file,
            prompt_guidelines=("改写前必须读；读完保留返回的 digest 供写入使用。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="search_text",
            label="搜索文本",
            description="在正文与设定中逐行搜索关键词，返回路径、行号与摘录（只读）。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要查找的文字"},
                    "limit": {"type": "integer", "minimum": 1, "description": "最多条数，默认 20"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            execute_fn=search_text,
            prompt_guidelines=("找伏笔、人物首次出现等具体内容时使用。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="write_file",
            label="写入文件",
            description=(
                "整体写入一个受管文件。新建文件不带 digest；改写已有文件必须带 read_file "
                "得到的 digest。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "text": {"type": "string", "description": "完整的新内容（UTF-8 文本）"},
                    "digest": {"type": "string", "description": "改写已有文件时必填"},
                },
                "required": ["path", "text"],
                "additionalProperties": False,
            },
            execute_fn=write_file,
            prompt_guidelines=("适合新建章节或整章重写；局部修改优先 edit_file。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="edit_file",
            label="局部修改",
            description=(
                "用唯一匹配的原文替换完成局部修改，返回新的 digest。原文出现 0 次或多次都会失败。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "description": "要被替换的原文，必须唯一"},
                    "new_text": {"type": "string", "description": "替换后的文字"},
                    "digest": {"type": "string", "description": "read_file 得到的 digest"},
                },
                "required": ["path", "old_text", "new_text", "digest"],
                "additionalProperties": False,
            },
            execute_fn=edit_file,
            prompt_guidelines=("改一句话、补一段时使用；不要为小改动重写整章。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="move_file",
            label="重命名章节",
            description="重命名或移动章节文件（设定集不可移动），需要 digest。",
            parameters={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "digest": {"type": "string"},
                },
                "required": ["source", "target", "digest"],
                "additionalProperties": False,
            },
            execute_fn=move_file,
            execution_mode="sequential",
        ),
        AgentTool(
            name="delete_file",
            label="删除章节",
            description="删除章节文件（设定集不可删除），需要 digest。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "digest": {"type": "string"}},
                "required": ["path", "digest"],
                "additionalProperties": False,
            },
            execute_fn=delete_file,
            execution_mode="sequential",
        ),
        AgentTool(
            name="git_diff",
            label="查看差异",
            description="查看工作区相对上次提交的差异（只读）。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            execute_fn=git_diff,
            prompt_guidelines=("确认自己改了什么时使用；新文件不出现在工作区差异里。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="git_log",
            label="查看历史",
            description="查看最近的提交标题与时间（只读）。",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1}},
                "additionalProperties": False,
            },
            execute_fn=git_log,
            execution_mode="sequential",
        ),
    ]


def build_harness(
    *,
    provider: ModelProvider,
    model: str,
    workspace: Workspace,
    run: RunContext,
    selected_path: str | None = None,
    max_turns: int = MAX_TURNS,
) -> AgentHarness:
    """单个写作 Agent：系统提示 + 文件工具 + 回合上限。"""
    return AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model=model,
            system=build_system_prompt(workspace, selected_path=selected_path),
            tools=build_tools(workspace, run),
            max_turns=max_turns,
        )
    )


def _json(payload: Mapping[str, Any]) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text=json.dumps(payload, ensure_ascii=False, indent=2))],
        details=dict(payload),
    )


def _text_argument(
    arguments: Mapping[str, JSONValue], name: str, *, allow_empty: bool = False
) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise _input_error(name, "必须是字符串")
    if not value and not allow_empty:
        raise _input_error(name, "不能为空")
    return value


def _int_argument(
    arguments: Mapping[str, JSONValue], name: str, *, default: int, minimum: int
) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _input_error(name, "必须是整数")
    if value < minimum:
        raise _input_error(name, f"必须 ≥ {minimum}")
    return value


def _digest_argument(arguments: Mapping[str, JSONValue], *, required: bool = False) -> str | None:
    value = arguments.get("digest")
    if value is None:
        if required:
            raise _input_error("digest", "必须提供 read_file 得到的 digest")
        return None
    if not isinstance(value, str) or not value:
        raise _input_error("digest", "必须是非空字符串")
    return value


def _required_digest(arguments: Mapping[str, JSONValue]) -> str:
    value = _digest_argument(arguments, required=True)
    assert value is not None
    return value


def _input_error(name: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="TOOL_INPUT_INVALID",
        where=f"tools.{name}",
        why=f"参数 {name} {why}",
        fix="按工具说明修正参数后重试",
    )
