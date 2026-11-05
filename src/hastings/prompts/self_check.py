"""写后自查四层与强制对账流程的提示词模板（v1.1 M5，v1.3 P2 扩展到四层）。

契约（q0-contracts 第 3、7 节与开发计划 M5）：

- **强制对账流程（第二道保险）**：起草提示词规定，收到写作目标第一步必须核对
  目标章与正文台账，发现更早章节缺正文不得写目标章、改为补缺。系统侧装配层
  确定性对账仍是主防线；被动注入台账模型不对账（试验 0/3），强制流程 3/3。
- **写后自查四层**随起草同回合产出：①必发生清单逐条定位核对；②铁律逐条检查；
  ③读者认知提示（当前误读是否立住、有无提前起疑），必须给可执行的修改建议；
  ④线索核对：本章计划线索是否落锚、回收动作是否与登记一致。
  自查是模型自检，概率性、如实标注「自查」，不承诺无漏报。
- 自查段缺失按 ``SELF_CHECK_MISSING_CONFLICT`` 任务失败归因；只缺线索核对层时
  按 ``SELF_CHECK_CLUE_MISSING`` 归因（知道该补哪一段）。

模板版本 ``SELF_CHECK_TEMPLATE_VERSION``：提示词措辞变更时递增，回执可核对用的
是哪一版。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from hastings.workspace.errors import WorkspaceError

__all__ = [
    "SELF_CHECK_MARKER",
    "SELF_CHECK_TEMPLATE_VERSION",
    "SelfCheckBlock",
    "prose_draft_system_prompt",
    "prose_draft_user_prompt",
    "self_check_error",
    "split_self_check",
]

#: 模板版本；提示词措辞变更时递增（v1.3 P2 新增第 4 层线索核对 → 2；
#: v1.3 P5 必发生项空清单不再引导模型报「模块缺失」→ 3）。
SELF_CHECK_TEMPLATE_VERSION = 3

#: 自查段标记；候选正文之后以该二级小节收尾，解析据此拆分。
SELF_CHECK_MARKER = "### 自查"

_SYSTEM_PROMPT = (
    "你是悬疑小说写作助手。只输出正文与自查段，不写解释或属性表。保持人物知情边界。"
    "写作流程有两条硬性规定："
    "第一步核对目标章与正文台账，更早的章节没有正文时不得写目标章，改为补写最早的缺章；"
    "正文写完后必须附自查段（以「### 自查」开头），逐条给出核对结论。"
)

#: 三层自查的固定要求文本；逐层给条目与结论，读者认知层必须给可执行建议。
#:
#: 第 1 层刻意写成「有则逐条对位、无则写本章无必发生项」：真模型探针里一旦
#: 空清单没写明，模型会自述「必发生项模块缺失」并建议补骨架——把沉默读成故障。
_SELF_CHECK_REQUIREMENT = (
    f"{SELF_CHECK_MARKER}\n"
    "1. 必发生核对：本章必发生项只取注入的结构与本章计划里写明的事件要求；"
    "有就逐条指出正文里对应的位置（短引文定位），一条都没写明就写「本章无必发生项」。"
    "不要据此判断模块或登记缺失，也不要建议补骨架。\n"
    "2. 铁律自查：逐条对照铁律，指出正文哪里遵守、哪里擦边。\n"
    "3. 读者认知：当前读者的误读是否立住、有无提前起疑；必须给出可执行的修改建议。\n"
    "4. 线索核对：逐条核对本章计划线索是否落锚、回收动作是否与登记一致；"
    "没有线索时如实写「本章无线索登记」。\n"
    "（自查是模型自检，非系统校验；发现不立住之处如实标注。）"
)


def prose_draft_system_prompt() -> str:
    """起草系统提示：强制对账流程 + 自查段要求（版本化措辞）。"""
    return _SYSTEM_PROMPT


def prose_draft_user_prompt(
    *,
    structure_text: str,
    ledger_text: str,
    moves: str,
    prose: str,
    quote: str,
    note: str,
    continue_mode: bool,
    style_section: str = "",
    clue_context: str = "",
    redirected_to: int | None = None,
    target_chapter: int | None = None,
) -> str:
    """起草/续写用户提示：注入铁律与结构、台账与对账结论、必发生清单要求。

    ``structure_text`` 为装配层准备好的 ``STRUCTURE.md`` 全文（铁律在前）；
    ``ledger_text`` 为台账事实与对账说明（含改派结论时 ``redirected_to`` 同步给出，
    提示词据此把写作目标改为补缺）。
    """
    target = f"第 {target_chapter} 章" if target_chapter is not None else "本章"
    task = (
        f"写作目标：{target}（续写选段之后的内容）。"
        if continue_mode
        else f"写作目标：{target}。请起草本章正文。"
    )
    if redirected_to is not None:
        task += f" 注意：对账发现更早章节缺正文，本轮改为补写第 {redirected_to} 章，不写原目标章。"
    parts = [task]
    if structure_text:
        parts.append(f"写作规则与结构（铁律在前，必须逐条遵守）：\n{structure_text}")
    if style_section:
        parts.append(style_section)
    if ledger_text:
        parts.append(f"正文台账（系统事实，只读；对账以它为准）：\n{ledger_text}")
    if clue_context:
        parts.append(clue_context)
    parts.append(f"本章已入账信息：{moves or '（无）'}")
    parts.append(f"当前正文：\n{prose[-4000:] if prose else '（空白）'}")
    if quote:
        parts.append(f"作者选段：{quote}")
    parts.append(f"作者补充：{note or '（无）'}")
    parts.append(_SELF_CHECK_REQUIREMENT)
    parts.append(
        "输出两部分：先正文，后自查段。自查段必须以「" + SELF_CHECK_MARKER + "」开头，"
        "逐层给出结论（必发生核对 / 铁律自查 / 读者认知 / 线索核对）。"
    )
    return "\n\n".join(parts)


@dataclass(frozen=True, slots=True)
class SelfCheckBlock:
    """从候选文本里解析出的自查段；缺失时 ``present=False``。"""

    present: bool
    prose: str = ""
    checklist_items: tuple[str, ...] = field(default=())
    iron_rule_items: tuple[str, ...] = field(default=())
    reader_note: str = ""
    clue_items: tuple[str, ...] = field(default=())
    missing_layers: tuple[str, ...] = field(default=())

    def to_rows(self) -> dict[str, object]:
        return {
            "present": self.present,
            "checklist_items": list(self.checklist_items),
            "iron_rule_items": list(self.iron_rule_items),
            "reader_note": self.reader_note,
            "clue_items": list(self.clue_items),
            "missing_layers": list(self.missing_layers),
        }


def split_self_check(text: str) -> SelfCheckBlock:
    """确定性拆分正文与自查段。

    取**最后一个**自查标记（正文里引用该短语不至于误判）；标记缺失或任一层
    缺失都按「自查段缺失」处理（``present=False``），调用方按任务失败归因，
    不静默跳过；``missing_layers`` 供区分「只缺线索核对」的归因。
    """
    marker_index = text.rfind(SELF_CHECK_MARKER)
    if marker_index < 0:
        return SelfCheckBlock(
            present=False, prose=text, missing_layers=("必发生", "铁律", "读者认知", "线索核对")
        )
    prose = text[:marker_index].rstrip()
    section = text[marker_index + len(SELF_CHECK_MARKER) :]
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    layers = _layer_lines(lines)
    checklist = tuple(layers.get("必发生", ()))
    iron = tuple(layers.get("铁律", ()))
    reader = tuple(layers.get("读者", ()))
    clue = tuple(layers.get("线索核对", ()))
    missing = tuple(
        name
        for name, items in (
            ("必发生", checklist),
            ("铁律", iron),
            ("读者认知", reader),
            ("线索核对", clue),
        )
        if not items
    )
    return SelfCheckBlock(
        present=not missing,
        prose=prose,
        checklist_items=checklist,
        iron_rule_items=iron,
        reader_note="\n".join(reader),
        clue_items=clue,
        missing_layers=missing,
    )


def _items_under(lines: list[str], keyword: str) -> tuple[str, ...]:
    """宽松解析某一层的条目：层头是以关键词开头的行，其后内容行都归这一层。

    自由格式下措辞不设死（序号、加粗、破折号、冒号变体都接受）；宽松匹配只
    影响自查展示，不做对错判定。
    """
    layer_lines = _layer_lines(lines)
    return tuple(layer_lines.get(keyword, ()))


#: 层头前的装饰：序号（1. / ①）、Markdown 标记（**、-、>）、空白与冒号。
_HEAD_DECORATION = re.compile(r"^[\s#>*_\-–—\d０-９.、)）:：①-⑳]+")


def _layer_head(line: str) -> str | None:
    """判断一行是不是层头：去掉序号与 Markdown 装饰后，必须以层名开头。

    真模型常用 ``**必发生核对**`` 这种加粗层头，也常在正文里写「读者」——
    按「包含关键词」判定会让后者把条目抢到别的层（v1.3 真模型探针实测：
    线索核对层的条目被含「读者」的句子截断）。层头只在行首识别。
    """
    cleaned = _HEAD_DECORATION.sub("", line)
    for key in ("必发生", "铁律", "读者", "线索核对"):
        if cleaned.startswith(key):
            return key
    return None


def _layer_lines(lines: list[str]) -> dict[str, list[str]]:
    layers: dict[str, list[str]] = {"必发生": [], "铁律": [], "读者": [], "线索核对": []}
    current: str | None = None
    for line in lines:
        matched = _layer_head(line)
        if matched is not None:
            current = matched
            rest = _HEAD_DECORATION.sub("", line)[len(matched) :]
            if rest.startswith("核对"):
                rest = rest[len("核对") :]
            rest = rest.strip("：:；; ，,*#")
            if rest:
                layers[current].append(rest)
            continue
        if current is not None:
            layers[current].append(line)
    return layers


def self_check_error(where: str, missing_layers: tuple[str, ...] = ()) -> WorkspaceError:
    """自查段缺失的确定性失败归因（不静默跳过）。

    只缺线索核对层时用 ``SELF_CHECK_CLUE_MISSING``：归因精确到层，模型知道
    该补哪一段；其余缺失仍按整体缺失归因。
    """
    if missing_layers and set(missing_layers) == {"线索核对"}:
        return WorkspaceError(
            code="SELF_CHECK_CLUE_MISSING",
            where=where,
            why="自查段缺少第 4 层线索核对（本章计划线索是否落锚、回收是否与登记一致）",
            fix="补写「4. 线索核对」一行后重新提交；没有线索时如实写「本章无线索登记」",
        )
    return WorkspaceError(
        code="SELF_CHECK_MISSING_CONFLICT",
        where=where,
        why="起草没有产出自查段（必发生核对 / 铁律自查 / 读者认知 / 线索核对不全）",
        fix="重新起草；正文与自查段随同产出，缺一不算完成",
    )
