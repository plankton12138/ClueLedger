"""订阅凭据的结构化记录（V09-1）。

订阅登录拿到的不是一个可以当 API Key 用的字符串，而是一组会持续变化的事实：
访问令牌、刷新令牌、过期时间、账号身份，以及供应商额外要求的东西（注册身份、稳定设备
标识等）。用普通 key 字段装它只有两种结局——要么把 JSON 当 Bearer 发出去，要么为了
「续期」在配置文件里塞明文。

所以这里定义一条独立记录：整条序列化后存进系统凭据存储，配置文件里只留
``credential_ref``。``access_token`` / ``refresh_token`` 不参与 repr，日志与错误信息
里不会意外出现。

过期判断带提前量：刷新请求要在令牌真的失效之前发出，否则这一轮会在请求中途失败，
作者看到的是「写作卡住」而不是「该续期了」。
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CHUNK_CHARS",
    "INDEX_TYPE",
    "REFRESH_SKEW_SECONDS",
    "SUBSCRIPTION_REF_PREFIX",
    "SubscriptionCredential",
    "SubscriptionCredentialError",
    "delete_subscription",
    "load_subscription",
    "mask_account",
    "save_subscription",
]

#: 凭据记录的类型标记，用于识别存储里的一条 JSON 是不是订阅凭据。
CREDENTIAL_TYPE = "subscription"

#: 提前续期的窗口：过期时间前这么久就视为「该刷新了」。
REFRESH_SKEW_SECONDS = 120.0

#: 单段长度上限。Windows 凭据管理器单条装不下 1280 个字符（2560 字节 UTF-16），
#: 取 1000 留出余量：超了是写入直接报错，不是截断。
CHUNK_CHARS = 1000

#: 分片索引的类型标记：索引存在即代表这条凭据完整。
INDEX_TYPE = "subscription-chunks"

#: 订阅凭据引用的前缀；连接服务据此判断该用分片方式读写与清理。
SUBSCRIPTION_REF_PREFIX = "hastings:subscription:"


class SubscriptionCredentialError(ValueError):
    """存储里的订阅凭据无法解析（消息随异常携带）。"""


@dataclass(frozen=True, slots=True)
class SubscriptionCredential:
    """一次订阅授权的完整凭据；只在内存与系统凭据存储之间流转。"""

    auth_provider: str
    #: 访问令牌与刷新令牌不进 repr：两者都是可直接调用供应商的凭证。
    access_token: str = field(repr=False)
    refresh_token: str = field(default="", repr=False)
    #: epoch 秒；``0`` 表示该供应商不提供到期时间（只能靠 401 判断）。
    expires_at: float = 0.0
    account_id: str = ""
    account_label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """存储形状：字段名短且稳定，便于以后加字段而不动读取逻辑。"""
        payload: dict[str, Any] = {
            "type": CREDENTIAL_TYPE,
            "provider": self.auth_provider,
            "access": self.access_token,
            "refresh": self.refresh_token,
            "expires_at": self.expires_at,
            "account_id": self.account_id,
            "account_label": self.account_label,
        }
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    def expired(self, *, now: float | None = None, skew: float = REFRESH_SKEW_SECONDS) -> bool:
        """是否到了该续期的时候；没有到期时间的供应商永远返回 False。"""
        if self.expires_at <= 0:
            return False
        moment = time.time() if now is None else now
        return moment >= self.expires_at - skew

    @classmethod
    def from_json(cls, raw: object) -> SubscriptionCredential:
        if not isinstance(raw, dict):
            raise SubscriptionCredentialError("订阅凭据应为 JSON 对象")
        if raw.get("type") != CREDENTIAL_TYPE:
            raise SubscriptionCredentialError(f"订阅凭据类型应为 {CREDENTIAL_TYPE}")
        metadata = raw.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise SubscriptionCredentialError("订阅凭据的 metadata 应为对象")
        return cls(
            auth_provider=_required_text(raw, "provider"),
            access_token=_required_text(raw, "access"),
            refresh_token=_optional_text(raw, "refresh"),
            expires_at=_timestamp(raw.get("expires_at")),
            account_id=_optional_text(raw, "account_id"),
            account_label=_optional_text(raw, "account_label"),
            metadata=dict(metadata),
        )


def save_subscription(store: Any, ref: str, credential: SubscriptionCredential) -> None:
    """把整条凭据写进系统凭据存储，必要时分片。

    Windows 凭据管理器单条只装 1280 个字符（2560 字节 UTF-16），而订阅凭据里有一枚
    JWT 访问令牌，整条 JSON 常常远超这个长度。超了就拆成几段，最后写一个索引段：
    索引出现即代表这条凭据完整。

    每段带一个代次标记，替换时先写新代次、再换索引、最后删旧代次——中途失败时旧索引
    仍然指向旧数据，不会出现「读到一半」的凭据。
    """
    payload = json.dumps(credential.to_json(), ensure_ascii=False, sort_keys=True)
    chunks = [payload[start : start + CHUNK_CHARS] for start in range(0, len(payload), CHUNK_CHARS)]
    previous = _stored_index(store, ref)
    if len(chunks) <= 1:
        store.save(ref, payload)
        if previous is not None:
            _drop_chunks(store, ref, previous)
        return
    generation = os.urandom(3).hex()
    written = 0
    try:
        for index, chunk in enumerate(chunks):
            store.save(_chunk_ref(ref, generation, index), chunk)
            written += 1
        store.save(
            ref,
            json.dumps(
                {"type": INDEX_TYPE, "parts": len(chunks), "gen": generation},
                sort_keys=True,
            ),
        )
    except Exception:
        for index in range(written):
            with contextlib.suppress(Exception):
                store.delete(_chunk_ref(ref, generation, index))
        raise
    if previous is not None:
        _drop_chunks(store, ref, previous)


def load_subscription(store: Any, ref: str) -> SubscriptionCredential | None:
    """读回凭据；存储里没有这条 ref 时返回 None（合法空结果）。"""
    raw = store.load(ref)
    if raw is None or not raw.strip():
        return None
    payload = _parse_json(raw)
    if isinstance(payload, dict) and payload.get("type") == INDEX_TYPE:
        parts = payload.get("parts")
        generation = payload.get("gen")
        if not isinstance(parts, int) or isinstance(parts, bool) or parts <= 0:
            raise SubscriptionCredentialError("订阅凭据分片索引损坏：缺少段数")
        if not isinstance(generation, str) or not generation:
            raise SubscriptionCredentialError("订阅凭据分片索引损坏：缺少代次")
        pieces: list[str] = []
        for index in range(parts):
            piece = store.load(_chunk_ref(ref, generation, index))
            if piece is None:
                raise SubscriptionCredentialError(
                    f"订阅凭据缺少分片 {index + 1}/{parts}；需要重新登录这个连接"
                )
            pieces.append(piece)
        payload = _parse_json("".join(pieces))
    return SubscriptionCredential.from_json(payload)


def delete_subscription(store: Any, ref: str) -> None:
    """删除整条凭据，包括它的所有分片。"""
    index = _stored_index(store, ref)
    if index is not None:
        _drop_chunks(store, ref, index)
    store.delete(ref)


def _parse_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SubscriptionCredentialError("订阅凭据不是合法 JSON") from exc


def _stored_index(store: Any, ref: str) -> tuple[str, int] | None:
    """读出「已分片」的代次与段数；单条存放或没有数据时返回 None。"""
    with contextlib.suppress(Exception):
        raw = store.load(ref)
        if raw:
            payload = json.loads(raw)
            if isinstance(payload, dict) and payload.get("type") == INDEX_TYPE:
                generation = payload.get("gen")
                parts = payload.get("parts")
                if isinstance(generation, str) and generation and isinstance(parts, int):
                    return generation, parts
    return None


def _drop_chunks(store: Any, ref: str, index: tuple[str, int]) -> None:
    generation, parts = index
    for position in range(parts):
        with contextlib.suppress(Exception):
            store.delete(_chunk_ref(ref, generation, position))


def _chunk_ref(ref: str, generation: str, index: int) -> str:
    return f"{ref}#{generation}-{index}"


def mask_account(label: str, account_id: str = "") -> str:
    """账号标识脱敏：邮箱保留首字母与域名，其他标识只留尾四位。"""
    text = (label or account_id).strip()
    if not text:
        return ""
    if "@" in text:
        local, _, domain = text.partition("@")
        head = local[:1] or "…"
        return f"{head}…@{domain}"
    if len(text) <= 4:
        return "…"
    return f"…{text[-4:]}"


def _required_text(raw: dict[str, Any], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SubscriptionCredentialError(f"订阅凭据缺少字段：{name}")
    return value.strip()


def _optional_text(raw: dict[str, Any], name: str) -> str:
    value = raw.get(name, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SubscriptionCredentialError(f"订阅凭据字段应为字符串：{name}")
    return value.strip()


def _timestamp(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SubscriptionCredentialError("订阅凭据的 expires_at 应为数字")
    return float(value)
