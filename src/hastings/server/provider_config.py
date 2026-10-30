"""服务级模型连接配置（0.4 V04-9A）。

一处事实源：``config.yaml`` 的连接目录；一个连接就是「作者自己的一个模型账号」——
含地址、协议、key 与可选模型。模型来源不从进程环境读取——作者在自己的机器上用产品，
不会去设置 ``OPENAI_API_KEY`` 这类变量；让某个字段能引用环境变量，只会把运维侧的
概念塞进产品配置，并让「同一份配置在不同机器上行为不同」。

缺文件 / 空文件 / ``providers: []`` → 没有连接（服务照常启动，发送消息的那一轮明确
回报「未配置模型」）；结构性损坏（根节点非映射、连接缺 name/base_url、连接名重复、
字段拼写错误）→ ProviderConfigError，消息说明哪里不对、建议怎么改——模型配置错了
应该当场知道，而不是写作到一半才发现。

空目录是合法状态：作者可以在界面里删掉最后一个连接，那时磁盘上就是空列表，重启后
保持「没有选择」，不能退回到「文件不存在」的推断里。

key 只进不出：磁盘上可直填 ``api_key``（首次启动后转入系统凭据存储，磁盘只留
``credential_ref``）；运行时用 SecretStr 持有（repr/日志不泄露），任何对外输出
只经 ``mask_key``（尾 4 位）。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from asyncio import AbstractEventLoop, get_running_loop
from collections.abc import MutableMapping
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from hastings.server.model_credential import (
    SubscriptionCredential,
    SubscriptionCredentialError,
    load_subscription,
    save_subscription,
)
from hastings.server.model_limits import (
    DEFAULT_PROJECT_INPUT_TOKENS,
    ModelCapacity,
    ModelLimitOverride,
    ModelLimits,
    resolve_model_limits,
)
from hastings.server.model_thinking import ThinkingLevel, thinking_levels
from hastings.server.provider_catalog import ProviderApi
from hastings.server.provider_runtime import ModelSnapshot, build_provider, snapshot
from tau_ai.env import RuntimeProviderAuth, RuntimeProviderAuthResolver

DEFAULT_CONFIG_PATH = Path("config.yaml")

SelectionMode = Literal["auto", "explicit", "none"]

#: 一条连接怎么证明「我是这个账号」：自带 API Key，或登录供应商的订阅账号。
AuthMethod = Literal["api_key", "subscription"]


class ProviderConfigError(ValueError):
    """config.yaml 结构损坏（消息随异常携带）。"""


class ProviderProfile(BaseModel):
    """一个连接：稳定名字 + 显示名 + 端点 + 协议 + key（直填或凭据引用）+ 模型。"""

    model_config = ConfigDict(extra="forbid")  # 拼错字段早失败（如 api_ky）

    name: str
    #: 作者可改的显示名；改它不改变 name（工作区与服务按 name 引用连接）。
    display_name: str = ""
    base_url: str
    api_key: SecretStr = SecretStr("")
    #: V03-1.3：OS 凭据存储引用。设置后 api_key 字段应为空（元数据不存明文）；
    # 实际密钥经 CredentialStore 读取（见 ProviderBook 物化）。
    credential_ref: str = ""
    default_model: str = ""
    #: 作者勾选为日常可用的模型；旧档案没有该字段时由 default_model 推导（见 models_for）。
    models: list[str] = Field(default_factory=list)
    #: 按精确模型名保存的容量。未配置的模型按未知处理，不按名称前缀推断。
    #: 旧配置没有该字段时为空，读取不受影响。
    model_limits: dict[str, ModelLimitOverride] = Field(default_factory=dict)
    #: 服务商在模型列表里返回的有效容量；这是供应商事实，不是作者的设置。
    #: 解析时排在手工容量之后、内置目录之前。旧配置没有该字段时为空。
    model_metadata: dict[str, ModelCapacity] = Field(default_factory=dict)
    # 免密端点（本机兼容服务）：无 key 也可用；空 key 本就不发 Authorization 头。
    no_auth: bool = False
    #: 供应商预设 ID；未知值按自定义服务展示，不阻止读取。
    catalog_id: str = ""
    api: ProviderApi = "openai-completions"
    #: 认证方式：API Key 或订阅账号登录。旧配置不写该字段时按 API Key 读。
    auth_method: AuthMethod = "api_key"
    #: 订阅来源标识（如 ``chatgpt``）；只有 ``auth_method == "subscription"`` 时有意义。
    auth_provider: str = ""
    #: 运行时物化的订阅凭据：磁盘上只有 ``credential_ref``，这里由凭据存储装回。
    subscription: SubscriptionCredential | None = Field(default=None, exclude=True, repr=False)

    def resolved_api_key(self) -> str:
        """解析实际 key：只用连接自身的 key（直填或已物化的凭据）。

        ``credential_ref`` 连接在 ProviderBook 物化阶段已把系统凭据装进
        ``api_key``（SecretStr，只进不出）；本方法不直接访问凭据存储，
        也不读进程环境。订阅连接没有 API Key，这里返回空串。
        """
        return self.api_key.get_secret_value()

    @property
    def available(self) -> bool:
        """可用性 = 免密端点能解析出非空 key，或订阅凭据已装回内存。"""
        if self.auth_method == "subscription":
            return self.subscription is not None and bool(self.subscription.access_token)
        return self.no_auth or bool(self.resolved_api_key())

    @property
    def auth_status(self) -> Literal["ready", "expired", "missing"]:
        """凭据状态：可以调用、需要续期、根本没有。

        「过期」不等于「不可用」：带有刷新令牌的订阅连接在运行时续期；
        只有完全没有凭据时才是 ``missing``。
        """
        if self.auth_method == "subscription":
            if self.subscription is None or not self.subscription.access_token:
                return "missing"
            return "expired" if self.subscription.expired() else "ready"
        return "ready" if self.resolved_api_key() else "missing"

    @property
    def title(self) -> str:
        """界面显示名；旧档案没有 display_name 时回退到 name。"""
        return self.display_name or self.name

    def models_for(self) -> list[str]:
        """可选模型视图；旧档案只有 default_model 时把它当作唯一可选模型。

        不在启动时批量改写磁盘：读的时候兼容，写的时候才落新字段。
        """
        if self.models:
            return list(self.models)
        return [self.default_model] if self.default_model else []


class ContextPolicy(BaseModel):
    """项目对单次请求输入的预算。

    这是应用策略，不是模型容量：同一个模型，作者可以为了成本与响应时间只开一部分窗口。
    配置为 null 不表示关闭保护——读取时缺省即默认值，写入时总是落盘，避免「看不见的
    上限」和「以为没开」两种误解。
    """

    model_config = ConfigDict(extra="forbid")

    max_input_tokens: int = DEFAULT_PROJECT_INPUT_TOKENS

    @field_validator("max_input_tokens", mode="before")
    @classmethod
    def _positive_integer(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(
                "[CONTEXT_POLICY_INVALID] 模型设置：max_input_tokens 必须是正整数。"
                "建议：填写一个大于零的整数后重试。"
            )
        return value


class ServerConfig(BaseModel):
    """服务级配置根：连接目录与项目输入预算。连接名唯一（工作区设置按名引用）。"""

    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderProfile]
    active_provider: str = ""
    active_model: str = ""
    active_thinking_level: ThinkingLevel | None = None
    #: 空选择的持久化语义：auto 沿用启动默认，explicit 用 active_provider，
    # none 表示作者明确清空，重启后保持未选。
    selection_mode: SelectionMode = "auto"
    #: 项目输入预算；旧配置没有该字段时取默认值，不影响读取。
    context_policy: ContextPolicy = Field(default_factory=ContextPolicy)

    @model_validator(mode="before")
    @classmethod
    def _infer_selection_mode(cls, value: object) -> object:
        """旧配置没有 selection_mode：有 active_provider 即 explicit，否则 auto。"""
        if isinstance(value, dict) and "selection_mode" not in value:
            raw = dict(value)
            raw["selection_mode"] = "explicit" if raw.get("active_provider") else "auto"
            return raw
        return value


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """当前生效的「连接 + 模型」，并说明它来自哪里。

    ``source`` 让界面显示与实际调用一致：``saved`` 是作者保存过的选择，
    ``startup`` 来自启动参数，``automatic`` 是首个可用连接的默认模型，
    ``none`` 表示没有选择。
    """

    connection_id: str | None
    model: str
    source: Literal["saved", "startup", "automatic", "none"]
    available: bool
    reason: str = ""
    thinking_level: ThinkingLevel | None = None
    thinking_levels: tuple[ThinkingLevel, ...] = ()

    @property
    def display_name(self) -> str:
        return self.connection_id or ""


def config_revision(path: Path = DEFAULT_CONFIG_PATH) -> str:
    """配置版本：文件内容的短摘要，用于提交时的并发校验。

    用内容而不是修改时间：同一个文件被复制、恢复或重新保存都会改变时间戳，
    但只有内容变化才意味着「你手里的列表已经过期」。
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return "0" * 16
    return hashlib.sha256(raw).hexdigest()[:16]


