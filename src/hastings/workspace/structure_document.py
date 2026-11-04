"""``STRUCTURE.md`` 的定位、铁律段切分、指纹与字数预算（1.2 M1，纯函数先行）。

结构文档是模型自维护的全书工作记忆：自由格式 Markdown，模型写、模型改，作者只读。
唯一结构约定是文件开头为「## 铁律」段——写作规则而非数据字段，装配器据此切分
常驻注入。标记丢失时降级为全文注入并如实标注，不猜、不静默修复。

本模块只回答「文件在哪、铁律是哪几条、内容是否被别人改过、有没有超预算」，
不触模型、不做业务落盘：写入走 ``files.py`` 的指纹写入路径（``STRUCTURE.md``
视同固定文件，随作品 Git 记录）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hastings.workspace.files import digest_bytes

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

#: 铁律段的固定起始标记。必须出现在文件首个非空行，否则视为标记丢失。
IRON_RULES_MARKER = "## 铁律"

#: 结构全文的字数预算（字符数）。超限时入档事务要求先提交压缩版，不静默截断。
BUDGET_LIMIT = 6000


@dataclass(frozen=True)
class IronRules:
    """铁律段切分结果。

    ``ok=False`` 表示标记丢失（或不在文件开头），此时 ``rules`` 为空、
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
    """切出铁律段与余下正文。

    约定：首个非空行必须是 ``## 铁律``，其后到下一个二级标题或文件尾之间
    的非空行各为一条铁律。标记不在开头按丢失处理——半途出现的「铁律」
    多半是模型改版时的残留，不能当成常驻规则注入。
    """
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None or lines[first].strip() != IRON_RULES_MARKER:
        return IronRules(ok=False, rules=(), remainder=text)

    rules: list[str] = []
    body_start = len(lines)
    for i in range(first + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("## "):
            body_start = i
            break
        if stripped:
            rules.append(stripped)
    remainder = "\n".join(lines[body_start:]).strip("\n")
    return IronRules(ok=True, rules=tuple(rules), remainder=remainder)


def structure_digest(text: str) -> str:
    """结构文档内容指纹，与 ``files.py`` 的正文指纹同一算法。"""
    return digest_bytes(text.encode("utf-8"))


def check_budget(text: str, limit: int = BUDGET_LIMIT) -> BudgetReport:
    """结构全文是否在冻结预算内。"""
    return BudgetReport(ok=len(text) <= limit, length=len(text), limit=limit)
