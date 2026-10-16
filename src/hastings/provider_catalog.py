"""内建 provider 目录（P7c T15）：登录式模型接入的数据底座。

对齐 tau_coding `/login` 的体验内核——provider 条目预填 base_url / models /
default_model / api_key_env，用户「点选 → 贴 key」两步即用，不必手填端点。
与 tau 的 catalog.toml 相比刻意瘦身：只保留 Hastings 用得到的字段
（来源分组、OpenAI 兼容端点与 /models 候选），不含 thinking/cost/context_window。
目录是纯数据（无密钥），key 由用户填入后经 POST /api/providers/connect
写入 config.yaml；自定义服务保留 PUT /api/providers/{name}——目录不参与凭证存储。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
    category: Literal["api", "coding_plan", "local"] = "api"
    no_auth: bool = False
    docs_url: str = ""
    note: str = ""


PROVIDER_CATALOG: tuple[ProviderCatalogEntry, ...] = (
    ProviderCatalogEntry(
        name="deepseek",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        models=("deepseek-flash", "deepseek-v4-pro"),
        default_model="deepseek-flash",
        api_key_env="DEEPSEEK_API_KEY",
        key_url="https://platform.deepseek.com/api_keys",
    ),
    ProviderCatalogEntry(
        name="zhipu",
        display_name="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=("glm-5.3", "glm-5.3-flash"),
        default_model="glm-5.3",
        api_key_env="ZHIPU_API_KEY",
        key_url="https://open.bigmodel.cn/usercenter/apikeys",
    ),
    ProviderCatalogEntry(
        name="moonshot",
        display_name="月之暗面 Kimi",
        base_url="https://api.moonshot.cn/v1",
        models=("kimi-k3", "kimi-k2.6", "kimi-k2.5"),
        default_model="kimi-k3",
        api_key_env="MOONSHOT_API_KEY",
        key_url="https://platform.moonshot.cn/console/api-keys",
    ),
    ProviderCatalogEntry(
        name="dashscope",
        display_name="阿里云百炼（通义）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=("qwen-plus", "qwen-turbo", "qwen3-max"),
        default_model="qwen-plus",
        api_key_env="DASHSCOPE_API_KEY",
        key_url="https://bailian.console.aliyun.com/?apiKey=1",
    ),
    ProviderCatalogEntry(
        name="zhipu-coding",
        display_name="智谱 GLM Coding Plan",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        models=("glm-5.3", "glm-5.3-flash"),
        default_model="glm-5.3",
        api_key_env="ZHIPU_CODING_API_KEY",
        key_url="https://bigmodel.cn/glm-coding",
        category="coding_plan",
        docs_url="https://docs.bigmodel.cn/cn/coding-plan/tool/others",
        note="使用套餐专用密钥；官方限定支持的工具环境，Hastings 的额度资格需向厂商确认。",
    ),
    ProviderCatalogEntry(
        name="bailian-coding",
        display_name="阿里云百炼 Coding Plan",
        base_url="https://coding.dashscope.aliyuncs.com/v1",
        models=("qwen3.7-plus", "qwen3.6-plus", "kimi-k2.5", "glm-5", "MiniMax-M2.5"),
        default_model="qwen3.7-plus",
        api_key_env="BAILIAN_CODING_API_KEY",
        key_url="https://bailian.console.aliyun.com/",
        category="coding_plan",
        docs_url="https://help.aliyun.com/zh/model-studio/coding-plan",
        note=(
            "需 sk-sp- 开头的套餐 Key，与普通百炼 Key 不通用；"
            "官方不允许自定义应用后端调用，Hastings 请使用百炼 API。"
        ),
    ),
    ProviderCatalogEntry(
        name="kimi-code",
        display_name="Kimi Code 订阅",
        base_url="https://api.kimi.com/coding/v1",
        models=("kimi-for-coding",),
        default_model="kimi-for-coding",
        api_key_env="KIMI_CODE_API_KEY",
        key_url="https://www.kimi.com/code/console",
        category="coding_plan",
        docs_url="https://www.kimi.com/help/kimi-code/third-party-agents",
        note="使用 Kimi Code 的接入密钥；与 Kimi 开放平台 Key 分开，工具可用性由厂商决定。",
    ),
    ProviderCatalogEntry(
        name="minimax",
        display_name="MiniMax API",
        base_url="https://api.minimaxi.com/v1",
        models=("MiniMax-M2.7", "MiniMax-M3"),
        default_model="MiniMax-M2.7",
        api_key_env="MINIMAX_API_KEY",
        key_url="https://platform.minimaxi.com/user-center/basic-information/interface-key",
        docs_url="https://platform.minimaxi.com/docs/api-reference/text-openai-api",
    ),
    ProviderCatalogEntry(
        name="minimax-plan",
        display_name="MiniMax M Plan / Token Plan",
        base_url="https://api.minimaxi.com/v1",
        models=("MiniMax-M3.1-Flash-Preview", "MiniMax-M3", "MiniMax-M2.7"),
        default_model="MiniMax-M3.1-Flash-Preview",
        api_key_env="MINIMAX_PLAN_API_KEY",
        key_url="https://www.minimax.cn/m-plan",
        category="coding_plan",
        docs_url="https://platform.minimaxi.com/docs/m-plan/other-tools",
        note="使用订阅 Key；M Plan 与旧 Token Plan 的模型权益不同，以账户可用模型为准。",
    ),
    ProviderCatalogEntry(
        name="ollama",
        display_name="Ollama 本地模型",
        base_url="http://localhost:11434/v1",
        models=(),
        default_model="",
        api_key_env="",
        key_url="https://ollama.com/download",
        category="local",
        no_auth=True,
        docs_url="https://docs.ollama.com/openai",
        note="自动读取本机已安装模型；无需 API Key，可在下方安装或下载模型。",
    ),
    ProviderCatalogEntry(
        name="lmstudio",
        display_name="LM Studio 本地模型",
        base_url="http://localhost:1234/v1",
        models=(),
        default_model="",
        api_key_env="",
        key_url="https://lmstudio.ai/",
        category="local",
        no_auth=True,
        docs_url="https://lmstudio.ai/docs/developer/openai-compat",
        note="在 LM Studio 中加载模型并启动本地服务，再刷新模型列表。",
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
