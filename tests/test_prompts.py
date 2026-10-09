"""System prompt 组装测试（T4）+ 对账消息模板测试（P5 T4）。

断言三节结构 + 工具 guidelines 全量拼接；无工具时不产生空标题节。
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hastings.bible.models import Character, TimelineEntry
from hastings.bible.store import StoryWorkspace, save_character, save_ledger
from hastings.manuscript.diff import DiffHunk, HunkKind
from hastings.prompts import (
    LANGUAGE_HEADING,
    ROLE_HEADING,
    TOOLS_HEADING,
    WORKFLOW_HEADING,
    build_language_section,
    build_reconcile_message,
    build_system_prompt,
)
from hastings.settings import DEFAULT_WRITING_LANGUAGE
from tau_agent import AgentTool, AgentToolResult
from tau_agent.types import JSONValue


def _tool(name: str, *guidelines: str) -> AgentTool:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: Any = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        return AgentToolResult(content="ok")

    return AgentTool(
        name=name,
        label=name,
        description=f"{name} 描述",
        parameters={"type": "object", "properties": {}},
        execute_fn=execute,
        prompt_guidelines=guidelines,
    )


def test_system_prompt_contains_role_and_workflow_sections() -> None:
    prompt = build_system_prompt()

    assert ROLE_HEADING in prompt
    assert WORKFLOW_HEADING in prompt
    # 无工具时不出现空的工具节
    assert TOOLS_HEADING not in prompt


def test_system_prompt_mentions_fair_play_rule() -> None:
    prompt = build_system_prompt()

    assert "fair play" in prompt.lower()
    assert "Bible" in prompt


def test_system_prompt_appends_every_tool_guideline() -> None:
    tools = [
        _tool("bible_upsert", "任何角色设定必须先经 bible_upsert 落盘，再写进正文。"),
        _tool("write_chapter", "写章节前，确认出场角色都已用 bible_upsert 落盘。"),
    ]

    prompt = build_system_prompt(tools)

    assert TOOLS_HEADING in prompt
    for tool in tools:
        for guideline in tool.prompt_guidelines:
            assert guideline in prompt


def test_system_prompt_is_deterministic() -> None:
    tools = [_tool("bible_query", "动笔前先 bible_query 核对既有设定。")]

    assert build_system_prompt(tools) == build_system_prompt(tools)


def test_system_prompt_states_transition_and_lock_semantics() -> None:
    """工作流程节（T7）：状态沿合法迁移边经工具变更 + 锁定/解锁一句话版。

    细节语义在各工具的 prompt_guidelines 里，这里只做一句话提醒，不重复维护。
    """
    prompt = build_system_prompt()

    assert "合法迁移边" in prompt
    assert "method_lock" in prompt
    assert "method_unlock" in prompt


def test_system_prompt_explains_reconcile_protocol() -> None:
    """工作流程节（P5 D6）：【手动修订同步】开头的消息按对账协议处理。"""
    prompt = build_system_prompt()

    assert "【手动修订同步】" in prompt
    assert "对账" in prompt


# --- 写作语言节（P6，D2）：语言注入 + 默认中文不破既有断言 ---


def test_system_prompt_defaults_to_chinese_language_section() -> None:
    """缺省 language 走中文——既有调用不破，正文不会再顺着设定写成日语。"""
    prompt = build_system_prompt()

    assert LANGUAGE_HEADING in prompt
    assert DEFAULT_WRITING_LANGUAGE in prompt


def test_language_section_orders_after_role_before_workflow() -> None:
    prompt = build_system_prompt()

    assert prompt.index(ROLE_HEADING) < prompt.index(LANGUAGE_HEADING)
    assert prompt.index(LANGUAGE_HEADING) < prompt.index(WORKFLOW_HEADING)


def test_language_section_covers_all_outputs_and_decouples_setting() -> None:
    """文案覆盖全部产出，并切断「设定在他国 ⇒ 用当地语言」的错误推断。"""
    section = build_language_section("English")

    assert "English" in section
    assert "Story Bible" in section
    assert "章节正文" in section
    assert "对话回复" in section
    assert "背景" in section  # 背景可设在任何国家
    assert "叙事语言" in section


def test_system_prompt_accepts_language_override() -> None:
    prompt = build_system_prompt(language="日本語")

    assert "日本語" in prompt
    assert DEFAULT_WRITING_LANGUAGE not in prompt


# --- 对账消息模板（P5 T4，D5/D6） ---


def _hunk(
    kind: HunkKind,
    old: str,
    new: str,
    old_index: int | None,
    new_index: int | None,
) -> DiffHunk:
    return DiffHunk(kind=kind, old_index=old_index, new_index=new_index, old_text=old, new_text=new)


def test_reconcile_message_starts_with_marker_and_role(tmp_path: Path) -> None:
    """协议标记开头（D6 识别锚点）+ 风格观察者/台账校对角色校准为模板首句。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    message = build_reconcile_message(workspace, "ch03", [_hunk("modified", "旧段", "新段", 1, 1)])

    assert message.startswith("【手动修订同步】第3章已被作者直接修改。")
    assert "风格观察者" in message
    assert "习得作者本人的" in message
    assert "不调用任何写正文的工具" in message


