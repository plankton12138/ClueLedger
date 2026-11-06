"""v1.4 P4：线簿订阅作为服务器注入的模型来源。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.identity import bind_user_id, reset_user_id
from hastings.relay.internal_auth import verify_grant
from hastings.server.model_factory import configured_provider_factory
from hastings.server.platform_channel import PLATFORM_CONNECTION, PlatformChannel
from hastings.server.provider_config import (
    ProviderBook,
    ServerConfig,
)
from hastings.workspace.errors import WorkspaceError

USER = "usr_platform000000000000000000000"
SECRET = "platform-secret"


def _channel() -> PlatformChannel:
    return PlatformChannel(
        base_url="http://127.0.0.1:8300",
        secret=SECRET,
        models=("deepseek-v4-pro", "deepseek-flash"),
    )


def test_channel_attaches_a_read_only_subscription_profile() -> None:
    channel = _channel()
    config = channel.attach(ServerConfig(providers=[]))
    assert len(config.providers) == 1
    profile = config.providers[0]
    assert profile.name == PLATFORM_CONNECTION
    assert profile.display_name == "线簿订阅"
    assert profile.catalog_id == "line-book"
    assert profile.no_auth is True
    assert profile.api_key.get_secret_value() == ""
    assert profile.models_for() == ["deepseek-v4-pro", "deepseek-flash"]
    assert profile.auth_status == "ready"
    assert profile.base_url == "http://127.0.0.1:8300/v1"

    # 再附加一次不会产生重复连接。
    again = channel.attach(config)
    assert len(again.providers) == 1


def test_grant_is_scoped_to_the_user_and_secret() -> None:
    channel = _channel()
    grant = channel.grant(USER)
    verified = verify_grant(grant, SECRET)
    assert verified is not None and verified.user_id == USER
    assert verify_grant(grant, "other-secret") is None
    assert f"Bearer {grant}" not in channel.profile().api_key.get_secret_value()


def test_provider_factory_mints_a_fresh_grant_for_platform_selection(
    tmp_path: Path,
) -> None:
    channel = _channel()
    profile = channel.profile()
    config = ServerConfig(
        providers=[profile],
        active_provider=PLATFORM_CONNECTION,
        active_model="deepseek-flash",
        selection_mode="explicit",
    )
    book = ProviderBook(
        config,
        boot_name="",
        boot_model="",
        config_path=tmp_path / "models.yaml",
    )
    token = bind_user_id(USER)
    try:
        snapshot = configured_provider_factory(book, platform=channel)()
    finally:
        reset_user_id(token)
    assert snapshot.connection_id == PLATFORM_CONNECTION
    assert snapshot.model == "deepseek-flash"
    assert snapshot.limits is not None
    grant = snapshot.provider._config.api_key  # type: ignore[attr-defined]
    verified = verify_grant(grant, SECRET)
    assert verified is not None and verified.user_id == USER
    config_text = (
        (tmp_path / "models.yaml").read_text(encoding="utf-8", errors="ignore")
        if (tmp_path / "models.yaml").exists()
        else ""
    )
    assert grant not in config_text


def test_platform_selection_without_channel_is_rejected(tmp_path: Path) -> None:
    channel = _channel()
    config = ServerConfig(
        providers=[channel.profile()],
        active_provider=PLATFORM_CONNECTION,
        active_model="deepseek-flash",
        selection_mode="explicit",
    )
    book = ProviderBook(
        config,
        boot_name="",
        boot_model="",
        config_path=tmp_path / "models.yaml",
    )
    token = bind_user_id(USER)
    try:
        with pytest.raises(WorkspaceError) as exc:
            configured_provider_factory(book)()
    finally:
        reset_user_id(token)
    assert exc.value.code == "MODEL_NOT_CONFIGURED"
