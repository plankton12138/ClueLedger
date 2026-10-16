"""E2E 驱动器（L2 Web E2E，方案见 dev-notes/v1/e2e-test-plan.md §4.1）。

单进程真服务：create_app 同时装配 REST/WS 与前端 build 产物静态托管，
session factory 注入 FakeProvider 剧本——后端与 tau 内核零改动，只经
create_app 既有注入点（registry.session_factory / static_dir / corpus_root）。

用法（Playwright webServer 自动执行；手动调试同款）：
    uv run python -m tests.e2e_server --port 8931
不需要 HASTINGS_MODEL/config.yaml/.env——工厂注入绕过 ProviderBook。

预置盘上状态：
- e2e-home：空工作区（J1 冒烟的自动选中项，bootWorkspaces 取排序首位）；
- e2e-writing：writing 阶段 + 角色档案 ×2 + ch01 章纲 + ch01 正文（J3/J4 的
  事实源）。V1 工作区已只读退役（Phase 17 收尾决策 §2.10）：agent 工具集
  只剩 bible_query / read_chapter / list_chapters / timeline_check，正文与
  账本的「工作区历史状态」直接落盘（作者侧事实而非 agent 行为）；agent
  行为部分全部走 FakeProvider 只读剧本（slug 前缀 e2e-w 路由 read 剧本）。
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from tests.fake_scripts import assistant_stream, chapter_read, character_lookup, closing
from tests.v2.agents.scenario import ScenarioRunner

from hastings.bible.store import StoryWorkspace
from hastings.server.app import create_app
from hastings.server.hastings_session import HastingsSession
from hastings.server.provider_config import ProviderBook, ProviderProfile, ServerConfig
from hastings.server.registry import WorkspaceRegistry
from hastings.server.session import WritingSession
from hastings.server.v2 import V2Runtime
from tau_agent import AgentHarness, AgentHarnessConfig
from tau_ai import FakeProvider

REPO_ROOT = Path(__file__).resolve().parents[1]

# J1 冒烟依赖「已有工作区被自动选中 → WS 已连接」：bootWorkspaces 取排序首位，
# "e2e-home" 按字典序先于测试用 e2e-j* slug，选中结果确定。
PRECREATE_SLUG = "e2e-home"

# J3/J4 预置写作工作区：slug 前缀 e2e-w 同时是 read 剧本的路由键。
WRITING_SLUG = "e2e-writing"

# 预置正文（直接落盘的作者侧事实；V1 只读后 agent 只回读不覆盖）。
CHAPTER_ONE_TITLE = "雨夜的客人"
CHAPTER_ONE_BODY = (
    "雨下了一整夜。艾琳把最后一间客房的钥匙推过柜台时，注意到那位客人的\n"
    "大衣口袋里露出一截怀表链——和三日前失踪的维恩随身那只一模一样。\n"
    "她没有作声，只是把登记簿悄悄翻回了上一页。"
)

# 剧本周期数：intake 每周期 3 个流（bible_query ×2 + 总结）供一轮 prompt，
# read 每周期 2 个流（read_chapter + 总结）。FakeProvider 流耗尽即空流，
# harness 将无事件可放（见 tau_ai/fake.py）——靠冗余周期兜底，8 轮远超任一旅程。
CYCLES = 8


def intake_turns() -> list[list[Any]]:
    """intake 剧本：委托回合 → bible_query ×2 → 总结，逐周期重复（V1 只读）。"""
    turns: list[list[Any]] = []
    for i in range(CYCLES):
        turns.append(
            assistant_stream(
                call=character_lookup(f"call-i{i}-1", "艾琳"),
                text="先查一下角色档案。",
                reason="toolUse",
            )
        )
        turns.append(
            assistant_stream(
                call=character_lookup(f"call-i{i}-2", "维恩"),
                text="",
                reason="toolUse",
            )
        )
        turns.append(closing("两位角色的档案状态已核对。V1 账本只读，登记请使用 V2 工作台。"))
    return turns


def write_turns() -> list[list[Any]]:
    """read 剧本：每轮 read_chapter（预置 ch01）→ 总结。

    依赖预置的 e2e-writing 工作区（stage=writing + 角色档案 + ch01 章纲 +
    ch01 正文，直接落盘）；正文是盘上事实，剧本只做读回。
    """
    turns: list[list[Any]] = []
    for i in range(CYCLES):
        turns.append(
            assistant_stream(
                call=chapter_read(f"call-w{i}-1", 1),
                text="",
                reason="toolUse",
            )
        )
        turns.append(closing("第一章已回读完毕。改写正文请使用 V2 工作台。"))
    return turns


def scenario_turns(slug: str) -> list[list[Any]]:
    """按 slug 前缀路由剧本：e2e-w* 走 read，其余走 intake。"""
    if slug.startswith("e2e-w"):
        return write_turns()
    return intake_turns()


def seed_writing_workspace(stories_root: Path) -> None:
    """预置 writing 阶段工作区（J3/J4 的盘上事实源，见模块 docstring）。"""
    from hastings.bible.models import Character, OutlineEntry
    from hastings.bible.store import (
        load_story_doc,
        save_character,
        save_ledger,
        save_story_doc,
    )

    workspace = StoryWorkspace(root=stories_root, slug=WRITING_SLUG)
    workspace.ensure_dirs()
    save_character(
        workspace,
        Character(name="艾琳", identity="旅馆老板娘，第一发现人", secret="她藏起了住客的怀表"),
    )
    save_character(
        workspace,
        Character(
            name="维恩",
            identity="流浪的钟表匠，嫌疑人之一",
            motivation="找回父亲留下的图纸",
        ),
    )
    outline = OutlineEntry(
        name="ch01-雨夜的客人",
        chapter=1,
        act="第一幕",
        beats=["雨夜投宿", "怀表失窃"],
        end_hook="深夜的脚步声",
    )
    save_ledger(workspace.outline_path, [outline])
    doc = load_story_doc(workspace)
    doc.stage = "writing"
    save_story_doc(workspace, doc)
    heading = f"# 第1章 {CHAPTER_ONE_TITLE}"
    chapter = workspace.chapters_dir / "ch01.md"
    chapter.write_text(f"{heading}\n\n{CHAPTER_ONE_BODY}\n", encoding="utf-8")


def make_session_factory() -> Callable[[StoryWorkspace], Awaitable[WritingSession]]:
    """剧本会话工厂：按 slug 路由 FakeProvider 剧本，装配同生产形状的会话。

    与 default_session_factory 同构但不含 restore_and_attach——E2E 追求确定性，
    会话历史只在 harness 内存中（registry 常驻缓存，页面刷新不丢）。
    V1 只读退役后（§2.10）会话无对账管线，reconcile_debounce 参数随
    HastingsSession 收缩一并移除。
    """

    async def factory(workspace: StoryWorkspace) -> WritingSession:
        from hastings.cli import create_writing_tools
        from hastings.prompts import build_system_prompt

        tools = create_writing_tools(workspace)
        harness = AgentHarness(
            AgentHarnessConfig(
                provider=FakeProvider(scenario_turns(workspace.slug)),
                model="fake",
                system=build_system_prompt(tools),
                tools=tools,
            )
        )
        return HastingsSession(
            harness=harness,
            workspace=workspace,
        )

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
    seed_writing_workspace(stories_root)
    registry = WorkspaceRegistry(root=stories_root, session_factory=make_session_factory())
    v2_runtime = V2Runtime.sqlite(str(stories_root / "v2.sqlite"))
    v2_runtime.configure_agent_runner(ScenarioRunner(v2_runtime.service))
    installed_models: list[str] = []

    def local_transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "e2e"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": name, "size": 1024} for name in installed_models]}
            )
        if request.url.path == "/api/pull":
            name = json.loads(request.content)["model"]
            if name not in installed_models:
                installed_models.append(name)
            return httpx.Response(200, text='{"status":"success"}\n')
        return httpx.Response(404)

    app = create_app(
        registry,
        static_dir=args.static_dir,
        corpus_root=corpus_root,
        v2_runtime=v2_runtime,
        book=ProviderBook(
            ServerConfig(
                providers=[ProviderProfile(name="default", base_url="https://example.invalid/v1")]
            ),
            boot_name="default",
            boot_model="",
            config_path=stories_root / "providers.yaml",
        ),
        model_transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": "qwen3:4b"},
                            {"id": "gpt-oss:20b"},
                        ]
                    },
                )
                if request.url.host == "localhost"
                else httpx.Response(404)
            )
        ),
        ollama_transport=httpx.MockTransport(local_transport),
    )
    print(f"E2E 服务已就绪：http://{args.host}:{args.port}（文章目录：{stories_root}）")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
