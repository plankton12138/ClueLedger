"""``python -m hastings.server <story-slug>`` Web 服务入口（P3 T5）。

与 CLI 入口（hastings.__main__）共享 env 处理——load_dotenv、
normalize_base_url、env_error 零复制复用；装配链 workspace →
HastingsSession → create_app → uvicorn.run。
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from hastings.__main__ import DEFAULT_STORIES_ROOT, env_error, normalize_base_url
from hastings.bible.store import StoryWorkspace
from hastings.server.app import create_app
from hastings.server.hastings_session import HastingsSession

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
        description="案卷 Hastings：悬疑小说写作 Agent 的 Web 服务入口",
    )
    parser.add_argument("slug", help="故事标识，例如 orient-express")
    parser.add_argument("--model", default=None, help="覆盖 HASTINGS_MODEL")
    parser.add_argument(
        "--stories-root",
        type=Path,
        default=DEFAULT_STORIES_ROOT,
        help="故事工作区根目录（默认 ./stories）",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（默认 8000）")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()
    normalize_base_url()

    error = env_error(args.model)
    if error is not None:
        print(error)
        return 1

    from tau_ai.env import openai_compatible_config_from_env
    from tau_ai.openai_compatible import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(openai_compatible_config_from_env())
    workspace = StoryWorkspace(root=args.stories_root, slug=args.slug)
    model = args.model or os.environ["HASTINGS_MODEL"]
    session = HastingsSession.from_workspace(workspace, provider=provider, model=model)
    static_dir = default_static_dir()
    app = create_app(session, workspace, static_dir=static_dir)

    print(f"案卷已打开：{workspace.story_dir}")
    print(f"模型：{model}｜写作台地址：http://{args.host}:{args.port}")
    if static_dir is not None:
        print(f"前端已托管：{static_dir}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
