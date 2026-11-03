"""结构层注入装配（v1.1 M3，纯函数先行）。

装配顺序契约（冻结，见 ``dev-notes/v1.1/notes/q0-contracts.md`` 第 4 节）：

1. 铁律（常驻；标记丢失时降级全文注入并如实标注）；
2. 结构全文（常驻，文件本身冻结 1600 字符预算）；
3. ``STYLE.md``（本版空占位）；
4. 当次涉及挂载模块（按写作目标按需选择，目标未知时注入模块索引）；
5. 正文台账（只读事实）；
6. 必发生清单（模型起草同回合自列，不落盘，进装配回执展示）。

本模块只回答「注入什么文本、快照记什么」，不触模型、不写文件：分区文本交给
``context.py`` 的分区装配，快照随 ``state_injected`` 事件进请求回执，作者可核对
实际注入了哪些层、改派了哪一章、宽松机检报了什么偏差。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hastings.workspace.prose_ledger import ProseLedger, ReconcileDecision
from hastings.workspace.structure_document import split_iron_rules

__all__ = [
    "STYLE_PLACEHOLDER",
    "LooseCheckRow",
    "StructureSnapshot",
    "StructureLayers",
    "chapter_number_of",
    "ledger_section",
    "loose_keyword_check",
    "reconcile_note",
    "split_structure_layers",
    "structure_section_text",
]

#: 第 3 层：``STYLE.md`` 空占位。本版不启用独立风格文件，占位如实说明这一点，
#: 不假装装入了风格内容。
STYLE_PLACEHOLDER = "（占位：本版未启用独立风格文件；上面的铁律即当前全部写作规则。）"

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
    """第 1—3 层的分区文本：铁律 → 结构全文 → STYLE 空占位。

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
    blocks.append(f"### 风格（STYLE.md）\n{STYLE_PLACEHOLDER}")
    return "\n\n".join(blocks)


def ledger_section(ledger: ProseLedger) -> str:
    """第 5 层：正文台账注入文本；空台账返回空文本（分区按空跳过）。

    只注入事实（哪些章已入档、对应哪个修订），不解释任务该写哪章——那是
    确定性对账（``reconcile``）与改派说明的职责，两件事不混在一个小节里。
    """
    if not ledger.chapters:
        return ""
    rows = "\n".join(
        f"- 第 {chapter} 章｜最近入档修订 {revision[:8]}"
        for chapter, revision in sorted(ledger.chapters.items())
    )
    return f"### 正文台账（系统事实，只读）\n{rows}\n\n{_LEDGER_PRIORITY_NOTE}"


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
        hits = tuple(
            keyword
            for keyword in clean_keywords
            if keyword.casefold() in normalized
        )
        rows.append(
            LooseCheckRow(item=item, keywords=tuple(clean_keywords), hits=hits)
        )
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
