"""章节只读工具：read_chapter / list_chapters（P4 T3）。

V1 写路径（write_chapter、作者草稿锁、对账运行守卫）已随「V1 工作区只读退役」
删除（Phase 17 收尾决策：项目未上线，V1 直接弃用）；正文读取是存留通道——
agent 必须能读回原文，长期记忆才从「账本摘要」扩到「原文细节」。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast

from hastings.bible.store import StoryWorkspace
from hastings.tools.errors import ToolInputError
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

_CHAPTER_NAME = re.compile(r"^ch(\d+)\.md$")


def _render_read(arguments: Mapping[str, JSONValue]) -> str | None:
    return f"读取第{arguments.get('chapter')}章"


def _render_list(arguments: Mapping[str, JSONValue]) -> str | None:
    del arguments
    return "列出章节目录"


def _require_chapter(arguments: Mapping[str, JSONValue]) -> int:
    chapter = arguments.get("chapter")
    if isinstance(chapter, bool) or not isinstance(chapter, int):
        raise ToolInputError("chapter 必须是整数（≥1）")
    if chapter < 1:
        raise ToolInputError("chapter 必须 ≥1")
    return chapter


def _available_chapters(workspace: StoryWorkspace) -> list[int]:
    """按文件名解析已有章节号，升序返回；非 chNN.md 文件忽略。"""
    if not workspace.chapters_dir.is_dir():
        return []
    numbers = [
        int(match.group(1))
        for path in workspace.chapters_dir.iterdir()
        if (match := _CHAPTER_NAME.match(path.name))
    ]
    return sorted(numbers)


def create_chapter_tools(workspace: StoryWorkspace) -> list[AgentTool]:
    """创建绑定 workspace 的 read_chapter / list_chapters 工具（只读）。"""

    async def execute_read(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        chapter = _require_chapter(arguments)
        path = workspace.chapters_dir / f"ch{chapter:02d}.md"
        if not path.exists():
            # 泄压阀：合法请求但无结果——错误消息指明下一步（可用章节）
            available = _available_chapters(workspace)
            if available:
                names = "、".join(str(n) for n in available)
                hint = f"可用章节：{names}（也可用 list_chapters 查看目录）"
            else:
                hint = "目前还没有任何章节"
            raise ToolInputError(f"第{chapter}章尚未写作。{hint}")
        text = path.read_text(encoding="utf-8")
        return AgentToolResult(
            content=[TextContent(text=text)],
            details={"path": str(path), "chapter": chapter, "chars": len(text)},
        )

    async def execute_list(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        entries: list[dict[str, JSONValue]] = []
        for number in _available_chapters(workspace):
            path = workspace.chapters_dir / f"ch{number:02d}.md"
            text = path.read_text(encoding="utf-8")
            first_line = text.split("\n", 1)[0]
            title_match = re.match(rf"#\s*第{number}章\s*(.*)", first_line)
            title = (title_match.group(1).strip() if title_match else "") or None
            entries.append({"chapter": number, "title": title, "chars": len(text)})
        if not entries:
            # 泄压阀：空目录是正常情形——返回正常结果并指明现状，不报错
            return AgentToolResult(
                content=[TextContent(text="还没有任何章节（chapters/ 为空）。")],
                details={"chapters": []},
            )
        lines = [
            f"- 第{e['chapter']}章{' ' + str(e['title']) if e['title'] else ''}（{e['chars']} 字）"
            for e in entries
        ]
        return AgentToolResult(
            content=[TextContent(text=f"共 {len(entries)} 章：\n" + "\n".join(lines))],
            # list 不型变：list[dict[str, JSONValue]] 需显式放宽为 JSONValue
            details={"chapters": cast("JSONValue", entries)},
        )

    return [
        AgentTool(
            name="read_chapter",
            label="读取章节",
            description=(
                "读回 chapters/chNN.md 的完整正文（只读）。章节不存在时会报错并附可用章节列表。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "chapter": {"type": "integer", "minimum": 1, "description": "章节号（≥1）"}
                },
                "required": ["chapter"],
                "additionalProperties": False,
            },
            execute_fn=execute_read,
            prompt_guidelines=(
                "续写建议、回收伏笔核对、时间线核对前使用；不确定有哪些章时先 list_chapters。",
            ),
            render_call=_render_read,
        ),
        AgentTool(
            name="list_chapters",
            label="章节目录",
            description="列出已写章节的目录（章节号、标题、字数，只读）。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            execute_fn=execute_list,
            prompt_guidelines=("判断当前进度时使用；无参数直接调用。",),
            render_call=_render_list,
        ),
    ]


__all__ = ["create_chapter_tools"]
