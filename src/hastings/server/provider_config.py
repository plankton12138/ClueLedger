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
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from tau_agent.provider import ModelProvider
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
    #: V03-1.3：OS 凭据存储引用。设置后 api_key 字段应为空（元数据不存明文）；
    # 实际密钥经 CredentialStore 读取（见 ProviderBook 物化）。
    credential_ref: str = ""
    default_model: str = ""
    # 免密端点（本地模型如 Ollama）：无 key 也可用；OpenAICompatibleProvider
    # 空 key 本就不发 Authorization 头。
    no_auth: bool = False
    catalog_id: str = ""
    api: Literal["openai-completions", "openai-responses"] = "openai-completions"

    def resolved_api_key(self) -> str:
        """解析实际 key：直填优先于 env 引用（D1）。两者皆空 → 空串。

        ``credential_ref`` 档案在 ProviderBook 物化阶段已把 OS 凭据装进
        ``api_key``（SecretStr，只进不出）；本方法不直接访问凭据存储。
        """
        direct = self.api_key.get_secret_value()
        if direct:
            return direct
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""

    @property
    def available(self) -> bool:
        """启动/切换的可用性 = 免密端点，或能解析出非空 key。"""
        return self.no_auth or bool(self.resolved_api_key())


class ServerConfig(BaseModel):
    """服务级配置根：档案目录。档案名唯一（工作区设置按名引用）。"""

    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderProfile]
    active_provider: str = ""
    active_model: str = ""


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


def _materialize_credentials(config: ServerConfig, store: Any) -> ServerConfig:
    """把 OS 凭据装回内存档案（credential_ref → api_key，SecretStr 只进不出）。

    磁盘元数据不改动；仅运行时视图持有明文（沿既有 SecretStr 泄露防线）。
    """
    providers = []
    for profile in config.providers:
        if profile.credential_ref and not profile.api_key.get_secret_value():
            secret = store.load(profile.credential_ref)
            if secret:
                profile = profile.model_copy(update={"api_key": SecretStr(secret)})
        providers.append(profile)
    return config.model_copy(update={"providers": providers})


def migrate_plaintext_credentials(
    config: ServerConfig, store: Any, path: Path
) -> dict[str, list[str]]:
    """旧明文连接升级（V03-1.3）：写入并验证 → 元数据切换 → 清理旧 Key 字段。

    每个含直填 Key 的档案独立迁移；任一步失败该档案保持旧连接可恢复
    （明文不动、磁盘不写），失败原因进报告由调用方明确提示。
    只在磁盘上仍有明文 Key 时执行；重复运行幂等（已迁移档案跳过）。
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
    """写回 config.yaml（P7 T12 档案 UI 化）：UI 增删改档案后落盘，重启仍生效。

    api_key 以明文写入——文件存储格式本就支持直填 key（与手写 config.yaml
    同一保密边界：key 安全性依赖文件系统权限，不依赖本函数）。空字段写成
    空串（load 侧一律容忍空值）。写盘 OSError 上抛，由调用方归因环境故障。
    """
    payload = {
        "providers": [
            {
                "name": profile.name,
                "base_url": profile.base_url,
                "api_key": profile.api_key.get_secret_value(),
                "api_key_env": profile.api_key_env,
                "default_model": profile.default_model,
                # 免密字段按需写入：False 不落盘，旧文件格式零扰动
                **({"no_auth": True} if profile.no_auth else {}),
                **({"catalog_id": profile.catalog_id} if profile.catalog_id else {}),
                **({"api": profile.api} if profile.api != "openai-completions" else {}),
                # V03-1.3：OS 凭据存储引用按需写入（明文 Key 迁移后为空串）
                **({"credential_ref": profile.credential_ref} if profile.credential_ref else {}),
            }
            for profile in config.providers
        ],
        **(
            {"active_provider": config.active_provider, "active_model": config.active_model}
            if config.active_provider
            else {}
        ),
    }
    header = "# 由设置页管理（P7 T12），也可手改；删掉本文件则回落 .env 单档案配置。\n"
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


def mask_key(key: str) -> str:
    """key 脱敏（尾 4 位）：key 只进不出，对外只给这个。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "…"
    return f"…{key[-4:]}"


def is_loopback_url(url: str) -> bool:
    return urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1")


class _LocalProvider(OpenAICompatibleProvider):
    """保持 Tau 的请求协议，本机请求不经系统代理，client 生命周期仍由 provider 拥有。"""

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds, trust_env=False)
        return self._client


def build_provider(profile: ProviderProfile, *, worker: bool = False) -> OpenAICompatibleProvider:
    """由档案构造 provider 实例（跨档案切换/启动装配共用；T2 复用）。"""
    key = profile.resolved_api_key()
    config = OpenAICompatibleConfig(
        api_key=key,
        base_url=profile.base_url.rstrip("/"),
        # 免密档案（Ollama 等）：空 key 不能发 `Authorization: Bearer `——
        # httpx 会以 Illegal header value 拒绝该请求。
        omit_authorization_header=profile.no_auth and not key,
        api=profile.api,
        infer_api_from_model=False,
    )
    if worker:
        config = replace(
            config,
            timeout_seconds=180 if is_loopback_url(profile.base_url) else 45,
            max_retries=0,
            max_tokens=4096,
        )
    provider_cls = _LocalProvider if is_loopback_url(profile.base_url) else OpenAICompatibleProvider
    return provider_cls(config)


