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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from hastings.infrastructure.credentials import build_credential_store
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
from hastings.server.provider_config import (
    DEFAULT_CONFIG_PATH,
    ProviderBook,
    ServerConfig,
    load_server_config,
)
from hastings.workspace.state import RecentWorkspaces

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


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
) -> AssembledServer:
    """按生产默认值装配文件工作区应用；任何位置失败都带可读的错误信息抛出，不静默降级。"""
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
    credentials = build_credential_store()
    recent = RecentWorkspaces(state_root)
    # 订阅登录的本机身份落在应用状态目录：换机器会重新注册一次，属于预期行为。
    # 会话服务同时被连接管理（登录/保存）与运行时（续期）使用。
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
        model_service=model_service,
        library_dir=resolved_library,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        assembled = assemble_server(
            workspace=args.workspace,
            static_dir=args.static_dir,
            state_root=args.state_dir,
            config=args.config,
            model=args.model,
            library_dir=args.library_dir,
        )
    except StartupError as exc:
        print(exc)
        return 1
    except ValueError as exc:
        print(exc)
        return 2

    if assembled.workspace is not None:
        print(f"作品目录：{assembled.workspace}")
    else:
        print("作品目录：未打开（用 --workspace 指定，或启动后在界面选择目录）")
    print(f"应用状态目录：{assembled.state_root}")
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