def test_reconcile_message_puts_style_learning_first(tmp_path: Path) -> None:
    """对账定位（P5 T7 用户澄清）：首要习得作者文风，其次校正账本。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    message = build_reconcile_message(workspace, "ch03", [_hunk("modified", "旧段", "新段", 1, 1)])

    assert "文风取向" in message
    assert "风格参照" in message
    assert "风格要点" in message
    assert "校正受影响的账本条目" in message


def test_reconcile_message_lists_hunk_previews(tmp_path: Path) -> None:
    """每段一行：[类别] 段号 + 预览；removed 用旧文本做预览。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    hunks = [
        _hunk("modified", "火车在九点四十七分抵达。", "火车在十点零七分抵达。", 1, 1),
        _hunk("removed", "被删掉的段落。", "", 2, None),
        _hunk("added", "", "新增的结尾。", None, 3),
    ]
    message = build_reconcile_message(workspace, "ch03", hunks)

    assert "[修改] 段1：火车在十点零七分抵达。" in message
    assert "[删除] 段2：被删掉的段落。" in message
    assert "[新增] 段3：新增的结尾。" in message


def test_reconcile_message_truncates_beyond_max_hunks(tmp_path: Path) -> None:
    """hunk 上限 20 段：超出截断并注明（全文用 read_chapter 自取）。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    hunks = [_hunk("modified", f"旧{i}", f"新{i}", i, i) for i in range(25)]

    message = build_reconcile_message(workspace, "ch03", hunks)

    assert message.count("[修改]") == 20
    assert "共 25 处变更，仅列出前 20 处" in message


def test_reconcile_message_matches_entities_by_name(tmp_path: Path) -> None:
    """相关账本条目：变更段文本命中实体名才附上（token 经济学）。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    save_character(workspace, Character(name="波洛", identity="侦探"))
    save_ledger(
        workspace.timeline_path,
        [TimelineEntry(name="列车晚点", day=1)],
    )
    hunks = [
        _hunk("modified", "波洛皱眉。", "波洛看表，想起列车晚点的事。", 1, 1),
        _hunk("added", "", "与案件无关的景物描写。", None, 2),
    ]

    message = build_reconcile_message(workspace, "ch03", hunks)

    assert "相关账本条目" in message
    assert "角色：波洛" in message
    assert "时间线：列车晚点" in message


def test_reconcile_message_degrades_when_ledger_unreadable(tmp_path: Path) -> None:
    """账本损坏不阻断对账：跳过匹配并注明（不静默吞）。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    workspace.bible_dir.mkdir(parents=True)
    workspace.timeline_path.write_text("::: 不是列表 :::\n", encoding="utf-8")

    message = build_reconcile_message(workspace, "ch03", [_hunk("modified", "旧", "新", 1, 1)])

    assert "账本条目读取失败" in message
