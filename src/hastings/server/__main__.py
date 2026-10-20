"""``python -m hastings.server`` Web 服务入口（0.4 默认启动）。

默认启动文件工作区应用：作品是本地 Markdown 目录，接口前缀 ``/api/file-workspaces``。
``--workspace <目录>`` 在启动时预先打开一个作品；不带该参数时服务照常启动，由界面或
接口再选择目录。应用状态（最近目录、后续的会话与事件）放在 ``~/.hastings``，可用
``HASTINGS_STATE_DIR`` 覆盖。

0.3 的旧工作台装配已从这个入口退出：不再读 ``config.yaml``、不再打开 ``data/v2.db``、
不再挂 ``/api/v2`` 与旧模型路由，也没有任何参数能回到旧应用。旧模块与旧测试仍在源码
里，供 V04-5 清理时核对，但它们不再构成一条可启动的链路。

退出码：目录不存在、前端目录不存在等环境故障 → 1；参数写错 → 2。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from hastings.server.file_app import create_workspace_app
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


def assemble_server(
    *,
    workspace: Path | None = None,
    static_dir: Path | None = None,
    state_root: Path | None = None,
) -> AssembledServer:
    """按生产默认值装配文件工作区应用；任何位置失败都带三要素抛出，不静默降级。"""
    resolved_static = resolve_static_dir(static_dir)
    if resolved_static is not None and not resolved_static.is_dir():
        raise StartupError(
            f"前端目录不存在：{resolved_static}。为什么错：--static-dir 指向的目录不存在。"
            "怎样算对：先构建前端（frontend/ 下 npm run build），或移除该参数走自动探测。"
        )

    binding_id: str | None = None
    opened: Path | None = None
    if workspace is not None:
        resolved = Path(workspace).expanduser()
        if not resolved.exists():
            raise StartupError(
                f"作品目录不存在：{resolved}。为什么错：--workspace 要求打开一个已有目录。"
                "怎样算对：先创建该目录，或去掉 --workspace 启动后通过界面新建作品。"
            )
        if not resolved.is_dir():
            raise StartupError(
                f"不是目录：{resolved}。为什么错：--workspace 指向的是文件。"
                "怎样算对：改为指向存放作品的那个文件夹。"
            )
        opened = resolved

    book = RecentWorkspaces(state_root)
    if opened is not None:
        binding = book.bind(opened)
        binding_id = binding.id
        opened = Path(binding.path)
    app = create_workspace_app(book=book, static_dir=resolved_static)
    return AssembledServer(
        app=app,
        workspace=opened,
        binding_id=binding_id,
        state_root=book.root,
        static_dir=resolved_static,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        assembled = assemble_server(workspace=args.workspace, static_dir=args.static_dir)
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
    print(f"写作台地址：http://{args.host}:{args.port}")
    if assembled.static_dir is not None:
        print(f"前端已托管：{assembled.static_dir}")
    uvicorn.run(assembled.app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
