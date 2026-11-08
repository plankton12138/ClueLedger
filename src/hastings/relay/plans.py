"""订阅档位定义。

价格与额度按「满额用尽也不亏」校准：额度点数 × 0.027 元（1 点 = 高峰
pro 输出 1000 tokens 成本）不超过售价扣除约 0.6% 通道费后的净收入。价格以
分为单位，时间以天为单位，集中在这里便于调整，不在路由里散落常量。
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
        price_cents=300,
        days=3,
        daily_points=0,
        total_points=100,
        description="3 天 100 点，不设每日上限，每账号限购一次。",
    ),
    Plan(
        code="light",
        name="轻享月卡",
        price_cents=1900,
        days=30,
        daily_points=20,
        total_points=0,
        description="30 天，每日 20 点，适合轻度日常使用。",
    ),
    Plan(
        code="standard",
        name="标准月卡",
        price_cents=3900,
        days=30,
        daily_points=40,
        total_points=0,
        description="30 天，每日 40 点，按购买时刻滚动重置。",
    ),
    Plan(
        code="long",
        name="长篇月卡",
        price_cents=7900,
        days=30,
        daily_points=85,
        total_points=0,
        description="30 天，每日 85 点，适合长篇集中写作。",
    ),
)

_PLANS_BY_CODE = {plan.code: plan for plan in PLANS}


def get_plan(code: str) -> Plan | None:
    return _PLANS_BY_CODE.get(code)
