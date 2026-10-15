"""System prompt 组装测试（T4）。V1 弃用后工作流节为只读顾问语义。

断言三节结构 + 工具 guidelines 全量拼接；无工具时不产生空标题节。
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from hastings.bible.models import (
    Character,
    Foreshadow,
    Method,
    OutlineEntry,
)
from hastings.bible.stats import build_workspace_status
from hastings.bible.store import StoryWorkspace, save_character, save_ledger, save_story_doc
from hastings.bible.story_state import StoryDoc
from hastings.prompts import (
    LANGUAGE_HEADING,
    OPTIONS_MARKER,
    ROLE_HEADING,
    STATUS_HEADING,
    TOOLS_HEADING,
    WORKFLOW_HEADING,
    WORKFLOW_SECTION,
    build_language_section,
    build_system_prompt,
    refresh_system_prompt,
)
from hastings.settings import DEFAULT_WRITING_LANGUAGE
from tau_agent import AgentHarness, AgentHarnessConfig, AgentTool, AgentToolResult, AssistantMessage
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent
from tau_agent.types import JSONValue
from tau_ai import FakeProvider


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
        _tool("bible_query", "动笔前先 bible_query 核对既有设定，避免正文与 Bible 矛盾。"),
        _tool("read_chapter", "核对时间线细节前使用 read_chapter。"),
    ]

    prompt = build_system_prompt(tools)

    assert TOOLS_HEADING in prompt
    for tool in tools:
        for guideline in tool.prompt_guidelines:
            assert guideline in prompt


def test_system_prompt_is_deterministic() -> None:
    tools = [_tool("bible_query", "动笔前先 bible_query 核对既有设定。")]

    assert build_system_prompt(tools) == build_system_prompt(tools)


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


# --- 状态注入节（P8 T5，D5）：只陈述事实（阶段/计数/缺口），不解释怎么做事 ---


def _seed_writing_workspace(tmp_path: Path) -> StoryWorkspace:
    """写作中工作区快照：2 角色 / 1 已锁手法 / 2 章纲 / 1 逾期伏笔 / 1 章正文。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    workspace.ensure_dirs()
    save_character(workspace, Character(name="波洛", identity="侦探"))
    save_character(workspace, Character(name="黑斯廷斯", identity="叙事者"))
    save_ledger(workspace.method_path, [Method(name="毒杀诡计", status="locked")])
    save_ledger(
        workspace.outline_path,
        [OutlineEntry(name="案发之夜", chapter=1), OutlineEntry(name="封路", chapter=2)],
    )
    save_ledger(
        workspace.foreshadows_path,
        [Foreshadow(name="保温瓶", expected_resolve_chapter="ch01", status="planted")],
    )
    (workspace.chapters_dir / "ch01.md").write_text("# 第1章\n\n正文。\n", encoding="utf-8")
    save_story_doc(workspace, StoryDoc(slug="demo", stage="writing"))
    return workspace


def test_status_body_empty_workspace_reports_intake_and_zeros(tmp_path: Path) -> None:
    """空工作区（旧数据/新开工）按 intake 兜底，计数全零是合法快照。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")

    body = build_workspace_status(workspace)

    assert body is not None
    assert "阶段：intake" in body
    assert "角色 0" in body
    assert "伏笔 0（逾期 0）" in body
    assert "手法 0" in body
    assert "章纲 0 条" in body
    assert "已写章节：0" in body
    assert "章纲未覆盖：全部未建" in body


def test_status_body_writing_workspace_reports_counts_and_gap(tmp_path: Path) -> None:
    """写作中快照：阶段 + 各账本计数 + 逾期数 + 章纲缺口（下一条未建章号）。"""
    workspace = _seed_writing_workspace(tmp_path)

    body = build_workspace_status(workspace)

    assert body is not None
    assert "阶段：writing" in body
    assert "角色 2" in body
    assert "伏笔 1（逾期 1）" in body
    assert "手法 1（已锁定）" in body
    assert "章纲 2 条" in body
    assert "已写章节：1" in body
    assert "章纲未覆盖：ch3 起未建" in body


def test_status_body_flags_unlocked_method(tmp_path: Path) -> None:
    """手法未锁定要显式标出——这是模型该先 method_lock 的事实信号。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    workspace.ensure_dirs()
    save_ledger(workspace.method_path, [Method(name="毒杀诡计", status="drafted")])

    body = build_workspace_status(workspace)

    assert "手法 1（未锁定）" in body


