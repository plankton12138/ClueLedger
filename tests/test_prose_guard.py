"""正文直出泄压阀测试（P10 T3，D2/L1；2026-10-12 阶段解耦）。

三层：① ProseLeakGuard 判定（触发 / 同回合一次 / AgentStartEvent 重置 /
任意阶段触发 / 短文本不触发 / 标题正则变体含整行粗体）；② attach_guards 装配后
经 FakeProvider 事件流端到端注入纠偏消息；③ warn_empty_chapters 观测信号。
"""

from pathlib import Path
from typing import Any

import pytest

from hastings.bible.store import StoryWorkspace, save_story_doc
from hastings.bible.story_state import StoryDoc
from hastings.manuscript.guards import attach_guards
from hastings.manuscript.prose_guard import (
    CORRECTION,
    ProseLeakGuard,
    warn_empty_chapters,
)
from tau_agent import AgentHarness, AgentHarnessConfig, AssistantMessage
from tau_agent.events import AgentStartEvent, MessageEndEvent
from tau_agent.messages import UserMessage
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent
from tau_ai import FakeProvider

_BODY = "夜色里雨声不断。" * 120  # 非空白字符 960 > 阈值
_LONG = f"# 第1章 案发之夜\n\n{_BODY}"


def _harness() -> AgentHarness:
    return AgentHarness(
        AgentHarnessConfig(provider=FakeProvider([]), model="fake", system="", tools=[])
    )


def _end(text: str) -> MessageEndEvent:
    return MessageEndEvent(message=AssistantMessage(model="fake", content=text, stop_reason="stop"))


def _workspace(tmp_path: Path, stage: str) -> StoryWorkspace:
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    save_story_doc(workspace, StoryDoc(slug="demo", stage=stage))
    return workspace


# ---------- ① 判定 ----------


def test_triggers_and_steers_in_writing_stage(tmp_path: Path, capsys: Any) -> None:
    workspace = _workspace(tmp_path, "writing")
    harness = _harness()
    guard = ProseLeakGuard(workspace, harness)

    guard.on_event(AgentStartEvent())
    guard.on_event(_end(_LONG))

    steered = harness.queued_messages.steering
    assert len(steered) == 1
    assert steered[0].text == CORRECTION
    err = capsys.readouterr().err
    assert "为什么错" in err and "怎样算对" in err  # stderr 三要素告警


def test_fires_at_most_once_per_turn(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "writing")
    harness = _harness()
    guard = ProseLeakGuard(workspace, harness)

    guard.on_event(AgentStartEvent())
    guard.on_event(_end(_LONG))
    guard.on_event(_end(_LONG))

    assert len(harness.queued_messages.steering) == 1


def test_agent_start_resets_per_turn_flag(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "writing")
    harness = _harness()
    guard = ProseLeakGuard(workspace, harness)

    guard.on_event(AgentStartEvent())
    guard.on_event(_end(_LONG))
    guard.on_event(AgentStartEvent())  # 新一轮 prompt
    guard.on_event(_end(_LONG))

    assert len(harness.queued_messages.steering) == 2


@pytest.mark.parametrize(
    "stage", ["intake", "bible_drafting", "bible_confirmed", "writing", "revising", "complete"]
)
def test_triggers_in_every_stage(tmp_path: Path, stage: str) -> None:
    """正文交付不变量在各阶段都成立——正文直出在 intake 抢跑时同样触发。"""
    workspace = _workspace(tmp_path, stage)
    harness = _harness()
    guard = ProseLeakGuard(workspace, harness)

    guard.on_event(AgentStartEvent())
    guard.on_event(_end(_LONG))

    assert len(harness.queued_messages.steering) == 1


def test_does_not_trigger_on_short_text(tmp_path: Path) -> None:
    harness = _harness()
    guard = ProseLeakGuard(_workspace(tmp_path, "writing"), harness)

    guard.on_event(AgentStartEvent())
    guard.on_event(_end("# 第1章\n\n短。"))

    assert harness.queued_messages.steering == ()


def test_does_not_trigger_without_chapter_title(tmp_path: Path) -> None:
    harness = _harness()
    guard = ProseLeakGuard(_workspace(tmp_path, "writing"), harness)

    guard.on_event(AgentStartEvent())
    guard.on_event(_end(_BODY))  # 长但无章节标题

    assert harness.queued_messages.steering == ()


