"""NO_PROXY 兼容与系统代理合并（httpx / urllib 的环境变量交互）。"""

from __future__ import annotations

import os

import httpx
import pytest

from hastings.server.proxy_env import apply_system_proxy_env, normalize_no_proxy_env

_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def test_bracketed_ipv6_entries_are_unwrapped(monkeypatch) -> None:
    monkeypatch.setenv("NO_PROXY", "localhost,[::1], example.com")
    monkeypatch.setenv("no_proxy", "localhost,[::1], example.com")

    normalize_no_proxy_env()

    assert os.environ["NO_PROXY"] == "localhost,::1,example.com"
    assert os.environ["no_proxy"] == "localhost,::1,example.com"
    # 规范化之后 httpx 才能构造客户端；这一步是原始报错的位置。
    with httpx.Client(timeout=5):
        pass


def test_entries_with_scheme_or_port_are_kept(monkeypatch) -> None:
    monkeypatch.setenv("NO_PROXY", "http://[::1]:8080,.local,10.0.0.0/8")

    normalize_no_proxy_env()

    assert os.environ["NO_PROXY"] == "http://[::1]:8080,.local,10.0.0.0/8"


def test_windows_system_proxy_is_merged_when_no_proxy_env_exists(monkeypatch) -> None:
    if os.name != "nt":
        pytest.skip("只在 Windows 上读取系统代理")
    for key in _PROXY_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NO_PROXY", "localhost,[::1],.cn")
    monkeypatch.setattr(
        "urllib.request.getproxies_registry",
        lambda: {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
    )

    apply_system_proxy_env()

    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:7890"


def test_explicit_proxy_env_is_not_overwritten(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://explicit.example:8888")
    monkeypatch.setattr(
        "urllib.request.getproxies_registry",
        lambda: pytest.fail("已有显式代理时不应读取系统代理"),
    )

    apply_system_proxy_env()

    assert os.environ["HTTPS_PROXY"] == "http://explicit.example:8888"
