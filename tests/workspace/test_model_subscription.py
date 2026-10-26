"""订阅账号接入的地基（V09-1）。

契约：
- 连接多一个「认证方式」：``api_key`` 与 ``subscription``；旧配置不写该字段时仍按 API Key 读。
- 订阅凭据是结构化记录（访问令牌 / 刷新令牌 / 过期时间 / 账号标识），整体存进系统凭据存储，
  对外只给脱敏账号；配置文件里不出现任何令牌。
- 授权会话是临时状态：创建 → 轮询 → 成功 / 取消 / 超时；凭据在连接保存成功后才归属，
  会话随即不可再用。
- 订阅连接没有可解析的凭据时与「没填 Key」一样不可用，不能靠界面状态蒙混过关。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.application.errors import EnvironmentFailure
from hastings.infrastructure.credentials import InMemoryCredentialStore
from hastings.server.file_app import configured_provider_factory, create_workspace_app
from hastings.server.model_auth import AuthOutcome, AuthStart, ModelAuthService
from hastings.server.model_connections import ConnectionDraft, ModelConnectionService
from hastings.server.model_credential import (
    SubscriptionCredential,
    load_subscription,
    mask_account,
)
from hastings.server.provider_config import ProviderBook, load_server_config
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.state import RecentWorkspaces

LEGACY = """providers:
- name: deepseek
  base_url: https://api.deepseek.com/v1
  api_key: sk-legacy-key
  default_model: deepseek-chat
