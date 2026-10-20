"""V03-1.3 个人 Key 与连接同源（计划 M02、执行指导 §5.3）。

覆盖：
- CredentialStore 内存实现的保存/读取/覆盖/删除契约；
- 旧明文连接升级顺序：新存储写入并验证 → 元数据切换 → 清理旧 Key 字段
  （磁盘 config.yaml 不再含明文），重跑幂等；
- 失败路径：存储/验证失败时旧连接保持可恢复并明确报告；
- 物化：credential_ref 档案在运行时视图拿到 Key（SecretStr 只进不出）；
- UI 连接：Key 入 OS 凭据存储、config.yaml 无明文、对外响应无 Key（脱敏）。
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from hastings.application.errors import EnvironmentFailure
from hastings.infrastructure.credentials import InMemoryCredentialStore
from hastings.server.provider_config import (
    ProviderBook,
    ProviderProfile,
    ServerConfig,
    load_server_config,
    migrate_plaintext_credentials,
    save_server_config,
)

SECRET = "sk-test-abcdef1234567890"


def test_in_memory_store_contract() -> None:
    store = InMemoryCredentialStore()
    assert store.load("missing") is None
    store.save("hastings:provider:deepseek", SECRET)
    assert store.load("hastings:provider:deepseek") == SECRET
    store.save("hastings:provider:deepseek", "rotated")  # 覆盖
    assert store.load("hastings:provider:deepseek") == "rotated"
    store.delete("hastings:provider:deepseek")
    assert store.load("hastings:provider:deepseek") is None
    store.delete("hastings:provider:deepseek")  # 删除不存在静默成功


def _config_with_plaintext(tmp_path: Path) -> Path:
    config = ServerConfig(
        providers=[
            ProviderProfile(
                name="deepseek",
                base_url="https://api.deepseek.com",
                api_key=SECRET,
                default_model="deepseek-chat",
            )
        ],
        active_provider="deepseek",
        active_model="deepseek-chat",
    )
    path = tmp_path / "config.yaml"
    save_server_config(config, path)
    return path


def test_migration_moves_key_and_clears_plaintext(tmp_path: Path) -> None:
    path = _config_with_plaintext(tmp_path)
    store = InMemoryCredentialStore()
    report = migrate_plaintext_credentials(load_server_config(path), store, path)
    assert report["migrated"] == ["deepseek"]
    assert report["failed"] == []
    # 磁盘元数据：credential_ref 有值、api_key 为空
    migrated = load_server_config(path).providers[0]
    assert migrated.credential_ref == "hastings:provider:deepseek"
    assert migrated.api_key.get_secret_value() == ""
    # 原始文件文本不含明文（脱敏终检）
    assert SECRET not in path.read_text(encoding="utf-8")
    # OS 存储里有真值
    assert store.load(migrated.credential_ref) == SECRET
    # 重跑幂等：不再迁移
    again = migrate_plaintext_credentials(load_server_config(path), store, path)
    assert again["migrated"] == []


def test_migration_failure_keeps_old_connection(tmp_path: Path) -> None:
    path = _config_with_plaintext(tmp_path)

    class BrokenStore(InMemoryCredentialStore):
        def save(self, ref: str, secret: str) -> None:
            raise EnvironmentFailure(
                code="CREDENTIAL_STORE_UNAVAILABLE",
                where="credentials.keyring",
                why="写入操作系统凭据存储失败",
                fix="解锁系统凭据管理器后重试；旧连接保持可用",
            )

    report = migrate_plaintext_credentials(load_server_config(path), BrokenStore(), path)
    assert report["migrated"] == []
    assert report["failed"] and "CREDENTIAL_STORE_UNAVAILABLE" in report["failed"][0]
    # 旧连接原样：明文还在、连接仍可用（不丢、不半迁移）
    profile = load_server_config(path).providers[0]
    assert profile.api_key.get_secret_value() == SECRET
    assert profile.credential_ref == ""
    assert profile.available


def test_book_materializes_credentials_from_store(tmp_path: Path) -> None:
    path = _config_with_plaintext(tmp_path)
    store = InMemoryCredentialStore()
    book = ProviderBook(
        load_server_config(path),
        boot_name="deepseek",
        boot_model="",
        config_path=path,
        credentials=store,
    )
    assert book.credential_migration["migrated"] == ["deepseek"]
    profile = book.profile("deepseek")
    assert profile is not None and profile.available  # 运行时视图拿到 Key
    assert profile.resolved_api_key() == SECRET
    assert book.selected()[0] is not None  # 保存/测试/任务绑定走同一连接


def test_ui_connect_stores_key_without_plaintext_on_disk(tmp_path: Path) -> None:
    """UI 连接：Key 入凭据存储；磁盘与 API 响应均无明文。"""
    path = _config_with_plaintext(tmp_path)
    store = InMemoryCredentialStore()
    book = ProviderBook(
        load_server_config(path),
        boot_name="deepseek",
        boot_model="",
        config_path=path,
        credentials=store,
    )
    client = TestClient(_app_with_book(book))
    response = client.post(
        "/api/providers/connect",
        json={
            "catalog_id": "moonshot",
            "api_key": "sk-moon-9999",
            "use_environment": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    # 响应不含完整 Key（只有尾 4 位提示）
    assert "sk-moon-9999" not in json.dumps(body)
    # 磁盘 config.yaml 无明文
    assert "sk-moon-9999" not in path.read_text(encoding="utf-8")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    moonshot = next(p for p in raw["providers"] if p["name"] == "moonshot")
    assert moonshot["credential_ref"] == "hastings:provider:moonshot"
    assert not moonshot.get("api_key")
    # 重启（新 book + 同一存储）：连接仍然可用（同源）
    reborn = ProviderBook(
        load_server_config(path),
        boot_name="deepseek",
        boot_model="",
        config_path=path,
        credentials=store,
    )
    assert reborn.profile("moonshot") is not None
    assert reborn.profile("moonshot").available
    _ = client


def _app_with_book(book: ProviderBook):
    from fastapi import FastAPI

    from hastings.server.model_access import create_model_access_router

    app = FastAPI()
    app.include_router(create_model_access_router(book))
    return app
