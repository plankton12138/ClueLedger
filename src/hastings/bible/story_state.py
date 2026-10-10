"""工作区生命周期状态机（P8 T1，计划 D1/D2）：纯函数迁移表，不碰文件系统。

story.yaml 是工作区级文件（与 bible/、chapters/ 平级），不是第五账本之外的新
账本——它描述「作品走到哪了」而非「故事里有什么」。职责分工沿项目四透镜
哲学：门是硬约束（write_chapter 工具层，T4 接线），推进是协议（模型在作者
确认后经 story_stage 工具调用，T2 接线）；本模块只提供迁移表与校验。

错误消息三要素与 state_machine.py 同哲学：哪里错（current → target）、
为什么错（无此边/前置条件不满足）、怎样算对（合法去向或缺什么）。
读写（YAML 落盘、旧工作区 intake 兜底）归 store 层（T1b）。
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

# 六阶段（计划 D1）：intake 引导 → bible_drafting 起草 → bible_confirmed 确认
# → writing 逐章写作 → revising 维护修订 → complete 终稿。
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

# 正向推进边（story_stage advance）；未列出的边一律拒绝。
ADVANCE_EDGES: Final[dict[str, list[str]]] = {
    "intake": ["bible_drafting"],
    "bible_drafting": ["bible_confirmed"],
    "bible_confirmed": ["writing"],
    "writing": ["revising", "complete"],
    "revising": ["complete"],
    "complete": [],
}

# 回退边（story_stage rollback）：作者驱动的相邻回退——推翻设定、终稿返工、
# 维护中继续写作。回退不做账本前置检查（作者有权随时重来）。
ROLLBACK_EDGES: Final[dict[str, list[str]]] = {
    "bible_confirmed": ["bible_drafting"],
    "revising": ["writing"],
    "complete": ["revising"],
}

_CONFIRM_TARGET = "bible_confirmed"


class StagePrereqs(BaseModel):
    """推进前置条件的输入快照（T2 工具层从工作区统计派生，本模块保持纯函数）。"""

    characters: int = Field(ge=0, default=0)
    methods: int = Field(ge=0, default=0)
    methods_locked: int = Field(ge=0, default=0)
    outlines: int = Field(ge=0, default=0)


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


class InvalidStageTransitionError(ValueError):
    """非法阶段迁移或推进前置条件不满足。

    错误消息三要素（喂回模型助其自我修正）：哪里错、为什么错、怎样算对。
    """


def _legal_targets(stage: str) -> str:
    advances = "、".join(ADVANCE_EDGES.get(stage, [])) or "（无，已是终点）"
    rollbacks = "、".join(ROLLBACK_EDGES.get(stage, [])) or "（无）"
    return f"可推进：{advances}；可回退：{rollbacks}"


def _confirmation_gap(prereqs: StagePrereqs) -> list[str]:
    """确认门缺口清单（计划 D2）：角色 ≥2、手法 ≥1 且全部 locked、章纲 ≥1。"""
    gaps: list[str] = []
    if prereqs.characters < 2:
        gaps.append(f"角色 {prereqs.characters}/2（至少 2 个）")
    if prereqs.methods < 1:
        gaps.append("手法 0 条（至少 1 条且须锁定）")
    elif prereqs.methods_locked < prereqs.methods:
        gaps.append(f"手法未全部锁定（{prereqs.methods_locked}/{prereqs.methods}）")
    if prereqs.outlines < 1:
        gaps.append("章纲 0 条（至少 1 条）")
    return gaps


def validate_transition(
    stage: str,
    target: str,
    *,
    prereqs: StagePrereqs | None = None,
) -> None:
    """校验 stage → target 是否合法；推进时按确认门检查账本前置条件。

    Raises:
        InvalidStageTransitionError: stage 未知、迁移边未登记、或推进
            bible_confirmed 时前置条件不满足。
    """
    if stage == target:  # no-op 放行（幂等重交）
        return
    if stage not in ADVANCE_EDGES:
        raise InvalidStageTransitionError(
            f"未知阶段：{stage!r}。"
            f"为什么错：阶段只取 {('、'.join(STAGES))}。"
            "怎样算对：先 bible_query/read story.yaml 核对当前阶段。"
        )

    is_advance = target in ADVANCE_EDGES[stage]
    is_rollback = target in ROLLBACK_EDGES.get(stage, [])
    if not (is_advance or is_rollback):
        raise InvalidStageTransitionError(
            f"阶段迁移被拒：{stage} → {target}。"
            "为什么错：迁移表没有登记这条边。"
            f"怎样算对：{_legal_targets(stage)}。"
        )

    if is_advance and target == _CONFIRM_TARGET:
        gap = _confirmation_gap(prereqs or StagePrereqs())
        if gap:
            raise InvalidStageTransitionError(
                f"推进被拒：{stage} → {target}。"
                f"为什么错：确认前置条件不满足——{'；'.join(gap)}。"
                "怎样算对：补齐缺口（建角色、bible_upsert 章纲、method_lock 锁定手法）"
                "后重试推进；推进须经作者明确确认。"
            )
