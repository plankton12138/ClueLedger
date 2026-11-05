"""模型侧线索登记（v1.3 P5）：把起草回合里新埋的计划外线索接进账。

起草时模型常在正文里临时埋点（门牌缺号、停摆的钟），过去只能停在自查段的
「建议登记」里，下一章对账就看不见它。这里定义一条**确定性**通道：

- 模型只负责说清「什么类型、叫什么、从哪段长出来」，以固定小节提交；
- 编号、段落锚、状态由系统算：短引在正文里定位到段落，锚是段落内容哈希；
- 定位不到就不登记，按 ``ProposalRefusal`` 回报，不猜、不模糊重试。

小节格式（``### 线索登记`` 之后每条一行）::

    - 伏笔｜箱扣缺漆｜老人开抽屉时露出铜牌掉漆，先埋下｜「老人从抽屉里摸出一把钥匙」

三段的职责：类型（限 ``_KINDS``）、说明（名称 + 为什么现在埋，进 ``note``）、
正文短引（用来定位段落）。短引是定位手段，不落盘。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import cast

from hastings.workspace.chapter_clues import (
    _KINDS,
    ClueAnchor,
    ClueEntry,
    Kind,
    anchor_paragraph,
    paragraphs,
)

__all__ = [
    "PROPOSAL_MARKER",
    "ClueProposal",
    "ProposalRefusal",
    "materialize_proposals",
    "next_clue_id",
    "split_clue_proposals",
]

#: 线索登记小节的标题（模型按提示词原样输出）。
PROPOSAL_MARKER = "### 线索登记"

#: 字段分隔符：全角为主，半角兜底（模型偶尔混用）。
_SEPARATORS = re.compile(r"[｜|]")
#: 行首装饰：列表符号、序号、加粗与空白。
_LINE_DECORATION = re.compile(r"^[\s>*_\-–—•\d０-９.、)）:：①-⑳]+")
#: 短引两侧的括号：定位时都剥掉，正文里不可能带这层包装。
_QUOTE_WRAPPERS = str.maketrans({"「": "", "」": "", "『": "", "』": "", "“": "", "”": "", '"': ""})
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")


@dataclass(frozen=True)
class ClueProposal:
    """一条待登记的新线索；编号与锚由系统补。"""

    kind: Kind
    note: str
    quote: str

    def to_json(self) -> dict[str, object]:
        return {"kind": self.kind, "note": self.note, "quote": self.quote}

    @classmethod
    def from_json(cls, value: object) -> ClueProposal:
        if not isinstance(value, dict):
            raise ValueError("线索登记建议不是对象")
        kind = str(value["kind"])
        if kind not in _KINDS:
            raise ValueError(f"线索类型非法：{kind}")
        return cls(
            kind=cast(Kind, kind),
            note=str(value.get("note") or ""),
            quote=str(value.get("quote") or ""),
        )


@dataclass(frozen=True)
class ProposalRefusal:
    """没能登记的条目：原因照实回报，不静默丢弃。"""

    proposal: ClueProposal
    reason: str

    def to_json(self) -> dict[str, str]:
        return {
            "kind": self.proposal.kind,
            "note": self.proposal.note,
            "quote": self.proposal.quote,
            "reason": self.reason,
        }


def _is_proposal_heading(line: str) -> bool:
    stripped = _LINE_DECORATION.sub("", line.lstrip("#").strip())
    return stripped.startswith("线索登记")


def _parse_line(line: str) -> ClueProposal | str:
    """解析一行：成功给 ``ClueProposal``，失败给拒绝原因。"""
    cleaned = _LINE_DECORATION.sub("", line).strip()
    if not cleaned:
        return "空行"
    fields = [field.strip() for field in _SEPARATORS.split(cleaned)]
    if len(fields) < 3 or not all(fields[:3]):
        return "字段不足：应写成「类型｜说明｜正文短引」，用全角竖线分隔"
    kind, note = fields[0], "｜".join(fields[1:-1]).strip()
    quote = fields[-1].translate(_QUOTE_WRAPPERS).strip(" ：:，,。")
    if kind not in _KINDS:
        return f"类型 {kind!r} 不在 {'/'.join(sorted(_KINDS))} 之内"
    if not quote:
        return "缺少正文短引：用「」引一段本章原文，系统据此定位段落"
    if not note:
        return "缺少说明：写明线索名称与为什么现在埋"
    return ClueProposal(kind=cast(Kind, kind), note=note, quote=quote)


def split_clue_proposals(
    text: str,
) -> tuple[str, list[ClueProposal], list[str]]:
    """从起草输出里切出「线索登记」小节。

    返回 ``(去掉该小节后的文本, 建议列表, 拒绝原因列表)``。小节取**最后一个**
    标题；到下一个标题行为止（模型偶尔把登记写在自查段前面）。去掉小节是为了
    让自查解析只看到四层，不被登记的条目行污染。
    """
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if _HEADING.match(line) and _is_proposal_heading(line):
            start = index
    if start is None:
        return text, [], []
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _HEADING.match(lines[index]):
            end = index
            break
    kept = [*lines[:start], *lines[end:]]
    proposals: list[ClueProposal] = []
    rejects: list[str] = []
    for line in lines[start + 1 : end]:
        if not line.strip():
            continue
        parsed = _parse_line(line)
        if isinstance(parsed, str):
            rejects.append(f"{line.strip()}（{parsed}）")
        else:
            proposals.append(parsed)
    return "\n".join(kept).strip(), proposals, rejects


def next_clue_id(taken: Iterable[str]) -> str:
    """按已有编号顺延：取现有最大号 + 1，两位补零（CL-07）。"""
    highest = 0
    for clue_id in taken:
        match = re.fullmatch(r"CL-(\d+)", clue_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"CL-{highest + 1:02d}"


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", text)


def locate_paragraph(quote: str, prose_text: str) -> str | None:
    """短引定位段落：去空白后做包含匹配，返回第一个命中的整段。"""
    needle = _normalized(quote)
    if not needle:
        return None
    for paragraph in paragraphs(prose_text):
        if needle in _normalized(paragraph):
            return paragraph
    return None


def materialize_proposals(
    proposals: Sequence[ClueProposal],
    prose_text: str,
    *,
    revision: str,
    existing: Sequence[ClueEntry] = (),
    taken: Iterable[str] = (),
) -> tuple[list[ClueEntry], list[ProposalRefusal]]:
    """把建议变成可入档条目：定位段落、建锚、顺延编号，并合入本章已有登记。

    ``existing`` 是本章当前的登记（覆盖式落盘，必须原样带上）；``taken`` 是
    全书已占用的编号（含计划编号），避免新条目与别处撞号。同一 ``kind`` +
    ``note`` 已在本章存在时跳过——同一笔候选重复保存不该长出重复条目。
    """
    entries = list(existing)
    known = {(entry.kind, (entry.note or "").strip()) for entry in entries}
    used = {entry.id for entry in entries}
    next_id = next_clue_id({*used, *taken})
    refusals: list[ProposalRefusal] = []
    for proposal in proposals:
        key = (proposal.kind, proposal.note.strip())
        if key in known:
            continue
        paragraph = locate_paragraph(proposal.quote, prose_text)
        if paragraph is None:
            refusals.append(
                ProposalRefusal(
                    proposal=proposal,
                    reason="正文里找不到这段引文，未登记（可能被改写成了概括）",
                )
            )
            continue
        while next_id in used or next_id in taken:
            next_id = next_clue_id({next_id})
        anchor: ClueAnchor = anchor_paragraph(paragraph, revision)
        entries.append(
            ClueEntry(
                id=next_id,
                kind=proposal.kind,
                status="planted",
                origin="emergent",
                note=proposal.note.strip(),
                anchor=anchor,
            )
        )
        known.add(key)
        used.add(next_id)
        next_id = next_clue_id({*used, *taken})
    return entries, refusals
