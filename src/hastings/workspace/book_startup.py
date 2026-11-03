"""对话起步：引导/自由发挥双模式、设定卡候选与确认门槛（v1.1 M1）。

新作品打开即进入会话；起步以设定卡候选结束——模型把对话内容整理为一段作品意图
（``book_intent`` 候选）在对话中呈现，确认端点核对当前状态后经既有联合提交写入
作品意图（可整体撤销）；重复确认拒绝。

起步约束（一次一问、数量上限、每问可跳过、随时「就这样开始」、未知保持未知）放
在任务意图与提示装配层，本模块只提供提示词与宽松机检，不硬编码业务规则。确认后
的起步结构起草提示词也在这里：产出第一版 ``STRUCTURE.md``（铁律/路线/骨架/模块
索引）与首批模块清单，随事务直接入档（Git 可回退），入档前用
``check_first_structure`` 做确定性检查。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_models import FORMAT_VERSION
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore
from hastings.workspace.structure_document import (
    BUDGET_LIMIT,
    BudgetReport,
    IronRules,
    check_budget,
    split_iron_rules,
)
from hastings.workspace.writing_actions import save_book_intent

__all__ = [
    "CARD_TEXT_LIMIT",
    "STARTUP_QUESTION_LIMIT",
    "STARTUP_PROMPT_VERSION",
    "StartupConfirmResult",
    "StartupMode",
    "StartupStructureReport",
    "STRUCTURE_PROMPT_VERSION",
    "card_report",
    "check_first_structure",
    "confirm_startup_book_intent",
    "guided_reply_report",
    "startup_conversation_prompt",
    "structure_draft_prompt",
]

#: 引导模式下模型可提出的最大问题数；超过即偏离「问题数量有上限」的约束。
STARTUP_QUESTION_LIMIT = 6

#: 设定卡候选文本上限：一段可继续发展的意图，不是全书大纲。
CARD_TEXT_LIMIT = 2000

#: 起步结构起草提示词版本；措辞变化时递增，便于评测对照。
STRUCTURE_PROMPT_VERSION = 1

#: 起步会话提示词版本。
STARTUP_PROMPT_VERSION = 1

#: 起步会话的双模式：引导提问或自由发挥。
StartupMode = Literal["guided", "freeform"]


def card_report(text: str) -> list[str]:
    """设定卡候选文本的确定性检查；返回问题列表，空列表即通过。"""
    problems: list[str] = []
    stripped = (text or "").strip()
    if not stripped:
        problems.append("设定卡为空")
        return problems
    if len(stripped) > CARD_TEXT_LIMIT:
        problems.append(f"设定卡超过 {CARD_TEXT_LIMIT} 字符（当前 {len(stripped)}）")
    return problems


@dataclass(frozen=True, slots=True)
class StartupConfirmResult:
    """起步设定卡确认结果：收据与确认后的构思修订号。"""

    change_id: str
    plan_revision: str


def confirm_startup_book_intent(
    store: StoryStore,
    *,
    text: str,
    request_key: str,
) -> StartupConfirmResult:
    """起步设定卡确认：一次性门槛 + 复用既有联合提交（可整体撤销）。

    与作者日常编辑作品意图的 ``save_book_intent``（PUT 端点）不同，起步确认只发
    生一次：作品已有非空意图时拒绝（``BOOK_INTENT_CONFIRMED_CONFLICT``），不覆盖。
    """
    problems = card_report(text)
    if problems:
        raise WorkspaceError(
            code="BOOK_INTENT_INVALID",
            where="起步设定卡",
            why="；".join(problems),
            fix="回到对话让模型重新整理设定卡，再确认",
        )
    variant = store.read().variant(MAIN_VARIANT_ID)
    if variant is not None:
        if variant.format_version < FORMAT_VERSION:
            raise WorkspaceError(
                code="STORY_FORMAT_UNSUPPORTED",
                where="起步设定卡",
                why="当前作品仍使用旧构思格式",
                fix="新建作品后使用对话起步；本版不迁移旧数据",
            )
        if variant.book_intent is not None and variant.book_intent.text.strip():
            raise WorkspaceError(
                code="BOOK_INTENT_CONFIRMED_CONFLICT",
                where="起步设定卡",
                why="作品意图已经确认过，起步确认不能重复",
                fix="直接修改作品意图，或撤销上次确认后重新起步",
            )
    expected = variant.revision if variant is not None else EMPTY_REVISION
    receipt = save_book_intent(
        store,
        text=text,
        expected_plan_revision=expected,
        request_key=request_key,
    )
    return StartupConfirmResult(
        change_id=receipt.change_id,
        plan_revision=store.require_variant(MAIN_VARIANT_ID).revision,
    )


def guided_reply_report(text: str) -> dict[str, object]:
    """引导回合的宽松机检：只报相对偏差，不判失败。

    统计助手回复里向作者提出的问题数；超过上限或一次多问时给出提示，
    供装配回执展示。真正的约束在提示层，这里不做拦截。
    """
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
    """起步会话的系统提示与首条开场提示（约束在提示层，不硬编码业务规则）。

    引导模式：一次一问、数量上限、每问可跳过、随时「就这样开始」、未知保持未知；
    问题只服务于找到可写的故事起点，不追问时间线、人物关系等内部属性清单。
    自由发挥：作者直接说想法，模型整理成可写的起点，缺什么保持未知。
    两种模式结束时都以设定卡候选收束：把对话内容整理为一段作品意图，完整呈现在
    对话里供作者确认（确认动作走确认端点，这里只产出候选文本）。
    """
    if mode == "guided":
        opening = (
            "这本书还没有任何设定。请用引导提问帮作者起步："
            f"一次只问一个问题，全程不超过 {STARTUP_QUESTION_LIMIT} 个；"
            "每个问题都允许作者跳过；作者随时说「就这样开始」就停止提问。"
            "问题只用于找到可写的故事起点（处境、悬念、人物所处的位置），"
            "不追问时间线、人物关系表这类内部属性；作者不知道的就保持未知，"
            "不替作者决定真相、凶手或结局。"
        )
    else:
        opening = (
            "这本书还没有任何设定。作者会直接说想法；"
            "你把它整理成可继续发展的故事起点，缺什么保持未知，"
            "不替作者决定真相、凶手或结局，也不追问内部属性清单。"
        )
    system = (
        "你是悬疑小说创作助手，正在陪作者为一本新书起步。"
        + opening
        + "当作者示意可以开始（说「就这样开始」或明确表示想看结果）时，"
        "把对话内容整理为一段作品意图（设定卡候选）在对话中完整呈现："
        "只描述起点与悬念，不写结局与真相；整理后等作者确认。"
    )
    return system, opening


def structure_draft_prompt(card_text: str, module_names: tuple[str, ...] = ()) -> tuple[str, str]:
    """起步结构起草提示词：第一版 ``STRUCTURE.md`` 与首批模块清单。

    返回 ``(system, prompt)``。铁律约三行；分层判据写入提示——结构文件放全书性
    内容（路线、骨架、模块索引），细节下沉为模块，不重复展开。
    """
    modules = "、".join(module_names) if module_names else "（尚无模块）"
    system = (
        "你是悬疑小说创作助手。根据设定卡为这部作品起草全书结构与首批资料模块。"
        "只输出要求的内容，不解释过程；不确定的内容保持未知，不替作者决定真相或结局。"
    )
    prompt = (
        f"设定卡（作品意图）：{card_text.strip()}\n"
        f"既有模块：{modules}\n\n"
        "请依次输出两部分，用一行 ===MODULES=== 分隔：\n\n"
        "第一部分是《STRUCTURE.md》全文，要求：\n"
        "1. 文件第一个非空行必须是「## 铁律」，其下约三行写作铁律"
        "（全书必须遵守的规则，如叙事视角、知情边界）。\n"
        "2. 之后依次为「## 路线」（全书走向，一段话，不写死结局）、「## 骨架」"
        "（章节骨架，每章一行：章号 + 一句本章发生什么，先给前 6 章）、「## 模块索引」"
        "（列出下面的模块与各自职责）。\n"
        f"3. 全文不超过 {BUDGET_LIMIT} 字符。只放全书性内容，人物、线索等细节写进模块，"
        "不在结构文件里展开。\n\n"
        "第二部分（===MODULES=== 之后）是首批模块清单，每行一个模块，格式为「名称：职责一句话」；"
        "通常包含伏笔账（读法而非状态）与主要人物，按这本书自选，不超过 5 个。"
    )
    return system, prompt


@dataclass(frozen=True, slots=True)
class StartupStructureReport:
    """起步结构草稿的确定性检查结果；超限/标记丢失是入档前必须处理的信号。"""

    iron_rules: IronRules
    budget: BudgetReport

    @property
    def ok(self) -> bool:
        return self.iron_rules.ok and self.budget.ok


def check_first_structure(text: str) -> StartupStructureReport:
    """起步结构草稿入档前的确定性检查：铁律标记在开头 + 预算内。"""
    return StartupStructureReport(
        iron_rules=split_iron_rules(text),
        budget=check_budget(text),
    )
