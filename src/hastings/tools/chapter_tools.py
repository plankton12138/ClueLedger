"""章节写作工具：write_chapter / read_chapter / list_chapters。

write_chapter 落盘 chapters/chNN.md（两位数零填充），章节标题由工具生成；
已存在时须 force=true 才覆盖，防止模型误改写旧稿；作者草稿锁（P5 D4）
优先于 force——作者有未保存草稿的章，模型不得写（硬约束在工具层）。
read_chapter / list_chapters（P4 T3）是只读回通道：正文写完不等于记忆——
agent 必须能读回原文，长期记忆才从「账本摘要」扩到「原文细节」。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast

from hastings.bible.store import StoryWorkspace
from hastings.manuscript.write_guard import EditMarkRegistry, RunKindGuard
from hastings.tools.errors import ToolInputError
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

_CHAPTER_NAME = re.compile(r"^ch(\d+)\.md$")


def _render_call(arguments: Mapping[str, JSONValue]) -> str | None:
    chapter = arguments.get("chapter")
    content = arguments.get("content")
    size = len(content) if isinstance(content, str) else 0
    return f"写入第{chapter}章（{size} 字）"


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


def create_chapter_tools(
    workspace: StoryWorkspace,
    edit_marks: EditMarkRegistry | None = None,
    run_kind: RunKindGuard | None = None,
) -> list[AgentTool]:
    """创建绑定 workspace 的 write_chapter / read_chapter / list_chapters 工具。

    edit_marks 是作者草稿锁注册表（P5 D4）：传入 session 持有的实例即接通
    双侧守门；缺省给空注册表（CLI 等单 agent 场景锁永不触发）。
    run_kind 是运行类别守卫（P5 T4，D4 末条）：reconcile 期间 write_chapter
    被拒——模型不得在对账时替作者改稿；缺省 None 即不设此守门。
    """
    marks = edit_marks if edit_marks is not None else EditMarkRegistry()

    async def execute_write(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        chapter = _require_chapter(arguments)
        title = arguments.get("title")
        if title is not None and not isinstance(title, str):
            raise ToolInputError("title 必须是字符串")
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ToolInputError("content 必须是非空字符串（章节正文）")
        force = arguments.get("force", False)
        if not isinstance(force, bool):
            raise ToolInputError("force 必须是布尔值")

        # D4 agent 侧守门：作者草稿优先，force 也不得越过（硬约束在工具层）
        if marks.mark_of(f"ch{chapter:02d}") is not None:
            raise ToolInputError(
                f"第{chapter}章正在被作者手动编辑。"
                "为什么错：覆盖会丢弃作者未保存的草稿。"
                "怎样算对：等待作者保存或放弃后再写，或提示作者。"
            )

        # D4 末条守门：对账运行期间模型不得替作者改稿——正文只经由
        # 作者的手或显式写作流变更；对账轮的职责只是让账本与正文一致。
        if run_kind is not None and run_kind.kind == "reconcile":
            raise ToolInputError(
                f"对账运行进行中，第{chapter}章写入被拒。"
                "为什么错：正文只经由作者的手或显式写作流变更；"
                "对账轮的任务是让账本与正文一致，不是修改正文。"
                "怎样算对：不调用 write_chapter，只处理对账消息列出的"
                "变更段，用 bible_upsert 把叙事影响落进账本。"
            )

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
            # 泄压阀：合法请求但无结果——错误消息指明下一步（可用章节/先写）
            available = _available_chapters(workspace)
            if available:
                names = "、".join(str(n) for n in available)
                hint = f"可用章节：{names}（也可用 list_chapters 查看目录）"
            else:
                hint = "目前还没有任何章节（可先用 write_chapter 落笔）"
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
            # 泄压阀：空账本是正常情形——返回正常结果并指明下一步，不报错
            return AgentToolResult(
                content=[
                    TextContent(
                        text="还没有任何章节（chapters/ 为空）。可用 write_chapter 写第一章。"
                    )
                ],
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
            execute_fn=execute_write,
            prompt_guidelines=(
                "写章节前，确认出场角色都已用 bible_upsert 落盘。",
                "写新章（非第 1 章）前，先用 read_chapter 读上一章的结尾，保持情节衔接。",
                "回收伏笔或引用旧情节前，先 read_chapter 核对相关章节的原文细节。",
                "content 只写纯正文（Markdown），章节标题由工具自动生成。",
            ),
            render_call=_render_call,
        ),
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
                "续写、回收伏笔、核对时间线细节前使用；不确定有哪些章时先 list_chapters。",
            ),
            render_call=_render_read,
        ),
        AgentTool(
            name="list_chapters",
            label="章节目录",
            description="列出已写章节的目录（章节号、标题、字数，只读）。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            execute_fn=execute_list,
            prompt_guidelines=("规划章节结构、判断当前进度时使用；无参数直接调用。",),
            render_call=_render_list,
        ),
    ]


__all__ = ["create_chapter_tools"]
