"""``STRUCTURE.md`` 的定位、铁律段切分、指纹与字数预算（1.2 M1，纯函数先行）。

结构文档是模型自维护的全书工作记忆：自由格式 Markdown，模型写、模型改，作者只读。
「## 铁律」段可位于文档中的任意位置，装配器据此切分写作规则并
常驻注入。标记丢失时降级为全文注入并如实标注，不猜、不静默修复。

本模块只回答「文件在哪、铁律是哪几条、内容是否被别人改过、有没有超预算」，
不触模型、不做业务落盘：写入走 ``files.py`` 的指纹写入路径（``STRUCTURE.md``
视同固定文件，随作品 Git 记录）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from markdown_it import MarkdownIt

from hastings.workspace.files import digest_bytes

_MARKDOWN = MarkdownIt("commonmark")

__all__ = [
    "BUDGET_LIMIT",
    "BudgetReport",
    "IRON_RULES_MARKER",
    "IronRules",
    "STRUCTURE_FILENAME",
    "check_budget",
    "split_iron_rules",
    "structure_digest",
    "structure_path",
]

#: 结构文件固定位于作品根目录。
STRUCTURE_FILENAME = "STRUCTURE.md"

#: 铁律段的固定起始标记。可以出现在任意 Markdown 小节。
IRON_RULES_MARKER = "## 铁律"

#: 结构全文的字数预算（字符数）。超限时入档事务要求先提交压缩版，不静默截断。
BUDGET_LIMIT = 6000


@dataclass(frozen=True)
class IronRules:
    """铁律段切分结果。

    ``ok=False`` 表示找不到可识别的铁律标题，此时 ``rules`` 为空、
    ``remainder`` 为全文——装配器按降级路径全文注入并如实标注。
    """

    ok: bool
    rules: tuple[str, ...]
    remainder: str


@dataclass(frozen=True)
class BudgetReport:
    """字数预算检查结果。超限不是错误，是入档前必须走压缩路径的信号。"""

    ok: bool
    length: int
    limit: int


def structure_path(work_root: Path) -> Path:
    """作品根目录下结构文档的固定路径。"""
    return Path(work_root) / STRUCTURE_FILENAME


def split_iron_rules(text: str) -> IronRules:
    """切出首个真实的铁律标题；标题前后的正文都保留。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    tokens = _MARKDOWN.parse(text)
    heading_end = None
    marker = None
    end = len(lines)
    for i, token in enumerate(tokens):
        if token.type != "heading_open" or token.level != 0 or token.map is None:
            continue
        if marker is None:
            if token.tag == "h2" and tokens[i + 1].content == "铁律":
                marker, heading_end = token.map
        elif token.tag in {"h1", "h2"}:
            end = token.map[0]
            break
    if marker is None or heading_end is None:
        return IronRules(ok=False, rules=(), remainder=text)

    rules = tuple(line.strip() for line in lines[heading_end:end] if line.strip())
    before = "\n".join(lines[:marker]).strip("\n")
    after = "\n".join(lines[end:]).strip("\n")
    remainder = "\n\n".join(part for part in (before, after) if part)
    return IronRules(ok=True, rules=rules, remainder=remainder)


def structure_digest(text: str) -> str:
    """结构文档内容指纹，与 ``files.py`` 的正文指纹同一算法。"""
    return digest_bytes(text.encode("utf-8"))


def check_budget(text: str, limit: int = BUDGET_LIMIT) -> BudgetReport:
    """结构全文是否在冻结预算内。"""
    return BudgetReport(ok=len(text) <= limit, length=len(text), limit=limit)
