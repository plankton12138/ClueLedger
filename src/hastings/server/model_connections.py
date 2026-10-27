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

from hastings.server.model_auth import ModelAuthService
from hastings.server.model_credential import (
    SUBSCRIPTION_REF_PREFIX,
    SubscriptionCredential,
    delete_subscription,
    load_subscription,
    mask_account,
    save_subscription,
)
from hastings.server.model_limits import ModelLimitOverride
from hastings.server.model_probe import DiscoveryResult, ProbeResult, discover_models
from hastings.server.model_probe import probe_response as run_response_probe
from hastings.server.model_probe import probe_tool as run_tool_probe
from hastings.server.model_thinking import ThinkingLevel, thinking_levels
from hastings.server.provider_catalog import ProviderApi, ProviderPreset, preset
from hastings.server.provider_config import (
    AuthMethod,
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
    #: 按精确模型名保存的容量；``None`` 表示沿用已保存的值，``{}`` 表示清空。
    #: 与模型列表分开保存：移除模型不删除它的容量，换回来还能继续用。
    model_limits: dict[str, ModelLimitOverride] | None = None
    api_key: str | None = None
    auth_method: AuthMethod = "api_key"
    #: 订阅来源标识（如 ``chatgpt``）；API Key 连接为空。
    auth_provider: str = ""
    #: 一次已完成的授权会话；保存成功后凭据归属该连接，会话随即作废。
    auth_session_id: str = ""


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
    #: 凭据是否已经就绪（API Key 或订阅凭据），界面据此判断能否设为当前。
    has_key: bool
    masked_key: str
    credential_storage: str
    auth_method: str = "api_key"
    auth_provider: str = ""
    #: 订阅账号的脱敏标识；API Key 连接为空。
    account: str = ""
    #: ``ready`` / ``expired`` / ``missing``：过期仍可续期，缺失则不可调用。
    auth_status: str = "missing"
    #: 作者填写的容量；未配置的模型按未知处理，界面据此给出设置入口。
    model_limits: dict[str, ModelLimitOverride] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConfigView:
    revision: str
    selection: ModelSelection
    connections: list[ConnectionView]


@dataclass(frozen=True, slots=True)
class StagedKey:
    """暂存的凭据引用：先写入并回读校验，配置提交成功后才成为唯一引用。"""

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
        auth: ModelAuthService | None = None,
    ) -> None:
        self.book = book
        self.config_path = config_path
        self.credentials = credentials
        self.auth = auth
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
        """新增连接；不自动改变当前选择。

        订阅连接在这里完成凭据归属：授权会话里的凭据写入系统凭据存储、配置文件只留引用，
        提交成功后才把这次会话标记为已用——保存失败时作者不必重新登录。
        """
        async with self._lock:
            self._require_revision(expected_revision)
            clean = self._validate(draft, creating=True)
            name = self._new_name()
            staged = self._stage(name, clean, current=None)
            profile = self._profile(name, clean, credential_ref=staged.ref)
            persisted = self._book_config()
            persisted = persisted.model_copy(update={"providers": [*persisted.providers, profile]})
            self._commit(persisted, staged=staged.ref)
            if clean.auth_method == "subscription" and self.auth is not None:
                # 归属只在这一步发生：配置已经落盘，这份授权不再属于「未保存的登录」。
                self.auth.consume(clean.auth_session_id, provider_id=clean.auth_provider)
            return self.view()

    async def update(
        self, name: str, draft: ConnectionDraft, *, expected_revision: str
    ) -> ConfigView:
        """编辑连接；提交里没有凭据表示沿用原来的认证方式与凭据。"""
        async with self._lock:
            self._require_revision(expected_revision)
            current = self._profile_or_404(name)
            clean = self._validate(draft, creating=False, current=current)
            staged = self._stage(name, clean, current=current)
            updated = self._profile(name, clean, credential_ref=staged.ref)
            persisted = self._book_config()
            providers = [updated if item.name == name else item for item in persisted.providers]
            persisted = persisted.model_copy(update={"providers": providers})
            replaced = current.credential_ref if staged.ref != current.credential_ref else ""
            self._commit(persisted, staged=staged.ref, replaced=replaced)
            if (
                clean.auth_session_id
                and clean.auth_method == "subscription"
                and self.auth is not None
            ):
                self.auth.consume(clean.auth_session_id, provider_id=clean.auth_provider)
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
                        "active_thinking_level": None,
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
        self,
        connection_id: str,
        model: str,
        *,
        expected_revision: str,
        thinking_level: ThinkingLevel | None = None,
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
            levels = thinking_levels(profile, chosen)
            if thinking_level is not None and thinking_level not in levels:
                raise WorkspaceError(
                    code="MODEL_THINKING_LEVEL_INVALID",
                    where="model.selection",
                    why=f"模型 {chosen!r} 不支持思考深度 {thinking_level!r}",
                    fix="选择默认，或使用当前模型提供的思考深度",
                    details={"model": chosen, "thinking_levels": list(levels)},
                )
            persisted = self._book_config().model_copy(
                update={
                    "active_provider": connection_id,
                    "active_model": chosen,
                    "active_thinking_level": thinking_level,
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
                update={
                    "active_provider": "",
                    "active_model": "",
                    "selection_mode": "none",
                    "active_thinking_level": None,
                }
            )
            self._commit(persisted)
            self.book.apply_selection(None, "")
            return self.view()

    # -- 发现与测试（不改配置） --------------------------------------------

    async def discover(
        self, draft: ConnectionDraft, *, connection_id: str | None = None
    ) -> DiscoveryResult:
        profile, resolved_id = await self._fresh_probe_profile(draft, connection_id)
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
        profile, _ = await self._fresh_probe_profile(draft, connection_id)
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
        staged: str = "",
        replaced: str = "",
    ) -> None:
        """先落盘，再发布内存快照，最后清理被替换的旧凭据。"""
        try:
            save_server_config(persisted, self.config_path)
        except OSError as exc:
            if staged:
                self._discard(staged)
            raise WorkspaceError(
                code="MODEL_CONFIG_WRITE_FAILED",
                where="model.config.write",
                why=f"配置写入失败：{exc}",
                fix="检查配置文件的目录是否可写，然后重试；旧连接与旧选择保持不变",
            ) from exc
        # 提交后发布内存快照：只把刚写下的内容读回内存，不再做别的写入。
        self.book.reload(load_server_config(self.config_path))
        if replaced and replaced != staged:
            # 旧引用已经没有任何连接指向它（换凭据或删连接）：可以清理；
            # 清理失败只影响存储占用，不影响这一轮能否调用。
            self._discard(replaced)

    def _stage(
        self, name: str, clean: ConnectionDraft, *, current: ProviderProfile | None
    ) -> StagedKey:
        """决定这次提交写哪条凭据引用：新凭据先写入并校验，未改动则沿用。"""
        if clean.auth_method == "subscription":
            if clean.auth_session_id:
                return StagedKey(ref=self._write_subscription(name, clean), secret="")
            if (
                current is not None
                and current.auth_method == "subscription"
                and current.credential_ref
            ):
                return StagedKey(ref=current.credential_ref, secret="")
            raise WorkspaceError(
                code="MODEL_AUTH_SESSION_REQUIRED",
                where="model.connections",
                why="切换到订阅登录需要先完成一次授权",
                fix="点击「登录」在浏览器里完成授权，再保存这个连接",
                details={"connection": name},
            )
        key = clean.api_key
        if key is not None and key.strip():
            return self._stage_key(name, key)
        if current is not None and current.auth_method == "api_key" and current.credential_ref:
            return StagedKey(ref=current.credential_ref, secret="")
        raise WorkspaceError(
            code="MODEL_CREDENTIAL_REQUIRED",
            where="model.connections",
            why="切回 API Key 需要重新填写 Key",
            fix="填写该服务签发的 API Key；订阅凭据不能当作 API Key 使用",
            details={"connection": name},
        )

    def _write_subscription(self, name: str, clean: ConnectionDraft) -> str:
        """把授权会话里的凭据写成一条独立的引用（先写后校验）。"""
        if self.auth is None:
            raise WorkspaceError(
                code="MODEL_AUTH_UNAVAILABLE",
                where="model.connections",
                why="当前进程没有可用的订阅授权服务",
                fix="重启应用后重试；若持续失败，检查是否在无凭据存储的环境里运行",
            )
        credential = self.auth.peek(clean.auth_session_id, provider_id=clean.auth_provider)
        ref = f"{SUBSCRIPTION_REF_PREFIX}{name}:{os.urandom(4).hex()}"
        try:
            save_subscription(self.credentials, ref, credential)
            if load_subscription(self.credentials, ref) != credential:
                raise RuntimeError("凭据存储回读校验不一致")
        except Exception as exc:
            self._discard(ref)
            code = getattr(exc, "code", type(exc).__name__)
            raise WorkspaceError(
                code="MODEL_CREDENTIAL_FAILED",
                where="model.credentials",
                why=f"订阅凭据没有保存成功：{code}",
                fix="解锁系统凭据管理器后重新登录；当前连接与旧凭据保持不变",
            ) from exc
        return ref

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
        """清掉一条不再被引用的凭据。

        订阅凭据是分片存的（系统凭据存储单条装不下），删的时候要连分片一起删；
        清理失败不影响已提交的配置，只影响存储占用。
        """
        with contextlib.suppress(Exception):
            if ref.startswith(SUBSCRIPTION_REF_PREFIX):
                delete_subscription(self.credentials, ref)
            else:
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
        if draft.auth_method not in ("api_key", "subscription"):
            raise WorkspaceError(
                code="MODEL_AUTH_METHOD_INVALID",
                where="model.connections",
                why=f"不支持的认证方式：{draft.auth_method!r}",
                fix="认证方式只能是「API Key」或「订阅登录」",
            )
        if draft.auth_method == "subscription":
            if not draft.auth_provider.strip():
                raise WorkspaceError(
                    code="MODEL_AUTH_PROVIDER_REQUIRED",
                    where="model.connections",
                    why="订阅登录没有指明订阅来源",
                    fix="选择一家支持的订阅来源（如 ChatGPT）后重新登录",
                )
            if creating and not draft.auth_session_id.strip():
                raise WorkspaceError(
                    code="MODEL_AUTH_SESSION_REQUIRED",
                    where="model.connections",
                    why="订阅连接必须先完成一次授权",
                    fix="点击「登录」在浏览器里完成授权，再保存这个连接",
                )
        elif creating and not (draft.api_key or "").strip():
            raise WorkspaceError(
                code="MODEL_CREDENTIAL_REQUIRED",
                where="model.connections",
                why="新增连接必须填写 API Key",
                fix="填写该服务签发的 API Key，或改用订阅登录；"
                "已经保存的连接可以留空表示沿用旧 Key",
            )
        if (
            draft.auth_method == "api_key"
            and draft.api_key is not None
            and not draft.api_key.strip()
            and not creating
        ):
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
            # 提交里没有容量表示沿用已保存的：旧界面保存连接不该把容量抹掉。
            model_limits=(
                dict(draft.model_limits)
                if draft.model_limits is not None
                else dict(current.model_limits) if current is not None else {}
            ),
            api_key=draft.api_key,
            auth_method=draft.auth_method,
            auth_provider=draft.auth_provider.strip(),
            auth_session_id=draft.auth_session_id.strip(),
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
            # 写盘只带引用：明文凭据只存在于凭据存储与运行时内存。
            credential_ref=credential_ref,
            default_model=clean.default_model,
            models=list(clean.models),
            model_limits=dict(clean.model_limits or {}),
            catalog_id=clean.catalog_id,
            api=clean.api,
            auth_method=clean.auth_method,
            auth_provider=clean.auth_provider,
        )

    def _view_of(self, profile: ProviderProfile) -> ConnectionView:
        key = profile.resolved_api_key()
        subscription = profile.subscription
        account = (
            mask_account(subscription.account_label, subscription.account_id)
            if subscription is not None
            else ""
        )
        return ConnectionView(
            name=profile.name,
            display_name=profile.title,
            catalog_id=profile.catalog_id,
            api=profile.api,
            base_url=profile.base_url,
            models=profile.models_for(),
            default_model=profile.default_model,
            has_key=profile.available,
            masked_key=mask_key(key) if profile.auth_method == "api_key" else "",
            credential_storage=self.storage,
            auth_method=profile.auth_method,
            auth_provider=profile.auth_provider,
            account=account,
            auth_status=profile.auth_status,
            model_limits=dict(profile.model_limits),
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

    async def _fresh_probe_profile(
        self, draft: ConnectionDraft, connection_id: str | None
    ) -> tuple[ProviderProfile, str | None]:
        """发现与测试前先确保订阅凭据可用：否则「列表能拿到、写作用不了」很难解释。"""
        profile, resolved = self._probe_profile(draft, connection_id)
        credential = profile.subscription
        if credential is None or not credential.expired():
            return profile, resolved
        if self.auth is None:
            raise WorkspaceError(
                code="MODEL_AUTH_REFRESH_UNSUPPORTED",
                where="model.probe",
                why="订阅凭据已过期，当前进程没有可用的续期能力",
                fix="在「模型设置」里重新登录这个连接",
            )
        try:
            refreshed = await self.auth.refresh(credential)
        except WorkspaceError:
            raise
        except Exception as exc:  # 续期失败要给可读原因，不要落到「未分类错误」
            raise WorkspaceError(
                code="MODEL_AUTH_REFRESH_FAILED",
                where="model.probe",
                why=f"订阅凭据续期失败：{exc}",
                fix="重新登录这个连接后重试",
            ) from exc
        if profile.name and profile.name != "draft":
            # 已保存的连接顺带回写，下一次启动不用再刷一次。
            self.book.update_subscription(profile.name, refreshed)
        return profile.model_copy(update={"subscription": refreshed}), resolved

    def _probe_profile(
        self, draft: ConnectionDraft, connection_id: str | None
    ) -> tuple[ProviderProfile, str | None]:
        """草稿也能测试：省略凭据时只允许读该连接自己的旧凭据。

        订阅连接有三种来源：刚完成的授权会话（还没保存）、已保存连接的凭据、
        以及显式填写的 API Key。三者都不会把凭据带出这一层。
        """
        key = (draft.api_key or "").strip()
        credential_ref = ""
        subscription: SubscriptionCredential | None = None
        auth_provider = draft.auth_provider.strip()
        if draft.auth_session_id and self.auth is not None:
            subscription = self.auth.peek(draft.auth_session_id, provider_id=auth_provider)
            auth_provider = subscription.auth_provider
        elif not key and connection_id:
            stored = self.book.profile(connection_id)
            if stored is None:
                raise WorkspaceError(
                    code="MODEL_CONNECTION_NOT_FOUND",
                    where="model.probe",
                    why=f"连接不存在：{connection_id!r}",
                    fix="刷新连接列表后重试",
                )
            credential_ref = stored.credential_ref
            if stored.auth_method == "subscription":
                subscription = stored.subscription
                auth_provider = stored.auth_provider
            else:
                key = stored.resolved_api_key()
        if subscription is None and not key:
            raise WorkspaceError(
                code="MODEL_CREDENTIAL_REQUIRED",
                where="model.probe",
                why="这次请求没有可用的凭据",
                fix="订阅连接先完成登录，或填写 API Key；对已保存的连接可以直接测试",
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
            auth_method="subscription" if subscription is not None else "api_key",
            auth_provider=auth_provider if subscription is not None else "",
            subscription=subscription,
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