"""


def _run(coro: object) -> Any:
    assert asyncio.iscoroutine(coro)
    return asyncio.run(coro)


def _credential(**overrides: object) -> SubscriptionCredential:
    fields: dict[str, object] = {
        "auth_provider": "chatgpt",
        "access_token": "access-token-abcdef",
        "refresh_token": "refresh-token-123456",
        "expires_at": 1_800_000_000.0,
        "account_id": "acct-0001",
        "account_label": "reader@example.com",
    }
    fields.update(overrides)
    return SubscriptionCredential(**fields)  # type: ignore[arg-type]


class _FakeAuthProvider:
    """按供应商适配的授权流程替身：只走「开始 → 轮询」两步。"""

    id = "chatgpt"
    name = "ChatGPT"

    def __init__(self) -> None:
        self.started = 0
        self.cancelled = 0
        self._outcome = AuthOutcome(status="pending")

    def begin(self) -> AuthStart:
        self.started += 1
        return AuthStart(
            url="https://auth.example.com/authorize?state=abc", flow=_Flow()
        )

    async def poll(self, flow: Any) -> AuthOutcome:
        return self._outcome

    async def cancel(self, flow: Any) -> None:
        self.cancelled += 1

    def finish(self, credential: SubscriptionCredential | None = None, *, code: str = "") -> None:
        if credential is None:
            self._outcome = AuthOutcome(status="failed", code=code or "AUTH_DENIED")
            return
        self._outcome = AuthOutcome(status="succeeded", credential=credential)


@dataclass(slots=True)
class _Flow:
    """替身适配的进行中状态；测试不关心内容，只验证服务原样转交。"""

    seen: int = 0


def _auth(
    clock: Any | None = None, ttl: float = 600.0
) -> tuple[ModelAuthService, _FakeAuthProvider]:
    provider = _FakeAuthProvider()
    service = ModelAuthService(
        providers={provider.id: provider},
        clock=clock if clock is not None else time.time,
        ttl_seconds=ttl,
    )
    return service, provider


def _service(
    tmp_path: Path, *, text: str = "", auth: ModelAuthService | None = None
) -> tuple[ModelConnectionService, ProviderBook, InMemoryCredentialStore, Path]:
    path = tmp_path / "config.yaml"
    if text:
        path.write_text(text, encoding="utf-8")
    store = InMemoryCredentialStore()
    book = ProviderBook(
        load_server_config(path), boot_name="", boot_model="", config_path=path, credentials=store
    )
    service = ModelConnectionService(
        book=book, config_path=path, credentials=store, auth=auth
    )
    return service, book, store, path


def _subscription_draft(**overrides: object) -> ConnectionDraft:
    fields: dict[str, object] = {
        "display_name": "ChatGPT 订阅",
        "catalog_id": "oai-subscription",
        "base_url": "https://api.openai.com/v1",
        "api": "openai-responses",
        "models": ["gpt-5-codex"],
        "default_model": "gpt-5-codex",
        "auth_method": "subscription",
        "auth_provider": "chatgpt",
    }
    fields.update(overrides)
    return ConnectionDraft(**fields)  # type: ignore[arg-type]


def _create_subscription(
    service: ModelConnectionService, session: str, **overrides: object
) -> None:
    _run(
        service.create(
            _subscription_draft(auth_session_id=session, **overrides),
            expected_revision=service.revision,
        )
    )


def _login(auth: ModelAuthService, provider: _FakeAuthProvider, **overrides: object) -> str:
    """跑完一次授权，返回已成功的会话 ID。"""
    view = _run(auth.create(provider.id))
    provider.finish(_credential(**overrides))
    _run(auth.status(view.id))
    return view.id


# -- 凭据结构 ---------------------------------------------------------------


def test_credential_roundtrips_through_credential_store() -> None:
    store = InMemoryCredentialStore()
    original = _credential()

    from hastings.server.model_credential import save_subscription

    ref = "hastings:subscription:conn_1:abcd1234"
    save_subscription(store, ref, original)

    raw = store.load(ref)
    assert raw is not None
    # 存储里是一条结构化记录，不是裸串 key。
    payload = json.loads(raw)
    assert payload["type"] == "subscription"
    assert payload["access"] == "access-token-abcdef"
    assert load_subscription(store, ref) == original


def test_missing_credential_loads_as_none() -> None:
    store = InMemoryCredentialStore()
    assert load_subscription(store, "hastings:subscription:none:0000") is None


def test_account_label_is_masked_for_display() -> None:
    assert mask_account("reader@example.com") == "r…@example.com"
    assert mask_account("acct-00000001") == "…0001"
    assert mask_account("") == ""


# -- 配置读取与写入 ---------------------------------------------------------


def test_legacy_config_reads_as_api_key_connection(tmp_path: Path) -> None:
    service, book, _store, _path = _service(tmp_path, text=LEGACY)

    profile = book.profile("deepseek")
    assert profile is not None
    assert profile.auth_method == "api_key"
    assert profile.auth_provider == ""
    connection = service.view().connections[0]
    assert connection.auth_method == "api_key"
    assert connection.auth_status == "ready"
    assert connection.account == ""


def test_subscription_connection_omits_api_key_on_disk(tmp_path: Path) -> None:
    auth, provider = _auth()
    service, _book, _store, path = _service(tmp_path, auth=auth)
    session = _login(auth, provider)

    _create_subscription(service, session)

    text = path.read_text(encoding="utf-8")
    assert "access-token-abcdef" not in text
    assert "refresh-token-123456" not in text
    assert "api_key:" not in text
    saved = load_server_config(path).providers[0]
    assert saved.auth_method == "subscription"
    assert saved.auth_provider == "chatgpt"
    assert saved.credential_ref.startswith("hastings:subscription:")


def test_subscription_credential_is_structured_in_store(tmp_path: Path) -> None:
    auth, provider = _auth()
    service, book, store, _path = _service(tmp_path, auth=auth)
    session = _login(auth, provider)

    _create_subscription(service, session)

    connection = service.view().connections[0]
    assert connection.has_key is True
    assert connection.auth_method == "subscription"
    assert connection.auth_provider == "chatgpt"
    assert connection.account == "r…@example.com"
    assert connection.masked_key == ""
    # 视图里不出现任何令牌。
    assert "access-token-abcdef" not in repr(service.view())
    profile = book.profile(connection.name)
    assert profile is not None
    assert profile.subscription is not None
    assert profile.subscription.refresh_token == "refresh-token-123456"
    # 结构化记录落到凭据存储里，原文是 JSON。
    raw = store.load(profile.credential_ref)
    assert raw is not None and json.loads(raw)["account_label"] == "reader@example.com"


def test_subscription_without_session_is_rejected(tmp_path: Path) -> None:
    auth, _provider = _auth()
    service, _book, _store, _path = _service(tmp_path, auth=auth)

    with pytest.raises(WorkspaceError) as caught:
        _run(service.create(_subscription_draft(), expected_revision=service.revision))

    assert caught.value.code == "MODEL_AUTH_SESSION_REQUIRED"


def test_subscription_requires_finished_session(tmp_path: Path) -> None:
    auth, _provider = _auth()
    service, _book, _store, _path = _service(tmp_path, auth=auth)
    pending = _run(auth.create("chatgpt"))

    with pytest.raises(WorkspaceError) as caught:
        _run(
            service.create(
                _subscription_draft(auth_session_id=pending.id), expected_revision=service.revision
            )
        )

    assert caught.value.code == "MODEL_AUTH_SESSION_NOT_COMPLETED"


def test_unknown_auth_provider_is_rejected(tmp_path: Path) -> None:
    auth, provider = _auth()
    service, _book, _store, _path = _service(tmp_path, auth=auth)
    session = _login(auth, provider)

    with pytest.raises(WorkspaceError) as caught:
        _run(
            service.create(
                _subscription_draft(auth_provider="claude", auth_session_id=session),
                expected_revision=service.revision,
            )
        )

    assert caught.value.code == "MODEL_AUTH_PROVIDER_MISMATCH"


def test_session_cannot_be_saved_twice(tmp_path: Path) -> None:
    auth, provider = _auth()
    service, _book, _store, _path = _service(tmp_path, auth=auth)
    session = _login(auth, provider)
    _create_subscription(service, session)

    with pytest.raises(WorkspaceError) as caught:
        _run(
            service.create(
                _subscription_draft(auth_session_id=session), expected_revision=service.revision
            )
        )

    assert caught.value.code == "MODEL_AUTH_SESSION_USED"


def test_update_keeps_existing_subscription_credential(tmp_path: Path) -> None:
    auth, provider = _auth()
    service, book, _store, _path = _service(tmp_path, auth=auth)
    session = _login(auth, provider)
    _create_subscription(service, session)
    name = service.view().connections[0].name
    before = book.profile(name)
    assert before is not None

    _run(
        service.update(
            name,
            _subscription_draft(display_name="改个名字", models=["gpt-5-codex", "o4-mini"]),
            expected_revision=service.revision,
        )
    )

    after = book.profile(name)
    assert after is not None
    assert after.display_name == "改个名字"
    assert after.credential_ref == before.credential_ref
    assert after.subscription == before.subscription


def test_relogin_replaces_credential_only_after_success(tmp_path: Path) -> None:
    auth, provider = _auth()
    service, book, _store, path = _service(tmp_path, auth=auth)
    session = _login(auth, provider)
    _create_subscription(service, session)
    name = service.view().connections[0].name
    original = book.profile(name)
    assert original is not None

    second = _login(
        auth, provider, access_token="access-token-second", account_label="other@example.com"
    )
    _run(
        service.update(
            name,
            _subscription_draft(auth_session_id=second),
            expected_revision=service.revision,
        )
    )

    updated = book.profile(name)
    assert updated is not None
    assert updated.subscription is not None
    assert updated.subscription.access_token == "access-token-second"
    assert updated.credential_ref != original.credential_ref
    assert "access-token-second" not in path.read_text(encoding="utf-8")


def test_delete_subscription_removes_stored_credential(tmp_path: Path) -> None:
    auth, provider = _auth()
    service, book, store, _path = _service(tmp_path, auth=auth)
    session = _login(auth, provider)
    _create_subscription(service, session)
    name = service.view().connections[0].name
    profile = book.profile(name)
    assert profile is not None
    ref = profile.credential_ref

    _run(service.delete(name, expected_revision=service.revision))

    assert store.load(ref) is None
    assert service.view().connections == []


def test_subscription_connection_can_be_selected_and_snapshotted(tmp_path: Path) -> None:
    # 选中后这一轮真的能拿到实例：请求形状由订阅适配决定，不经过 API Key 配置。
    auth, provider = _auth()
    service, book, _store, _path = _service(tmp_path, auth=auth)
    session = _login(auth, provider)
    _create_subscription(service, session)
    name = service.view().connections[0].name
    _run(service.select(name, "gpt-5-codex", expected_revision=service.revision))

    snapshot = book.snapshot()

    assert snapshot.model == "gpt-5-codex"
    assert snapshot.connection_id == name


def test_subscription_without_credential_is_unavailable(tmp_path: Path) -> None:
    # 凭据被外部删掉（系统凭据管理器被清空）：连接还在，但不能当可用。
    text = """providers:
