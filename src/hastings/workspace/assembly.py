"""上下文装配：分区、预算与注入记录（0.6 V06-E1）。

装配回答两个问题：这一轮该给什么、实际给了多少。分区带稳定 key 与预算——

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
    "INSTRUCTIONS_BUDGET",
    "MATERIALS_BUDGET",
    "PROFILE_BUDGETS",
    "CLUES_BUDGET",
    "GLOSSARY_BUDGET",
    "ROSTER_BUDGET",
    "TIMELINE_BUDGET",
    "SETTINGS_BUDGET",
    "STATE_BUDGET",
    "AssembledContext",
    "Partition",
    "Profile",
    "Skip",
    "assemble",
]

#: 用途决定装哪些分区。受理时只知道用途（写正文、建索引、做检查），
#: 不知道作者这一轮想讨论还是想改稿，所以「讨论」不单列类型。
Profile = Literal["writer", "extract", "review"]

#: 各分区初始预算，集中定义、集中测试，不做成用户配置。
INSTRUCTIONS_BUDGET = 4_000
CATALOG_BUDGET = 12_000
MATERIALS_BUDGET = 8_000
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
PROFILE_BUDGETS: dict[Profile, int] = {
    "writer": INSTRUCTIONS_BUDGET
    + CATALOG_BUDGET
    + MATERIALS_BUDGET
    + 8_000
    + STATE_BUDGET
    + GLOSSARY_BUDGET
    + ROSTER_BUDGET
    + TIMELINE_BUDGET
    + CLUES_BUDGET
    + SETTINGS_BUDGET
    + HISTORY_BUDGET,
    "extract": INSTRUCTIONS_BUDGET + CATALOG_BUDGET,
    "review": INSTRUCTIONS_BUDGET
    + CATALOG_BUDGET
    + MATERIALS_BUDGET
    + ROSTER_BUDGET
    + TIMELINE_BUDGET
    + CLUES_BUDGET
    + SETTINGS_BUDGET,
}


@dataclass(frozen=True, slots=True)
class Partition:
    """一段装入提示词的内容。``required`` 为真时不允许裁掉。"""

    key: str
    title: str
    text: str
    budget: int
    required: bool = False

    def render(self) -> str:
        body = self.text.strip()
        return f"## {self.title}\n\n{body}" if body else ""


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
        """写入 ``state_injected`` 事件的形状：只记身份与规模，不复制正文。"""
        return {
            "profile": self.profile,
            "parts": [{"key": part.key, "chars": len(part.text.strip())} for part in self.parts],
            "skipped": [
                {"key": skip.key, "reason": skip.reason, "chars": skip.chars}
                for skip in self.skipped
            ],
            "budget": {"limit": self.limit, "used": self.used},
        }


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
        size = len(body)
        if size > part.budget:
            if part.required:
                raise WorkspaceError(
                    code="CONTEXT_ASSEMBLY_FAILED",
                    where=f"context.{part.key}",
                    why=f"{part.title}分区需要 {size} 字符，超出预算 {part.budget}",
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
