"""``python -m hastings.server`` Web 服务入口（0.4 默认启动）。

默认启动文件工作区应用：作品是本地 Markdown 目录，接口前缀 ``/api/file-workspaces``。
``--workspace <目录>`` 在启动时预先打开一个作品；不带该参数时服务照常启动，由界面或
接口再选择目录。应用状态（最近目录、后续的会话与事件）默认放在 ``~/.hastings``，
可用 ``--state-dir`` 指定别处。

0.3 的旧工作台装配已从这个入口退出：不再打开 ``data/v2.db``、不再挂 ``/api/v2`` 与
旧业务路由，也没有任何参数能回到旧应用。只保留模型来源这一条复用：读取 ``config.yaml``
解析当前档案与模型，供写作 Agent 调用。模型来源不从进程环境读取——作者在产品里
配一次端点与 key，之后由系统凭据存储保管。

退出码：目录不存在、前端目录不存在等环境故障 → 1；参数写错 → 2。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from hastings.accounts.config import AccountsConfig, SmtpSettings
from hastings.accounts.runtime import AuthRuntime, build_auth_runtime
from hastings.accounts.workspaces import UserWorkspaces
from hastings.infrastructure.credentials import (
    CredentialStoreError,
    build_credential_store,
)
from hastings.library.modules import ModuleLibrary
from hastings.library.storage import DEFAULT_LIBRARY_DIR
from hastings.server.file_app import (
    configured_limits_factory,
    configured_provider_factory,
    create_workspace_app,
)
from hastings.server.model_auth import ModelAuthService
from hastings.server.model_auth_chatgpt import ChatGPTCodexAuthProvider, ChatGPTHostIdentity
from hastings.server.model_connections import ModelConnectionService
from hastings.server.platform_channel import DEFAULT_PLATFORM_MODELS, PlatformChannel
from hastings.server.production import production_config_errors
from hastings.server.provider_config import (
    DEFAULT_CONFIG_PATH,
    ProviderBook,
    ServerConfig,
    load_server_config,
)
from hastings.server.user_services import UserResourcesRegistry, build_user_resources
from hastings.workspace.state import RecentWorkspaces

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _env_path(name: str) -> Path | None:
    value = _env(name)
    return Path(value).expanduser() if value is not None else None


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value is not None else default


def _env_flag(name: str) -> bool:
    return _env(name) in {"1", "true", "yes", "on"}


def _env_secret(name: str) -> str:
    direct = _env(name)
    path_text = _env(f"{name}_FILE")
    if direct and path_text:
        raise ValueError(f"{name} 与 {name}_FILE 只能设置一个")
    if path_text:
        try:
            return Path(path_text).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"无法读取 {name}_FILE：{path_text}") from exc
    return direct or ""


def _allowed_origins(values: Sequence[str]) -> tuple[str, ...]:
    merged = list(values)
    raw = _env("CLUELEDGER_ALLOWED_ORIGINS")
    if raw is not None:
        merged.extend(part.strip() for part in raw.split(",") if part.strip())
    return tuple(merged)


def find_static_dir(root: Path) -> Path | None:
    """前端 build 产物目录（<root>/frontend/dist）；不存在则不托管（纯 API 模式）。"""
    candidate = root / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


def default_static_dir() -> Path | None:
    """以仓库根为基准的 dist 探测（src/hastings/server/__main__.py → 上溯 3 级）。"""
    return find_static_dir(Path(__file__).resolve().parents[3])


def resolve_static_dir(static_dir: Path | None) -> Path | None:
    """前端目录解析：显式 ``--static-dir`` 优先，缺省走仓库探测。"""
    if static_dir is not None:
        return static_dir
    return default_static_dir()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hastings-server",
        description="Hastings：悬疑小说写作工作台（Markdown 工作目录）",
    )
    parser.add_argument(
        "--mode",
        choices=("local", "server"),
        default=_env("CLUELEDGER_MODE") or "local",
        help="运行模式：local 沿用本地信任边界；server 要求邮箱登录（默认 local）",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        default=_env("CLUELEDGER_ENV") == "production",
        help="生产校验：要求 HTTPS、显式数据目录、SMTP、订阅代理与凭据主密钥",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=_env_path("CLUELEDGER_DATABASE"),
        help="账号数据库路径；server 模式必填，不自动回退本地状态目录",
    )
    parser.add_argument(
        "--credential-key-file",
        type=Path,
        default=_env_path("CLUELEDGER_CREDENTIAL_KEY_FILE"),
        help="服务器凭据加密主密钥文件；生产模式必填，与数据库分开保管",
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        default=_env_path("CLUELEDGER_CREDENTIALS_FILE"),
        help="加密凭据文件；缺省为数据库同目录的 .credentials 文件",
    )
    parser.add_argument(
        "--public-base-url",
        default=_env("CLUELEDGER_PUBLIC_BASE_URL"),
        help="公网访问地址，用于邮件链接与 Cookie Secure 判断",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="额外允许的写请求来源（可重复）；公共地址始终允许",
    )
    parser.add_argument(
        "--mail-outbox",
        type=Path,
        default=_env_path("CLUELEDGER_MAIL_OUTBOX"),
        help="未配置 SMTP 时的文件投递目录；缺省 <state-dir>/mail-outbox",
    )
    parser.add_argument(
        "--workspaces-root",
        type=Path,
        default=_env_path("CLUELEDGER_WORKSPACES_ROOT"),
        help="server 模式的服务器作品存储根；缺省放在账号数据库旁的 workspaces 目录",
    )
    parser.add_argument(
        "--libraries-root",
        type=Path,
        default=_env_path("CLUELEDGER_LIBRARIES_ROOT"),
        help="server 模式的私有模块库根；缺省放在账号数据库旁的 libraries 目录",
    )
    parser.add_argument(
        "--billing-url",
        default=_env("CLUELEDGER_BILLING_URL"),
        help="订阅模型代理内部地址（如 http://127.0.0.1:8300）；设置后启用线簿订阅通道",
    )
    parser.add_argument(
        "--platform-models",
        default=_env("CLUELEDGER_PLATFORM_MODELS"),
        help="订阅模型名，逗号分隔；缺省使用内置 pro/flash 列表",
    )
    parser.add_argument(
        "--smtp-host", default=_env("CLUELEDGER_SMTP_HOST"), help="SMTP 主机；配置后改用邮件发送"
    )
    parser.add_argument(
        "--smtp-port", type=int, default=_env_int("CLUELEDGER_SMTP_PORT", 587), help="SMTP 端口"
    )
    parser.add_argument("--smtp-username", default=_env("CLUELEDGER_SMTP_USERNAME"))
    parser.add_argument("--smtp-from", default=_env("CLUELEDGER_SMTP_FROM"), help="发件地址")
    parser.add_argument(
        "--smtp-starttls",
        action=argparse.BooleanOptionalAction,
        default=not _env_flag("CLUELEDGER_SMTP_NO_STARTTLS"),
        help="SMTP 是否使用 STARTTLS（默认开启）",
    )
    parser.add_argument(
        "--trust-proxy",
        action="store_true",
        default=_env_flag("CLUELEDGER_TRUST_PROXY"),
        help="信任反向代理写入的 X-Forwarded-For 第一项",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="启动时打开这个作品目录；缺省时由界面或接口选择",
    )
    parser.add_argument(
        "--static-dir",
        type=Path,
        default=None,
        help="前端 build 产物目录（桌面 sidecar 用）；缺省自动探测 frontend/dist",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="模型来源配置（默认 ./config.yaml；缺失时不配置模型，服务照常启动）",
    )
    parser.add_argument("--model", default=None, help="覆盖启动档案的模型名")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="应用状态目录（最近目录、会话与事件）；缺省 ~/.hastings",
    )
    parser.add_argument(
        "--library-dir",
        type=Path,
        default=None,
        help=f"全局模块库目录；缺省 {DEFAULT_LIBRARY_DIR}，首次写入时才创建",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（默认 8000）")
    return parser


class StartupError(RuntimeError):
    """启动期的环境故障：退出码 1（与参数错误的 2 区分）。

    显式 ``--static-dir`` 不存在这类故障属于「环境不对」，fail fast 并给出下一步；
    把它和参数错误混成一个退出码，运维脚本就无法分类处理。
    """


@dataclass(frozen=True, slots=True)
class AssembledServer:
    """一次完整装配的结果：应用、预打开的作品与生效路径。"""

    app: FastAPI
    workspace: Path | None
    binding_id: str | None
    state_root: Path
    static_dir: Path | None
    config_path: Path
    boot_profile: str
    boot_model: str
    mode: str = "local"
    database: Path | None = None
    mail_outbox: Path | None = None
    workspaces_root: Path | None = None
    libraries_root: Path | None = None
    billing_url: str | None = None
    production: bool = False
    credentials_file: Path | None = None
    auth: AuthRuntime | None = None
    model_service: ModelConnectionService | None = None
    library_dir: Path | None = None


def load_provider_book(
    config_path: Path,
    model: str | None,
    credentials: object,
    auth: object | None = None,
) -> tuple[ProviderBook, str, str]:
    """读取模型连接并选出启动连接；没有可用连接时返回空引导，不阻止启动。"""
    server_config: ServerConfig = load_server_config(config_path)
    available = next((profile for profile in server_config.providers if profile.available), None)
    boot = available or (server_config.providers[0] if server_config.providers else None)
    boot_name = boot.name if boot else ""
    boot_model = model or (boot.default_model if boot else "")
    book = ProviderBook(
        server_config,
        boot_name=boot_name,
        boot_model=boot_model,
        config_path=config_path,
        credentials=credentials,
        cli_model=model or "",
        auth=auth,
    )
    return book, boot_name, boot_model


def assemble_server(
    *,
    workspace: Path | None = None,
    static_dir: Path | None = None,
    state_root: Path | None = None,
    config: Path | None = None,
    model: str | None = None,
    library_dir: Path | None = None,
    mode: str = "local",
    database: Path | None = None,
    public_base_url: str | None = None,
    mail_outbox: Path | None = None,
    workspaces_root: Path | None = None,
    libraries_root: Path | None = None,
    billing_url: str | None = None,
    platform_models: str | None = None,
    smtp: SmtpSettings | None = None,
    allowed_origins: Sequence[str] = (),
    trust_proxy: bool = False,
    production: bool = False,
    credential_key_file: Path | None = None,
    credentials_file: Path | None = None,
) -> AssembledServer:
    """按生产默认值装配文件工作区应用；任何位置失败都带可读的错误信息抛出，不静默降级。"""
    if mode not in {"local", "server"}:
        raise StartupError(f"未知运行模式：{mode}。建议：使用 local 或 server。")
    if mode == "server" and database is None:
        raise StartupError(
            "server 模式需要 --database 指定账号数据库。"
            "建议：显式给出持久化路径，例如 --database /srv/clueledger/clueledger.sqlite3。"
        )
    if mode == "server" and workspace is not None:
        raise StartupError(
            "server 模式的作品目录由服务器分配，不能使用 --workspace 预打开本地目录。"
            "建议：启动后在作品列表新建或打开作品。"
        )
    if mode == "server" and config is not None:
        raise StartupError(
            "server 模式不使用共享的 --config；每个账号在模型设置里配置自己的连接。"
            "建议：移除 --config，或改用 local 模式。"
        )
    if mode == "server" and model is not None:
        raise StartupError(
            "server 模式不使用 --model 覆盖共享模型；模型选择按账号保存。"
            "建议：移除 --model 后启动。"
        )
    if mode == "server" and library_dir is not None:
        raise StartupError(
            "server 模式的私有模块库按账号分配，不能指定共享 --library-dir。"
            "建议：移除 --library-dir，或使用 --libraries-root 指定账号库根目录。"
        )
    if production:
        errors = production_config_errors(
            mode=mode,
            public_base_url=public_base_url,
            database=database,
            workspaces_root=workspaces_root,
            libraries_root=libraries_root,
            credential_key_file=credential_key_file,
            smtp_configured=smtp is not None,
            billing_url=billing_url,
            billing_secret=_env_secret("CLUELEDGER_BILLING_INTERNAL_SECRET"),
            trust_proxy=trust_proxy,
        )
        if errors:
            raise StartupError("生产配置不完整：\n- " + "\n- ".join(errors))
    resolved_static = resolve_static_dir(static_dir)
    if resolved_static is not None and not resolved_static.is_dir():
        raise StartupError(
            f"--static-dir 指向的目录不存在：{resolved_static}。"
            "建议：先构建前端（frontend/ 下 npm run build），或移除该参数走自动探测。"
        )

    binding_id: str | None = None
    opened: Path | None = None
    if workspace is not None:
        resolved = Path(workspace).expanduser()
        if not resolved.exists():
            raise StartupError(
                f"--workspace 指向的目录不存在：{resolved}。"
                "建议：先创建该目录，或去掉 --workspace 启动后通过界面新建作品。"
            )
        if not resolved.is_dir():
            raise StartupError(
                f"--workspace 指向的是文件而不是目录：{resolved}。"
                "建议：改为指向存放作品的那个文件夹。"
            )
        opened = resolved

    config_path = Path(config).expanduser() if config is not None else DEFAULT_CONFIG_PATH
    resolved_database = Path(database).expanduser() if database is not None else None
    credential_key = (
        Path(credential_key_file).expanduser() if credential_key_file is not None else None
    )
    credentials_path = (
        Path(credentials_file).expanduser()
        if credentials_file is not None
        else (resolved_database.with_suffix(".credentials") if resolved_database else None)
    )
    try:
        credentials = (
            build_credential_store()
            if credential_key is None
            else build_credential_store(key_file=credential_key, credentials_file=credentials_path)
        )
    except (CredentialStoreError, OSError, ValueError) as exc:
        raise StartupError(
            f"凭据存储无法初始化：{exc}。"
            "建议：检查 --credential-key-file 是否存在且为 32 字节/base64，"
            "并确认 --credentials-file 所在目录可写。"
        ) from exc
    recent = RecentWorkspaces(state_root)

    auth_runtime: AuthRuntime | None = None
    resolved_outbox: Path | None = None
    resolved_workspaces_root: Path | None = None
    resolved_libraries_root: Path | None = None
    resolved_library: Path | None = None
    model_service: ModelConnectionService | None = None
    platform: PlatformChannel | None = None
    boot_profile = ""
    boot_model = ""

    if mode == "server":
        assert resolved_database is not None  # 已在函数入口拒绝缺项
        resolved_outbox = (
            Path(mail_outbox).expanduser()
            if mail_outbox is not None
            else recent.root / "mail-outbox"
        )
        base_url = public_base_url or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
        auth_config = AccountsConfig(
            database_path=resolved_database,
            public_base_url=base_url,
            mail_outbox=resolved_outbox,
            allowed_origins=tuple(allowed_origins),
            smtp=smtp,
            trust_proxy=trust_proxy,
        )
        try:
            auth_runtime = build_auth_runtime(auth_config)
        except (OSError, sqlite3.Error) as exc:
            raise StartupError(
                f"账号数据库无法打开：{resolved_database}。"
                "建议：检查目录存在且当前进程可写，或换一个持久化路径。"
            ) from exc
        resolved_workspaces_root = (
            Path(workspaces_root).expanduser()
            if workspaces_root is not None
            else resolved_database.parent / "workspaces"
        )
        resolved_libraries_root = (
            Path(libraries_root).expanduser()
            if libraries_root is not None
            else resolved_database.parent / "libraries"
        )
        billing_secret = _env_secret("CLUELEDGER_BILLING_INTERNAL_SECRET")
        if billing_url or billing_secret:
            if not billing_url or not billing_secret:
                raise StartupError(
                    "订阅通道需要同时设置 --billing-url 与 "
                    "CLUELEDGER_BILLING_INTERNAL_SECRET。"
                    "建议：两者必须与支付服务的 RELAY_INTERNAL_SECRET 一致。"
                )
            raw_models = platform_models or ",".join(DEFAULT_PLATFORM_MODELS)
            models = tuple(item.strip() for item in raw_models.split(",") if item.strip())
            platform = PlatformChannel(
                base_url=billing_url,
                secret=billing_secret,
                models=models,
            )
        try:
            registry = UserWorkspaces(recent.root, auth_runtime.database, resolved_workspaces_root)
        except OSError as exc:
            raise StartupError(
                f"服务器作品存储目录无法创建：{resolved_workspaces_root}。"
                "建议：检查目录权限，或用 --workspaces-root 指定可写位置。"
            ) from exc
        resources = UserResourcesRegistry(
            builder=lambda user_id, context: build_user_resources(
                user_id,
                context,
                state_root=recent.root,
                libraries_root=resolved_libraries_root,
                base_credentials=credentials,
                platform=platform,
            )
        )
        app = create_workspace_app(
            workspaces=registry,
            static_dir=resolved_static,
            auth=auth_runtime,
            resources=resources,
        )
    else:
        # 订阅登录的本机身份落在应用状态目录：换机器会重新注册一次，属于预期行为。
        auth = ModelAuthService(
            providers={
                "chatgpt": ChatGPTCodexAuthProvider(
                    identity=ChatGPTHostIdentity(recent.root / "chatgpt-host.json")
                )
            }
        )
        book, boot_profile, boot_model = load_provider_book(config_path, model, credentials, auth)
        # 设置页与写作轮次共用这一个服务：保存的连接就是下一轮实际调用的连接。
        model_service = ModelConnectionService(
            book=book, config_path=config_path, credentials=credentials, auth=auth
        )
        if opened is not None:
            binding = recent.bind(opened)
            binding_id = binding.id
            opened = Path(binding.path)
        # 库是作者内容，默认放在系统文档目录；索引缓存放应用状态目录，可随时重建。
        resolved_library = (
            Path(library_dir).expanduser() if library_dir is not None else DEFAULT_LIBRARY_DIR
        )
        library = ModuleLibrary(resolved_library, cache_file=recent.root / "library-catalog.json")
        app = create_workspace_app(
            workspaces=recent,
            provider_factory=configured_provider_factory(book),
            limits_factory=configured_limits_factory(book),
            model_service=model_service,
            static_dir=resolved_static,
            library=library,
        )
    return AssembledServer(
        app=app,
        workspace=opened,
        binding_id=binding_id,
        state_root=recent.root,
        static_dir=resolved_static,
        config_path=config_path,
        boot_profile=boot_profile,
        boot_model=boot_model,
        mode=mode,
        database=resolved_database,
        mail_outbox=resolved_outbox,
        workspaces_root=resolved_workspaces_root,
        libraries_root=resolved_libraries_root,
        billing_url=billing_url if platform is not None else None,
        production=production,
        credentials_file=credentials_path if credential_key is not None else None,
        auth=auth_runtime,
        model_service=model_service,
        library_dir=resolved_library,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from hastings.logging import configure_logging

    configure_logging(
        json_mode=args.production or _env("CLUELEDGER_LOG_JSON") in {"1", "true", "yes", "on"}
    )
    try:
        smtp: SmtpSettings | None = None
        if args.smtp_host:
            if not args.smtp_from:
                print("--smtp-host 需要同时给出 --smtp-from（发件地址）。")
                return 2
            smtp = SmtpSettings(
                host=args.smtp_host,
                port=args.smtp_port,
                username=args.smtp_username or "",
                password=_env_secret("CLUELEDGER_SMTP_PASSWORD"),
                from_address=args.smtp_from,
                starttls=args.smtp_starttls,
            )
        assembled = assemble_server(
            workspace=args.workspace,
            static_dir=args.static_dir,
            state_root=args.state_dir,
            config=args.config,
            model=args.model,
            library_dir=args.library_dir,
            mode=args.mode,
            database=args.database,
            public_base_url=args.public_base_url or f"http://{args.host}:{args.port}",
            mail_outbox=args.mail_outbox,
            workspaces_root=args.workspaces_root,
            libraries_root=args.libraries_root,
            billing_url=args.billing_url,
            platform_models=args.platform_models,
            production=args.production,
            credential_key_file=args.credential_key_file,
            credentials_file=args.credentials_file,
            smtp=smtp,
            allowed_origins=args.allowed_origin,
            trust_proxy=args.trust_proxy,
        )
    except StartupError as exc:
        print(exc)
        return 1
    except ValueError as exc:
        print(exc)
        return 2

    print(f"运行模式：{assembled.mode}{'（production）' if assembled.production else ''}")
    if assembled.credentials_file is not None:
        print(f"加密凭据文件：{assembled.credentials_file}")
    if assembled.database is not None:
        print(f"账号数据库：{assembled.database}")
    if assembled.workspaces_root is not None:
        print(f"作品存储根：{assembled.workspaces_root}")
    if assembled.libraries_root is not None:
        print(f"私有模块库根：{assembled.libraries_root}")
    if assembled.billing_url is not None:
        print(f"线簿订阅通道：{assembled.billing_url}")
    if assembled.mail_outbox is not None:
        if smtp is None:
            print(f"邮件：未配置 SMTP，验证与重置链接写入 {assembled.mail_outbox}")
        else:
            print(f"邮件：SMTP {smtp.host}:{smtp.port}，发件地址 {smtp.from_address}")
    if assembled.auth is not None and not assembled.auth.config.cookie_secure:
        print("注意：公共地址不是 HTTPS，会话 Cookie 不会加 Secure；上线前请配置 HTTPS。")
    if assembled.workspace is not None:
        print(f"作品目录：{assembled.workspace}")
    else:
        print("作品目录：未打开（用 --workspace 指定，或启动后在界面选择目录）")
    print(f"应用状态目录：{assembled.state_root}")
    if assembled.mode == "server":
        print("模型：按账号配置（每个账号在设置中添加自己的连接）")
    else:
        model_display = assembled.boot_model or "未配置（配置模型后才能对话）"
        profile_display = assembled.boot_profile or "无"
        print(f"模型：{model_display}｜档案：{profile_display}｜配置：{assembled.config_path}")
    if assembled.model_service is not None and assembled.model_service.storage == "session":
        print("注意：系统凭据存储不可用，API Key 只在本次运行有效（重启后需重新填写）。")
    print(f"写作台地址：http://{args.host}:{args.port}")
    if assembled.static_dir is not None:
        print(f"前端已托管：{assembled.static_dir}")
    uvicorn.run(assembled.app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