- name: conn_sub
  display_name: ChatGPT 订阅
  base_url: https://api.openai.com/v1
  api: openai-responses
  catalog_id: oai-subscription
  auth_method: subscription
  auth_provider: chatgpt
  credential_ref: hastings:subscription:conn_sub:dead0000
  default_model: gpt-5-codex
"""
    service, book, _store, _path = _service(tmp_path, text=text)

    connection = service.view().connections[0]
    assert connection.auth_status == "missing"
    assert connection.has_key is False
    profile = book.profile("conn_sub")
    assert profile is not None and profile.available is False


# -- 授权会话 ---------------------------------------------------------------


def test_create_session_exposes_url_only() -> None:
    auth, provider = _auth()

    view = _run(auth.create("chatgpt"))

    assert view.status == "pending"
    assert view.auth_url.startswith("https://auth.example.com/authorize")
    assert view.provider_name == "ChatGPT"
    assert "access-token" not in repr(view)
    assert provider.started == 1


def test_unknown_provider_cannot_start_session() -> None:
    auth, _provider = _auth()

    with pytest.raises(WorkspaceError) as caught:
        _run(auth.create("nope"))

    assert caught.value.code == "MODEL_AUTH_PROVIDER_UNKNOWN"


def test_status_polls_provider_until_success() -> None:
    auth, provider = _auth()
    view = _run(auth.create("chatgpt"))

    assert _run(auth.status(view.id)).status == "pending"
    provider.finish(_credential())
    succeeded = _run(auth.status(view.id))

    assert succeeded.status == "succeeded"
    assert succeeded.account == "r…@example.com"
    assert "access-token-abcdef" not in repr(succeeded)


def test_failed_authorization_reports_code() -> None:
    auth, provider = _auth()
    view = _run(auth.create("chatgpt"))
    provider.finish(None, code="AUTH_DENIED")

    failed = _run(auth.status(view.id))

    assert failed.status == "failed"
    assert failed.code == "AUTH_DENIED"


def test_cancel_cleans_session_state() -> None:
    auth, provider = _auth()
    view = _run(auth.create("chatgpt"))

    cancelled = _run(auth.cancel(view.id))
    assert cancelled.status == "cancelled"
    assert provider.cancelled == 1

    with pytest.raises(WorkspaceError) as caught:
        _run(auth.status(view.id))
    assert caught.value.code == "MODEL_AUTH_SESSION_NOT_FOUND"


def test_session_expires_after_ttl() -> None:
    now = [1000.0]
    auth, provider = _auth(clock=lambda: now[0], ttl=60.0)
    view = _run(auth.create("chatgpt"))
    provider.finish(_credential())

    now[0] += 61.0

    expired = _run(auth.status(view.id))
    assert expired.status == "expired"

    with pytest.raises(WorkspaceError) as caught:
        auth.consume(view.id, provider_id="chatgpt")
    assert caught.value.code == "MODEL_AUTH_SESSION_EXPIRED"


def test_unknown_session_is_reported() -> None:
    auth, _provider = _auth()

    with pytest.raises(WorkspaceError) as caught:
        _run(auth.status("does-not-exist"))

    assert caught.value.code == "MODEL_AUTH_SESSION_NOT_FOUND"


def test_provider_failure_to_start_is_reported() -> None:
    class _Broken(_FakeAuthProvider):
        def begin(self) -> AuthStart:
            raise RuntimeError("127.0.0.1 上没有可用端口")

    auth = ModelAuthService(providers={"chatgpt": _Broken()})

    with pytest.raises(WorkspaceError) as caught:
        _run(auth.create("chatgpt"))

    assert caught.value.code == "MODEL_AUTH_UNAVAILABLE"
    assert "端口" in caught.value.why



# -- 接口 -------------------------------------------------------------------


def _client(
    tmp_path: Path, *, auth: ModelAuthService | None = None
) -> tuple[TestClient, ModelConnectionService, Path]:
    if auth is None:
        auth = _auth()[0]
    service, book, _store, path = _service(tmp_path, auth=auth)
    app = create_workspace_app(
        workspaces=RecentWorkspaces(tmp_path / "state"),
        provider_factory=configured_provider_factory(book),
        model_service=service,
    )
    return TestClient(app), service, path


def test_auth_session_endpoints(tmp_path: Path) -> None:
    auth, provider = _auth()
    client, _service_, _path = _client(tmp_path, auth=auth)

    created = client.post("/api/model-auth/sessions", json={"provider_id": "chatgpt"})
    assert created.status_code == 201, created.text
    session = created.json()
    assert session["status"] == "pending"
    assert session["auth_url"].startswith("https://auth.example.com/")

    provider.finish(_credential())
    polled = client.get(f"/api/model-auth/sessions/{session['id']}")
    assert polled.status_code == 200
    assert polled.json()["status"] == "succeeded"
    assert "access-token-abcdef" not in polled.text

    removed = client.delete(f"/api/model-auth/sessions/{session['id']}")
    assert removed.status_code == 200
    assert client.get(f"/api/model-auth/sessions/{session['id']}").status_code == 404


def test_connection_api_saves_subscription(tmp_path: Path) -> None:
    auth, provider = _auth()
    client, _service_, path = _client(tmp_path, auth=auth)
    session = _login(auth, provider)

    revision = client.get("/api/model-connections").json()["revision"]
    body = {
        "display_name": "ChatGPT 订阅",
        "catalog_id": "oai-subscription",
        "base_url": "https://api.openai.com/v1",
        "api": "openai-responses",
        "models": ["gpt-5-codex"],
        "default_model": "gpt-5-codex",
        "auth_method": "subscription",
        "auth_provider": "chatgpt",
        "auth_session_id": session,
    }
    created = client.post("/api/model-connections", json=body, headers={"If-Match": revision})
    assert created.status_code == 200, created.text
    connection = created.json()["connections"][0]
    assert connection["auth_method"] == "subscription"
    assert connection["account"] == "r…@example.com"
    assert connection["auth_status"] == "ready"
    assert connection["has_key"] is True
    assert "access-token" not in created.text
    assert "api_key" not in path.read_text(encoding="utf-8")


def test_providers_endpoint_marks_subscription_sources(tmp_path: Path) -> None:
    client, _service_, _path = _client(tmp_path)

    presets = client.get("/api/model-providers").json()

    chatgpt = next(item for item in presets if item["id"] == "chatgpt")
    assert chatgpt["auth_providers"] == ["chatgpt"]
    deepseek = next(item for item in presets if item["id"] == "deepseek")
    assert deepseek["auth_providers"] == []


def test_expired_session_releases_callback_listener() -> None:
    now = [1000.0]
    auth, provider = _auth(clock=lambda: now[0], ttl=60.0)
    _run(auth.create("chatgpt"))

    now[0] += 61.0
    _run(auth.create("chatgpt"))

    # 超时的会话必须把手上的回调监听关掉，否则端口和线程会一直攒着。
    assert provider.cancelled == 1


def test_status_expiry_also_releases_listener() -> None:
    now = [1000.0]
    auth, provider = _auth(clock=lambda: now[0], ttl=60.0)
    view = _run(auth.create("chatgpt"))

    now[0] += 61.0
    expired = _run(auth.status(view.id))

    assert expired.status == "expired"
    assert provider.cancelled == 1


class _LimitedStore(InMemoryCredentialStore):
    """模拟 Windows 凭据管理器：单条超过 1280 个字符就拒绝写入。"""

    def save(self, ref: str, secret: str) -> None:
        if len(secret) > 1280:
            raise EnvironmentFailure(
                code="CREDENTIAL_STORE_UNAVAILABLE",
                where=f"credentials.keyring.{ref}",
                why=f"写入操作系统凭据存储失败：数据过长（{len(secret)} 字符）",
                fix="解锁系统凭据管理器后重试",
            )
        super().save(ref, secret)


def test_subscription_credential_chunks_past_a_small_store_limit(tmp_path: Path) -> None:
    # 订阅凭据里有一枚 JWT，整条 JSON 远超系统凭据存储的单条上限；不分片就存不进去。
    auth, provider = _auth()
    path = tmp_path / "config.yaml"
    store = _LimitedStore()
    book = ProviderBook(
        load_server_config(path), boot_name="", boot_model="", config_path=path, credentials=store
    )
    service = ModelConnectionService(book=book, config_path=path, credentials=store, auth=auth)
    session = _login(auth, provider, access_token="jwt-" + "a" * 3000)

    _create_subscription(service, session)

    name = service.view().connections[0].name
    profile = book.profile(name)
    assert profile is not None
    assert profile.subscription is not None
    assert profile.subscription.access_token.endswith("a" * 20)
    # 索引与分片都在存储里，删除时一并清掉，不留残渣。
    assert len(store.refs()) > 1
    _run(service.delete(name, expected_revision=service.revision))
    assert store.refs() == set()
