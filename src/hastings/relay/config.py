"""支付服务配置。

生产配置只从环境变量读取；本地沙箱从项目根的 ``.alipay-sandbox.json`` 读取。
Python SDK 要求 PKCS#1 私钥，因此沙箱映射固定取 ``appPrivatePkcsKey``，
不读取 Java 使用的 ``appPrivateKey``，也不做任何格式转换。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from hastings.relay.metering import DEFAULT_RATES, ModelRate

DEFAULT_PORT = 8300
SANDBOX_GATEWAY = "https://openapi-sandbox.dl.alipaydev.com/gateway.do"
PRODUCTION_GATEWAY = "https://openapi.alipay.com/gateway.do"


class RelayConfigError(ValueError):
    """支付配置缺失或互相矛盾。"""


@dataclass(frozen=True, slots=True)
class RelaySettings:
    app_id: str
    private_key: str
    alipay_public_key: str
    gateway: str
    seller_id: str
    notify_url: str
    return_url: str
    database_path: Path
    admin_token: str
    allowed_origins: tuple[str, ...]
    sandbox: bool
    public_base_url: str
    site_url: str
    cookie_name: str
    reconcile_interval_seconds: float
    platform_base_url: str = "https://api.deepseek.com/v1"
    platform_api_key: str = ""
    platform_models: tuple[ModelRate, ...] = DEFAULT_RATES
    platform_default_output_tokens: int = 4096
    internal_secret: str = ""
    reservation_ttl_seconds: int = 3600
    review_ttl_seconds: int = 3600
    max_concurrent_requests: int = 2
    production: bool = False

    @property
    def platform_enabled(self) -> bool:
        return bool(self.platform_api_key and self.internal_secret)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _secret(env: Mapping[str, str], name: str) -> str:
    """读取密钥：支持 ``NAME`` 或 ``NAME_FILE`` 指向的文件，不能同时设置。"""
    direct = _clean(env.get(name))
    path_text = _clean(env.get(f"{name}_FILE"))
    if direct and path_text:
        raise RelayConfigError(
            f"[RELAY_CONFIG_INVALID] config.load：{name} 与 {name}_FILE 只能设置一个。"
        )
    if not path_text:
        return direct
    try:
        return Path(path_text).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RelayConfigError(
            f"[RELAY_CONFIG_INVALID] config.load：无法读取 {name}_FILE：{path_text}。"
            "建议：检查文件路径与运行用户权限。"
        ) from exc


def _load_sandbox_entry(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RelayConfigError(
            f"[RELAY_CONFIG_MISSING] config.load：未找到 {path}。"
            "建议：先运行支付宝 AI 付 Skill 的沙箱准备，或设置 ALIPAY_APP_ID、"
            "ALIPAY_APP_PRIVATE_KEY、ALIPAY_PUBLIC_KEY。"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RelayConfigError(
            f"[RELAY_CONFIG_INVALID] config.load：无法读取 {path}。"
            "建议：重新生成沙箱配置，或改用生产环境变量。"
        ) from exc
    apps = payload.get("appIds") if isinstance(payload, dict) else None
    if not isinstance(apps, list):
        raise RelayConfigError(
            "[RELAY_CONFIG_INVALID] config.load：沙箱配置缺少 appIds。建议：重新运行沙箱准备。"
        )
    for item in apps:
        if isinstance(item, dict) and _clean(cast(str | None, item.get("appId"))):
            return cast(dict[str, Any], item)
    raise RelayConfigError(
        "[RELAY_CONFIG_INVALID] config.load：沙箱配置没有可用的 appId。建议：重新运行沙箱准备。"
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_settings(
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    production: bool = False,
) -> RelaySettings:
    env = os.environ if environ is None else environ
    base = (root or _repo_root()).resolve()
    app_id = _clean(env.get("ALIPAY_APP_ID"))
    private_key = _secret(env, "ALIPAY_APP_PRIVATE_KEY")
    public_key = _secret(env, "ALIPAY_PUBLIC_KEY")
    seller_id = _clean(env.get("ALIPAY_SELLER_ID"))
    supplied = bool(app_id or private_key or public_key)
    if production and not supplied:
        raise RelayConfigError(
            "[RELAY_CONFIG_INCOMPLETE] config.load：生产模式必须显式设置支付宝应用配置，"
            "不能回退沙箱文件。建议：设置 ALIPAY_APP_ID、ALIPAY_APP_PRIVATE_KEY、"
            "ALIPAY_PUBLIC_KEY、ALIPAY_SELLER_ID。"
        )

    if supplied:
        if not (app_id and private_key and public_key):
            raise RelayConfigError(
                "[RELAY_CONFIG_INCOMPLETE] config.load：生产支付配置缺少字段。"
                "建议：同时设置 ALIPAY_APP_ID、ALIPAY_APP_PRIVATE_KEY、ALIPAY_PUBLIC_KEY。"
            )
        gateway = _clean(env.get("ALIPAY_GATEWAY")) or PRODUCTION_GATEWAY
        sandbox = "sandbox" in gateway
    else:
        entry = _load_sandbox_entry(base / ".alipay-sandbox.json")
        app_id = _clean(cast(str | None, entry.get("appId")))
        private_key = _clean(cast(str | None, entry.get("appPrivatePkcsKey")))
        public_key = _clean(cast(str | None, entry.get("alipayPublicKey")))
        seller_id = seller_id or _clean(cast(str | None, entry.get("pid")))
        gateway = SANDBOX_GATEWAY
        sandbox = True
        if not private_key:
            raise RelayConfigError(
                "[RELAY_CONFIG_INVALID] config.load：沙箱配置缺少 appPrivatePkcsKey。"
                "建议：重新运行沙箱准备；Python 使用 PKCS#1 私钥。"
            )

    if not (app_id and private_key and public_key):
        raise RelayConfigError(
            "[RELAY_CONFIG_INVALID] config.load：支付配置缺少 appId、私钥或支付宝公钥。"
            "建议：检查环境变量或重新运行沙箱准备。"
        )

    if not sandbox and not seller_id:
        raise RelayConfigError(
            "[RELAY_CONFIG_INCOMPLETE] config.load：生产配置缺少 ALIPAY_SELLER_ID。"
            "建议：从支付宝商家平台获取收款账号的 2088 开头商家 ID 并设置。"
        )
    public_base = _clean(env.get("RELAY_PUBLIC_BASE_URL")) or f"http://127.0.0.1:{DEFAULT_PORT}"
    site_url = _clean(env.get("RELAY_SITE_URL")) or public_base
    return_url = _clean(env.get("ALIPAY_RETURN_URL")) or f"{public_base.rstrip('/')}/return/alipay"
    notify_url = _clean(env.get("ALIPAY_NOTIFY_URL"))
    if not sandbox:
        if not return_url.startswith("https://"):
            raise RelayConfigError(
                "[RELAY_CONFIG_INVALID] config.load：生产 return_url 必须使用 HTTPS。"
                "建议：设置 ALIPAY_RETURN_URL 或 RELAY_PUBLIC_BASE_URL 为正式 HTTPS 地址。"
            )
        if notify_url and not notify_url.startswith("https://"):
            raise RelayConfigError(
                "[RELAY_CONFIG_INVALID] config.load：生产 notify_url 必须使用 HTTPS。"
                "建议：使用支付宝服务器可访问的 HTTPS 通知地址，或省略该变量改为主动查单。"
            )
    database_text = _clean(env.get("RELAY_DB_PATH"))
    database_path = (
        Path(database_text).expanduser() if database_text else base / "data" / "clueledger.sqlite3"
    )
    origins_text = _clean(env.get("RELAY_ALLOWED_ORIGINS")) or (
        f"http://localhost:5173,http://127.0.0.1:5173,{site_url.rstrip('/')}"
    )
    origins = tuple(item.strip() for item in origins_text.split(",") if item.strip())
    reconcile_text = _clean(env.get("RELAY_RECONCILE_SECONDS"))
    try:
        reconcile_seconds = float(reconcile_text) if reconcile_text else 60.0
    except ValueError as exc:
        raise RelayConfigError(
            "[RELAY_CONFIG_INVALID] config.load：RELAY_RECONCILE_SECONDS 需要数字。"
            "建议：填写秒数，或设为 0 关闭后台查单。"
        ) from exc
    rates = _platform_rates(_clean(env.get("PLATFORM_MODELS")))
    admin_token = _secret(env, "RELAY_ADMIN_TOKEN")
    platform_api_key = _secret(env, "PLATFORM_MODEL_API_KEY")
    internal_secret = _secret(env, "RELAY_INTERNAL_SECRET")
    if production:
        production_errors: list[str] = []
        if sandbox:
            production_errors.append("网关仍是沙箱地址")
        if not public_base.startswith("https://") or not site_url.startswith("https://"):
            production_errors.append("RELAY_PUBLIC_BASE_URL / RELAY_SITE_URL 必须是 HTTPS")
        if not database_text:
            production_errors.append("RELAY_DB_PATH 必须显式指定")
        if not admin_token:
            production_errors.append("RELAY_ADMIN_TOKEN 必须设置")
        if not platform_api_key or not internal_secret:
            production_errors.append("PLATFORM_MODEL_API_KEY 与 RELAY_INTERNAL_SECRET 必须设置")
        if production_errors:
            raise RelayConfigError(
                "[RELAY_CONFIG_INCOMPLETE] config.load：生产配置不完整："
                + "；".join(production_errors)
                + "。建议：补齐后重新启动，不能回退沙箱或本地默认值。"
            )
    return RelaySettings(
        app_id=app_id,
        private_key=private_key,
        alipay_public_key=public_key,
        gateway=gateway,
        seller_id=seller_id,
        notify_url=notify_url,
        return_url=return_url,
        database_path=database_path,
        admin_token=admin_token,
        allowed_origins=origins,
        sandbox=sandbox,
        public_base_url=public_base,
        site_url=site_url,
        cookie_name=_clean(env.get("RELAY_COOKIE_NAME")) or "clue_session",
        reconcile_interval_seconds=reconcile_seconds,
        platform_base_url=(
            _clean(env.get("PLATFORM_MODEL_BASE_URL")) or "https://api.deepseek.com/v1"
        ).rstrip("/"),
        platform_api_key=platform_api_key,
        platform_models=rates,
        platform_default_output_tokens=_int_env(env, "PLATFORM_DEFAULT_OUTPUT_TOKENS", 4096),
        internal_secret=internal_secret,
        reservation_ttl_seconds=_int_env(env, "RELAY_RESERVATION_TTL_SECONDS", 3600),
        review_ttl_seconds=_int_env(env, "RELAY_REVIEW_TTL_SECONDS", 3600),
        max_concurrent_requests=_int_env(env, "RELAY_MAX_CONCURRENT_REQUESTS", 2),
        production=production,
    )


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _clean(env.get(name))
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RelayConfigError(
            f"[RELAY_CONFIG_INVALID] config.load：{name} 需要整数。建议：填写大于 0 的整数。"
        ) from exc
    if value <= 0:
        raise RelayConfigError(
            f"[RELAY_CONFIG_INVALID] config.load：{name} 必须大于 0。建议：填写合适的正整数。"
        )
    return value


def _platform_rates(raw: str) -> tuple[ModelRate, ...]:
    if not raw:
        return DEFAULT_RATES
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RelayConfigError(
            "[RELAY_CONFIG_INVALID] config.load：PLATFORM_MODELS 需要 JSON 数组。"
            "建议：每项包含 name、multiplier、max_output_tokens。"
        ) from exc
    if not isinstance(items, list) or not items:
        raise RelayConfigError(
            "[RELAY_CONFIG_INVALID] config.load：PLATFORM_MODELS 需要非空数组。"
            "建议：至少配置一个订阅模型。"
        )
    rates: list[ModelRate] = []
    for item in items:
        if not isinstance(item, dict) or not _clean(cast(str | None, item.get("name"))):
            raise RelayConfigError(
                "[RELAY_CONFIG_INVALID] config.load：PLATFORM_MODELS 的模型缺少 name。"
            )
        try:
            multiplier = Decimal(str(item.get("multiplier", "1.0")))
            max_output = int(item.get("max_output_tokens", 131072))
        except (InvalidOperation, ValueError) as exc:
            raise RelayConfigError(
                "[RELAY_CONFIG_INVALID] config.load：PLATFORM_MODELS 的倍率或输出上限无效。"
            ) from exc
        if multiplier <= 0 or max_output <= 0:
            raise RelayConfigError(
                "[RELAY_CONFIG_INVALID] config.load：PLATFORM_MODELS 数值必须大于 0。"
            )
        rates.append(
            ModelRate(
                name=_clean(cast(str | None, item.get("name"))),
                multiplier=multiplier,
                max_output_tokens=max_output,
            )
        )
    return tuple(rates)
