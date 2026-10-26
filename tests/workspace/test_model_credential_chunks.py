"""订阅凭据的分片存储（V09-6）。

Windows 凭据管理器单条只能装 1280 个字符（2560 字节 UTF-16），超了就直接报错；
而订阅凭据里有一枚 JWT 访问令牌，整条 JSON 远超这个长度。所以一条凭据要拆成几段存，
最后写一个索引段——索引存在即代表这条凭据完整。

契约：
- 小凭据仍然单条保存，旧数据照旧读得出来。
- 分片写入失败要清理已写的段，不留下「读得出索引、拼不出凭据」的半条数据。
- 续期后长度变化（段数变少）时，多出来的旧段要删掉。
- 删除连接时所有段一起删。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from hastings.server.model_credential import (
    CHUNK_CHARS,
    INDEX_TYPE,
    SubscriptionCredential,
    SubscriptionCredentialError,
    delete_subscription,
    load_subscription,
    save_subscription,
)

REF = "hastings:subscription:conn_sub:abcd1234"


class _BoundedStore:
    """模拟 Windows 凭据管理器：单条超过上限就写入失败。"""

    def __init__(self, limit: int = 1280) -> None:
        self.limit = limit
        self.values: dict[str, str] = {}
        self.fail_on_write: int | None = None
        self.writes = 0

    def save(self, ref: str, secret: str) -> None:
        self.writes += 1
        if len(secret) > self.limit:
            raise RuntimeError(f"CredWrite 拒绝：{len(secret)} > {self.limit}")
        if self.fail_on_write is not None and self.writes == self.fail_on_write:
            raise RuntimeError("写入中断")
        self.values[ref] = secret

    def load(self, ref: str) -> str | None:
        return self.values.get(ref)

    def delete(self, ref: str) -> None:
        self.values.pop(ref, None)


def _credential(length: int = 3000, **overrides: Any) -> SubscriptionCredential:
    fields: dict[str, Any] = {
        "auth_provider": "chatgpt",
        "access_token": "a" * length,
        "refresh_token": "refresh-token-1",
        "expires_at": 1_800_000_000.0,
        "account_id": "user_0001",
        "account_label": "reader@example.com",
        "metadata": {"client_id": "oaiapp_issued_1", "resource": "https://api.openai.com/v1"},
    }
    fields.update(overrides)
    return SubscriptionCredential(**fields)  # type: ignore[arg-type]


def test_large_credential_is_split_and_round_trips() -> None:
    store = _BoundedStore()

    save_subscription(store, REF, _credential())

    index = json.loads(store.values[REF])
    assert index["type"] == INDEX_TYPE
    assert index["parts"] >= 3
    # 每一段都在存储容量之内。
    assert all(len(value) <= store.limit for value in store.values.values())
    assert load_subscription(store, REF) == _credential()


def test_small_credential_stays_a_single_entry() -> None:
    store = _BoundedStore()

    save_subscription(store, REF, _credential(length=40))

    payload = json.loads(store.values[REF])
    assert payload["type"] == "subscription"
    assert list(store.values) == [REF]
    assert load_subscription(store, REF) == _credential(length=40)


def test_chunk_size_stays_under_the_store_limit() -> None:
    assert CHUNK_CHARS < 1280


def test_delete_removes_every_chunk() -> None:
    store = _BoundedStore()
    save_subscription(store, REF, _credential())

    delete_subscription(store, REF)

    assert store.values == {}


def test_missing_chunk_is_reported_not_silently_empty() -> None:
    store = _BoundedStore()
    save_subscription(store, REF, _credential())
    chunks = sorted(ref for ref in store.values if ref != REF)
    store.delete(chunks[1])

    with pytest.raises(SubscriptionCredentialError) as caught:
        load_subscription(store, REF)

    assert "分片" in str(caught.value)


def test_partial_write_failure_leaves_nothing_behind() -> None:
    store = _BoundedStore()
    store.fail_on_write = 3

    with pytest.raises(RuntimeError):
        save_subscription(store, REF, _credential())

    assert store.values == {}


def test_shrinking_credential_drops_extra_chunks() -> None:
    store = _BoundedStore()
    save_subscription(store, REF, _credential(length=3000))
    before = {ref for ref in store.values if ref != REF}
    assert len(before) >= 3

    save_subscription(store, REF, _credential(length=40))

    assert set(store.values) == {REF}
    assert load_subscription(store, REF) == _credential(length=40)
