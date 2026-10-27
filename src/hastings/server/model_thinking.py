"""已核对的思考档位；设置入口与请求构造共用同一份能力规则。

默认值 None 表示不发送参数，交给供应商决定。未识别的模型也保持默认，
不把协议兼容理解成支持所有思考档位。规则依据见开发记录 model-thinking.md。
"""

from __future__ import annotations

import re
from typing import Literal, Protocol
from urllib.parse import urlsplit

from hastings.server.provider_catalog import ProviderApi

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]


class ThinkingProfile(Protocol):
    api: ProviderApi
    base_url: str


def is_deepseek(profile: ThinkingProfile) -> bool:
    return (
        getattr(profile, "catalog_id", "") == "deepseek"
        or urlsplit(profile.base_url).hostname == "api.deepseek.com"
    )


def thinking_levels(profile: ThinkingProfile, model: str) -> tuple[ThinkingLevel, ...]:
    """只声明已经接线并核对过的模型；不请求供应商，也不改变配置。"""
    if profile.api not in {"openai-completions", "openai-responses"}:
        return ()
    name = model.lower()
    if is_deepseek(profile):
        if profile.api != "openai-completions":
            return ()
        if name == "deepseek-flash" or re.fullmatch(
            r"deepseek-v4(?:\.1)?-(?:flash|pro)(?:-\d{4})?", name
        ):
            return ("off", "low", "high", "max")
        return ()

    # 自定义网关可转发这些公开模型名；未知别名仍保持默认。
    # chat-latest 属于聊天快照，不继承同代推理模型的能力。
    if "chat" in name or "pro" in name:
        return ()
    if re.fullmatch(r"gpt-(?:6\.1-sol|6-astra)(?:-\d{4}-\d{2}-\d{2})?", name):
        return ("low", "medium", "high", "xhigh", "max")
    if re.fullmatch(
        r"gpt-(?:6-(?:sol|luna)|5\.6(?:-(?:sol|terra|luna))?)(?:-\d{4}-\d{2}-\d{2})?",
        name,
    ):
        return ("off", "low", "medium", "high", "xhigh", "max")
    if re.fullmatch(r"gpt-5\.[23]-codex(?:-\d{4}-\d{2}-\d{2})?", name):
        return ("low", "medium", "high", "xhigh")
    if re.fullmatch(r"gpt-5\.[245](?:-(?:mini|nano))?(?:-\d{4}-\d{2}-\d{2})?", name):
        return ("off", "low", "medium", "high", "xhigh")
    if re.fullmatch(r"gpt-5\.1-codex-max(?:-\d{4}-\d{2}-\d{2})?", name):
        return ("low", "medium", "high", "xhigh")
    if re.fullmatch(r"gpt-5\.1(?:-codex(?:-mini)?)?(?:-\d{4}-\d{2}-\d{2})?", name):
        return ("low", "medium", "high") if "codex" in name else ("off", "low", "medium", "high")
    if name == "gpt-5-codex":
        return ("low", "medium", "high")
    if re.fullmatch(r"gpt-5(?:-(?:mini|nano))?(?:-\d{4}-\d{2}-\d{2})?", name):
        return ("minimal", "low", "medium", "high")
    if re.fullmatch(r"(?:o1|o3|o3-mini|o4-mini)(?:-\d{4}-\d{2}-\d{2})?", name):
        return ("low", "medium", "high")
    return ()
