"""Hastings 真模型评测驱动（C 组 v2 对照实验）。

多臂设计：同一委托书、同一模型、同一 .env，跑两条臂——
- ``harness``（Arm 1）：复用 build_harness / PlainRenderer，与 cli.run_repl 同链路，
  rounds 走完 intake → Bible 确认 → write_chapter 至成稿；
- ``bare``（Arm 0）：同一委托书单轮直写，绕过全部 hastings 工具与协议。

两臂成稿统一落盘为 ``<case>-s<k>.article.md`` 供 CP2 成对盲评；harness 臂的
Bible 留在 stories/<slug>/ 供 CP1 设定精度对照。转录 *.log 属运行产物不入库，
driver/spec/报告按 eval/runs/<日期>/ 约定归档。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--stories-root", type=Path, default=None, help="工作区根目录（缺省为仓库根下 stories/）"
    )
    parser.add_argument("--model", default=None, help="覆盖 HASTINGS_MODEL")
    parser.add_argument(
        "--arm",
        choices=["harness", "bare"],
        default=None,
        help="只跑指定臂（缺省跑 spec 里声明的全部臂）",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=1,
        help="每臂每题采样次数（创意输出方差大，正式评测建议 ≥3）",
    )
    return parser.parse_args()


async def run_harness_case(case: dict[str, Any], args: argparse.Namespace, model: str) -> None:
    from hastings.bible.store import StoryWorkspace
    from hastings.cli import PlainRenderer, build_harness, create_writing_tools
    from hastings.context_manager import compact_context
    from hastings.prompts import refresh_system_prompt
    from hastings.session_persist import restore_and_attach
    from tau_ai.env import openai_compatible_config_from_env
    from tau_ai.openai_compatible import OpenAICompatibleProvider

    print(
        f"### 启动 {case['case']}（slug={case['slug']}，harness 臂，{len(case['rounds'])} 轮）",
        file=sys.stderr,
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
            refresh_system_prompt(harness, workspace)  # L7：每轮 prompt 前注入最新状态
            await compact_context(harness, workspace)  # L9：刷新后两档压缩
            async for event in harness.prompt(text):
                renderer.handle(event)
        print(f"\n===== 结束（未渲染事件类型：{renderer.unhandled_event_types or '无'}）=====")

    # 成稿合并落盘：chapters/*.md 按文件名排序拼接，与 bare 臂同构供盲评
    chapters = sorted(workspace.chapters_dir.glob("*.md"))
    article = "\n\n".join(p.read_text(encoding="utf-8").strip() for p in chapters)
    article_path = args.out_dir / f"{case['case']}.article.md"
    article_path.write_text(article, encoding="utf-8")
    await provider.aclose()
    if not chapters:
        # L8/D7：0 章是结果不是异常——只告警、不改退出码（报告要的是证据）。
        print(
            f"[driver] {case['case']}：0 章成稿。"
            "为什么错：全程无 write_chapter 落盘，或正文直出被 prose_guard 捕获未落盘。"
            "怎样算对：检查转录中 [tool] write_chapter 行与 prose_guard 告警定位缺口。",
            file=sys.stderr,
        )
    print(
        f"### 完成 {case['case']} -> {transcript}（{len(chapters)} 章 -> {article_path}）",
        file=sys.stderr,
    )


async def run_bare_case(case: dict[str, Any], args: argparse.Namespace, model: str) -> None:
    from tau_agent.messages import UserMessage
    from tau_agent.provider_events import AssistantMessageEvent, TextDeltaEvent
    from tau_ai.env import openai_compatible_config_from_env
    from tau_ai.openai_compatible import OpenAICompatibleProvider

    print(f"### 启动 {case['case']}（bare 臂，单轮直写）", file=sys.stderr)
    provider = OpenAICompatibleProvider(openai_compatible_config_from_env())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    transcript = args.out_dir / f"{case['case']}.log"
    chunks: list[str] = []
    with open(transcript, "w", encoding="utf-8") as fh, contextlib.redirect_stdout(fh):
        print(f"===== {case['case']} ｜ bare 单轮 =====")
        print(f"[user]\n{case['prompt']}")
        print("\n[assistant]")
        event: AssistantMessageEvent
        async for event in provider.stream_response(
            model=model,
            system=case.get("system", ""),
            messages=[UserMessage(content=case["prompt"])],
            tools=[],
        ):
            if isinstance(event, TextDeltaEvent):
                chunks.append(event.delta)
                print(event.delta, end="", flush=True)
        print()
    article_path = args.out_dir / f"{case['case']}.article.md"
    article_path.write_text("".join(chunks), encoding="utf-8")
    await provider.aclose()
    print(f"### 完成 {case['case']} -> {transcript}（成稿 -> {article_path}）", file=sys.stderr)


async def run_case(case: dict[str, Any], args: argparse.Namespace, model: str) -> None:
    arm = case["arm"]
    if args.arm and arm != args.arm:
        return
    for k in range(1, args.sample + 1):
        sampled = {**case}
        suffix = "" if args.sample == 1 else f"-s{k}"
        if "slug" in sampled:
            sampled["slug"] = f"{sampled['slug']}{suffix}"
        sampled["case"] = f"{sampled['case']}{suffix}"
        if arm == "bare":
            await run_bare_case(sampled, args, model)
        else:
            await run_harness_case(sampled, args, model)


def find_repo_root() -> Path:
    """从脚本位置向上定位仓库根（含 pyproject.toml 的目录）。

    勿用 cwd：从仓库外启动时 .env 会落空、stories 会写错位置；
    “可从任意目录运行”的承诺须由脚本自身位置兜底。
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    print("未定位到仓库根（向上未找到 pyproject.toml）", file=sys.stderr)
    raise SystemExit(1)


async def main() -> None:
    args = parse_args()
    repo_root = find_repo_root()
    if args.stories_root is None:
        args.stories_root = repo_root / "stories"
    load_dotenv(dotenv_path=repo_root / ".env")
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