def _reject_env_reference(raw: dict[str, Any], path: Path) -> None:
    """旧版 ``api_key_env``：环境变量引用已从产品里移除，给出可操作的迁移提示。

    不静默忽略：字段存在说明作者的配置仍指望进程环境提供 key；若悄悄丢掉，
    结果只是「档案突然不可用」，比一句直白的迁移说明难查得多。
    """
    providers = raw.get("providers")
    if not isinstance(providers, list):
        return
    offenders = [
        str(entry.get("name") or index)
        for index, entry in enumerate(providers)
        if isinstance(entry, dict) and "api_key_env" in entry
    ]
    if not offenders:
        return
    raise ProviderConfigError(
        f"config.yaml 已不再支持环境变量引用：连接 {offenders} 使用了 api_key_env（{path}）。"
        "模型来源只从本文件读取，应用不再读进程环境里的 key；"
        "保留该字段会让连接看起来配好了、实际没有 key。"
        "建议：删掉 api_key_env，把 key 直接写进同一连接的 api_key"
        "（首次启动后会转入系统凭据存储，磁盘上只留 credential_ref）。"
    )


def load_server_config(path: Path = DEFAULT_CONFIG_PATH) -> ServerConfig:
    """加载服务级配置；缺文件/空文件/空列表 → 空连接目录，结构性损坏 → 报错并说明改法。"""
    if not path.exists():
        return ServerConfig(providers=[])
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        # 空文件等价于「还没配置」→ 空连接目录（与「删掉文件」同一结果）
        return ServerConfig(providers=[])
    if not isinstance(raw, dict):
        raise ProviderConfigError(
            f"config.yaml 损坏：根节点应为 YAML 映射（providers 列表），"
            f"实际是 {type(raw).__name__}（{path}）。"
            "文件结构不对，无法解析出任何模型端点。"
            "建议：参照 config.example.yaml 修复文件结构。"
        )
    _reject_env_reference(raw, path)
    try:
        config = ServerConfig.model_validate(raw)
    except ValidationError as exc:
        raise ProviderConfigError(_validation_error_message(path, exc)) from exc
    names = [profile.name for profile in config.providers]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ProviderConfigError(
            f"config.yaml 损坏：连接名重复 {duplicates}（{path}）。"
            "工作区设置按连接名引用端点，重名无法解析。"
            "建议：为每个连接取唯一的名字。"
        )
    return config


