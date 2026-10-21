"""供应商接入预设（V04-9A）。

预设回答的是「这家服务怎么接」：默认地址、协议、文档入口、能不能读模型列表。
它不含任何密钥、也不代表作者已经连上——作者自己的账号是另一个对象（连接），
所以同一个预设可以建多个连接。

目录放在后端，前端只渲染接口返回值：供应商表有两份就会各自漂移，而一处地址写错
的表现是「保存成功、调用失败」，最难让作者自己看出来。

地址与协议只填已核对的公开接入信息；没核对的地址留空让作者手填，不靠拼接域名补齐。
运行时不自动刷新第三方目录，也不在后台探测供应商——只有作者点「获取模型列表」
才会发出请求。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "PRESETS",
    "Discovery",
    "ProviderApi",
    "ProviderPreset",
    "RegionOption",
    "preset",
    "presets",
]

#: 已实现的四类请求协议。连接保存的就是其中一个值，运行时不再按供应商名推断。
ProviderApi = Literal[
    "openai-completions",
    "openai-responses",
    "anthropic-messages",
    "google-generative-ai",
]

#: 模型列表读取策略；None 表示该预设没有列表接口，只能手填。
Discovery = Literal["openai-models", "anthropic-models", "google-models"]


@dataclass(frozen=True, slots=True)
class RegionOption:
    """同一家服务的不同服务区域。区域影响地址，必须由作者选，不猜。"""

    id: str
    label: str
    base_url: str


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    """一个供应商的接入预设：默认地址 + 协议 + 文档 + 列表策略（+ 可选区域）。"""

    id: str
    display_name: str
    api: ProviderApi
    base_url: str
    docs_url: str
    discovery: Discovery | None
    #: 该预设允许作者在高级设置里改成的协议；第一个是默认值。
    api_options: tuple[ProviderApi, ...] = ()
    regions: tuple[RegionOption, ...] = ()
    note: str = ""
    #: 是否在界面上作为「自定义服务」入口（地址留空，协议自选）。
    custom: bool = False

    @property
    def apis(self) -> tuple[ProviderApi, ...]:
        return self.api_options or (self.api,)


_QWEN_REGIONS = (
    RegionOption(
        id="beijing",
        label="华北2（北京）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    RegionOption(
        id="singapore",
        label="新加坡",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    ),
)

PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        id="openai",
        display_name="OpenAI",
        api="openai-responses",
        base_url="https://api.openai.com/v1",
        docs_url="https://platform.openai.com/docs/api-reference/responses",
        discovery="openai-models",
        api_options=("openai-responses", "openai-completions"),
        note="默认走 Responses；旧账号或代理网关不支持时，可在高级设置改用 Completions。",
    ),
    ProviderPreset(
        id="deepseek",
        display_name="DeepSeek",
        api="openai-completions",
        base_url="https://api.deepseek.com/v1",
        docs_url="https://api-docs.deepseek.com/",
        discovery="openai-models",
    ),
    ProviderPreset(
        id="qwen",
        display_name="通义千问（阿里云百炼）",
        api="openai-completions",
        base_url=_QWEN_REGIONS[0].base_url,
        docs_url="https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope",
        discovery="openai-models",
        regions=_QWEN_REGIONS,
        note="服务区域不同，接入地址也不同；请按账号所在区域选择。",
    ),
    ProviderPreset(
        id="moonshot",
        display_name="Moonshot（月之暗面）",
        api="openai-completions",
        base_url="https://api.moonshot.cn/v1",
        docs_url="https://platform.moonshot.cn/docs/api/chat",
        discovery="openai-models",
    ),
    ProviderPreset(
        id="zhipu",
        display_name="智谱 AI",
        api="openai-completions",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        docs_url="https://open.bigmodel.cn/dev/api",
        discovery="openai-models",
    ),
    ProviderPreset(
        id="openrouter",
        display_name="OpenRouter",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        docs_url="https://openrouter.ai/docs/api-reference/overview",
        discovery="openai-models",
        note="一个连接代表你在 OpenRouter 的账号；它转发的模型仍属于这个连接。",
    ),
    ProviderPreset(
        id="anthropic",
        display_name="Anthropic",
        api="anthropic-messages",
        base_url="https://api.anthropic.com/v1",
        docs_url="https://docs.anthropic.com/en/api/messages",
        discovery="anthropic-models",
    ),
    ProviderPreset(
        id="google",
        display_name="Google Gemini（API Key）",
        api="google-generative-ai",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        docs_url="https://ai.google.dev/api/models",
        discovery="google-models",
        note="用 Google AI Studio 的 API Key，不用订阅账号登录。",
    ),
    ProviderPreset(
        id="custom",
        display_name="自定义服务",
        api="openai-completions",
        base_url="",
        docs_url="",
        discovery=None,
        api_options=(
            "openai-completions",
            "openai-responses",
            "anthropic-messages",
            "google-generative-ai",
        ),
        note="填写完整地址并选择协议；模型需要手动添加。",
        custom=True,
    ),
)

_BY_ID = {preset.id: preset for preset in PRESETS}


def preset(preset_id: str) -> ProviderPreset | None:
    """按 ID 取预设；未知值返回 None（旧配置里的未知 catalog_id 不阻止读取）。"""
    return _BY_ID.get(preset_id)


def presets() -> tuple[ProviderPreset, ...]:
    return PRESETS
