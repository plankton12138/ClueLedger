"""``python -m hastings.server`` Web 服务入口（P4 多工作区化，P6 D1 多档案）。

与 CLI 入口（hastings.__main__）共享 env 处理——load_dotenv、
normalize_base_url、env_error 零复制复用；装配链 ProviderBook →
default_session_factory → WorkspaceRegistry → create_app → uvicorn.run。
不再绑定单一 slug：stories/ 下所有工作区（含运行中新建的）都经
registry 按 slug 懒装配、各自恢复会话历史。

P6 D1：config.yaml 存在时为权威配置（第一个有 key 的档案作为引导档案，
.env 不再必需）；缺失/为空则回落 env 单档案（P1 现状，零破坏）。引导档案
与 --model 折进 ProviderBook（进程级端点目录），供各工作区按设置解析。

当前工作台默认装配 V2 REST/WS/SSE 和持久仓库；--v2-db 仅覆盖数据库路径，
不再作为启用开关。默认复用 data/v2.db 或 data/v2.sqlite，并打印实际打开路径。

0.4（V04-1）：``--workspace <目录>`` 走文件工作区模式——只装配文件工作区应用，
不读 config.yaml、不打开旧数据库，作品内容就是该目录下的 Markdown 文件。
两种模式互斥，默认仍是旧工作台，直到 V04-5 切换默认入口。
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from hastings.__main__ import DEFAULT_STORIES_ROOT, env_error, normalize_base_url
from hastings.infrastructure.credentials import build_credential_store
from hastings.server.app import create_app
from hastings.server.file_app import create_workspace_app
from hastings.server.hastings_session import default_session_factory
from hastings.server.provider_config import (
    DEFAULT_CONFIG_PATH,
    ProviderBook,
    ProviderConfigError,
    env_fallback_config,
    load_server_config,
)
from hastings.server.registry import WorkspaceRegistry
from hastings.server.v2 import V2Runtime
from hastings.workspace.state import RecentWorkspaces

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_V2_DB = Path("data/v2.db")


def find_static_dir(root: Path) -> Path | None:
    """前端 build 产物目录（<root>/frontend/dist）；不存在则不托管（纯 API 模式）。"""
    candidate = root / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


def default_static_dir() -> Path | None:
    """以仓库根为基准的 dist 探测（src/hastings/server/__main__.py → 上溯 3 级）。"""
    return find_static_dir(Path(__file__).resolve().parents[3])


def resolve_config_path(config: Path | None, config_dir: Path | None) -> Path:
    """provider 档案路径解析（P9 T9，D10）：--config > --config-dir/config.yaml > 默认。"""
    if config is not None and config_dir is not None:
        raise ValueError(
            "--config 与 --config-dir 只能传一个。为什么错：两个参数同时给出会产生"
            "歧义。怎样算对：只保留其中一个。"
        )
    if config is not None:
        return config
    if config_dir is not None:
        return config_dir / "config.yaml"
    return DEFAULT_CONFIG_PATH


def resolve_static_dir(static_dir: Path | None) -> Path | None:
    """前端目录解析（P9 T9，D7）：显式 --static-dir 优先，缺省走仓库探测。"""
    if static_dir is not None:
        return static_dir
    return default_static_dir()


def resolve_v2_db(explicit: Path | None) -> Path:
    """默认启动当前工作台，优先复用已有的约定数据库，避免打开空仓库。"""
    if explicit is not None:
        return explicit
    candidates = (DEFAULT_V2_DB, Path("data/v2.sqlite"))
    existing = [path for path in candidates if path.exists()]
    if len(existing) > 1:
        raise ValueError(
            "工作台数据库不明确：data/v2.db 与 data/v2.sqlite 同时存在。"
            "为什么错：无法判断当前作品属于哪个仓库，不能静默选择。"
            "怎样算对：用 --v2-db 指定要打开的数据库路径。"
        )
    return existing[0] if existing else DEFAULT_V2_DB


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hastings-server",
        description="Hastings：悬疑小说作者工作台的 Web 服务入口",
    )
    parser.add_argument("--model", default=None, help="覆盖启动档案的 default_model")
    parser.add_argument(
        "--stories-root",
        type=Path,
        default=DEFAULT_STORIES_ROOT,
        help="故事工作区根目录（默认 ./stories）",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="服务级 provider 档案配置（默认 ./config.yaml；缺失回落 .env）",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="provider 档案目录（P9 T9）：取该目录下 config.yaml；与 --config 互斥",
    )
    parser.add_argument(
        "--static-dir",
        type=Path,
        default=None,
        help="前端 build 产物目录（P9 T9，桌面 sidecar 用）；缺省自动探测 frontend/dist",
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=Path("./corpus"),
        help="语料库目录（P7 T6，默认 ./corpus；作者级资产，与 stories/ 平级）",
    )
    parser.add_argument(
        "--v2-db",
        type=Path,
        default=None,
        help="工作台数据库路径；默认复用 data/v2.db 或 data/v2.sqlite，新建时用 data/v2.db",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="以文件工作区模式启动（0.4）：直接打开这个作品目录，不装配旧工作台、不打开旧数据库",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（默认 8000）")
    return parser


class StartupError(RuntimeError):
    """启动期的环境故障：退出码 1（与参数歧义的 2 区分）。

    显式 ``--static-dir`` 不存在这类故障属于「环境不对」，fail fast 并给出
    下一步；把它和参数错误混成一个退出码，运维脚本就无法分类处理。
    """


@dataclass(frozen=True, slots=True)
class AssembledServer:
    """一次完整装配的结果：应用 + 各部件与实际生效的路径。

    V03-8.3：把 ``main`` 里的装配抽成可复用入口，闭环冒烟测试因此走**同一条
    生产装配链**（同样的 config 解析、ProviderBook、registry、SQLite 工作台），
    而不是另搭一个测试运行时——否则「生产能起」永远只是假设。
    """

    app: FastAPI
    v2_runtime: V2Runtime
    config_path: Path
    v2_db: Path
    static_dir: Path | None
    stories_root: Path
    corpus_root: Path
    boot_profile_name: str
    boot_model: str
    unconfigured: bool
    env_hint: str = ""
    credential_migration_failed: tuple[str, ...] = ()


def assemble_server(
    *,
    stories_root: Path = DEFAULT_STORIES_ROOT,
    corpus_root: Path | None = None,
    v2_db: Path | None = None,
    config: Path | None = None,
    config_dir: Path | None = None,
    static_dir: Path | None = None,
    model: str | None = None,
    load_env: bool = True,
) -> AssembledServer:
    """按生产默认值装配应用；任何位置失败都带三要素抛出，不静默降级。

    ``load_env=False`` 供测试使用：不读真实 ``.env``／不调用 ``normalize_base_url``，
    其余路径与 ``python -m hastings.server`` 完全一致（**没有测试专用开关**）。
    """
    try:
        config_path = resolve_config_path(config, config_dir)
        db_path = resolve_v2_db(v2_db)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    resolved_static = resolve_static_dir(static_dir)
    if resolved_static is not None and not resolved_static.is_dir():
        raise StartupError(
            f"前端目录不存在：{resolved_static}。为什么错：--static-dir 指向的目录不存在。"
            "怎样算对：先构建前端（frontend/ 下 npm run build），或移除该参数走自动探测。"
        )
    if load_env:
        load_dotenv()
        normalize_base_url()

    server_config = load_server_config(config_path)

    boot_profile_name = "default"
    boot_model = model or ""
    unconfigured = False
    env_hint = ""
    if config_path.exists():
        # config.yaml 权威：第一个有 key 的档案作为引导档案（.env 不再必需）。
        # 空启动（首次使用无任何配置）：服务照起，用户在设置页配置后再对话——
        # 未配置的会话在 prompt 时 fail fast（MODEL_NOT_CONFIGURED_MESSAGE）。
        if not server_config.providers:
            print(
                f"config.yaml 没有任何档案（{config_path}），回落 env 单档案。"
                "为什么错：providers 列表为空，没有可解析的端点。"
                "怎样算对：在设置页添加档案（写回本文件），或删除本文件回落 .env。"
            )
            server_config = env_fallback_config()
        profile = next((p for p in server_config.providers if p.available), None)
        if profile is None:
            fallback = server_config.providers[0]
            boot_profile_name = fallback.name
            boot_model = model or fallback.default_model
            unconfigured = True
            print(
                "当前没有可用档案（所有档案都缺 api_key / api_key_env，"
                "或引用的环境变量为空），服务以未配置模式启动。"
                "怎样算对：在设置页为档案配置密钥与模型名后即可对话。"
            )
        else:
            boot_profile_name = profile.name
            boot_model = model or profile.default_model
            if not boot_model:
                unconfigured = True
                print(
                    f"引导档案 {profile.name!r} 没有配置模型名（{config_path}），"
                    "服务以未配置模式启动。"
                    "怎样算对：在设置页为该档案填写模型名（可先拉取模型列表），"
                    "或用 --model 启动参数指定。"
                )
    else:
        # env 回落（P1 现状）：缺 env 不拒绝启动——空启动，设置页配置后再对话
        env_hint = env_error(model) or ""
        if env_hint:
            unconfigured = True
        boot_profile_name = "default"
        boot_model = model or os.environ.get("HASTINGS_MODEL", "")

    credential_store = build_credential_store()
    book = ProviderBook(
        server_config,
        boot_name=boot_profile_name,
        boot_model=boot_model,
        config_path=config_path,
        credentials=credential_store,
    )
    registry = WorkspaceRegistry(
        root=stories_root,
        session_factory=default_session_factory(book),
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    v2_runtime = V2Runtime.sqlite(str(db_path))
    app = create_app(
        registry,
        static_dir=resolved_static,
        book=book,
        corpus_root=corpus_root if corpus_root is not None else Path("./corpus"),
        v2_runtime=v2_runtime,
    )
    return AssembledServer(
        app=app,
        v2_runtime=v2_runtime,
        config_path=config_path,
        v2_db=db_path,
        static_dir=resolved_static,
        stories_root=stories_root,
        corpus_root=corpus_root if corpus_root is not None else Path("./corpus"),
        boot_profile_name=boot_profile_name,
        boot_model=boot_model,
        unconfigured=unconfigured,
        env_hint=env_hint,
        credential_migration_failed=tuple(book.credential_migration["failed"]),
    )


@dataclass(frozen=True, slots=True)
class AssembledFileWorkspace:
    """0.4 文件工作区模式的一次装配：只有新应用与作品目录，不含旧运行时。"""

    app: FastAPI
    workspace: Path
    binding_id: str
    state_root: Path
    static_dir: Path | None


def assemble_file_workspace(
    *,
    workspace: Path,
    static_dir: Path | None = None,
    state_root: Path | None = None,
) -> AssembledFileWorkspace:
    """装配文件工作区应用：作品目录必须已存在，打开即登记到最近目录。

    V04-1 只做文件闭环，因此这里不读 ``config.yaml``、不建 ``data/v2.db``：
    没有模型和旧数据库也应该能打开、读写中文文件。
    """
    resolved = Path(workspace).expanduser()
    if not resolved.exists():
        raise StartupError(
            f"作品目录不存在：{resolved}。为什么错：--workspace 要求打开一个已有目录。"
            "怎样算对：先创建该目录，或通过界面新建作品。"
        )
    if not resolved.is_dir():
        raise StartupError(
            f"不是目录：{resolved}。为什么错：--workspace 指向的是文件。"
            "怎样算对：改为指向存放作品的那个文件夹。"
        )
    resolved_static = resolve_static_dir(static_dir)
    if resolved_static is not None and not resolved_static.is_dir():
        raise StartupError(
            f"前端目录不存在：{resolved_static}。为什么错：--static-dir 指向的目录不存在。"
            "怎样算对：先构建前端（frontend/ 下 npm run build），或移除该参数走自动探测。"
        )
    book = RecentWorkspaces(state_root)
    binding = book.bind(resolved)
    app = create_workspace_app(book=book, static_dir=resolved_static)
    return AssembledFileWorkspace(
        app=app,
        workspace=Path(binding.path),
        binding_id=binding.id,
        state_root=book.root,
        static_dir=resolved_static,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    file_workspace: AssembledFileWorkspace | None = None
    assembled: AssembledServer | None = None
    try:
        if args.workspace is not None:
            if args.v2_db is not None:
                raise ValueError(
                    "--workspace 与 --v2-db 不能同时使用。为什么错：文件工作区模式不打开旧工作台"
                    "数据库，两个参数放在一起说明意图不明确。怎样算对：只保留 --workspace。"
                )
            file_workspace = assemble_file_workspace(
                workspace=args.workspace, static_dir=args.static_dir
            )
        else:
            assembled = assemble_server(
                stories_root=args.stories_root,
                corpus_root=args.corpus_root,
                v2_db=args.v2_db,
                config=args.config,
                config_dir=args.config_dir,
                static_dir=args.static_dir,
                model=args.model,
            )
    except StartupError as exc:
        print(exc)
        return 1
    except ProviderConfigError as exc:  # 配置损坏：先于 ValueError（它是子类）
        print(exc)
        return 1
    except ValueError as exc:
        print(exc)
        return 2

    if file_workspace is not None:
        print(f"作品目录：{file_workspace.workspace}")
        print(f"应用状态目录：{file_workspace.state_root}")
        print(f"写作台地址：http://{args.host}:{args.port}")
        if file_workspace.static_dir is not None:
            print(f"前端已托管：{file_workspace.static_dir}")
        uvicorn.run(file_workspace.app, host=args.host, port=args.port, log_level="info")
        return 0

    assert assembled is not None
    if assembled.env_hint:
        print(f"{assembled.env_hint}服务仍将启动（未配置模式）：在设置页配置档案与模型后即可对话。")
    for failed in assembled.credential_migration_failed:
        print(f"⚠ 凭据迁移未完成：{failed}（旧连接保持可用；修复后重启重试）")
    print(f"文章目录已打开：{assembled.stories_root}")
    print(f"语料库目录：{assembled.corpus_root}")
    print(f"工作台数据库：{assembled.v2_db.resolve()}")
    model_display = assembled.boot_model or "未配置（在设置页配置模型后即可对话）"
    print(
        f"模型：{model_display}｜档案：{assembled.boot_profile_name}｜"
        f"写作台地址：http://{args.host}:{args.port}"
    )
    if assembled.static_dir is not None:
        print(f"前端已托管：{assembled.static_dir}")
    uvicorn.run(assembled.app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
