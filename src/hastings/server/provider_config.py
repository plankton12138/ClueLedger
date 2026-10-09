"""服务级 provider 档案配置（P6 T1，D1）。

一处事实源：config.yaml 多档案（可更换的模型端点目录）+ env 单档案回落。
与工作区级 settings.yaml 的职责分界：
- config.yaml 是服务级的——描述「有哪些可用端点」；
- settings.yaml 是工作区级的——描述「这个故事用哪一个」（P6 T2 扩展）。

泄压阀（沿用 settings.yaml 惯例）：缺文件/空文件 → env 单档案回落
（name="default"，P1 起的现状，零破坏）；结构性损坏（根节点非映射、
档案缺 name/base_url、档案名重复、空列表）→ ProviderConfigError 三要素
——模型配置错了应该当场知道，而不是写作到一半才发现。

key 只进不出：ProviderProfile.api_key 用 SecretStr（repr/日志不泄露），
任何对外输出只经 mask_key（尾 4 位）。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from tau_ai.env import DEFAULT_OPENAI_COMPATIBLE_BASE_URL, OpenAICompatibleConfig
from tau_ai.openai_compatible import OpenAICompatibleProvider

DEFAULT_CONFIG_PATH = Path("config.yaml")


class ProviderConfigError(ValueError):
    """config.yaml 结构损坏或启动时无可用档案（三要素文案随异常携带）。"""


class ProviderProfile(BaseModel):
    """一个模型端点档案：名字 + 端点 + key（直填或 env 引用）+ 默认模型。"""

    model_config = ConfigDict(extra="forbid")  # 拼错字段早失败（如 api_ky）

    name: str
    base_url: str
    api_key: SecretStr = SecretStr("")
    api_key_env: str = ""
    default_model: str = ""

    def resolved_api_key(self) -> str:
        """解析实际 key：直填优先于 env 引用（D1）。两者皆空 → 空串。"""
        direct = self.api_key.get_secret_value()
        if direct:
            return direct
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""

    @property
    def available(self) -> bool:
        """启动/切换的可用性 = 能解析出非空 key。"""
        return bool(self.resolved_api_key())


class ServerConfig(BaseModel):
    """服务级配置根：档案目录。档案名唯一（工作区设置按名引用）。"""

    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderProfile]


def env_fallback_config() -> ServerConfig:
    """由当前 env 编译出的单档案（config.yaml 缺失时的回落，即 P1 现状）。"""
    base_url = os.environ.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_COMPATIBLE_BASE_URL
    return ServerConfig(
        providers=[
            ProviderProfile(
                name="default",
                base_url=base_url.rstrip("/"),
                api_key_env="OPENAI_API_KEY",
                default_model=os.environ.get("HASTINGS_MODEL", ""),
            )
        ]
    )


def load_server_config(path: Path = DEFAULT_CONFIG_PATH) -> ServerConfig:
    """加载服务级配置；缺文件/空文件 → env 回落，结构性损坏 → 三要素报错。"""
    if not path.exists():
        return env_fallback_config()
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        # 空文件等价于「未配置」→ env 回落（与 settings.yaml 的空文件惯例一致）
        return env_fallback_config()
    if not isinstance(raw, dict):
        raise ProviderConfigError(
            f"config.yaml 损坏：根节点应为 YAML 映射（providers 列表），"
            f"实际是 {type(raw).__name__}（{path}）。"
            "为什么错：文件结构不对，无法解析出任何模型端点。"
            "怎样算对：参照 config.example.yaml 修复文件结构，"
            "或删除该文件回落 .env 单档案配置。"
        )
    try:
        config = ServerConfig.model_validate(raw)
    except ValidationError as exc:
        raise ProviderConfigError(_validation_error_message(path, exc)) from exc
    if not config.providers:
        raise ProviderConfigError(
            f"config.yaml 损坏：providers 列表为空（{path}）。"
            "为什么错：启动后没有任何可用的模型端点。"
            "怎样算对：配置至少一个档案（含 name / base_url 与 key），"
            "或删除该文件回落 .env 单档案配置。"
        )
    names = [profile.name for profile in config.providers]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ProviderConfigError(
            f"config.yaml 损坏：档案名重复 {duplicates}（{path}）。"
            "为什么错：工作区设置按档案名引用端点，重名无法解析。"
            "怎样算对：为每个档案取唯一的名字。"
        )
    return config


def _validation_error_message(path: Path, exc: ValidationError) -> str:
    """字段级定位原样透传（loc: msg），不做人话翻译——错误消息是喂给用户的 API。"""
    lines = "；".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
    )
    return (
        f"config.yaml 损坏：{lines}（{path}）。"
        "为什么错：字段缺失、类型不对或存在未知字段（多为拼写错误）。"
        "怎样算对：每个档案必含 name / base_url，可选 api_key（直填）、"
        "api_key_env（env 引用）与 default_model；参照 config.example.yaml。"
    )


def mask_key(key: str) -> str:
    """key 脱敏（尾 4 位）：key 只进不出，对外只给这个。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "…"
    return f"…{key[-4:]}"


def build_provider(profile: ProviderProfile) -> OpenAICompatibleProvider:
    """由档案构造 provider 实例（跨档案切换/启动装配共用；T2 复用）。"""
    return OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            api_key=profile.resolved_api_key(),
            base_url=profile.base_url.rstrip("/"),
        )
    )


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ProviderConfigError",
    "ProviderProfile",
    "ServerConfig",
    "build_provider",
    "env_fallback_config",
    "load_server_config",
    "mask_key",
]