def _validation_error_message(path: Path, exc: ValidationError) -> str:
    """字段级定位原样透传（loc: msg），不做人话翻译——错误消息是喂给用户的 API。"""
    lines = "；".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
    )
    return (
        f"config.yaml 损坏：{lines}（{path}）。"
        "字段缺失、类型不对或存在未知字段（多为拼写错误）。"
        "建议：每个连接必含 name / base_url，可选 api_key（直填）"
        "与 default_model；参照 config.example.yaml。"
    )


def _materialize_credentials(config: ServerConfig, store: Any) -> ServerConfig:
    """把系统凭据装回内存连接（credential_ref → 凭据，SecretStr 只进不出）。

    磁盘元数据不改动；仅运行时视图持有明文（沿既有 SecretStr 泄露防线）。
    订阅连接装回的是结构化记录；记录损坏按「没有凭据」处理——连接仍可删可改，
    重登一次即可恢复，不能因为一条坏记录让整个配置读不出来。
    """
    providers = []
    for profile in config.providers:
        if not profile.credential_ref:
            providers.append(profile)
            continue
        if profile.auth_method == "subscription":
            try:
                credential = load_subscription(store, profile.credential_ref)
            except SubscriptionCredentialError:
                credential = None
            providers.append(profile.model_copy(update={"subscription": credential}))
            continue
        if not profile.api_key.get_secret_value():
            secret = store.load(profile.credential_ref)
            if secret:
                profile = profile.model_copy(update={"api_key": SecretStr(secret)})
        providers.append(profile)
    return config.model_copy(update={"providers": providers})


