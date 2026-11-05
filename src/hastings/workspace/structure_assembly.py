"""结构层注入装配（v1.1 M3，纯函数先行）。

装配顺序契约（冻结，见 ``dev-notes/v1.1/notes/q0-contracts.md`` 第 4 节）：

1. ``STRUCTURE.md`` 全文（铁律在前；标记丢失时降级全文注入并如实标注）；
2. 当次涉及的已装模块；
3. 正文台账（只读事实）；
4. 必发生清单（模型起草同回合自列，不落盘，进装配回执展示）。

本模块只回答「注入什么文本、快照记什么」，不触模型、不写文件：分区文本交给
``context.py`` 的分区装配，快照随 ``state_injected`` 事件进请求回执，作者可核对
实际注入了哪些层、改派了哪一章、宽松机检报了什么偏差。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hastings.workspace.chapter_clues import ClueFinding, CluePlan, ClueState
from hastings.workspace.prose_ledger import ProseLedger, ReconcileDecision
from hastings.workspace.structure_document import split_iron_rules

__all__ = [
    "LooseCheckRow",
    "StructureSnapshot",
    "StructureLayers",
    "chapter_number_of",
    "clue_context_section",
    "clue_report_section",
    "ledger_section",
    "loose_keyword_check",
    "reconcile_note",
    "split_structure_layers",
    "structure_section_text",
]

#: 台账与结构文件冲突时的优先级声明，随台账一起注入。
_LEDGER_PRIORITY_NOTE = (
    "台账由系统事务层维护，模型只读：这里没有登记的章就是还没有正文；"
    "与 STRUCTURE.md 里任何「已写」说法冲突时，以本台账为准。"
)


@dataclass(frozen=True, slots=True)
class StructureLayers:
    """结构文档按铁律段切分后的注入层。

    ``degraded=True`` 表示标记丢失：铁律层为空、``body`` 为全文——装配时降级
    全文注入并如实标注，不猜、不静默修复。
    """

    iron_rules: tuple[str, ...]
    body: str
    degraded: bool


def split_structure_layers(text: str) -> StructureLayers:
    """按冻结契约切出铁律层与结构全文层；空文本按降级空层处理。"""
    if not text.strip():
        return StructureLayers(iron_rules=(), body="", degraded=False)
    report = split_iron_rules(text)
    if not report.ok:
        return StructureLayers(iron_rules=(), body=text.strip(), degraded=True)
    return StructureLayers(iron_rules=report.rules, body=report.remainder, degraded=False)


def structure_section_text(layers: StructureLayers) -> str:
    """唯一全局语义分区：``STRUCTURE.md`` 的铁律与其余全文。

    降级时铁律小节如实标注丢失原因，并全文注入——常驻规则不能因为标记丢失
    就整块消失，但也不能假装知道边界在哪。
    """
    blocks: list[str] = []
    if layers.degraded:
        blocks.append(
            "### 铁律（写作规则，逐条遵守）\n"
            "（铁律段标记「## 铁律」丢失或不在文件开头：以下按结构全文降级注入，"
            "不猜测铁律边界；下次更新结构时把标记放回开头。）\n"
            f"{layers.body}"
        )
    else:
        if layers.iron_rules:
            rules = "\n".join(f"- {rule}" for rule in layers.iron_rules)
            blocks.append(f"### 铁律（写作规则，逐条遵守）\n{rules}")
        if layers.body:
            blocks.append(f"### 全书结构（STRUCTURE.md，模型自维护）\n{layers.body}")
    return "\n\n".join(blocks)


def ledger_section(ledger: ProseLedger) -> str:
    """第 5 层：正文台账注入文本；空台账返回空文本（分区按空跳过）。

    分区标题（「## 正文台账」）由装配层统一提供，这里只给登记行与优先级声明，
    不重复小节头。只注入事实（哪些章已入档、对应哪个修订），不解释任务该写
    哪章——那是确定性对账（``reconcile``）与改派说明的职责，两件事不混在一起。
    """
    if not ledger.chapters:
        return ""
    rows = "\n".join(
        f"- 第 {chapter} 章｜最近入档修订 {revision[:8]}"
        for chapter, revision in sorted(ledger.chapters.items())
    )
    return f"{rows}\n\n{_LEDGER_PRIORITY_NOTE}"


def reconcile_note(decision: ReconcileDecision | None) -> str:
    """确定性对账结论的注入说明；放行且无跳章时不注入（事实已在台账里）。"""
    if decision is None:
        return ""
    if decision.action == "redirect":
        return (
            f"### 对账改派（系统决定，确定性执行）\n{decision.reason}。"
            f"本次按第 {decision.target_to} 章推进，不要写第 {decision.target} 章；"
            "如作者明确要跳着写，先向作者说明缺章，得到确认后再回到目标章。"
        )
    if decision.skipped:
        skipped = "、".join(str(chapter) for chapter in decision.skipped)
        return (
            f"### 对账跳章（作者确认）\n作者明确跳过缺章（第 {skipped} 章）写第 "
            f"{decision.target} 章；这些章仍缺正文，后续需要回补。"
        )
    return ""


def clue_report_section(findings: tuple[ClueFinding, ...] | list[ClueFinding]) -> str:
    """线索对账报告注入文本（P1）：空报告返回空文本（分区按空跳过）。

    只注入确定性发现项（哑火/悬枪/失联/空响/计划未落实），不在此处展开全部
    线索清单——「未回收线索」清单属于起草注入（P2），两者职责不同：报告说
    「哪里不对」，清单说「现在手上还压着什么」。
    """
    if not findings:
        return ""
    rows = "\n".join(f"- [{finding.code}] {finding.message}" for finding in findings)
    return (
        "### 线索对账（确定性推导，报告项不拦写作）\n"
        f"{rows}\n\n"
        "这些是系统从章级登记聚合出来的偏差：该埋的埋、该收的收，"
        "别让它们继续悬着；确实是故意不收的，在对应章登记 retired 并写明理由。"
    )


def clue_context_section(
    states: list[ClueState],
    plans: list[CluePlan],
    chapter: int,
    findings: tuple[ClueFinding, ...] | list[ClueFinding] = (),
    *,
    open_total: int | None = None,
) -> str:
    """起草注入的三类线索清单（P2）：未回收、本章计划、失联待重锚。

    只列这三类，已收/已退役的不占上下文——长篇越写注入越膨胀的问题在这里
    就被掐住。三块都空时返回空文本，分区按空跳过。
    """
    plan_by_id = {plan.id: plan for plan in plans}
    blocks: list[str] = []
    if states:
        rows: list[str] = []
        for state in states:
            plan = plan_by_id.get(state.id)
            bits = [state.kind, f"第 {state.planted_in} 章埋"]
            if plan is not None and plan.resolve_chapter is not None:
                bits.append(f"计划第 {plan.resolve_chapter} 章收")
            elif state.status == "advanced":
                bits.append("已推进，未收")
            else:
                bits.append("未收")
            row = f"- {state.id}"
            if state.note:
                row += f" {state.note}"
            row += f"（{'，'.join(bits)}）"
            if state.anchor_prefix:
                row += f"：「{state.anchor_prefix}…」"
            rows.append(row)
        total = open_total if open_total is not None else len(states)
        tail = ""
        if total > len(states):
            tail = f"\n（另有 {total - len(states)} 条未回收线索未列出，可在线索对账里查看）"
        blocks.append("【未回收线索】\n" + "\n".join(rows) + tail)
    chapter_plans = [
        plan for plan in plans if plan.plant_chapter == chapter or plan.resolve_chapter == chapter
    ]
    if chapter_plans:
        rows = []
        for plan in chapter_plans:
            actions: list[str] = []
            if plan.plant_chapter == chapter:
                actions.append("计划本章埋设")
            if plan.resolve_chapter == chapter:
                actions.append("计划本章回收")
            row = f"- {plan.id}（{'、'.join(actions)}）"
            if plan.note:
                row += f"：{plan.note}"
            rows.append(row)
        blocks.append("【本章计划】\n" + "\n".join(rows))
    orphans = [finding for finding in findings if finding.code == "CLUE_ORPHANED_ANCHOR"]
    if orphans:
        blocks.append("【失联待重锚】\n" + "\n".join(f"- {finding.message}" for finding in orphans))
    if not blocks:
        return ""
    return (
        "### 线索清单（系统事实，只读）\n"
        + "\n".join(blocks)
        + "\n\n写本章时该推进的推进、该收的收；新埋的线索要说得出埋在哪个段落，"
        "别只写进正文却不登记。"
    )


def chapter_number_of(path: str) -> int | None:
    """从正文路径解析章号；非章节路径（设定、结构等）返回 ``None``。"""
    name = path.rsplit("/", 1)[-1]
    return int(name[:4]) if name[:4].isdigit() else None


# -- 必发生清单宽松机检 -------------------------------------------------------

#: 相对偏差按「未命中关键词」报告；命中不判失败，缺漏也只报偏差，不阻断入档。


@dataclass(frozen=True, slots=True)
class LooseCheckRow:
    """一条必发生项的宽松核对结果：只报命中与缺漏，不下结论。"""

    item: str
    keywords: tuple[str, ...]
    hits: tuple[str, ...]

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(keyword for keyword in self.keywords if keyword not in self.hits)

    def to_dict(self) -> dict[str, object]:
        return {
            "item": self.item,
            "keywords": list(self.keywords),
            "hits": list(self.hits),
            "missing": list(self.missing),
        }


def loose_keyword_check(
    entries: list[tuple[str, list[str]]],
    prose: str,
) -> list[LooseCheckRow]:
    """写后宽松关键词机检：每条清单项在正文里找关键词，命中与否都不判失败。

    清单项与关键词都来自模型起草同回合的自列（第 6 层，不落盘）；机检只把
    「相对偏差」（哪些关键词没在正文出现）报进回执，交模型复检与作者一眼
    否决。匹配按折叠空白与大小写后的包含关系，不做分词与同义。
    """
    normalized = " ".join(prose.split()).casefold()
    rows: list[LooseCheckRow] = []
    for item, keywords in entries:
        clean_keywords = [keyword.strip() for keyword in keywords if keyword.strip()]
        hits = tuple(keyword for keyword in clean_keywords if keyword.casefold() in normalized)
        rows.append(LooseCheckRow(item=item, keywords=tuple(clean_keywords), hits=hits))
    return rows


# -- 装配快照 -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StructureSnapshot:
    """结构层装配快照：实际注入了哪些层、改派了哪章、清单机检报了什么。

    随 ``state_injected`` 事件进请求回执，作者据此核对装配契约是否如实执行；
    不复制注入正文，只记身份与判定。
    """

    structure_injected: bool = False
    iron_rules_count: int = 0
    iron_rules_degraded: bool = False
    ledger_injected: bool = False
    ledger_chapters: tuple[int, ...] = ()
    reconcile_action: str = ""
    reconcile_target: int | None = None
    reconcile_redirected_to: int | None = None
    skipped_chapters: tuple[int, ...] = ()
    modules_selected: tuple[str, ...] = ()
    checklist: tuple[LooseCheckRow, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "structure_injected": self.structure_injected,
            "iron_rules_count": self.iron_rules_count,
            "iron_rules_degraded": self.iron_rules_degraded,
            "ledger_injected": self.ledger_injected,
            "ledger_chapters": list(self.ledger_chapters),
            "reconcile": (
                {
                    "action": self.reconcile_action,
                    "target": self.reconcile_target,
                    "redirected_to": self.reconcile_redirected_to,
                    "skipped": list(self.skipped_chapters),
                }
                if self.reconcile_action
                else None
            ),
            "modules_selected": list(self.modules_selected),
            "checklist": [row.to_dict() for row in self.checklist],
        }
