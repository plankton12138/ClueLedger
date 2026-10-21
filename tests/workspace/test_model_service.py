"""连接管理的写入路径（V04-9B）。

契约：
- 写盘只有凭据引用，文件里不出现明文 key；对外视图只有是否配置与尾四位。
- 任何一步失败都保留旧连接：新 key 写入/校验失败、配置写失败都回到原状态。
- 写操作要求配置版本；过期版本返回冲突且不落盘。
- 删除当前连接进入「未选择」，不自动挑下一个；删掉最后一个连接后配置是合法空列表。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hastings.infrastructure.credentials import InMemoryCredentialStore
from hastings.server.model_connections import (
    ConnectionDraft,
    ModelConnectionService,
    credential_storage_kind,
)
from hastings.server.provider_config import ProviderBook, load_server_config
from hastings.workspace.errors import WorkspaceError

LEGACY = """providers:
- name: deepseek
  base_url: https://api.deepseek.com/v1
  api_key: sk-legacy-key
  default_model: deepseek-chat
"""


def _run(coro: object) -> object:
    assert asyncio.iscoroutine(coro)
    return asyncio.run(coro)


def _service(
    tmp_path: Path, text: str = ""
) -> tuple[ModelConnectionService, ProviderBook, InMemoryCredentialStore, Path]:
    path = tmp_path / "config.yaml"
    if text:
        path.write_text(text, encoding="utf-8")
    store = InMemoryCredentialStore()
    config = load_server_config(path)
    book = ProviderBook(config, boot_name="", boot_model="", config_path=path, credentials=store)
    service = ModelConnectionService(book=book, config_path=path, credentials=store)
    return service, book, store, path


def _draft(**overrides: object) -> ConnectionDraft:
    fields: dict[str, object] = {
        "display_name": "我的连接",
        "catalog_id": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "api": "openai-completions",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
        "api_key": "sk-new-key-1234",
    }
    fields.update(overrides)
    return ConnectionDraft(**fields)  # type: ignore[arg-type]


def _create(service: ModelConnectionService, draft: ConnectionDraft) -> str:
    _run(service.create(draft, expected_revision=service.revision))
    return service.view().connections[-1].name


# -- 新增 -------------------------------------------------------------------


def test_create_writes_reference_only_and_masks_key(tmp_path: Path) -> None:
    service, _book, store, path = _service(tmp_path)

    name = _create(service, _draft())

    text = path.read_text(encoding="utf-8")
    assert "sk-new-key-1234" not in text
    assert "credential_ref" in text
    view = service.view()
    connection = view.connections[0]
    assert connection.name == name
    assert connection.display_name == "我的连接"
    assert connection.has_key is True
    assert connection.masked_key == "…1234"
    assert connection.models == ["deepseek-chat", "deepseek-reasoner"]
    # key 真的能取回来（用于构造实例），但接口视图里没有它。
    profile = _book.profile(name)
    assert profile is not None
    assert profile.resolved_api_key() == "sk-new-key-1234"
    assert "sk-new-key-1234" not in repr(view)


def test_create_does_not_change_current_selection(tmp_path: Path) -> None:
    service, _book, _store, _path = _service(tmp_path)

    _create(service, _draft())

    # 保存不写入 active_provider：新连接只是「可用」，没有被标成当前选择。
    config = load_server_config(_path)
    assert (config.active_provider, config.selection_mode) == ("", "auto")
    assert service.view().selection.source != "saved"


def test_same_preset_supports_multiple_connections(tmp_path: Path) -> None:
    service, _book, _store, _path = _service(tmp_path)

    first = _create(service, _draft(display_name="账号一"))
    second = _create(service, _draft(display_name="账号二"))

    assert first != second
    assert [item.display_name for item in service.view().connections] == ["账号一", "账号二"]


@pytest.mark.parametrize(
    "draft",
    [
        _draft(api_key=""),
        _draft(base_url="api.deepseek.com"),
        _draft(models=[]),
        _draft(default_model="not-in-list"),
        _draft(api="not-an-api"),
    ],
)
def test_create_rejects_incomplete_connection(tmp_path: Path, draft: ConnectionDraft) -> None:
    service, _book, _store, path = _service(tmp_path)

    with pytest.raises(WorkspaceError):
        _run(service.create(draft, expected_revision=service.revision))

    assert not path.exists()


def test_create_dedupes_models_and_defaults_to_first(tmp_path: Path) -> None:
    service, _book, _store, _path = _service(tmp_path)

    _create(service, _draft(models=["a", "a", " b "], default_model=""))

    assert service.view().connections[0].models == ["a", "b"]
    assert service.view().connections[0].default_model == "a"


# -- 编辑 -------------------------------------------------------------------


def test_update_without_key_keeps_old_credential(tmp_path: Path) -> None:
    service, book, _store, _path = _service(tmp_path)
    name = _create(service, _draft())

    _run(
        service.update(
            name,
            _draft(api_key=None, display_name="改了名字"),
            expected_revision=service.revision,
        )
    )

    profile = book.profile(name)
    assert profile is not None
    assert profile.resolved_api_key() == "sk-new-key-1234"
    assert profile.title == "改了名字"


def test_update_with_new_key_replaces_and_discards_old_reference(tmp_path: Path) -> None:
    service, book, store, _path = _service(tmp_path)
    name = _create(service, _draft())
    before = book.profile(name)
    assert before is not None and before.credential_ref

    _run(
        service.update(
            name, _draft(api_key="sk-other-key-9876"), expected_revision=service.revision
        )
    )

    after = book.profile(name)
    assert after is not None
    assert after.resolved_api_key() == "sk-other-key-9876"
    assert after.credential_ref != before.credential_ref
    assert store.load(before.credential_ref) is None


def test_update_rejects_removing_selected_model(tmp_path: Path) -> None:
    service, _book, _store, _path = _service(tmp_path)
    name = _create(service, _draft())
    _run(service.select(name, "deepseek-chat", expected_revision=service.revision))

    with pytest.raises(WorkspaceError) as raised:
        _run(
            service.update(
                name, _draft(models=["deepseek-reasoner"]), expected_revision=service.revision
            )
        )

    assert raised.value.code == "MODEL_MODEL_IN_USE"
    assert service.view().connections[0].models == ["deepseek-chat", "deepseek-reasoner"]


def test_update_unknown_connection_is_not_found(tmp_path: Path) -> None:
    service, _book, _store, _path = _service(tmp_path)

    with pytest.raises(WorkspaceError) as raised:
        _run(service.update("nope", _draft(), expected_revision=service.revision))

    assert raised.value.code == "MODEL_CONNECTION_NOT_FOUND"


# -- 选择 -------------------------------------------------------------------


def test_select_persists_explicit_choice(tmp_path: Path) -> None:
    service, _book, _store, path = _service(tmp_path)
    name = _create(service, _draft())

    _run(service.select(name, "deepseek-reasoner", expected_revision=service.revision))

    config = load_server_config(path)
    assert (config.active_provider, config.active_model) == (name, "deepseek-reasoner")
    assert config.selection_mode == "explicit"
    selection = service.view().selection
    assert (selection.connection_id, selection.model, selection.source) == (
        name,
        "deepseek-reasoner",
        "saved",
    )


def test_select_rejects_model_outside_connection(tmp_path: Path) -> None:
    service, _book, _store, _path = _service(tmp_path)
    name = _create(service, _draft())

    with pytest.raises(WorkspaceError) as raised:
        _run(service.select(name, "gpt-4", expected_revision=service.revision))

    assert raised.value.code == "MODEL_MODEL_INVALID"


def test_clear_selection_persists_none(tmp_path: Path) -> None:
    service, _book, _store, path = _service(tmp_path)
    name = _create(service, _draft())
    _run(service.select(name, "deepseek-chat", expected_revision=service.revision))

    _run(service.clear_selection(expected_revision=service.revision))

    config = load_server_config(path)
    assert config.selection_mode == "none"
    assert config.active_provider == ""
    assert service.view().selection.source == "none"


# -- 删除 -------------------------------------------------------------------


def test_delete_current_connection_clears_selection_without_fallback(tmp_path: Path) -> None:
    service, _book, _store, path = _service(tmp_path)
    first = _create(service, _draft(display_name="一"))
    second = _create(service, _draft(display_name="二"))
    _run(service.select(first, "deepseek-chat", expected_revision=service.revision))

    _run(service.delete(first, expected_revision=service.revision))

    config = load_server_config(path)
    assert [item.name for item in config.providers] == [second]
    assert config.selection_mode == "none"
    assert service.view().selection.connection_id is None


def test_delete_last_connection_leaves_valid_empty_config(tmp_path: Path) -> None:
    service, _book, _store, path = _service(tmp_path)
    name = _create(service, _draft())

    _run(service.delete(name, expected_revision=service.revision))

    config = load_server_config(path)
    assert config.providers == []
    assert config.active_provider == ""
    # 删掉最后一个连接后不再有选择：重启读取也不会冒出旧连接。
    assert service.view().selection.connection_id is None


# -- 并发与失败 -------------------------------------------------------------


def test_stale_revision_is_rejected_without_writing(tmp_path: Path) -> None:
    service, _book, _store, path = _service(tmp_path)
    stale = service.revision
    _create(service, _draft())
    before = path.read_text(encoding="utf-8")

    with pytest.raises(WorkspaceError) as raised:
        _run(service.create(_draft(display_name="另一个"), expected_revision=stale))

    assert raised.value.code == "MODEL_CONFIG_CONFLICT"
    assert path.read_text(encoding="utf-8") == before


def test_credential_failure_keeps_old_connection(tmp_path: Path) -> None:
    service, book, store, path = _service(tmp_path)
    name = _create(service, _draft())
    before = path.read_text(encoding="utf-8")

    class _BrokenStore(InMemoryCredentialStore):
        def save(self, ref: str, secret: str) -> None:
            raise RuntimeError("凭据存储拒绝写入")

    service.credentials = _BrokenStore()
    with pytest.raises(WorkspaceError) as raised:
        _run(
            service.update(
                name, _draft(api_key="sk-broken-key"), expected_revision=service.revision
            )
        )

    assert raised.value.code == "MODEL_CREDENTIAL_FAILED"
    assert path.read_text(encoding="utf-8") == before
    profile = book.profile(name)
    assert profile is not None
    assert profile.resolved_api_key() == "sk-new-key-1234"
    assert store.load(profile.credential_ref) == "sk-new-key-1234"


def test_config_write_failure_keeps_old_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, book, _store, path = _service(tmp_path)
    name = _create(service, _draft())
    before = path.read_text(encoding="utf-8")

    def _refuse(config: object, target: Path) -> Path:
        raise OSError("磁盘只读")

    monkeypatch.setattr("hastings.server.model_connections.save_server_config", _refuse)

    with pytest.raises(WorkspaceError) as raised:
        _run(service.delete(name, expected_revision=service.revision))

    assert raised.value.code == "MODEL_CONFIG_WRITE_FAILED"
    assert book.profile(name) is not None
    assert path.read_text(encoding="utf-8") == before
    assert load_server_config(path).providers[0].name == name


def test_storage_kind_reports_session_for_memory_store() -> None:
    assert credential_storage_kind(InMemoryCredentialStore()) == "session"

    class _KeyringLike:
        def save(self, ref: str, secret: str) -> None: ...

    assert credential_storage_kind(_KeyringLike()) == "system"


def test_legacy_plaintext_connection_is_migrated_on_load(tmp_path: Path) -> None:
    service, book, store, path = _service(tmp_path, LEGACY)

    connection = service.view().connections[0]

    assert connection.has_key is True
    assert "sk-legacy-key" not in path.read_text(encoding="utf-8")
    profile = book.profile("deepseek")
    assert profile is not None
    assert store.load(profile.credential_ref) == "sk-legacy-key"