def migrate_plaintext_credentials(
    config: ServerConfig, store: Any, path: Path
) -> dict[str, list[str]]:
    """旧明文连接升级（V03-1.3）：写入并验证 → 元数据切换 → 清理旧 Key 字段。

    每个含直填 Key 的连接独立迁移；任一步失败该连接保持旧连接可恢复
    （明文不动、磁盘不写），失败原因进报告由调用方明确提示。
    只在磁盘上仍有明文 Key 时执行；重复运行幂等（已迁移连接跳过）。
    """
    migrated: list[str] = []
    failed: list[str] = []
    updated: list[Any] = []
    changed = False
    for profile in config.providers:
        key = profile.api_key.get_secret_value()
        if not key or profile.credential_ref:
            updated.append(profile)
            continue
        ref = f"hastings:provider:{profile.name}"
        try:
            store.save(ref, key)
            if store.load(ref) != key:
                raise ValueError("OS 凭据存储回读校验不一致")
        except Exception as exc:  # 存储/验证失败：旧连接原样保留
            code = getattr(exc, "code", type(exc).__name__)
            failed.append(f"{profile.name}: {code}")
            updated.append(profile)
            continue
        migrated.append(profile.name)
        updated.append(profile.model_copy(update={"credential_ref": ref, "api_key": SecretStr("")}))
        changed = True
    if changed:
        try:
            save_server_config(config.model_copy(update={"providers": updated}), path)
        except OSError as exc:
            # 元数据切换失败：磁盘仍是旧明文，旧连接可用；OS 存储已写入（可重试）
            return {"migrated": [], "failed": [f"config.yaml 写入失败: {exc}"]}
    return {"migrated": migrated, "failed": failed}


