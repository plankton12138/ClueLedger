"""内建 provider 目录（P7c T15）：登录式模型接入的数据底座。

对齐 tau_coding `/login` 的体验内核——provider 条目预填 base_url / models /
default_model / api_key_env，用户「点选 → 贴 key」两步即用，不必手填端点。
与 tau 的 catalog.toml 相比刻意瘦身：只保留 Hastings 用得到的字段
（OpenAI 兼容 + /models 发现），不含 thinking/cost/context_window。
目录是纯数据（无密钥），key 仍由用户填入后经既有 PUT /api/providers/{name}
upsert 写入 config.yaml——目录不参与凭证存储。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderCatalogEntry:
    """目录中的一个模型来源：登录表单的预填默认值。"""

    name: str
    display_name: str
    base_url: str
    models: tuple[str, ...]
    default_model: str
    api_key_env: str
    key_url: str


PROVIDER_CATALOG: tuple[ProviderCatalogEntry, ...] = (
    ProviderCatalogEntry(
        name="deepseek",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        models=("deepseek-chat", "deepseek-reasoner"),
        default_model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        key_url="https://platform.deepseek.com/api_keys",
    ),
    ProviderCatalogEntry(
        name="zhipu",
        display_name="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=("glm-4.7", "glm-4.7-flash", "glm-4.6"),
        default_model="glm-4.7",
        api_key_env="ZHIPU_API_KEY",
        key_url="https://open.bigmodel.cn/usercenter/apikeys",
    ),
    ProviderCatalogEntry(
        name="moonshot",
        display_name="月之暗面 Kimi",
        base_url="https://api.moonshot.cn/v1",
        models=("kimi-k2.5", "kimi-k2-turbo-preview", "moonshot-v1-128k"),
        default_model="kimi-k2.5",
        api_key_env="MOONSHOT_API_KEY",
        key_url="https://platform.moonshot.cn/console/api-keys",
    ),
    ProviderCatalogEntry(
        name="dashscope",
        display_name="阿里云百炼（通义）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=("qwen3-max", "qwen3-plus", "qwen-turbo"),
        default_model="qwen3-max",
        api_key_env="DASHSCOPE_API_KEY",
        key_url="https://bailian.console.aliyun.com/?apiKey=1",
    ),
    ProviderCatalogEntry(
        name="openai",
        display_name="OpenAI",
        base_url="https://api.openai.com/v1",
        models=("gpt-5.4", "gpt-5.4-mini", "gpt-4.1"),
        default_model="gpt-5.4",
        api_key_env="OPENAI_API_KEY",
        key_url="https://platform.openai.com/api-keys",
    ),
    ProviderCatalogEntry(
        name="openrouter",
        display_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        models=("anthropic/claude-sonnet-4.5", "openai/gpt-5.4", "deepseek/deepseek-chat"),
        default_model="anthropic/claude-sonnet-4.5",
        api_key_env="OPENROUTER_API_KEY",
        key_url="https://openrouter.ai/keys",
    ),
    ProviderCatalogEntry(
        name="siliconflow",
        display_name="硅基流动 SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        models=(
            "deepseek-ai/DeepSeek-V3.2",
            "Qwen/Qwen3-235B-A22B-Instruct",
            "moonshotai/Kimi-K2-Instruct",
        ),
        default_model="deepseek-ai/DeepSeek-V3.2",
        api_key_env="SILICONFLOW_API_KEY",
        key_url="https://cloud.siliconflow.cn/account/ak",
    ),
)


def catalog_entry(name: str) -> ProviderCatalogEntry | None:
    """按目录名取条目；未收录返回 None（自定义 provider 走手填路径）。"""
    for entry in PROVIDER_CATALOG:
        if entry.name == name:
            return entry
    return None
