"""story.yaml 文档模型（V1 弃用后的存留读半区）。

story.yaml 是工作区级文件（与 bible/、chapters/ 平级），描述「作品走到哪了」。
V1 生命周期写路径（story_stage 工具、推进/回退迁移表、intake 写门）已随
「V1 工作区只读退役」删除（Phase 17 收尾决策）；本模块只保留读取所需的
模型：阶段字面量、迁移留痕与文档模型——REST /story 快照、stats 状态注入、
迁移导入器（story.yaml 转为项目里程碑说明）仍消费它们。
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

# 六阶段（历史语义）：intake 引导 → bible_drafting 起草 → bible_confirmed 确认
# → writing 逐章写作 → revising 维护修订 → complete 终稿。只读展示与导入分类用。
StoryStage = Literal[
    "intake",
    "bible_drafting",
    "bible_confirmed",
    "writing",
    "revising",
    "complete",
]

STAGES: Final[tuple[str, ...]] = (
    "intake",
    "bible_drafting",
    "bible_confirmed",
    "writing",
    "revising",
    "complete",
)


class StageTransition(BaseModel):
    """阶段迁移留痕（story.yaml 的 stage_history 列表项，计划 D1）。

    YAML 键用 from/to（与计划文档一致），字段名避开保留字走 alias；
    at 由 store 层写盘时填 ISO 时间戳，本模块不做时钟依赖。
    """

    model_config = ConfigDict(populate_by_name=True)

    from_stage: str = Field(alias="from")
    to_stage: str = Field(alias="to")
    reason: str = ""
    at: str = ""  # ISO 8601


class StoryDoc(BaseModel):
    """story.yaml 文档模型：工作区生命周期状态（非账本，计划 D1）。"""

    slug: str
    stage: StoryStage = "intake"
    stage_history: list[StageTransition] = Field(default_factory=list)
