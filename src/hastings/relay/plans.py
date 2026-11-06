"""订阅档位定义。

数值来自 ``dev-notes/v1.4/subscription-plan.md`` 的建议值。价格以分为单位，
时间以天为单位，集中在这里便于上线前调整，不在路由里散落常量。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Plan:
    code: str
    name: str
    price_cents: int
    days: int
    daily_points: int
    total_points: int
    description: str

    @property
    def price(self) -> Decimal:
        return (Decimal(self.price_cents) / Decimal(100)).quantize(Decimal("0.01"))

    @property
    def price_text(self) -> str:
        return f"{self.price:.2f}"


PLANS: tuple[Plan, ...] = (
    Plan(
        code="experience",
        name="体验卡",
        price_cents=900,
        days=3,
        daily_points=0,
        total_points=180,
        description="3 天 180 点，不设每日上限，每账号限购一次。",
    ),
    Plan(
        code="standard",
        name="标准月卡",
        price_cents=6900,
        days=30,
        daily_points=60,
        total_points=0,
        description="30 天，每日 60 点，按购买时刻滚动重置。",
    ),
    Plan(
        code="long",
        name="长篇月卡",
        price_cents=12900,
        days=30,
        daily_points=120,
        total_points=0,
        description="30 天，每日 120 点，适合长篇集中写作。",
    ),
)

_PLANS_BY_CODE = {plan.code: plan for plan in PLANS}


def get_plan(code: str) -> Plan | None:
    return _PLANS_BY_CODE.get(code)
