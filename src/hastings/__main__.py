"""``python -m hastings <story-slug>`` 命令行入口（Phase 1 T5）。"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from hastings.bible.store import StoryWorkspace
from hastings.cli import run_repl

DEFAULT_STORIES_ROOT = Path("stories")

ENV_HINT = """没有配置好模型端点，文章还打不开。请照着下面的清单准备 .env：

1. 把 .env.example 复制为 .env（.env 不会被提交到仓库）
2. 填入 OPENAI_API_KEY —— 任意 OpenAI 兼容端点的 key
3. 填入 OPENAI_BASE_URL —— 例如 DeepSeek https://api.deepseek.com/v1
                            通义 Qwen https://dashscope.aliyuncs.com/compatible-mode/v1
                            智谱 GLM  https://open.bigmodel.cn/api/paas/v4
4. 填入 HASTINGS_MODEL —— 例如 deepseek-chat / qwen-plus / glm-4-plus

也可以用 --model 命令行参数临时覆盖模型名。
没有 key 也能看效果：uv run pytest -q 会回放一段确定性的写作过程。"""


def env_error(model_override: str | None = None) -> str | None:
    """检查环境变量；缺配置时返回中文指引，否则返回 None。"""
    missing = [
        name
        for name, value in (
            ("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY")),
            ("HASTINGS_MODEL", model_override or os.environ.get("HASTINGS_MODEL")),
        )
        if not value
    ]
    return ENV_HINT if missing else None


def normalize_base_url() -> None:
    """归一化 OPENAI_BASE_URL：留空则移除，让 tau 的官方默认端点生效。

    .env.example 模板里 `OPENAI_BASE_URL=` 留空时，load_dotenv 会把空串写入
    environ，而 tau 侧 ``environ.get(var, DEFAULT)`` 空串会覆盖默认值，
    导致请求打到空地址报模糊错误（R3 评审 #1）。
    """
    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url is None:
        return
    stripped = base_url.strip()
    if stripped:
        os.environ["OPENAI_BASE_URL"] = stripped
    else:
        del os.environ["OPENAI_BASE_URL"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hastings",
        description="Hastings：悬疑小说写作 Agent 的命令行入口",
    )
    parser.add_argument("slug", help="故事标识，例如 orient-express")
    parser.add_argument("--model", default=None, help="覆盖 HASTINGS_MODEL")
    parser.add_argument(
        "--stories-root",
        type=Path,
        default=DEFAULT_STORIES_ROOT,
        help="故事工作区根目录（默认 ./stories）",
    )
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

    try:
        asyncio.run(run_repl(workspace, provider, model))
    except KeyboardInterrupt:
        print("\n故事已合上。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
