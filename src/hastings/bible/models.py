"""Story Bible 实体 schema（Pydantic，P2 五账本）。

P1 只有 Character；P2 扩为五账本：character（+弧光状态机）、clue、
foreshadow、method（含 alibi_claims，计划 D3）、timeline（无状态机，D1/D2）。
状态字段的默认值须与 state_machine.INITIAL_STATUS 一致——强制不变量在
工具层校验（validate_initial / validate_transition），模型默认值只是让
合法路径零参数可用。
"""

from typing import Literal

from pydantic import BaseModel, Field

# 状态字面量与 state_machine 迁移表共用取值；默认值对齐 INITIAL_STATUS。
ArcStatus = Literal["flat", "developing", "transformed"]
ClueStatus = Literal["planned", "planted", "discovered", "interpreted", "resolved"]
ForeshadowStatus = Literal["planned", "planted", "echoed", "resolved"]
MethodStatus = Literal["drafted", "locked", "revealed"]
PlantMode = Literal["explicit", "hint", "symbolic"]


class Character(BaseModel):
    """角色卡：一人一文件，存于 bible/characters/<name>.yaml。"""

    name: str = Field(min_length=1, max_length=64)
    identity: str = ""
    motivation: str = ""
    secret: str = ""
    relationships: list[str] = Field(default_factory=list)
    arc: ArcStatus = "flat"  # 弧光三阶段；迁移边见 state_machine.TRANSITIONS
    notes: str = ""


class Clue(BaseModel):
    """线索：clues.yaml 列表项，name 为主键（D7）。"""

    name: str = Field(min_length=1, max_length=64)
    content: str = ""
    chapter: str = ""  # 出现章节，如 "ch03"
    points_to: str = ""  # 指向的嫌疑人/方向
    is_red_herring: bool = False  # 干扰项
    is_genuine: bool = False  # 真实线索
    status: ClueStatus = "planned"


class AlibiClaim(BaseModel):
    """不在场主张（D3 方案 A）：timeline_check 将其与真实事件区间求交。

    day/t_start/t_end 的合法性由 timeline_check R0 统一回喂，
    模型层不做格式硬约束（保持"数据错误"与"参数违规"同一出口）。
    """

    person: str = Field(min_length=1, max_length=64)
    day: int  # 故事内第几天（从 1 起）
    t_start: str | None = None  # "HH:MM"，可选（D2）
    t_end: str | None = None
    claim: str = ""  # 主张原文，如 "声称整晚在大厅下棋"


class MethodHistoryEntry(BaseModel):
    """手法锁定/解锁记录（T4：章节 + 理由 + 时间戳）。"""

    action: Literal["locked", "unlocked"]
    chapter: str = ""
    reason: str = ""
    timestamp: str = ""  # ISO 8601


class Method(BaseModel):
    """诡计手法：method.yaml 列表项，允许多条（支线案件），锁定按 name（D7）。"""

    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    time_window: str = ""  # 自然语言窗口描述，如 "第3天晚 21:00-23:00"
    requirements: list[str] = Field(default_factory=list)  # 诡计成立的前提
    alibi_claims: list[AlibiClaim] = Field(default_factory=list)
    history: list[MethodHistoryEntry] = Field(default_factory=list)
    status: MethodStatus = "drafted"


class Foreshadow(BaseModel):
    """伏笔：foreshadows.yaml 列表项（D7）。"""

    name: str = Field(min_length=1, max_length=64)
    content: str = ""
    plant_chapter: str = ""  # 埋设章节
    plant_mode: PlantMode = "hint"  # 明写 / 暗示 / 象征
    expected_resolve_chapter: str = ""  # 预计回收章节（P5 对账用）
    status: ForeshadowStatus = "planned"


class TimelineEntry(BaseModel):
    """时间线事件：timeline.yaml 列表项；只增改，无状态机（D1/D2）。"""

    name: str = Field(min_length=1, max_length=64)
    day: int  # 故事内第几天（从 1 起）；day<1 由 timeline_check R0 回喂
    t_start: str | None = None  # "HH:MM"，可选（D2）
    t_end: str | None = None
    chapter: str = ""  # 该事件在哪一章叙述
    participants: list[str] = Field(default_factory=list)  # 须已建档（D8）
    note: str = ""  # 相对时间原文（"三天后"），day 为换算结果


__all__ = [
    "AlibiClaim",
    "ArcStatus",
    "Character",
    "Clue",
    "ClueStatus",
    "Foreshadow",
    "ForeshadowStatus",
    "Method",
    "MethodHistoryEntry",
    "MethodStatus",
    "PlantMode",
    "TimelineEntry",
]
