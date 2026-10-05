"""章节写作工具：write_chapter。

正文落盘 chapters/chNN.md（两位数零填充），章节标题由工具生成；
已存在时须 force=true 才覆盖，防止模型误改写旧稿。
"""

from __future__ import annotations

from collections.abc import Mapping

from hastings.bible.store import StoryWorkspace
from hastings.tools.errors import ToolInputError
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue


def _render_call(arguments: Mapping[str, JSONValue]) -> str | None:
    chapter = arguments.get("chapter")
    content = arguments.get("content")
    size = len(content) if isinstance(content, str) else 0
    return f"写入第{chapter}章（{size} 字）"


def create_chapter_tools(workspace: StoryWorkspace) -> list[AgentTool]:
    """创建绑定 workspace 的 write_chapter 工具。"""

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        chapter = arguments.get("chapter")
        if isinstance(chapter, bool) or not isinstance(chapter, int):
            raise ToolInputError("chapter 必须是整数（≥1）")
        if chapter < 1:
            raise ToolInputError("chapter 必须 ≥1")
        title = arguments.get("title")
        if title is not None and not isinstance(title, str):
            raise ToolInputError("title 必须是字符串")
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ToolInputError("content 必须是非空字符串（章节正文）")
        force = arguments.get("force", False)
        if not isinstance(force, bool):
            raise ToolInputError("force 必须是布尔值")

        workspace.ensure_dirs()
        path = workspace.chapters_dir / f"ch{chapter:02d}.md"
        if path.exists() and not force:
            raise ToolInputError(f"{path.name} 已存在；确认要覆盖请传 force=true")
        heading = f"# 第{chapter}章"
        if isinstance(title, str) and title.strip():
            heading += f" {title.strip()}"
        path.write_text(f"{heading}\n\n{content.rstrip()}\n", encoding="utf-8")
        return AgentToolResult(
            content=[TextContent(text=f"第{chapter}章已写入 {path}（{len(content)} 字）")],
            details={"path": str(path), "chapter": chapter, "force": force},
        )

    return [
        AgentTool(
            name="write_chapter",
            label="写入章节",
            description=(
                "把一章正文写入 chapters/chNN.md（NN 为两位数章节号）。"
                "文件已存在时须 force=true 才覆盖。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "chapter": {"type": "integer", "minimum": 1, "description": "章节号（≥1）"},
                    "title": {"type": "string", "description": "章节标题（可选）"},
                    "content": {"type": "string", "description": "章节正文（Markdown）"},
                    "force": {"type": "boolean", "description": "已存在时是否覆盖"},
                },
                "required": ["chapter", "content"],
                "additionalProperties": False,
            },
            execute_fn=execute,
            prompt_guidelines=(
                "写章节前，确认出场角色都已用 bible_upsert 落盘。",
                "content 只写纯正文（Markdown），章节标题由工具自动生成。",
            ),
            render_call=_render_call,
        )
    ]


__all__ = ["create_chapter_tools"]
