"""E2E 驱动器（L2 Web E2E，方案见 dev-notes/e2e-test-plan.md §4.1）。

单进程真服务：create_app 同时装配 REST/WS 与前端 build 产物静态托管，
session factory 注入 FakeProvider 剧本——后端与 tau 内核零改动，只经
create_app 既有注入点（registry.session_factory / static_dir / corpus_root）。

用法（Playwright webServer 自动执行；手动调试同款）：
    uv run python -m tests.e2e_server --port 8931
不需要 HASTINGS_MODEL/config.yaml/.env——工厂注入绕过 ProviderBook。
"""

from __future__ import annotations

import argparse
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import uvicorn
from tests.fake_scripts import assistant_stream, character_upsert, closing

from hastings.bible.store import StoryWorkspace
from hastings.server.app import create_app
from hastings.server.hastings_session import HastingsSession
from hastings.server.registry import WorkspaceRegistry
from hastings.server.session import WritingSession
from tau_agent import AgentHarness, AgentHarnessConfig
from tau_ai import FakeProvider

REPO_ROOT = Path(__file__).resolve().parents[1]

# J1 冒烟依赖「已有工作区被自动选中 → WS 已连接」：bootWorkspaces 取排序首位，
# "e2e-home" 按字典序先于测试用 e2e-j* slug，选中结果确定。
PRECREATE_SLUG = "e2e-home"

# 剧本周期数：每周期 3 个 provider 流（两次 bible_upsert + 总结）供一轮 prompt，
# 8 周期 = 单 slug 可承受 8 轮对话，远超任一旅程的发送次数（FakeProvider 流耗尽
# 即空流，harness 将无事件可放——靠冗余周期兜底，见 tau_ai/fake.py）。
INTAKE_CYCLES = 8


def intake_turns() -> list[list[Any]]:
    """intake 剧本（方案 §5）：委托回合 → bible_upsert ×2 → 总结，逐周期重复。"""
    turns: list[list[Any]] = []
    for i in range(INTAKE_CYCLES):
        turns.append(
            assistant_stream(
                call=character_upsert(f"call-i{i}-1", "艾琳", "旅馆老板娘，第一发现人"),
                text="先把角色档案立起来。",
                reason="toolUse",
            )
        )
        turns.append(
            assistant_stream(
                call=character_upsert(f"call-i{i}-2", "维恩", "流浪的钟表匠，嫌疑人之一"),
                text="",
                reason="toolUse",
            )
        )
        turns.append(closing("两位角色已建档。接下来可以安排委托场景了。"))
    return turns


def make_session_factory() -> Callable[[StoryWorkspace], Awaitable[WritingSession]]:
    """剧本会话工厂：任何 slug 都装配同一 intake 剧本的 FakeProvider 会话。

    与 default_session_factory 同构但不含 restore_and_attach——E2E 追求确定性，
    会话历史只在 harness 内存中（registry 常驻缓存，页面刷新不丢）。
    """

    async def factory(workspace: StoryWorkspace) -> WritingSession:
        from hastings.cli import create_writing_tools
        from hastings.prompts import build_system_prompt

        tools = create_writing_tools(workspace)
        harness = AgentHarness(
            AgentHarnessConfig(
                provider=FakeProvider(intake_turns()),
                model="fake",
                system=build_system_prompt(tools),
                tools=tools,
            )
        )
        return HastingsSession(harness=harness, workspace=workspace)

    return factory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hastings E2E 驱动器（FakeProvider 剧本服务）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8931)
    parser.add_argument(
        "--stories-root",
        type=Path,
        default=None,
        help="工作区根目录（缺省临时目录，天然隔离）",
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=None,
        help="语料目录（缺省临时目录）",
    )
    parser.add_argument("--static-dir", type=Path, default=REPO_ROOT / "frontend" / "dist")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.static_dir.is_dir():
        # 显式目录缺失属环境故障，fail fast 三要素（与生产入口 __main__ 同口径）。
        print(
            f"前端目录不存在：{args.static_dir}。"
            "为什么错：--static-dir 指向的目录不存在。"
            "怎样算对：先在 frontend/ 下 npm run build，再启动本驱动器。"
        )
        return 1
    stories_root = (
        args.stories_root
        if args.stories_root is not None
        else Path(tempfile.mkdtemp(prefix="hastings-e2e-stories-"))
    )
    corpus_root = (
        args.corpus_root
        if args.corpus_root is not None
        else Path(tempfile.mkdtemp(prefix="hastings-e2e-corpus-"))
    )
    StoryWorkspace(root=stories_root, slug=PRECREATE_SLUG).ensure_dirs()
    registry = WorkspaceRegistry(root=stories_root, session_factory=make_session_factory())
    app = create_app(registry, static_dir=args.static_dir, corpus_root=corpus_root)
    print(f"E2E 服务已就绪：http://{args.host}:{args.port}（文章目录：{stories_root}）")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
