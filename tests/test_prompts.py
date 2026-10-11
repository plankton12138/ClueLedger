"""System prompt 组装测试（T4）+ 对账消息模板测试（P5 T4）。

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
    TimelineEntry,
)
from hastings.bible.stats import build_workspace_status
from hastings.bible.store import StoryWorkspace, save_character, save_ledger, save_story_doc
from hastings.bible.story_state import StoryDoc
from hastings.manuscript.diff import DiffHunk, HunkKind
from hastings.prompts import (
    CONFIRM_MARKER,
    LANGUAGE_HEADING,
    OPTIONS_MARKER,
    ROLE_HEADING,
    STATUS_HEADING,
    TOOLS_HEADING,
    WORKFLOW_HEADING,
    WORKFLOW_SECTION,
    build_language_section,
    build_reconcile_message,
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


def test_workflow_states_intake_protocol() -> None:
    """intake 协议：三段式理解输出，禁止未确认先建账/写正文，每轮 1–2 问。"""
    section = WORKFLOW_SECTION

    assert "intake" in section
    assert "五维理解摘要" in section
    for dimension in ("核心诡计", "人物群像", "结构", "结局", "文风"):
        assert dimension in section
    assert "缺口清单" in section
    assert "1–2 个" in section  # 每轮只问 1–2 个
    assert "多轮对话" in section  # 其余缺口多轮补齐
    assert "5 个" not in section
    assert "禁止" in section
    assert "等作者答复" in section
    # 想法有限 → 候选方案走【选项】标记协议
    assert "候选方案" in section
    assert OPTIONS_MARKER in section
    assert "候选方案输出协议" in section


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
    assert "Bible 总览" in section
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


# --- 作者确认标记协议（P10 T10，D9）：尾部确认块 + 格式 + 适用场合 ---


def test_confirm_marker_constant() -> None:
    """确认标记常量：前后端靠同一文本锚点识别。"""
    assert CONFIRM_MARKER == "【确认】"


def test_workflow_confirm_protocol_specifies_trailing_block_and_fields() -> None:
    """确认块必须在消息最末尾，含待确认/确认后/暂不三字段。"""
    section = WORKFLOW_SECTION

    assert "作者确认输出协议" in section
    assert "最末尾" in section
    assert "待确认：" in section
    assert "确认后：" in section
    assert "暂不：" in section


def test_workflow_confirm_protocol_states_when_required() -> None:
    """适用场合明确：Bible 建账完成后、推进阶段之前；硬约束仍归 story_stage。"""
    section = WORKFLOW_SECTION

    assert "Bible 建账完成后" in section
    assert "story_stage 工具把门" in section


def test_workflow_states_bible_drafting_protocol() -> None:
    """bible_drafting 协议：建账顺序固定 + 总览确认三类点 + 确认后 story_stage 推进。"""
    section = WORKFLOW_SECTION

    assert "建账顺序固定" in section
    assert "人物 → 手法 → 线索与伏笔 → 时间线 → 章纲" in section
    assert "Bible 总览" in section
    assert "三类点" in section
    assert "story_stage" in section
    assert "未经确认" in section


def test_workflow_states_writing_protocol_anchors_outline_reading() -> None:
    """writing 协议：动笔前读章纲与设定，写完报告落盘与迁移（原第 6 条保留）。"""
    section = WORKFLOW_SECTION

    assert "动笔前先读该章章纲" in section
    assert "简要报告" in section
    assert "落盘" in section


# --- 走查修复（F3/F5）：账本修改的阶段语义 + intake 缺口锚点 ---


def test_workflow_states_ledger_edits_allowed_in_every_stage() -> None:
    """F3（B4 走查）：bible_upsert 任何阶段可用——阶段门只限制写正文，不限制账本
    修改；写作/修订期更正设定直接改账本，不换阶段绕行。属协议解释，不动迁移表。
    """
    section = WORKFLOW_SECTION

    assert "阶段门只限制" in section
    assert "bible_upsert" in section
    assert "不需要换阶段" in section


def test_workflow_forbids_premature_ledger_writes_justified_by_gates() -> None:
    """F5（B1 走查）：后续阶段的门约束不构成本阶段提前落账的理由。"""
    section = WORKFLOW_SECTION

    assert "提前落账" in section


def test_intake_gap_anchors_include_character_backstory() -> None:
    """F5（B1 走查）：intake 缺口清单锚点补「人物身世与往事」维度。"""
    section = WORKFLOW_SECTION

    assert "人物身世与往事" in section


def test_workflow_keeps_reconcile_protocol_in_every_stage() -> None:
    """对账协议原样保留，且作用于任何阶段。"""
    assert "【手动修订同步】" in WORKFLOW_SECTION
    assert "对账" in WORKFLOW_SECTION
    assert "任何阶段" in WORKFLOW_SECTION


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
