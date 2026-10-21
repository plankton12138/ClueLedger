"""连接管理与选择（V04-9B）。

设置界面不自己存一份模型配置：它编辑的是启动时真正使用的 ``config.yaml``。这个服务
是那份文件的唯一写入口，因此三件容易出错的事都在这里收口：

- **并发**：写操作串行执行，并在提交前核对磁盘内容版本。两个页面同时提交时，
  后到的那个拿到 ``MODEL_CONFIG_CONFLICT`` 并保留自己的表单，而不是悄悄覆盖别人。
- **凭据分步提交**：新 key 先写入系统凭据存储并回读校验，再写「只含引用」的配置，
  最后清理旧引用。任何一步失败都保留旧连接可用；清理失败只报告清理未完成，
  不会把已经成功的保存说成没保存。
- **空选择的语义**：删掉最后一个连接、或作者主动清空时，配置里写
  ``selection_mode: none``；重启后保持未选，不退回「首个可用连接」。

写盘只写凭据引用，不把已经物化到内存的 key 交给序列化器；对外视图只给
``has_key`` 与尾四位脱敏值。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from hastings.server.model_probe import DiscoveryResult, ProbeResult, discover_models
from hastings.server.model_probe import probe_response as run_response_probe
from hastings.server.model_probe import probe_tool as run_tool_probe
from hastings.server.provider_catalog import ProviderApi, ProviderPreset, preset
from hastings.server.provider_config import (
    ModelSelection,
    ProviderBook,
    ProviderProfile,
    ServerConfig,
    config_revision,
    load_server_config,
    mask_key,
    save_server_config,
)
from hastings.server.provider_runtime import MANAGED_APIS
from hastings.workspace.errors import WorkspaceError

__all__ = [
    "ConfigView",
    "ConnectionDraft",
    "ConnectionView",
    "ModelConnectionService",
    "StagedKey",
]

#: 接口层转换后的提交内容；``api_key=None`` 表示「保留原 key」。
@dataclass(frozen=True, slots=True)
class ConnectionDraft:
    display_name: str = ""
    catalog_id: str = ""
    base_url: str = ""
    api: ProviderApi = "openai-completions"
    models: list[str] = field(default_factory=list)
    default_model: str = ""
    api_key: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectionView:
    """一个连接的对外形状：没有 key，只有是否配置与尾四位。"""

    name: str
    display_name: str
    catalog_id: str
    api: str
    base_url: str
    models: list[str]
    default_model: str
    has_key: bool
    masked_key: str
    credential_storage: str


@dataclass(frozen=True, slots=True)
class ConfigView:
    revision: str
    selection: ModelSelection
    connections: list[ConnectionView]


@dataclass(frozen=True, slots=True)
class StagedKey:
    ref: str
    secret: str


def credential_storage_kind(store: Any) -> str:
    """凭据保存期限：系统存储重启后仍在，内存降级只在本次运行有效。"""
    name = type(store).__name__
    return "session" if name == "InMemoryCredentialStore" else "system"


class ModelConnectionService:
    """连接目录的读写、发现、测试与选择。"""

    def __init__(
        self,
        *,
        book: ProviderBook,
        config_path: Path,
        credentials: Any,
        probe_factory: Any | None = None,
        transport: Any | None = None,
    ) -> None:
        self.book = book
        self.config_path = config_path
        self.credentials = credentials
        self._probe_factory = probe_factory
        self._transport = transport
        self._lock = asyncio.Lock()

    # -- 读取 -------------------------------------------------------------

    @property
    def revision(self) -> str:
        return config_revision(self.config_path)

    @property
    def storage(self) -> str:
        return credential_storage_kind(self.credentials)

    def view(self) -> ConfigView:
        """当前配置的脱敏视图：连接列表 + 有效选择 + 版本。"""
        config = self.book.config
        return ConfigView(
            revision=self.revision,
            selection=self.book.selection(),
            connections=[self._view_of(profile) for profile in config.providers],
        )

    def presets(self) -> tuple[ProviderPreset, ...]:
        from hastings.server.provider_catalog import PRESETS

        return PRESETS

    # -- 写入 -------------------------------------------------------------

    async def create(self, draft: ConnectionDraft, *, expected_revision: str) -> ConfigView:
        """新增连接；不自动改变当前选择。"""
        async with self._lock:
            self._require_revision(expected_revision)
            clean = self._validate(draft, creating=True)
            name = self._new_name()
            assert draft.api_key  # _validate(creating=True) 已保证
            staged = self._stage_key(name, draft.api_key)
            profile = self._profile(name, clean, credential_ref=staged.ref)
            persisted = self._book_config()
            persisted = persisted.model_copy(update={"providers": [*persisted.providers, profile]})
            self._commit(persisted, staged=staged)
            return self.view()

    async def update(
        self, name: str, draft: ConnectionDraft, *, expected_revision: str
    ) -> ConfigView:
        """编辑连接；提交里没有 api_key 表示保留旧 key。"""
        async with self._lock:
            self._require_revision(expected_revision)
            current = self._profile_or_404(name)
            clean = self._validate(draft, creating=False, current=current)
            staged: StagedKey | None = None
            if draft.api_key is not None:
                staged = self._stage_key(name, draft.api_key)
            updated = self._profile(
                name,
                clean,
                credential_ref=staged.ref if staged else current.credential_ref,
            )
            persisted = self._book_config()
            providers = [updated if item.name == name else item for item in persisted.providers]
            persisted = persisted.model_copy(update={"providers": providers})
            self._commit(persisted, staged=staged, replaced=current.credential_ref)
            return self.view()

    async def delete(self, name: str, *, expected_revision: str) -> ConfigView:
        """删除连接；若它是当前选择，则进入未选择状态（不自动换下一个）。"""
        async with self._lock:
            self._require_revision(expected_revision)
            current = self._profile_or_404(name)
            persisted = self._book_config()
            remaining = [item for item in persisted.providers if item.name != name]
            clears = persisted.active_provider == name
            if clears:
                persisted = persisted.model_copy(
                    update={
                        "providers": remaining,
                        "active_provider": "",
                        "active_model": "",
                        "selection_mode": "none",
                    }
                )
            else:
                persisted = persisted.model_copy(update={"providers": remaining})
            self._commit(persisted, replaced=current.credential_ref)
            if clears:
                self.book.apply_selection(None, "")
            return self.view()

    async def select(
        self, connection_id: str, model: str, *, expected_revision: str
    ) -> ConfigView:
        """显式选择连接与模型；``model`` 留空表示用该连接的默认模型。"""
        async with self._lock:
            self._require_revision(expected_revision)
            profile = self._profile_or_404(connection_id)
            chosen = model or profile.default_model
            allowed = profile.models_for()
            if chosen not in allowed:
                raise WorkspaceError(
                    code="MODEL_MODEL_INVALID",
                    where="model.selection",
                    why=f"模型 {chosen!r} 不在这个连接的可选列表里",
                    fix="先在连接编辑里把该模型加入可选列表，或选择列表中的模型",
                    details={"connection": connection_id, "models": allowed},
                )
            persisted = self._book_config().model_copy(
                update={
                    "active_provider": connection_id,
                    "active_model": chosen,
                    "selection_mode": "explicit",
                }
            )
            self._commit(persisted)
            self.book.apply_selection(connection_id, chosen)
            return self.view()

    async def clear_selection(self, *, expected_revision: str) -> ConfigView:
        """清空当前选择；重启后保持未选。"""
        async with self._lock:
            self._require_revision(expected_revision)
            persisted = self._book_config().model_copy(
                update={"active_provider": "", "active_model": "", "selection_mode": "none"}
            )
            self._commit(persisted)
            self.book.apply_selection(None, "")
            return self.view()

    # -- 发现与测试（不改配置） --------------------------------------------

    async def discover(
        self, draft: ConnectionDraft, *, connection_id: str | None = None
    ) -> DiscoveryResult:
        profile, resolved_id = self._probe_profile(draft, connection_id)
        return await discover_models(
            profile,
            discovery=self._discovery_for(draft.catalog_id, draft.api),
            connection_id=resolved_id,
            transport=self._transport,
        )

    async def probe(
        self,
        draft: ConnectionDraft,
        *,
        kind: str,
        model: str,
        connection_id: str | None = None,
    ) -> ProbeResult:
        profile, _ = self._probe_profile(draft, connection_id)
        if kind == "tool":
            return await run_tool_probe(profile, model, factory=self._probe_factory)
        if kind == "response":
            return await run_response_probe(profile, model, factory=self._probe_factory)
        raise WorkspaceError(
            code="MODEL_PROBE_KIND_INVALID",
            where="model.probe",
            why=f"未知的测试类型：{kind!r}",
            fix="测试类型只能是 response（响应）或 tool（工具调用）",
        )

    # -- 内部 -------------------------------------------------------------

    def _book_config(self) -> ServerConfig:
        """以磁盘为准重新读取：避免把内存里物化过的 key 写回文件。"""
        return load_server_config(self.config_path)

    def _require_revision(self, expected: str) -> None:
        current = self.revision
        if expected and expected == current:
            return
        raise WorkspaceError(
            code="MODEL_CONFIG_CONFLICT",
            where="model.config",
            why="配置文件在你打开这一页之后被改过（可能来自另一个页面或手工编辑）",
            fix="重新读取配置后再提交；当前表单内容不会被覆盖",
            details={"expected": expected, "actual": current},
        )

    def _commit(
        self,
        persisted: ServerConfig,
        *,
        staged: StagedKey | None = None,
        replaced: str = "",
    ) -> None:
        """先落盘，再发布内存快照，最后清理被替换的旧凭据。"""
        try:
            save_server_config(persisted, self.config_path)
        except OSError as exc:
            if staged is not None:
                self._discard(staged.ref)
            raise WorkspaceError(
                code="MODEL_CONFIG_WRITE_FAILED",
                where="model.config.write",
                why=f"配置写入失败：{exc}",
                fix="检查配置文件的目录是否可写，然后重试；旧连接与旧选择保持不变",
            ) from exc
        # 提交后发布内存快照：只把刚写下的内容读回内存，不再做别的写入。
        self.book.reload(load_server_config(self.config_path))
        if staged is not None and replaced and replaced != staged.ref:
            # 新 key 已经生效，旧引用可以清理；清理失败只影响存储占用。
            self._discard(replaced)

    def _stage_key(self, name: str, key: str) -> StagedKey:
        """新 key 先写到新引用并回读校验，避免覆盖仍在使用的旧引用。"""
        ref = f"hastings:provider:{name}:{os.urandom(4).hex()}"
        try:
            self.credentials.save(ref, key)
            if self.credentials.load(ref) != key:
                raise RuntimeError("凭据存储回读校验不一致")
        except Exception as exc:
            self._discard(ref)
            code = getattr(exc, "code", type(exc).__name__)
            raise WorkspaceError(
                code="MODEL_CREDENTIAL_FAILED",
                where="model.credentials",
                why=f"API Key 没有保存成功：{code}",
                fix="解锁系统凭据管理器后重试；当前连接与旧 Key 保持不变",
            ) from exc
        return StagedKey(ref=ref, secret=key)

    def _discard(self, ref: str) -> None:
        # 清理失败不影响已提交的配置：只占存储空间，不影响这一轮能否调用。
        with contextlib.suppress(Exception):
            self.credentials.delete(ref)

    def _profile_or_404(self, name: str) -> ProviderProfile:
        profile = self.book.profile(name)
        if profile is None:
            raise WorkspaceError(
                code="MODEL_CONNECTION_NOT_FOUND",
                where="model.connections",
                why=f"连接不存在：{name!r}",
                fix="刷新连接列表后重试",
                details={"connection": name},
            )
        return profile

    def _new_name(self) -> str:
        existing = {profile.name for profile in self.book.config.providers}
        for _ in range(8):
            candidate = f"conn_{os.urandom(4).hex()}"
            if candidate not in existing:
                return candidate
        raise WorkspaceError(
            code="MODEL_CONNECTION_INVALID",
            where="model.connections",
            why="无法生成唯一的连接标识",
            fix="重试；若持续失败，检查配置里是否有大量同名连接",
        )

    def _validate(
        self, draft: ConnectionDraft, *, creating: bool, current: ProviderProfile | None = None
    ) -> ConnectionDraft:
        base_url = draft.base_url.strip()
        if not base_url.startswith(("http://", "https://")):
            raise WorkspaceError(
                code="MODEL_CONNECTION_INVALID",
                where="model.connections",
                why="连接地址必须以 http:// 或 https:// 开头",
                fix="从供应商预设里选择，或填写完整的服务地址",
            )
        if draft.api not in MANAGED_APIS:
            raise WorkspaceError(
                code="MODEL_CONNECTION_INVALID",
                where="model.connections",
                why=f"不支持的协议：{draft.api!r}",
                fix="协议只能从已实现的四类中选择",
            )
        if creating and not (draft.api_key or "").strip():
            raise WorkspaceError(
                code="MODEL_CREDENTIAL_REQUIRED",
                where="model.connections",
                why="新增连接必须填写 API Key",
                fix="填写该服务签发的 API Key；已经保存的连接可以留空表示沿用旧 Key",
            )
        if draft.api_key is not None and not draft.api_key.strip() and not creating:
            # 显式空串不当作「清空 key」：删除密钥只能通过删除连接完成。
            raise WorkspaceError(
                code="MODEL_CREDENTIAL_REQUIRED",
                where="model.connections",
                why="API Key 不能是空字符串",
                fix="留空表示沿用旧 Key；要删除密钥请删除整个连接",
            )
        models = _dedupe(draft.models)
        if not models:
            raise WorkspaceError(
                code="MODEL_MODEL_REQUIRED",
                where="model.connections",
                why="这个连接还没有可选模型",
                fix="点击「获取模型列表」勾选模型，或手动添加模型 ID",
            )
        if current is not None:
            # 先挡住「移除当前使用的模型」：否则默认模型校验会先报一个更含糊的错。
            self._reject_model_removal(current, models)
        default_model = draft.default_model.strip() or models[0]
        if default_model not in models:
            raise WorkspaceError(
                code="MODEL_MODEL_INVALID",
                where="model.connections",
                why=f"默认模型 {default_model!r} 不在可选列表里",
                fix="先把它加入可选模型，或改选列表中的模型",
            )
        return ConnectionDraft(
            display_name=draft.display_name.strip(),
            catalog_id=draft.catalog_id.strip(),
            base_url=base_url.rstrip("/"),
            api=draft.api,
            models=models,
            default_model=default_model,
            api_key=draft.api_key,
        )

    def _reject_model_removal(self, current: ProviderProfile, models: list[str]) -> None:
        """从可选列表移除当前选中的模型时拒绝保存：否则下一轮会悄悄换模型。"""
        selection = self.book.selection()
        if selection.connection_id != current.name or not selection.model:
            return
        if selection.model in models:
            return
        raise WorkspaceError(
            code="MODEL_MODEL_IN_USE",
            where="model.connections",
            why=f"当前正在使用模型 {selection.model!r}，不能把它从可选列表移除",
            fix="先把当前模型切换到列表中的另一个模型，再保存这次修改",
            details={"connection": current.name, "model": selection.model},
        )

    def _profile(
        self, name: str, clean: ConnectionDraft, *, credential_ref: str
    ) -> ProviderProfile:
        title = clean.display_name
        if not title:
            known = preset(clean.catalog_id)
            title = known.display_name if known else name
        return ProviderProfile(
            name=name,
            display_name=title,
            base_url=clean.base_url,
            # 写盘只带引用：明文 key 只存在于凭据存储与运行时内存。
            credential_ref=credential_ref,
            default_model=clean.default_model,
            models=list(clean.models),
            catalog_id=clean.catalog_id,
            api=clean.api,
        )

    def _view_of(self, profile: ProviderProfile) -> ConnectionView:
        key = profile.resolved_api_key()
        return ConnectionView(
            name=profile.name,
            display_name=profile.title,
            catalog_id=profile.catalog_id,
            api=profile.api,
            base_url=profile.base_url,
            models=profile.models_for(),
            default_model=profile.default_model,
            has_key=profile.available,
            masked_key=mask_key(key),
            credential_storage=self.storage,
        )

    def _discovery_for(self, catalog_id: str, api: str) -> str | None:
        """预设给出的策略优先；自定义服务按作者选的协议决定。"""
        known = preset(catalog_id)
        if known is not None and not known.custom:
            return known.discovery
        if api.startswith("openai-"):
            return "openai-models"
        if api == "anthropic-messages":
            return "anthropic-models"
        return "google-models"

    def _probe_profile(
        self, draft: ConnectionDraft, connection_id: str | None
    ) -> tuple[ProviderProfile, str | None]:
        """草稿也能测试：省略 key 时只允许读该连接自己的旧凭据。"""
        key = (draft.api_key or "").strip()
        credential_ref = ""
        if not key and connection_id:
            stored = self.book.profile(connection_id)
            if stored is None:
                raise WorkspaceError(
                    code="MODEL_CONNECTION_NOT_FOUND",
                    where="model.probe",
                    why=f"连接不存在：{connection_id!r}",
                    fix="刷新连接列表后重试",
                )
            credential_ref = stored.credential_ref
            key = stored.resolved_api_key()
        if not key:
            raise WorkspaceError(
                code="MODEL_CREDENTIAL_REQUIRED",
                where="model.probe",
                why="这次请求没有可用的 API Key",
                fix="填写 API Key，或对已保存的连接直接测试",
            )
        profile = ProviderProfile(
            name=connection_id or "draft",
            display_name=draft.display_name or connection_id or "未保存的连接",
            base_url=draft.base_url.strip().rstrip("/"),
            api_key=SecretStr(key),
            credential_ref=credential_ref,
            default_model=draft.default_model,
            models=list(draft.models),
            catalog_id=draft.catalog_id,
            api=draft.api,
        )
        return profile, connection_id


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique
