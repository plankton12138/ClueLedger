"""上下文装配：分区、预算与注入记录（0.6 V06-E1）。

装配回答两个问题：这一轮该给什么、实际给了多少。分区带稳定 key 与预算——

- 预算按**完整渲染字符数**计量：分区标题、截断提示与正文都占位，避免「正文刚好卡在
  线上、加上标题就超」这类表面通过；
- 必装分区超预算**直接失败**，不截断后宣称完整（约定被裁一半比缺一块更危险）；
- 可裁分区超预算时整块裁掉，留下原因，由调用方写进会话事件。

这里不判断内容对不对，只判断该不该出现。真相有没有漏进写作上下文属于白名单问题，
正文有没有矛盾属于检查问题，两者不混在一起。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hastings.workspace.errors import WorkspaceError

__all__ = [
    "CATALOG_BUDGET",
    "HISTORY_BUDGET",
    "INDEX_BUDGET",
    "INSTRUCTIONS_BUDGET",
    "MATERIALS_BUDGET",
    "MODULES_BUDGET",
    "IDEAS_BUDGET",
    "PROFILE_BUDGETS",
    "CLUES_BUDGET",
    "GLOSSARY_BUDGET",
    "ROSTER_BUDGET",
    "TIMELINE_BUDGET",
    "SETTINGS_BUDGET",
    "STATE_BUDGET",
    "STORY_BUDGET",
    "AssembledContext",
    "Exclusion",
    "Partition",
    "Profile",
    "Skip",
    "assemble",
    "body_budget",
]

#: 用途决定装哪些分区。受理时只知道用途（写正文、做检查），不知道作者这一轮想讨论
#: 还是想改稿，所以普通「讨论」不单列类型。
Profile = Literal["writer", "review"]

#: 各分区初始预算，集中定义、集中测试，不做成用户配置。单位是渲染后的字符数。
INSTRUCTIONS_BUDGET = 4_000
CATALOG_BUDGET = 12_000
#: 已装模块的短目录；只放实例、精确版本与条目数，不读条目正文。
MODULES_BUDGET = 2_000
#: 构思结构的分区预算：必需内容装不下时由装配层报缺口，不截断。
STORY_BUDGET = 12_000
MATERIALS_BUDGET = 8_000
#: 作者本轮明确选中的灵感修订；按修订冻结，和正文材料分开计量。
IDEAS_BUDGET = 3_000
#: 章节索引摘要在写作上下文里的位置；单列成常量，便于核对总预算的构成。
INDEX_BUDGET = 8_000
SETTINGS_BUDGET = 6_000
HISTORY_BUDGET = 3_000
#: 状态层注入：只放当前章节可用的条目，超出后按优先级裁剪。
STATE_BUDGET = 1_500
#: 专名表注入：名字与别名，按类别列。
GLOSSARY_BUDGET = 800
#: 人物状态注入：位置、生死、立场，已死者优先。
ROSTER_BUDGET = 700
#: 时间线注入：最近几个时间锚点与「从哪接」。
TIMELINE_BUDGET = 400
#: 线索台账注入：只列未回收的，超期在前。
CLUES_BUDGET = 500

#: 每种用途的总预算：分区预算之和，超出的判断仍按分区做。
#:
#: 这是**字符**预算，不是 token 上限：写作用途合计 61,900 字符，只用来解释这一轮
#: 装进了多少材料。上游实际消耗的 token 由用量记录单独上报，两者不互相换算，也不能
#: 拿这里的数字代替 token 统计。
PROFILE_BUDGETS: dict[Profile, int] = {
    "writer": INSTRUCTIONS_BUDGET
    + CATALOG_BUDGET
    + MODULES_BUDGET
    + MATERIALS_BUDGET
    + STORY_BUDGET
    + IDEAS_BUDGET
    + INDEX_BUDGET
    + STATE_BUDGET
    + GLOSSARY_BUDGET
    + ROSTER_BUDGET
    + TIMELINE_BUDGET
    + CLUES_BUDGET
    + SETTINGS_BUDGET
    + HISTORY_BUDGET,
    "review": INSTRUCTIONS_BUDGET
    + CATALOG_BUDGET
    + MODULES_BUDGET
    + MATERIALS_BUDGET
    + STORY_BUDGET
    + IDEAS_BUDGET
    + ROSTER_BUDGET
    + TIMELINE_BUDGET
    + CLUES_BUDGET
    + SETTINGS_BUDGET,
}


@dataclass(frozen=True, slots=True)
class Exclusion:
    """一个没有进入本轮上下文的候选条目：保留定位与原因，不静默丢弃。

    ``location`` 是模型与作者都能继续查的入口；``chars`` 是这条完整渲染后的字符数，
    不用来换算 token。
    """

    id: str
    reason: Literal["budget", "limit"]
    chars: int = 0
    location: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "reason": self.reason,
            "chars": self.chars,
            "location": self.location,
        }


@dataclass(frozen=True, slots=True)
class Partition:
    """一段装入提示词的内容。

    ``budget`` 是这段内容**渲染后**允许占用的字符数（含标题行与提示文字）；
    ``required`` 为真时不允许裁掉。``excluded`` 记下这一段里没有装入的候选条目。
    """

    key: str
    title: str
    text: str
    budget: int
    required: bool = False
    excluded: tuple[Exclusion, ...] = ()

    def render(self) -> str:
        body = self.text.strip()
        return f"## {self.title}\n\n{body}" if body else ""

    @property
    def size(self) -> int:
        """实际写进提示词的字符数；预算按这个数比较。"""
        return len(self.render())


@dataclass(frozen=True, slots=True)
class Skip:
    """没装进去的分区：``budget`` 说明超预算，``empty`` 说明本来就没有内容。"""

    key: str
    reason: Literal["budget", "empty"]
    chars: int = 0


@dataclass(frozen=True, slots=True)
class AssembledContext:
    profile: Profile
    text: str
    parts: tuple[Partition, ...]
    skipped: tuple[Skip, ...]
    used: int
    limit: int

    def summary(self) -> dict[str, object]:
        """写入 ``state_injected`` 事件的形状：只记身份与规模，不复制正文。

        分区里的 ``excluded`` 只记条目 id、原因、实际字符数与继续查看的入口；
        UI 与日志读同一份结果，不另外拼一套选择记录。
        """
        parts: list[dict[str, object]] = []
        for part in self.parts:
            row: dict[str, object] = {"key": part.key, "chars": part.size}
            if part.excluded:
                row["excluded"] = [item.to_dict() for item in part.excluded]
            parts.append(row)
        return {
            "profile": self.profile,
            "parts": parts,
            "skipped": [
                {"key": skip.key, "reason": skip.reason, "chars": skip.chars}
                for skip in self.skipped
            ],
            "budget": {"limit": self.limit, "used": self.used},
        }


def body_budget(title: str, budget: int) -> int:
    """分区预算里留给正文的字符数：``## 标题`` 与空行同样占位。

    自带长度限制的分区（线索、人物状态、时间线）按这个数生成正文，避免「正文刚好
    卡满预算、加上标题就整块被跳过」。
    """
    return max(budget - len(f"## {title}\n\n"), 0)


def assemble(profile: Profile, partitions: list[Partition]) -> AssembledContext:
    """按分区顺序装入；超预算的可裁分区整块跳过，必装分区直接抛错。"""
    kept: list[Partition] = []
    skipped: list[Skip] = []
    used = 0
    for part in partitions:
        body = part.text.strip()
        if not body:
            skipped.append(Skip(key=part.key, reason="empty"))
            continue
        size = part.size
        if size > part.budget:
            if part.required:
                raise WorkspaceError(
                    code="CONTEXT_ASSEMBLY_FAILED",
                    where=f"context.{part.key}",
                    why=f"{part.title}分区渲染后需要 {size} 字符，超出预算 {part.budget}",
                    fix="缩短这一部分的内容后重试；必需分区不会被自动截断",
                    details={"key": part.key, "chars": size, "budget": part.budget},
                )
            skipped.append(Skip(key=part.key, reason="budget", chars=size))
            continue
        kept.append(part)
        used += size
    text = "\n\n".join(part.render() for part in kept)
    return AssembledContext(
        profile=profile,
        text=text,
        parts=tuple(kept),
        skipped=tuple(skipped),
        used=used,
        limit=PROFILE_BUDGETS[profile],
    )
