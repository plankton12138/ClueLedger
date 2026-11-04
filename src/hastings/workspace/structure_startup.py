"""空作品的对话起步与第一版 ``STRUCTURE.md`` 提示（v1.2）。

起步没有设定卡或确认门槛。作者给出明确创作请求时，模型把自然语言整理为第一版结构并
直接入档；确实无法判断是否要开始作品时只问一个选择问题。人物、世界和线索等详细资料
不在这里自动创建，交给上下文模块建议流程另行确认。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hastings.workspace.structure_document import (
    BUDGET_LIMIT,
    BudgetReport,
    IronRules,
    check_budget,
    split_iron_rules,
)

__all__ = [
    "STARTUP_QUESTION_LIMIT",
    "STARTUP_PROMPT_VERSION",
    "STRUCTURE_PROMPT_VERSION",
    "StartupMode",
    "StartupStructureReport",
    "check_first_structure",
    "guided_reply_report",
    "resolve_startup_mode",
    "startup_conversation_prompt",
    "structure_draft_prompt",
]

STARTUP_QUESTION_LIMIT = 6
STRUCTURE_PROMPT_VERSION = 2
STARTUP_PROMPT_VERSION = 2

StartupMode = Literal["guided", "freeform"]

_GUIDED_MARKERS = (
    "引导",
    "带我想",
    "帮我想想",
    "陪我",
    "问问我",
    "提问",
    "不知道写什么",
    "没想法",
    "没头绪",
)


def resolve_startup_mode(text: str) -> StartupMode:
    return "guided" if any(marker in (text or "") for marker in _GUIDED_MARKERS) else "freeform"


def guided_reply_report(text: str) -> dict[str, object]:
    stripped = (text or "").strip()
    questions = stripped.count("？") + stripped.count("?")
    return {
        "questions": questions,
        "within_limit": questions <= STARTUP_QUESTION_LIMIT,
        "single_question": questions <= 1,
        "note": (
            ""
            if questions <= 1
            else (
                f"本回合提出了 {questions} 个问题；引导模式一次只问一个问题，"
                f"全程不超过 {STARTUP_QUESTION_LIMIT} 个"
            )
        ),
    }


def startup_conversation_prompt(mode: StartupMode) -> tuple[str, str]:
    if mode == "guided":
        opening = (
            "作者希望被引导。一次只问一个问题，问题必须与故事起点直接相关；全程不超过 "
            f"{STARTUP_QUESTION_LIMIT} 个；每个问题都可以跳过。作者说可以开始后，"
            "立即整理并写入第一版 STRUCTURE.md。"
        )
    else:
        opening = (
            "作者会直接描述想法。请求足以开始创作时，立即整理并写入第一版 "
            "STRUCTURE.md；不要要求作者填写结构字段或进行额外确认。"
        )
    system = (
        "你是线簿的写作模型。STRUCTURE.md 记录全书结构和叙事约束，STYLE.md 记录作者的写作约定。"
        + opening
        + "不确定的内容保持未知，不替作者决定其明确保留的真相或结局。"
    )
    return system, opening


def structure_draft_prompt(author_request: str) -> tuple[str, str]:
    system = (
        "你是线簿的写作模型。当前作品还没有 STRUCTURE.md。作者的自然语言就是创作输入；"
        "你负责组织结构，不让作者维护文件、字段或章节骨架。"
    )
    instructions = (
        f"作者当前请求：\n{author_request.strip() or '（未提供明确创作内容）'}\n\n"
        "若这是明确创作请求：先登记 STRUCTURE.md 的任务范围，确认文件现状，再直接写入第一版。"
        "结构第一个非空行是「## 铁律」，之后按作品需要组织方向、路线、章节骨架与模块索引；"
        f"全文不超过 {BUDGET_LIMIT} 字符。未知内容明确保留为未知，不虚构作者排除的内容。"
        "如果作者要求正文，结构写入成功后继续同一任务。"
        "如果当前上下文确实不足以判断作者是否要开始作品，只问一次：现在直接建立作品，还是继续聊？"
        "不要创建其他平行全局摘要。"
        "人物小传、故事背景等详细资料不在本步骤自动创建；需要时通过模块建议请求一次作者决定。"
    )
    return system, instructions


@dataclass(frozen=True, slots=True)
class StartupStructureReport:
    iron_rules: IronRules
    budget: BudgetReport

    @property
    def ok(self) -> bool:
        return self.iron_rules.ok and self.budget.ok


def check_first_structure(text: str) -> StartupStructureReport:
    return StartupStructureReport(
        iron_rules=split_iron_rules(text),
        budget=check_budget(text),
    )
