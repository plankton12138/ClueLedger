"""Hastings 真模型走查驱动（eval/intake A+B 组）。

复用 build_harness / PlainRenderer，与 cli.run_repl 同链路；prompt 来源是
spec 文件（多行提示词原样喂入）。跨进程续跑依赖 restore_and_attach 的
会话存档回放。逐案例输出转录文件，供人工按用例观察清单评判。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--stories-root", type=Path, default=Path("stories"))
    parser.add_argument("--model", default=None, help="覆盖 HASTINGS_MODEL")
    return parser.parse_args()


async def run_case(case: dict, args: argparse.Namespace, model: str) -> None:
    from hastings.bible.store import StoryWorkspace
    from hastings.cli import PlainRenderer, build_harness, create_writing_tools
    from hastings.session_persist import restore_and_attach
    from tau_ai.env import openai_compatible_config_from_env
    from tau_ai.openai_compatible import OpenAICompatibleProvider

    print(
        f"### 启动 {case['case']}（slug={case['slug']}，{len(case['rounds'])} 轮）", file=sys.stderr
    )
    provider = OpenAICompatibleProvider(openai_compatible_config_from_env())
    workspace = StoryWorkspace(root=args.stories_root, slug=case["slug"])
    tools = create_writing_tools(workspace)
    harness = build_harness(workspace, provider, model)
    workspace.ensure_dirs()
    renderer = PlainRenderer({tool.name: tool for tool in tools})
    await restore_and_attach(workspace, harness, on_warning=lambda w: print(w, file=sys.stderr))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    transcript = args.out_dir / f"{case['case']}.log"
    with open(transcript, "w", encoding="utf-8") as fh, contextlib.redirect_stdout(fh):
        for i, text in enumerate(case["rounds"], 1):
            print(f"\n===== {case['case']} ｜ 轮次 {i} =====")
            print(f"[user]\n{text}")
            print("\n[agent]")
            async for event in harness.prompt(text):
                renderer.handle(event)
        print(f"\n===== 结束（未渲染事件类型：{renderer.unhandled_event_types or '无'}）=====")
    print(f"### 完成 {case['case']} -> {transcript}", file=sys.stderr)


async def main() -> None:
    args = parse_args()
    # 脚本可从任意目录运行，显式按 cwd 定位仓库根 .env
    load_dotenv(dotenv_path=Path.cwd() / ".env")
    model = args.model or os.environ.get("HASTINGS_MODEL", "")
    if not model:
        print("缺少 HASTINGS_MODEL", file=sys.stderr)
        raise SystemExit(1)
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    for case in spec["cases"]:
        await run_case(case, args, model)
    print("### 全部用例完成", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
