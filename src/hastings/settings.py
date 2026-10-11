"""工作区级设置（P6 写作语言 + 模型端点档案）。

一处事实源：可配置的写作语言预设与默认值、provider 档案名与模型名，
读写 stories/<slug>/settings.yaml。
缺文件/空文件视为默认值（向后兼容 P1 起的旧工作区）；根节点非映射等结构性
损坏抛 SettingsFormatError——与"空设置"（合法状态）区分开，属数据故障。

设计约束：全部 yaml 调用收敛在本模块（沿用 bible/store.py 惯例），
业务代码只面对 Pydantic 模型。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel

from hastings.bible.store import StoryWorkspace

# 预设写作语言；模型产出的账本条目 / 正文 / 对话回复一律用该语言。
WRITING_LANGUAGES: Final[tuple[str, ...]] = ("中文", "English", "日本語")
DEFAULT_WRITING_LANGUAGE: Final[str] = "中文"

# 默认 provider 档案名（与 config.yaml 缺失时的 env 回落档案同名）。
DEFAULT_PROVIDER_PROFILE: Final[str] = "default"

# 上下文窗口（token，P10 D8/L9）：压缩触发阈值 = 本值 − reserve（16k）。
# 缺省 128k 对齐云端常用窗口；本地小窗口（如 32k）在 settings.yaml 显式下调。
DEFAULT_CONTEXT_WINDOW_TOKENS: Final[int] = 128_000


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


def save_settings(workspace: StoryWorkspace, settings: StorySettings) -> Path:
    """整表覆盖写设置文件，返回文件路径；自建父目录。"""
    workspace.ensure_dirs()
    path = workspace.settings_path
    path.write_text(
        yaml.safe_dump(settings.model_dump(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


__all__ = [
    "DEFAULT_CONTEXT_WINDOW_TOKENS",
    "DEFAULT_PROVIDER_PROFILE",
    "DEFAULT_WRITING_LANGUAGE",
    "WRITING_LANGUAGES",
    "SettingsFormatError",
    "StorySettings",
    "load_settings",
    "save_settings",
]
