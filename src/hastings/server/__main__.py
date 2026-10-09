"""``python -m hastings.server`` Web 服务入口（P4 多工作区化，P6 D1 多档案）。

与 CLI 入口（hastings.__main__）共享 env 处理——load_dotenv、
normalize_base_url、env_error 零复制复用；装配链 ProviderBook →
default_session_factory → WorkspaceRegistry → create_app → uvicorn.run。
不再绑定单一 slug：stories/ 下所有工作区（含运行中新建的）都经
registry 按 slug 懒装配、各自恢复会话历史。

P6 D1：config.yaml 存在时为权威配置（第一个有 key 的档案作为引导档案，
.env 不再必需）；缺失/为空则回落 env 单档案（P1 现状，零破坏）。引导档案
与 --model 折进 ProviderBook（进程级端点目录），供各工作区按设置解析。
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from hastings.__main__ import DEFAULT_STORIES_ROOT, env_error, normalize_base_url
from hastings.server.app import create_app
from hastings.server.hastings_session import default_session_factory
from hastings.server.provider_config import (
    DEFAULT_CONFIG_PATH,
    ProviderBook,
    ProviderConfigError,
    load_server_config,
)
from hastings.server.registry import WorkspaceRegistry

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def find_static_dir(root: Path) -> Path | None:
    """前端 build 产物目录（<root>/frontend/dist）；不存在则不托管（纯 API 模式）。"""
    candidate = root / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


def default_static_dir() -> Path | None:
    """以仓库根为基准的 dist 探测（src/hastings/server/__main__.py → 上溯 3 级）。"""
    return find_static_dir(Path(__file__).resolve().parents[3])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hastings-server",
        description="Hastings：悬疑小说写作 Agent 的 Web 服务入口（多工作区）",
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
        default=DEFAULT_CONFIG_PATH,
        help="服务级 provider 档案配置（默认 ./config.yaml；缺失回落 .env）",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（默认 8000）")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()
    normalize_base_url()

    try:
        server_config = load_server_config(args.config)
    except ProviderConfigError as exc:
        print(exc)
        return 1

    if args.config.exists():
        # config.yaml 权威：第一个有 key 的档案作为引导档案（.env 不再必需）
        profile = next((p for p in server_config.providers if p.available), None)
        if profile is None:
            print(
                f"config.yaml 中没有可用档案（{args.config}）。"
                "为什么错：所有档案都缺 api_key / api_key_env，"
                "或引用的环境变量为空。"
                "怎样算对：为至少一个档案直填 api_key，"
                "或用 api_key_env 引用已设置的环境变量。"
            )
            return 1
        boot_profile_name = profile.name
        boot_model = args.model or profile.default_model
        if not boot_model:
            print(
                f"引导档案 {profile.name!r} 没有可用的模型名（{args.config}）。"
                "为什么错：档案未配 default_model，也未传 --model。"
                "怎样算对：在该档案补 default_model: <模型名>，"
                "或用 --model 启动参数指定。"
            )
            return 1
    else:
        # env 回落（P1 现状）：缺 env 仍给中文指引
        error = env_error(args.model)
        if error is not None:
            print(error)
            return 1
        boot_profile_name = "default"
        boot_model = args.model or os.environ["HASTINGS_MODEL"]

    # ProviderBook 是进程级端点目录（P6 D1/T2）：按档案名缓存 provider 实例，
    # 跨档案切换复用、无需关闭；boot_* 决定新工作区的引导端点与模型。
    # config_path（P7 T12）：设置页 UI 增删改档案后写回同一文件。
    book = ProviderBook(
        server_config,
        boot_name=boot_profile_name,
        boot_model=boot_model,
        config_path=args.config,
    )
    registry = WorkspaceRegistry(
        root=args.stories_root,
        session_factory=default_session_factory(book),
    )
    static_dir = default_static_dir()
    app = create_app(registry, static_dir=static_dir, book=book)

    print(f"文章目录已打开：{args.stories_root}")
    print(
        f"模型：{boot_model}｜档案：{boot_profile_name}｜写作台地址：http://{args.host}:{args.port}"
    )
    if static_dir is not None:
        print(f"前端已托管：{static_dir}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