def save_server_config(config: ServerConfig, path: Path) -> Path:
    """写回 config.yaml：连接增删改后落盘，重启仍生效。

    api_key 以明文写入——文件存储格式本就支持直填 key（与手写 config.yaml
    同一保密边界）；已迁入系统凭据存储的连接此处写空串，只留 credential_ref。
    设置界面的写入路径不经过这里把明文交给序列化器：它写的是只有引用、没有 key
    的配置对象（见 model_connections）。
    """
    payload = {
        "providers": [_profile_payload(profile) for profile in config.providers],
        # 项目预算总是写出来：读者不必从默认值推断当前生效的上限。
        "context_policy": {"max_input_tokens": config.context_policy.max_input_tokens},
        **(
            {
                "active_provider": config.active_provider,
                "active_model": config.active_model,
                **(
                    {"active_thinking_level": config.active_thinking_level}
                    if config.active_thinking_level is not None
                    else {}
                ),
            }
            if config.selection_mode == "explicit" and config.active_provider
            else {}
        ),
        **({"selection_mode": "none"} if config.selection_mode == "none" else {}),
    }
    header = "# 由设置页或手工维护；模型来源只从这个文件读取，不读进程环境。\n"
    text = header + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def _profile_payload(profile: ProviderProfile) -> dict[str, object]:
    """一个连接的磁盘形状：默认值不落盘，旧文件格式零扰动。

    订阅连接不写 ``api_key``：那条记录里根本没有 key，写一个空串会让手工读配置的人
    以为「填一下就能用」，而实际需要的是重新登录。
    """
    subscription = profile.auth_method == "subscription"
    return {
        "name": profile.name,
        "base_url": profile.base_url,
        **({"api_key": profile.api_key.get_secret_value()} if not subscription else {}),
        "default_model": profile.default_model,
        **({"display_name": profile.display_name} if profile.display_name else {}),
        **({"models": list(profile.models)} if profile.models else {}),
        # 容量按精确模型写入；未填的项不落盘，文件里只看得到作者真的填过的值。
        **(
            {
                "model_limits": {
                    name: limit.model_dump(exclude_none=True)
                    for name, limit in profile.model_limits.items()
                }
            }
            if profile.model_limits
            else {}
        ),
        # 服务商返回的容量单独保存：它随「获取模型列表」更新，不与作者手填的值混写。
        **(
            {
                "model_metadata": {
                    name: capacity.model_dump(exclude_none=True)
                    for name, capacity in profile.model_metadata.items()
                }
            }
            if profile.model_metadata
            else {}
        ),
        # 免密字段按需写入：False 不落盘
        **({"no_auth": True} if profile.no_auth else {}),
        **({"catalog_id": profile.catalog_id} if profile.catalog_id else {}),
        **({"api": profile.api} if profile.api != "openai-completions" else {}),
        **({"auth_method": "subscription"} if subscription else {}),
        **({"auth_provider": profile.auth_provider} if profile.auth_provider else {}),
        # V03-1.3：OS 凭据存储引用按需写入（明文 Key 迁移后为空串）
        **({"credential_ref": profile.credential_ref} if profile.credential_ref else {}),
    }


