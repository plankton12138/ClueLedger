"""Story Bible 实体 schema（Pydantic）。

字段是 DESIGN.md §2.2 角色行的 P1 最小子集；弧光阶段等状态机字段
属于 P2，届时在此扩展并配套迁移校验。
"""

from pydantic import BaseModel, Field


class Character(BaseModel):
    """角色卡：一人一文件，存于 bible/characters/<name>.yaml。"""

    name: str = Field(min_length=1, max_length=64)
    identity: str = ""
    motivation: str = ""
    secret: str = ""
    relationships: list[str] = Field(default_factory=list)
    notes: str = ""


__all__ = ["Character"]