def test_status_body_degrades_on_corrupt_ledger_with_stderr(tmp_path: Path, capsys: Any) -> None:
    """泄压阀（P5 OSError 教训）：账本损坏 → 返回 None + stderr 三要素，不抛异常。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    workspace.bible_dir.mkdir(parents=True)
    workspace.method_path.write_text("::: 不是列表 :::\n", encoding="utf-8")

    assert build_workspace_status(workspace) is None
    err = capsys.readouterr().err
    assert "状态" in err
    assert "为什么错" in err
    assert "怎样算对" in err


def test_system_prompt_orders_status_between_language_and_workflow(tmp_path: Path) -> None:
    """注入节位置（D5）：语言节之后、工作流程之前——先事实后协议。"""
    workspace = _seed_writing_workspace(tmp_path)

    prompt = build_system_prompt(workspace=workspace)

    assert STATUS_HEADING in prompt
    assert prompt.index(LANGUAGE_HEADING) < prompt.index(STATUS_HEADING)
    assert prompt.index(STATUS_HEADING) < prompt.index(WORKFLOW_HEADING)


def test_system_prompt_without_workspace_has_no_status_section() -> None:
    """不传 workspace 不注入——既有调用点（CLI/测试）零破坏。"""
    prompt = build_system_prompt()

    assert STATUS_HEADING not in prompt


# --- 阶段感知工作流程（P8 T6，D6）：prompt 讲协议，门在工具层 ---


def test_workflow_states_read_only_semantics() -> None:
    """V1 弃用后：工作流节宣告只读能力与写入口指向 V2。"""
    section = WORKFLOW_SECTION

    assert "只读" in section
    assert "bible_query" in section
    assert "read_chapter" in section
    assert "timeline_check" in section
    assert "没有任何写工具" in section
    assert "V2 仓库项目" in section


# --- 对话详略协议（P9 T1，D2）：结论先行/分条/≤300 字，交付物不受限 ---


def test_workflow_states_dialogue_style_protocol() -> None:
    """对话风格节：答疑汇报结论先行、分条、默认 ≤300 字。"""
    section = WORKFLOW_SECTION

    assert "对话风格" in section
    assert "结论先行" in section
    assert "300 字" in section


def test_workflow_exempts_structured_deliverables_from_word_limit() -> None:
    """结构化交付物不受字数限制，但仍须分条有重点。"""
    section = WORKFLOW_SECTION

    assert "结构化交付物" in section
    assert "不受 300 字限制" in section
    assert "设定总览" in section
    assert "缺口清单" in section


# --- 候选方案标记协议（P9 T1，D3）：尾部标记块 + 格式 + few-shot ---


def test_options_marker_constant() -> None:
    """协议标记常量：前后端靠同一文本锚点识别。"""
    assert OPTIONS_MARKER == "【选项】"


def test_workflow_options_protocol_specifies_trailing_block_and_format() -> None:
    """选项块必须在消息最末尾，选项键 A./B.，取舍说明用 —— 分隔。"""
    section = WORKFLOW_SECTION

    assert "最末尾" in section
    assert "A. <方案名> —— " in section
    assert "B. <方案名> —— " in section
    assert "连续大写字母" in section


def test_workflow_options_protocol_includes_few_shot_example() -> None:
    """few-shot 样例钉死格式（毒杀/钝器两选项）。"""
    section = WORKFLOW_SECTION

    assert "格式样例" in section
    assert "A. 毒杀 —— " in section
    assert "B. 钝器击打 —— " in section


def test_workflow_options_protocol_single_decision_point() -> None:
    """D5 收敛：默认一次只问一个决策点，多维度留待后续轮次。"""
    section = WORKFLOW_SECTION

    assert "一次只问一个决策点" in section
    assert "选影响最大的一个先问" in section


def test_workflow_options_protocol_rejects_format_variants() -> None:
    """D5 反例：标记带标题前缀 / 块内穿插维度标题行 / 全角冒号分隔均渲染失败。"""
    section = WORKFLOW_SECTION

    assert "### 【选项】" in section  # 标题前缀反例
    assert "**章纲划分**" in section  # 块内维度标题行反例
    assert "全角冒号" in section


def test_workflow_options_protocol_allows_dimension_blocks() -> None:
    """D10：允许多维度的【选项：<维度>】块，每块键各自从 A 起、连着排在末尾。"""
    section = WORKFLOW_SECTION

    assert "【选项：<维度>】" in section
    assert "每维度单独成块" in section
    assert "各自从 A 起" in section


# --- 刷新点共享（P10 T2，D6/L7）：三端单一事实源 ---


def _stop_turn(text: str) -> list[Any]:
    message = AssistantMessage(content=text, stop_reason="stop")
    return [
        AssistantStartEvent(partial=AssistantMessage(model="fake")),
        AssistantDoneEvent(reason="stop", message=message),
    ]


def test_refresh_system_prompt_rebuilds_with_latest_status(tmp_path: Path) -> None:
    """直接调用即把工作区实况写回 config.system（状态注入唯一刷新点）。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider([_stop_turn("ok")]),
            model="fake",
            system=build_system_prompt(workspace=workspace),
            tools=[],
        )
    )
    assert "角色 0" in harness.config.system

    save_character(workspace, Character(name="波洛", identity="侦探"))
    save_character(workspace, Character(name="黑斯廷斯", identity="叙事者"))
    save_story_doc(workspace, StoryDoc(slug="demo", stage="writing"))

    refresh_system_prompt(harness, workspace)

    assert "角色 2" in harness.config.system
    assert "阶段：writing" in harness.config.system


@pytest.mark.anyio
async def test_refresh_injects_latest_status_into_second_round_request(tmp_path: Path) -> None:
    """两轮对话：轮间建账 + refresh → 第二轮请求的 system 含更新后的计数。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    provider = FakeProvider([_stop_turn("第一轮"), _stop_turn("第二轮")])
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system=build_system_prompt(workspace=workspace),
            tools=[],
        )
    )

    async for _ in harness.prompt("第一轮提问"):
        pass
    # 轮间建账（模拟工具落盘）+ 刷新
    save_character(workspace, Character(name="波洛", identity="侦探"))
    save_character(workspace, Character(name="黑斯廷斯", identity="叙事者"))
    refresh_system_prompt(harness, workspace)
    async for _ in harness.prompt("第二轮提问"):
        pass

    assert len(provider.calls) == 2
    first_system = provider.calls[0][1]
    second_system = provider.calls[1][1]
    assert "角色 2" not in first_system  # 建账前
    assert "角色 2" in second_system  # 建账后刷新生效
