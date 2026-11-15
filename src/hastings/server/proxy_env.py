"""代理环境变量的兼容处理。

两件事在应用启动时做一次，让所有 httpx 客户端（模型调用、模型发现、授权登录）都受益：

- **带方括号的 IPv6**：httpx 0.28 把 ``NO_PROXY`` 里的 ``[::1]`` 当成 URL 模式解析，
  客户端构造阶段就会抛 ``InvalidURL: Invalid port: ':1]'``。去掉方括号后 httpx 能正确
  识别，不改变原本「本机地址不走代理」的语义。
- **系统代理被 NO_PROXY 挡住**：urllib 的 ``getproxies()`` 是「环境代理或注册表代理」，
  只要环境里有 ``NO_PROXY``（哪怕没有 ``HTTP_PROXY``），它就不再读 Windows 系统代理。
  结果是「系统代理开着，应用却直连」。没有显式代理变量时，这里把系统代理补进环境。
"""

from __future__ import annotations

import os

__all__ = ["apply_system_proxy_env", "normalize_no_proxy_env"]

_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def normalize_no_proxy_env() -> None:
    """把 ``NO_PROXY`` / ``no_proxy`` 里的带方括号 IPv6 地址改成裸地址。"""
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key)
        if not raw:
            continue
        normalized = ",".join(_normalize(entry) for entry in raw.split(","))
        if normalized != raw:
            os.environ[key] = normalized


def apply_system_proxy_env() -> None:
    """没有显式代理变量时，把 Windows 系统代理写进环境；其他平台不做处理。"""
    if os.name != "nt":
        return
    if any(os.environ.get(key) for key in _PROXY_KEYS):
        return
    from urllib.request import getproxies_registry

    proxies = getproxies_registry()
    http = proxies.get("http")
    https = proxies.get("https") or http
    if https:
        os.environ.setdefault("HTTPS_PROXY", https)
        os.environ.setdefault("https_proxy", https)
    if http:
        os.environ.setdefault("HTTP_PROXY", http)
        os.environ.setdefault("http_proxy", http)


def _normalize(entry: str) -> str:
    value = entry.strip()
    if value.startswith("[") and value.endswith("]") and ":" in value:
        return value[1:-1]
    return value
