"""服务端骨架测试（P3 T1）：app 工厂依赖注入 + health 端点。

create_app 只认 WritingSession 协议——测试用桩会话注入即可跑通 HTTP 层，
证明 FastAPI 层不耦合具体 harness 构造（WS 可测性的前提）。
"""

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Literal

from fastapi.testclient import TestClient

from hastings.bible.store import StoryWorkspace
from hastings.server.app import create_app
from hastings.server.session import AgentBusyError, WritingSession
from tau_agent.events import AgentEvent
from tau_agent.harness import EventListener
from tau_agent.messages import AgentMessage


class StubSession:
    """最小 WritingSession 桩：HTTP/WS 测试通用的注入替身。

    prompt 复刻 harness 的同步抛出契约（调用即抛 AgentBusyError，而非
    async generator 迭代时才抛）——WS 层的 busy 翻译依赖该时序。
    """

    slug: str = "orient-express"
    model: str = "fake"

    @property
    def is_running(self) -> bool:
        return self._running

    def __init__(self) -> None:
        self._running = False

    @property
    def tool_names(self) -> tuple[str, ...]:
        return ("bible_upsert", "write_chapter")

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        return ()

    def prompt(self, content: str) -> AsyncIterator[AgentEvent]:
        del content
        raise AgentBusyError(
            "prompt 被拒绝：stub 会话正忙。"
            "为什么错：同一个故事会话同时只能运行一个写作流。"
            "怎样算对：用 steer 在当前流中插话、用 follow_up 排队下一轮，"
            "或先 abort 中止当前流。"
        )

    def steer(self, content: str) -> None:
        del content

    def follow_up(self, content: str) -> None:
        del content

    def cancel(self) -> None:
        self._running = False

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        del listener
        return lambda: None


def _client(session: WritingSession, workspace: StoryWorkspace) -> TestClient:
    return TestClient(create_app(session, workspace))


def test_health_reports_session_state(tmp_path: Path) -> None:
    workspace = StoryWorkspace(root=tmp_path, slug="orient-express")
    body = _client(StubSession(), workspace).get("/api/health").json()
    assert body == {
        "status": "ok",
        "story": "orient-express",
        "model": "fake",
        "is_running": False,
        "tools": ["bible_upsert", "write_chapter"],
    }


def test_health_reflects_running_state(tmp_path: Path) -> None:
    session = StubSession()
    session._running = True  # noqa: SLF001 — 测试桩内部状态，直接拨动
    workspace = StoryWorkspace(root=tmp_path, slug="orient-express")
    body: dict[str, Literal["ok"] | str | bool | list[str]] = (
        _client(session, workspace).get("/api/health").json()
    )
    assert body["is_running"] is True
