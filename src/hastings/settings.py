"""工作区级设置（P6 写作语言 + 模型端点档案）。

一处事实源：可配置的写作语言预设与默认值、provider 档案名与模型名，
读写 stories/<slug>/settings.yaml。
缺文件/空文件视为默认值（向后兼容 P1 起的旧工作区）；根节点非映射等结构性
损坏抛 SettingsFormatError——与"空设置"（合法状态）区分开，属数据故障。

设计约束：全部 yaml 调用收敛在本模块（沿用 bible/store.py 惯例），
业务代码只面对 Pydantic 模型。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel

from hastings.bible.store import StoryWorkspace
from hastings.interfaces.architecture_version import (
    ArchitectureVersion,
    resolve_architecture_version,
)

# 预设写作语言；模型产出的账本条目 / 正文 / 对话回复一律用该语言。
WRITING_LANGUAGES: Final[tuple[str, ...]] = ("中文", "English", "日本語")
DEFAULT_WRITING_LANGUAGE: Final[str] = "中文"

# 默认 provider 档案名（与 config.yaml 缺失时的 env 回落档案同名）。
DEFAULT_PROVIDER_PROFILE: Final[str] = "default"

# 上下文窗口（token，P10 D8/L9）：压缩触发阈值 = 本值 − reserve（16k）。
# 缺省 128k 对齐云端常用窗口；本地小窗口（如 32k）在 settings.yaml 显式下调。
DEFAULT_CONTEXT_WINDOW_TOKENS: Final[int] = 128_000

# V2 迁移映射键（settings.yaml 承载的项目级元数据，T17.5）：
# v2_project_id —— 迁移得到的 V2 项目；v2_db_path —— V2 仓库数据库路径。
V2_PROJECT_ID_KEY: Final[str] = "v2_project_id"
V2_DB_PATH_KEY: Final[str] = "v2_db_path"


class SettingsFormatError(ValueError):
    """settings.yaml 根节点非映射等结构性损坏。

    区别于"缺文件/空文件"（合法，回落默认值）与 pydantic 字段错误（原样上抛），
    这是环境/数据故障，需要显式暴露给用户修复。
    """


class StorySettings(BaseModel):
    """一个工作区的可配置项：写作语言 + 模型端点档案 + 模型名（P6 T2）。

    ``provider_profile`` 指向服务级 config.yaml 的档案名（D1）；
    ``model`` 为空表示取该档案的 default_model。
    """

    writing_language: str = DEFAULT_WRITING_LANGUAGE
    provider_profile: str = DEFAULT_PROVIDER_PROFILE
    model: str = ""
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS


def workspace_architecture_version(workspace: StoryWorkspace) -> ArchitectureVersion:
    """读取工作区项目元数据并解析架构版本（ADR §2.2 的「项目解析这一处」）。

    settings.yaml 是本项目唯一的项目级元数据承载处：``architecture_version: v2``
    把该项目路由到 V2 装配（domain handler 写路径、bible_upsert 拒绝桩）；缺键、
    缺文件或取值非法一律回退 v1，与「缺文件按兜底值」的既有惯例一致。

    读取（I/O）收敛在本模块，解析（纯函数）委托给
    ``hastings.interfaces.architecture_version``——两处装配点（CLI 与 Web 会话）
    因此共用同一判定，而不是各自判 flag。
    """
    path = workspace.settings_path
    if not path.exists():
        return resolve_architecture_version(None)
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return resolve_architecture_version(raw if isinstance(raw, Mapping) else None)


def load_settings(workspace: StoryWorkspace) -> StorySettings:
    """读取工作区设置；缺文件或空文件 → 默认值（P1 旧工作区兼容）。"""
    path = workspace.settings_path
    if not path.exists():
        return StorySettings()
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return StorySettings()
    if not isinstance(raw, dict):
        msg = f"设置文件损坏（根节点应为 YAML 映射，实际为 {type(raw).__name__}）：{path}"
        raise SettingsFormatError(msg)
    return StorySettings.model_validate(raw)


def _raw_settings(workspace: StoryWorkspace) -> dict[str, object]:
    """读 settings.yaml 原始映射；缺文件/空文件/结构损坏一律回落空映射。

    与 ``load_settings`` 的「结构性损坏抛错」不同：迁移映射的读取方（CLI/WS）
    只想知道「有没有迁移过」，坏文件在架构版本解析（``workspace_architecture_version``）
    与设置页读取两条既有路径上自有各自的暴露方式，这里不重复报错。
    """
    path = workspace.settings_path
    if not path.exists():
        return {}
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return dict(raw) if isinstance(raw, dict) else {}


def v2_project_id(workspace: StoryWorkspace) -> str | None:
    """该工作区迁移得到的 V2 项目 ID；未迁移返回 None。

    这是「slug → V2 项目」映射的唯一读取点（CLI 与 Web 共用），映射本体在
    迁移落盘时由 ``record_v2_migration`` 写入——与 ``architecture_version``
    同处 settings.yaml，避免第二套元数据承载。
    """
    value = _raw_settings(workspace).get(V2_PROJECT_ID_KEY)
    return str(value) if value else None


def v2_db_path(workspace: StoryWorkspace) -> str | None:
    """V2 仓库数据库路径（服务启动 --v2-db 的值，迁移时记录）；未记录返回 None。"""
    value = _raw_settings(workspace).get(V2_DB_PATH_KEY)
    return str(value) if value else None


def record_v2_migration(
    workspace: StoryWorkspace, project_id: str, db_path: str | None = None
) -> Path:
    """迁移落盘：写 ``architecture_version: v2`` + 项目/数据库映射，保留未知键。

    与 ``save_settings`` 同一防护：先读回既有映射，只覆盖迁移相关键——
    不走 ``StorySettings`` 模型（迁移映射不是用户设置）。
    写入 ``architecture_version`` 后，旧写路径门（REST/工具层/CLI）即刻生效。
    """
    workspace.ensure_dirs()
    path = workspace.settings_path
    payload = _raw_settings(workspace)
    payload["architecture_version"] = "v2"
    payload[V2_PROJECT_ID_KEY] = project_id
    if db_path is not None:
        payload[V2_DB_PATH_KEY] = db_path
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def save_settings(workspace: StoryWorkspace, settings: StorySettings) -> Path:
    """整表覆盖写设置文件，返回文件路径；自建父目录。

    ``settings.yaml`` 同时承载**项目级元数据**（如 ``architecture_version``），
    它们不属于 ``StorySettings`` 的字段。整表覆盖若直接 dump 模型会把元数据抹掉——
    一次普通的「切换写作语言」会静默把 V2 项目降级回 V1 路由，使旧写路径门失效
    （Phase 17 T17.6 补验收发现）。因此先读回既有映射、只覆盖模型字段，保留未知键。
    """
    workspace.ensure_dirs()
    path = workspace.settings_path
    preserved: dict[str, object] = {}
    if path.exists():
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            fields = set(StorySettings.model_fields)
            preserved = {key: value for key, value in raw.items() if key not in fields}
    payload = {**preserved, **settings.model_dump()}
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


__all__ = [
    "DEFAULT_CONTEXT_WINDOW_TOKENS",
    "DEFAULT_PROVIDER_PROFILE",
    "DEFAULT_WRITING_LANGUAGE",
    "V2_DB_PATH_KEY",
    "V2_PROJECT_ID_KEY",
    "WRITING_LANGUAGES",
    "SettingsFormatError",
    "StorySettings",
    "load_settings",
    "record_v2_migration",
    "save_settings",
    "v2_db_path",
    "v2_project_id",
    "workspace_architecture_version",
]
