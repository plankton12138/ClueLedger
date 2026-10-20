"""生成 P1 演示 transcript：用 FakeProvider 确定性回放一次完整写作 loop。

无需任何 API key 即可复现 Agent 的完整决策链与落盘结果，作为参赛演示素材与
"AI 实践佐证"。产物：dev-notes/v0.1/artifacts/p1-demo-transcript.md

用法：
    uv run python scripts/render_demo_transcript.py
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import tempfile
from pathlib import Path
from typing import Any

from hastings.bible.store import StoryWorkspace
from hastings.cli import PlainRenderer, build_harness, create_writing_tools
from tau_agent import AssistantMessage, ToolCall
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallEndEvent,
)
from tau_ai import FakeProvider

OUTPUT = Path(__file__).resolve().parents[1] / "dev-notes" / "artifacts" / "p1-demo-transcript.md"

PROMPT = "建立一个 1930 年代封闭列车上的谋杀案，先建两张角色卡，再写第一章。"
POIROT = {
    "name": "波洛",
    "identity": "比利时侦探",
    "secret": "曾因一桩未破案离职，办案动机是好奇而非正义",
}
CUBE = {
    "name": "奎勒",
    "identity": "东方快车乘务长",
    "motivation": "掩盖十二年前的旧案，掌握全列乘务动线",
}
CHAPTER_TITLE = "雪封之夜"
CHAPTER_BODY = (
    "雪从午夜开始落。到三点十七分，列车已经完全被封在白色里，前后两头的瞭望窗只剩下模糊的灰。\n\n"
    "波洛注意到车厢连接处的门是虚掩着的——列车在行驶中从不允许这件事。他把这个细节收进心里，"
    "像把一枚硬币放进背心口袋，不作声。\n\n"
    "十二号包厢的门从里面锁着，但门缝下没有灯光。死者是位美国商人，而行李架上，"
    "还放着一只属于别人的手套。\n\n"
    "“那位夫人说她在餐车喝过一杯酒，”乘务员奎勒的声音里有种不必要的确定，"
    "“我亲自送过去的。”波洛注意到他说的是“她”，而死者本人从未有过夫人同行。"
)


def _stream(*, call: ToolCall | None, text: str, reason: str) -> list[Any]:
    events: list[Any] = [AssistantStartEvent(partial=AssistantMessage(model="fake"))]
    if text:
        events.append(TextStartEvent(content_index=0, partial=AssistantMessage(model="fake")))
        events.append(
            TextDeltaEvent(
                content_index=0, delta=text, partial=AssistantMessage(content=text, model="fake")
            )
        )
        events.append(
            TextEndEvent(
                content_index=0, content=text, partial=AssistantMessage(content=text, model="fake")
            )
        )
    if call is not None:
        events.append(
            ToolCallEndEvent(
                content_index=1,
                tool_call=call,
                partial=AssistantMessage(content=[call], model="fake"),
            )
        )
    message: AssistantMessage = (
        AssistantMessage(content=[call], stop_reason="toolUse")
        if call is not None
        else AssistantMessage(content=text, stop_reason="stop")
    )
    events.append(AssistantDoneEvent(reason=reason, message=message))
    return events


def _scripted_provider() -> FakeProvider:
    upsert_poirot = ToolCall(
        id="call-1", name="bible_upsert", arguments={"entity_type": "character", "data": POIROT}
    )
    upsert_cube = ToolCall(
        id="call-2", name="bible_upsert", arguments={"entity_type": "character", "data": CUBE}
    )
    query = ToolCall(id="call-3", name="bible_query", arguments={"entity_type": "character"})
    write = ToolCall(
        id="call-4",
        name="write_chapter",
        arguments={"chapter": 1, "title": CHAPTER_TITLE, "content": CHAPTER_BODY},
    )
    closing = (
        "案卷已开卷：波洛与奎勒已建档，第一章《雪封之夜》已落盘。"
        "下一章动笔前，我会先把凶手的不在场证明写进账本，避免第九章才补漏洞。"
    )
    return FakeProvider(
        [
            _stream(call=upsert_poirot, text="先建第一张角色卡：波洛。", reason="toolUse"),
            _stream(call=upsert_cube, text="再建第二张：乘务长奎勒。", reason="toolUse"),
            _stream(call=query, text="动笔前核对一下账本。", reason="toolUse"),
            _stream(call=write, text="账本没问题，开写第一章。", reason="toolUse"),
            _stream(call=None, text=closing, reason="stop"),
        ]
    )


async def _run(workspace: StoryWorkspace) -> str:
    harness = build_harness(workspace, _scripted_provider(), "fake-replay")
    tools = create_writing_tools(workspace)
    renderer = PlainRenderer({tool.name: tool for tool in tools})
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        print(f"> {PROMPT}\n")
        async for event in harness.prompt(PROMPT):
            renderer.handle(event)
    return buffer.getvalue()


def _file_block(path: Path) -> str:
    return (
        "```"
        + ("yaml" if path.suffix == ".yaml" else "markdown")
        + "\n"
        + path.read_text(encoding="utf-8").rstrip()
        + "\n```"
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hastings-demo-") as tmp:
        workspace = StoryWorkspace(root=Path(tmp), slug="orient-express")
        transcript = asyncio.run(_run(workspace))

        characters = sorted(workspace.characters_dir.glob("*.yaml"))
        chapter = workspace.chapters_dir / "ch01.md"
        tree = [f.relative_to(workspace.story_dir).as_posix() for f in [*characters, chapter]]

        parts = [
            "# P1 演示 transcript · 案卷 Hastings",
            "",
            "> 确定性回放：FakeProvider 预录事件流，无需 API key，"
            "任何人执行 `uv run python scripts/render_demo_transcript.py` 都能得到逐字相同的结果。",
            "",
            "## 1. 用户输入",
            "",
            f"> {PROMPT}",
            "",
            "## 2. Agent 决策链（终端渲染，CLI 真实渲染器输出）",
            "",
            "```text",
            transcript.rstrip(),
            "```",
            "",
            "## 3. 落盘的工作区结构",
            "",
            "```text",
            f"{workspace.slug}/",
            *[
                f"└── {name}" if i == len(tree) - 1 else f"├── {name}"
                for i, name in enumerate(tree)
            ],
            "```",
            "",
        ]

        parts += ["## 4. 落盘文件真实内容", ""]
        for path in [*characters, chapter]:
            parts += [
                f"### `{path.relative_to(workspace.story_dir).as_posix()}`",
                "",
                _file_block(path),
                "",
            ]

        parts += [
            "## 5. 这份 transcript 证明了什么",
            "",
            "| 主张 | 证据 |",
            "|---|---|",
            "| 模型不能绕过工具写文件 | 全部经 `bible_upsert` / `write_chapter` 落盘 |",
            "| 动笔前先核对账本 | 剧本第三步是 `bible_query`，而非直接开写 |",
            "| 格式一致性由工具保证 | 文件名为 `ch01.md`，标题由工具生成 |",
            "| Agent 行为可确定性测试 | 同一份 FakeProvider 脚本可逐字复现 |",
            "",
        ]

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text("\n".join(parts), encoding="utf-8")

    print(f"已生成 {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
