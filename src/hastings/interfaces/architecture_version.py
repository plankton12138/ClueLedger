"""architecture_version 解析点（Phase 11 T11.6；决策见 decisions/architecture-version-routing.md）。

``architecture_version = v1 | v2`` 是项目级静态元数据，不是全局配置、用户偏好
或请求参数。按 ADR §2.2，判定点收敛在「项目解析这一处」，不得散落到各用例内部
各自判 flag。

本模块只做**纯解析**：给元数据 → 返回路由版本；元数据的读取（slug → 元数据，
由项目注册表 / 工作区承载）留给调用方，以便解析保持纯函数、可单测，无需起服务。
缺失或非法一律回退 v1——对齐 V1「缺文件按兜底值处理」的既有惯例。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

__all__ = [
    "ARCHITECTURE_VERSION_KEY",
    "ArchitectureVersion",
    "V1",
    "V2",
    "resolve_architecture_version",
]

ARCHITECTURE_VERSION_KEY = "architecture_version"

ArchitectureVersion = Literal["v1", "v2"]

V1: Final[ArchitectureVersion] = "v1"
V2: Final[ArchitectureVersion] = "v2"


def resolve_architecture_version(metadata: Mapping[str, object] | None) -> ArchitectureVersion:
    """从项目元数据解析架构版本；缺失/非法回退 v1。

    只认精确的 ``"v2"``：其余取值（包含 None、大小写变体、未知字符串）一律
    视为 v1，绝不让一个拼错的开关把项目静默切到 V2 写路径。
    """
    if not metadata:
        return V1
    return V2 if metadata.get(ARCHITECTURE_VERSION_KEY) == V2 else V1