@pytest.mark.parametrize(
    "title",
    ["# 第1章", "## 第12章", "# ch01", "### ch3", "# 《雾雨山庄》", "**暴风雨夜的铜刀**"],
)
def test_title_regex_variants_trigger(tmp_path: Path, title: str) -> None:
    harness = _harness()
    guard = ProseLeakGuard(_workspace(tmp_path, "writing"), harness)

    guard.on_event(AgentStartEvent())
    guard.on_event(_end(f"{title}\n\n{_BODY}"))

    assert len(harness.queued_messages.steering) == 1


def test_bold_inline_label_is_not_a_title(tmp_path: Path) -> None:
    """整行粗体才算标题：``**手法定稿**：…``（粗体后有正文）不触发。"""
    harness = _harness()
    guard = ProseLeakGuard(_workspace(tmp_path, "writing"), harness)

    guard.on_event(AgentStartEvent())
    guard.on_event(_end(f"**手法定稿**：铜刀作为凶器。\n\n{_BODY}"))

    assert harness.queued_messages.steering == ()


def test_medium_length_prose_triggers_at_lowered_threshold(tmp_path: Path) -> None:
    """阈值下调到 400：C1 压测实际漏报的 ~505 字正文应被捕获。"""
    harness = _harness()
    guard = ProseLeakGuard(_workspace(tmp_path, "intake"), harness)

    guard.on_event(AgentStartEvent())
    guard.on_event(_end(f"**暴风雨夜的铜刀**\n\n{'雨声敲窗。' * 100}"))

    assert len(harness.queued_messages.steering) == 1


def test_char_threshold_is_configured(tmp_path: Path) -> None:
    """阈值是策略常量，可注入调低——便于测试与未来调整。"""
    harness = _harness()
    guard = ProseLeakGuard(_workspace(tmp_path, "writing"), harness, min_chars=5)

    guard.on_event(AgentStartEvent())
    guard.on_event(_end("# 第1章\n\n短正文。"))  # 非空白字符 8 > 5 触发

    assert len(harness.queued_messages.steering) == 1


# ---------- ② attach_guards 装配（经 FakeProvider 事件流端到端） ----------


def _stop_turn(text: str) -> list[Any]:
    return [
        AssistantStartEvent(partial=AssistantMessage(model="fake")),
        AssistantDoneEvent(
            reason="stop", message=AssistantMessage(model="fake", content=text, stop_reason="stop")
        ),
    ]


@pytest.mark.anyio
async def test_attach_guards_wires_prose_guard_into_event_flow(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "writing")
    provider = FakeProvider([_stop_turn(_LONG), _stop_turn("好的，我改用 write_chapter 落盘。")])
    harness = AgentHarness(AgentHarnessConfig(provider=provider, model="fake", system="", tools=[]))
    attach_guards(harness, workspace)

    _ = [event async for event in harness.prompt("写第一章")]

    # 纠偏消息在纯文本轮被 loop 排空（loop.py L174）并注入下一轮：第二轮的
    # messages 应含它——这是「纠偏已进入模型上下文」的端到端证据。
    assert len(provider.calls) == 2
    second_messages = provider.calls[1][2]
    corrections = [
        m for m in second_messages if isinstance(m, UserMessage) and m.text == CORRECTION
    ]
    assert len(corrections) == 1


# ---------- ③ 观测信号 ----------


def test_warn_empty_chapters_in_writing_with_empty_dir(tmp_path: Path, capsys: Any) -> None:
    warn_empty_chapters(_workspace(tmp_path, "writing"))

    err = capsys.readouterr().err
    assert "chapters/" in err
    assert "为什么错" in err and "怎样算对" in err


def test_warn_empty_chapters_silent_when_chapter_exists(tmp_path: Path, capsys: Any) -> None:
    workspace = _workspace(tmp_path, "writing")
    workspace.ensure_dirs()
    (workspace.chapters_dir / "ch01.md").write_text("# 第1章\n", encoding="utf-8")

    warn_empty_chapters(workspace)

    assert capsys.readouterr().err == ""


def test_warn_empty_chapters_silent_outside_writing_stage(tmp_path: Path, capsys: Any) -> None:
    warn_empty_chapters(_workspace(tmp_path, "bible_drafting"))

    assert capsys.readouterr().err == ""