@dataclass(frozen=True, slots=True)
class ResolvedProvider:
    """一次会话装配所需的端点事实：生效档案名 + 生效模型 + provider 实例。"""

    profile_name: str
    model: str
    provider: ModelProvider


class ProviderBook:
    """档案目录的运行时视图（P6 T2）：按名复用 provider 实例 + 解析生效档案。

    与 ServerConfig 的分工：ServerConfig 是磁盘事实（Pydantic 校验），
    ProviderBook 是进程事实（httpx client 按档案名缓存复用——跨档案来回切
    不重复建 client，也不需要在重建会话时关闭被共享的 provider）。
    """

    def __init__(
        self,
        config: ServerConfig,
        *,
        boot_name: str,
        boot_model: str,
        config_path: Path | None = None,
        credentials: Any | None = None,
    ) -> None:
        self._credentials = credentials
        self.credential_migration: dict[str, list[str]] = {"migrated": [], "failed": []}
        if credentials is not None:
            # 升级顺序（V03-1.3）：新存储写入并验证 → 元数据切换 → 清理旧
            # Key 字段；失败保持旧连接可恢复，报告由调用方明确提示。
            self.credential_migration = migrate_plaintext_credentials(
                config, credentials, config_path or DEFAULT_CONFIG_PATH
            )
            config = _materialize_credentials(config, credentials)
        self._config = config
        self._boot_name = boot_name
        self._boot_model = boot_model
        # 档案写回路径（P7 T12）：由 CLI --config 传入；None → 写默认路径。
        self._config_path = config_path
        self._instances: dict[str, ModelProvider] = {}
        self._initial_provider = next(
            (p.name for p in config.providers if p.available and p.default_model), ""
        )

    @property
    def config(self) -> ServerConfig:
        return self._config

    @property
    def credentials(self) -> Any | None:
        """OS 凭据存储；None 表示未启用（连接走旧明文路径，仅限无存储环境）。"""
        return self._credentials

    @property
    def config_path(self) -> Path:
        return self._config_path or DEFAULT_CONFIG_PATH

    @property
    def boot_name(self) -> str:
        return self._boot_name

    def profile(self, name: str) -> ProviderProfile | None:
        """严格查找（PUT settings 校验用）；未知档案名返回 None。"""
        for profile in self._config.providers:
            if profile.name == name:
                return profile
        return None

    def update_config(self, new: ServerConfig) -> None:
        """替换磁盘事实，并清理已删除或配置已变化的档案实例。

        配置未变的档案复用 client；密钥、地址或协议更新后不再返回旧实例。
        """
        changed = {p.name for p in new.providers if self.profile(p.name) != p}
        if self._credentials is not None:
            # UI 保存的连接只写 credential_ref；运行时视图从 OS 凭据装回
            new = _materialize_credentials(new, self._credentials)
            changed = {p.name for p in new.providers if self.profile(p.name) != p}
        self._config = new
        live = {profile.name for profile in new.providers}
        for name in [name for name in self._instances if name not in live or name in changed]:
            del self._instances[name]

    def selected(self) -> tuple[ProviderProfile | None, str]:
        """显式选择优先；凭证失效时保留选择，不静默切换到其他厂商。"""
        if self.config.active_provider:
            return self.profile(self.config.active_provider), self.config.active_model
        boot = self.profile(self.boot_name)
        if boot is not None and boot.available and (self._boot_model or boot.default_model):
            return boot, self._boot_model or boot.default_model
        profile = self.profile(self._initial_provider)
        return profile, profile.default_model if profile else ""

    def provider(self, name: str) -> ModelProvider:
        """按名取 provider 实例（缓存复用）；未知档案名抛三要素错。"""
        cached = self._instances.get(name)
        if cached is not None:
            return cached
        profile = self.profile(name)
        if profile is None:
            msg = (
                f"provider 档案不存在：{name!r}。"
                "为什么错：档案名不在服务级 config.yaml 的 providers 列表中。"
                "怎样算对：GET /api/providers 查看可用档案名。"
            )
            raise ProviderConfigError(msg)
        instance = build_provider(profile)
        self._instances[name] = instance
        return instance

    def resolve(self, profile_name: str, model_override: str) -> ResolvedProvider:
        """装配路径的宽容解析：未知或缺 key 的档案回落引导档案（重启后仍可用）。

        模型优先级：工作区设置 > 引导档案的引导模型（CLI --model / env）>
        档案 default_model。引导模型对引导档案优先，使 ``--model`` 覆盖
        config.yaml 里该档案的 default_model（CLI 优先于文件）。
        """
        profile = self.profile(profile_name)
        if profile is None or not profile.available:
            profile = self.profile(self._boot_name)
        if profile is None:  # 引导档案也找不到（config.yaml 被改坏）
            msg = (
                f"引导档案不存在：{self._boot_name!r}。"
                "为什么错：工作区设置的档案与服务级配置均无法解析出可用端点。"
                "怎样算对：检查 config.yaml 的 providers 列表（或删除它回落 .env）。"
            )
            raise ProviderConfigError(msg)
        model = model_override
        if not model and profile.name == self._boot_name:
            model = self._boot_model
        if not model:
            model = profile.default_model
        return ResolvedProvider(
            profile_name=profile.name,
            model=model,
            provider=self.provider(profile.name),
        )


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ProviderBook",
    "ProviderConfigError",
    "ProviderProfile",
    "ResolvedProvider",
    "ServerConfig",
    "build_provider",
    "env_fallback_config",
    "load_server_config",
    "mask_key",
    "save_server_config",
]
