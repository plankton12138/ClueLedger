"""正文直出泄压阀（P10 T3，计划 D2/L1）：检测章节正文经对话直出并纠偏。

弱模型压测暴露 L1——写作阶段模型把章节正文直接写在回复里，未经
``write_chapter`` 落盘，harness 臂因此 0 章成稿。三层防线，本模块是第 2/3 层：

- 第 1 层（软，prompts.WORKFLOW_SECTION）：明示「正文唯一交付通道是
  write_chapter」；
- 第 2 层（本模块 ``ProseLeakGuard``，半硬）：订阅 ``MessageEndEvent``，
  **任意阶段**的 assistant 长文本（非空白字符 > 阈值）命中章节标题特征时，
  ``harness.steer`` 注入纠偏消息 + stderr 三要素告警；同回合至多一次
  （``AgentStartEvent`` 重置）——纯文本收尾时纠偏消息留队下一轮生效（可接受）。
  不按阶段门控：弱模型压测（C1-qwen3-32k，2026-10-12）里正文直出发生在
  intake 抢跑（模型始终未推进到 writing，故 writing 门控永不生效），
  正文交付不变量（唯一通道 write_chapter）在各阶段都成立；
- 第 3 层（观测，``warn_empty_chapters``）：CLI/driver 在 prompt 循环结束处
  检查 writing/revising 且 chapters/ 为空则 stderr 提示，不阻断退出码。

阈值与正则是策略不是约束（模块常量便于测试调整）。误报成本 = 一条可忽略的
提醒，漏报成本 = 空稿，故阈值取宽松值（400 字）偏向捕获，标题特征兼容弱模型
的 ``**粗体**`` 单行标题。纠偏消息带逃生门（「如为讨论性文字请忽略」），
防道歉-重试循环（四透镜③）。
"""

from __future__ import annotations

import re
import sys
from typing import Final

from hastings.bible.store import StoryWorkspace, load_story_doc
from tau_agent.events import AgentEvent, AgentStartEvent, MessageEndEvent
from tau_agent.harness import AgentHarness
from tau_agent.messages import AssistantMessage

PROSE_MIN_CHARS: Final = 400
_WRITING_STAGES: Final = ("writing", "revising")

# 章节标题特征：Markdown 标题 + 「第 N 章 / chNN」编号，书名标题《…》，
# 或整行粗体标题（``**标题**`` 独占一行）——弱模型常用后者排版。
_CHAPTER_TITLE_RE: Final = re.compile(
    r"^#{1,3}\s*(?:ch\d+|第[一二三四五六七八九十百\d]+章)"
    r"|^#\s*《.+》"
    r"|^\*\*[^*\n]+?\*\*\s*$",
    re.MULTILINE,
)

# 纠偏消息（steering 注入；逃生门防道歉-重试循环）。
CORRECTION: Final = (
    "检测到你在回复中直接输出了章节正文。正文唯一交付通道是 write_chapter 工具，"
    "作者端不保存对话中的正文。请将刚才的内容（或按章纲重写）经 write_chapter 落盘；"
    "如刚才只是讨论性文字，请忽略本提醒并继续。"
)


def _non_whitespace_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _warn_stderr(stage: str, text: str) -> None:
    """三要素告警（哪里错 / 为什么错 / 怎样算对）——CLI 转录可见。"""
    print(
        f"[prose_guard] 检测到正文直出（{stage} 阶段）：回复含章节标题且正文 "
        f"{_non_whitespace_len(text)} 字，疑未经 write_chapter 落盘。"
        "为什么错：正文唯一交付通道是 write_chapter，对话中的正文作者端不保存。"
        "怎样算对：把该章经 write_chapter 落盘；若为讨论性文字可忽略本提醒。",
        file=sys.stderr,
    )


class ProseLeakGuard:
    """正文直出检测器（任意阶段）：AgentStartEvent 重置 + MessageEndEvent 判定。"""

    def __init__(
        self,
        workspace: StoryWorkspace,
        harness: AgentHarness,
        *,
        min_chars: int = PROSE_MIN_CHARS,
    ) -> None:
        self._workspace = workspace
        self._harness = harness
        self._min_chars = min_chars
        self._fired = False

    def on_event(self, event: AgentEvent) -> None:
        if isinstance(event, AgentStartEvent):
            self._fired = False
            return
        if self._fired or not isinstance(event, MessageEndEvent):
            return
        message = event.message
        if not isinstance(message, AssistantMessage):
            return
        text = message.text
        if _non_whitespace_len(text) <= self._min_chars:
            return
        if _CHAPTER_TITLE_RE.search(text) is None:
            return
        self._fired = True
        stage = load_story_doc(self._workspace).stage
        _warn_stderr(stage, text)
        self._harness.steer(CORRECTION)


def warn_empty_chapters(workspace: StoryWorkspace) -> None:
    """观测信号（第 3 层）：writing/revising 阶段 chapters/ 为空时 stderr 提示。

    0 章是结果不是异常——只提示、不改退出码（复跑验收靠此信号快速定位）。
    """
    stage = load_story_doc(workspace).stage
    if stage not in _WRITING_STAGES:
        return
    chapters = workspace.chapters_dir
    if chapters.exists() and any(chapters.glob("ch*.md")):
        return
    print(
        f"[prose_guard] 提醒：当前处于 {stage} 阶段，但 chapters/ 下没有正文文件。"
        "为什么错：写作阶段应经 write_chapter 逐章落盘。"
        "怎样算对：检查本轮是否只有讨论性回复，或据转录中 [tool] write_chapter 行"
        "与 prose_guard 告警定位正文直出。",
        file=sys.stderr,
    )


__all__ = [
    "CORRECTION",
    "PROSE_MIN_CHARS",
    "ProseLeakGuard",
    "warn_empty_chapters",
]