def mask_key(key: str) -> str:
    """key 脱敏（尾 4 位）：key 只进不出，对外只给这个。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "…"
    return f"…{key[-4:]}"


def is_loopback_url(url: str) -> bool:
    """本机地址判断（兼容旧导入路径）。"""
    return urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1")


class ProviderBook:
    """连接目录的运行时视图：解析当前选择 + 构造每一轮使用的实例。

    与 ServerConfig 的分工：ServerConfig 是磁盘事实（Pydantic 校验），
    ProviderBook 是进程事实（当前选择、本轮快照）。这里不再缓存 provider 实例：
    实例随快照创建、随轮次关闭，编辑与删除因此不需要「摘掉缓存里那个可能还在用的
    client」。
    """

    def __init__(
        self,
        config: ServerConfig,
        *,
        boot_name: str,
        boot_model: str,
        config_path: Path | None = None,
        credentials: Any | None = None,
        cli_model: str = "",
        auth: Any | None = None,
    ) -> None:
        self._credentials = credentials
        # 订阅连接的续期由授权服务完成：凭据存储只负责存放，不解释令牌。
        self._auth = auth
        self.credential_migration: dict[str, list[str]] = {"migrated": [], "failed": []}
        if credentials is not None:
            # 升级顺序（V03-1.3）：新存储写入并验证 → 元数据切换 → 清理旧
            # Key 字段；失败保持旧连接可恢复，报告由调用方明确提示。
            self.credential_migration = migrate_plaintext_credentials(
                config, credentials, config_path or DEFAULT_CONFIG_PATH
            )
            if self.credential_migration["migrated"]:
                # 迁移把明文换成了引用并写回磁盘：内存视图按磁盘重读，避免
                # 继续持有一份「有明文 key、没有 credential_ref」的旧对象。
                config = load_server_config(config_path or DEFAULT_CONFIG_PATH)
            config = _materialize_credentials(config, credentials)
        self._config = config
        self._boot_name = boot_name
        self._boot_model = boot_model
        # CLI --model 是临时覆盖：与 boot_model 分开记，界面一旦选择就被取代。
        self._cli_model = cli_model
        self._config_path = config_path
        # 本次运行中作者通过界面作出的选择（含清空）；None 表示还没动过。
        self._runtime_selection: ModelSelection | None = None

    @property
    def config(self) -> ServerConfig:
        return self._config

    @property
    def credentials(self) -> Any | None:
        """系统凭据存储；None 表示未启用（连接走旧明文路径，仅限无存储环境）。"""
        return self._credentials

    @property
    def config_path(self) -> Path:
        return self._config_path or DEFAULT_CONFIG_PATH

    @property
    def boot_name(self) -> str:
        return self._boot_name

    def profile(self, name: str) -> ProviderProfile | None:
        """严格查找（写操作校验用）；未知连接名返回 None。"""
        for profile in self._config.providers:
            if profile.name == name:
                return profile
        return None

    def reload(self, new: ServerConfig) -> ServerConfig:
        """装入新配置并装回系统凭据，返回运行时视图。"""
        if self._credentials is not None:
            new = _materialize_credentials(new, self._credentials)
        self._config = new
        return new

    def selection(self) -> ModelSelection:
        """当前有效选择。

        优先级：界面选择（含清空）→ CLI ``--model`` 临时覆盖 → 配置里的
        explicit / none → 仅 auto 模式下的启动默认规则。
        """
        if self._runtime_selection is not None:
            current = self._runtime_selection
            profile = self.profile(current.connection_id) if current.connection_id else None
            if profile is not None:
                return self._selection_of(profile, current.model, "saved")
            return self._runtime_selection

        explicit = self._explicit()
        if self._cli_model:
            target = explicit or self.profile(self._boot_name)
            if target is None:
                return ModelSelection(None, "", "none", False, self._no_profile_reason())
            return self._selection_of(target, self._cli_model, "startup")
        if self._config.selection_mode == "none":
            return ModelSelection(
                None, "", "none", False, "已清空当前选择；选择连接与模型后才能对话"
            )
        if explicit is not None:
            model = self._config.active_model or explicit.default_model
            return self._selection_of(explicit, model, "saved")

        boot = self.profile(self._boot_name)
        if boot is not None and boot.available:
            model = self._boot_model or boot.default_model
            if model:
                # 没有 --model 时这就是「启动默认规则」的结果，不是命令行覆盖。
                return self._selection_of(boot, model, "automatic")
        auto = next(
            (item for item in self._config.providers if item.available and item.default_model),
            None,
        )
        if auto is not None:
            return self._selection_of(auto, auto.default_model, "automatic")
        return ModelSelection(None, "", "none", False, self._no_profile_reason())

    def apply_selection(self, connection_id: str | None, model: str) -> ModelSelection:
        """记录界面选择（由管理服务在写盘成功后调用）；清空即 none。"""
        self._cli_model = ""  # 界面一经改动即取代 --model 的临时覆盖
        if connection_id is None:
            self._runtime_selection = ModelSelection(
                None, "", "none", False, "已清空当前选择；选择连接与模型后才能对话"
            )
            return self._runtime_selection
        profile = self.profile(connection_id)
        if profile is None:
            self._runtime_selection = ModelSelection(
                connection_id, model, "none", False, f"连接不存在：{connection_id}"
            )
            return self._runtime_selection
        chosen = model or profile.default_model
        self._runtime_selection = self._selection_of(profile, chosen, "saved")
        return self._runtime_selection

    @property
    def project_input_limit(self) -> int:
        """当前生效的项目输入预算；界面与请求检查读同一份。"""
        return self._config.context_policy.max_input_tokens

    def current_limits(self) -> ModelLimits | None:
        """当前选择的容量与预算，只读配置、不构造 provider。

        用量面板在首次发送前与空闲时用它显示当前容量与预算；读取不写事件、
        不读凭据存储、不调用模型。选择不可用时返回 ``None``，页面提示先选模型。
        """
        selection = self.selection()
        profile = self.profile(selection.connection_id) if selection.connection_id else None
        if profile is None or not selection.available or not selection.model:
            return None
        return resolve_model_limits(
            profile, selection.model, project_limit=self.project_input_limit
        )

    def subscription_resolver(self, profile: ProviderProfile) -> RuntimeProviderAuthResolver | None:
        """订阅连接的请求前凭据解析：快过期就续期，同一账号串行。

        返回值不是「一枚令牌」，而是「每次请求前问一次」：令牌有寿命，快照却活在整轮
        写作里，把解析推迟到发请求那一刻才是准的。
        """
        if profile.subscription is None:
            return None
        name = profile.name
        initial = profile.subscription

        async def resolve() -> RuntimeProviderAuth:
            current = self._current_subscription(name) or initial
            if not current.expired():
                return RuntimeProviderAuth(api_key=current.access_token)
            # 刷新令牌会轮换：并发请求若都去花同一枚，后到的那次必然失败，
            # 还可能把已作废的令牌写回磁盘。锁内重读一次，确认仍然过期才真的续期。
            async with _subscription_refresh_lock(current.account_id or name):
                current = self._current_subscription(name) or current
                if current.expired():
                    refreshed = await self._refresh_subscription(current)
                    self._store_subscription(name, refreshed)
                    current = refreshed
            return RuntimeProviderAuth(api_key=current.access_token)

        return resolve

    def update_subscription(self, name: str, credential: SubscriptionCredential) -> None:
        """外部（连接服务）续期后回写凭据：存储与内存视图一起更新。"""
        self._store_subscription(name, credential)

    def _current_subscription(self, name: str) -> SubscriptionCredential | None:
        profile = self.profile(name)
        return profile.subscription if profile is not None else None

    async def _refresh_subscription(
        self, credential: SubscriptionCredential
    ) -> SubscriptionCredential:
        refresh = getattr(self._auth, "refresh", None)
        if refresh is None:
            raise ProviderConfigError(
                "订阅凭据需要续期，但当前进程没有可用的续期能力。"
                "建议：在「模型设置」里重新登录这个连接。"
            )
        refreshed = await refresh(credential)
        if not isinstance(refreshed, SubscriptionCredential):
            raise ProviderConfigError(
                "续期返回的不是可用的订阅凭据。建议：在「模型设置」里重新登录这个连接。"
            )
        return refreshed

    def _store_subscription(self, name: str, credential: SubscriptionCredential) -> None:
        """续期结果先落存储再更新内存；写盘失败不打断这一轮。

        写不进去时这一轮仍能用新令牌，但下次启动会拿到旧的刷新令牌而在续期时报错，
        界面上表现为「需要重新登录」——比让作者丢掉正在写的这一轮更合适。
        """
        profile = self.profile(name)
        if profile is None:
            return
        if profile.credential_ref and self._credentials is not None:
            with contextlib.suppress(Exception):
                save_subscription(self._credentials, profile.credential_ref, credential)
        providers = [
            item.model_copy(update={"subscription": credential}) if item.name == name else item
            for item in self._config.providers
        ]
        self._config = self._config.model_copy(update={"providers": providers})

    def snapshot(self, *, worker: bool = True) -> ModelSnapshot:
        """解析当前选择并构造一轮快照；未配置时抛错并说明如何配置。"""
        return snapshot_for(self.selection(), self)

    def _explicit(self) -> ProviderProfile | None:
        if self._config.selection_mode != "explicit" or not self._config.active_provider:
            return None
        return self.profile(self._config.active_provider)

    def _selection_of(
        self,
        profile: ProviderProfile,
        model: str,
        source: Literal["saved", "startup", "automatic"],
    ) -> ModelSelection:
        levels = thinking_levels(profile, model)
        level = (
            self._config.active_thinking_level
            if source == "saved"
            and self._config.active_provider == profile.name
            and self._config.active_model == model
            and self._config.active_thinking_level in levels
            else None
        )
        if not profile.available:
            return ModelSelection(
                profile.name,
                model,
                source,
                False,
                f"连接「{profile.title}」还没有可用的凭据；"
                + (
                    "订阅账号需要重新登录后再发送"
                    if profile.auth_method == "subscription"
                    else "填好 API Key 后再发送"
                ),
            )
        if not model:
            return ModelSelection(profile.name, "", source, False, "这个连接还没有选择模型")
        return ModelSelection(
            profile.name, model, source, True, thinking_level=level, thinking_levels=levels
        )

    def _no_profile_reason(self) -> str:
        """没有可用选择时的提示：区分「一个连接都没有」与「连接都在但都缺凭据」。"""
        if not self._config.providers:
            return "还没有可用的模型连接；先在模型设置里添加一个连接"
        titles = "、".join(f"「{item.title}」" for item in self._config.providers)
        return f"连接{titles}还没有可用的凭据；订阅连接需要重新登录，API Key 连接需要填写 Key"


def snapshot_for(selection: ModelSelection, book: ProviderBook) -> ModelSnapshot:
    """把选择变成实例；选择不可用时抛错（轮次与脚本共用同一句提示）。"""
    profile = book.profile(selection.connection_id) if selection.connection_id else None
    if profile is None or not selection.available or not selection.model:
        raise ProviderConfigError(
            f"没有可用的模型来源：{selection.reason or '当前没有选择连接与模型'}。"
            "这一轮不会调用模型，也不会有任何文件改动。"
            "建议：在「模型设置」里添加并选择一个连接与模型，"
            "或在 config.yaml 里补齐 base_url、api_key 与模型名。"
        )
    limits = resolve_model_limits(
        profile,
        selection.model,
        project_limit=book.config.context_policy.max_input_tokens,
    )
    if profile.auth_method == "subscription":
        if profile.subscription is None:
            raise ProviderConfigError(
                f"连接「{profile.title}」的订阅凭据已经不可用。"
                "这一轮不会调用模型，也不会有任何文件改动。"
                "建议：在「模型设置」里重新登录这个连接，或改选别的连接。"
            )
        return replace(
            snapshot(
                profile,
                selection.model,
                thinking_level=selection.thinking_level,
                credential_resolver=book.subscription_resolver(profile),
            ),
            limits=limits,
        )
    return replace(
        snapshot(profile, selection.model, thinking_level=selection.thinking_level),
        limits=limits,
    )


_REFRESH_LOCKS: MutableMapping[AbstractEventLoop, dict[str, asyncio.Lock]] = WeakKeyDictionary()


def _subscription_refresh_lock(key: str) -> asyncio.Lock:
    """取本事件循环上某个账号的续期锁。

    锁按事件循环分别缓存：``asyncio.Lock`` 在第一次争用时才绑定循环，跨循环复用会
    在「无争用」的路径上看起来正常，直到某次真的并发才报错——那种故障最难查。
    """
    locks = _REFRESH_LOCKS.setdefault(get_running_loop(), {})
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "AuthMethod",
    "ContextPolicy",
    "ModelSelection",
    "ProviderBook",
    "ProviderConfigError",
    "ProviderProfile",
    "SelectionMode",
    "ServerConfig",
    "build_provider",
    "config_revision",
    "is_loopback_url",
    "load_server_config",
    "mask_key",
    "save_server_config",
    "snapshot_for",
]
