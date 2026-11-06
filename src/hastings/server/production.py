"""生产启动校验：缺项直接失败，不回退沙箱、本地状态目录或内存凭据。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["production_config_errors"]


def _https(url: str | None) -> bool:
    return bool(url) and urlsplit(url).scheme.lower() == "https"


def production_config_errors(
    *,
    mode: str,
    public_base_url: str | None,
    database: Path | None,
    workspaces_root: Path | None,
    libraries_root: Path | None,
    credential_key_file: Path | None,
    smtp_configured: bool,
    billing_url: str | None,
    billing_secret: str | None,
    trust_proxy: bool,
) -> list[str]:
    """返回所有缺项，而不是遇到第一个就停，便于一次改完部署环境。"""
    errors: list[str] = []
    if mode != "server":
        errors.append("--production 只能与 --mode server 一起使用")
    if not _https(public_base_url):
        errors.append("--public-base-url 必须是 HTTPS 正式地址")
    if database is None:
        errors.append("缺少 --database 账号数据库路径")
    if workspaces_root is None:
        errors.append("缺少 --workspaces-root 作品存储根")
    if libraries_root is None:
        errors.append("缺少 --libraries-root 私有模块库根")
    if credential_key_file is None:
        errors.append("缺少 --credential-key-file 凭据主密钥文件")
    if not smtp_configured:
        errors.append("缺少 SMTP 配置；生产不能用文件投递替代注册验证邮件")
    if not billing_url or not billing_secret:
        errors.append("缺少 --billing-url 或 CLUELEDGER_BILLING_INTERNAL_SECRET 订阅代理配置")
    if not trust_proxy:
        errors.append("缺少 --trust-proxy；反向代理后的限速需要读取 X-Forwarded-For")
    return errors
