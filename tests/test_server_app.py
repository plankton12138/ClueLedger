"""服务端骨架测试（P3 T1，P4 T4 多工作区化）：app 工厂依赖注入 + health 端点。

create_app 只认 WorkspaceRegistry——测试用桩工厂注入即可跑通 HTTP 层，
证明 FastAPI 层不耦合具体 harness 构造（WS 可测性的前提）。
health 是全局探活：只报 status，不绑定具体工作区（工作区状态看
/api/{slug}/state）。
"""

from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path

from fastapi.testclient import TestClient

from hastings.bible.store import StoryWorkspace
from hastings.manuscript.diff import DiffHunk
from hastings.manuscript.write_guard import EditMarkRegistry
from hastings.server.app import create_app
from hastings.server.reconcile import NoticeListener
from hastings.server.registry import WorkspaceRegistry
from hastings.server.session import AgentBusyError, WritingSession
from tau_agent.events import AgentEvent
from tau_agent.harness import EventListener
from tau_agent.messages import AgentMessage

DEFAULT_SLUG = "orient-express"


class StubSession:
    """最小 WritingSession 桩：HTTP/WS 测试通用的注入替身。

    prompt 复刻 harness 的同步抛出契约（调用即抛 AgentBusyError，而非
    async generator 迭代时才抛）——WS 层的 busy 翻译依赖该时序。
    """

    slug: str = DEFAULT_SLUG
    model: str = "fake"

    @property
    def is_running(self) -> bool:
        return self._running

    def __init__(self) -> None:
        self._running = False
        self._edit_marks = EditMarkRegistry()

    @property
    def edit_marks(self) -> EditMarkRegistry:
        return self._edit_marks

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

    async def request_reconcile(self, chapter_id: str, hunks: Sequence[DiffHunk]) -> None:
        del chapter_id, hunks

    def subscribe_notice(self, listener: NoticeListener) -> Callable[[], None]:
        del listener
        return lambda: None


def stub_registry(root: Path, session: WritingSession | None = None) -> WorkspaceRegistry:
    """桩工厂注册表：任何 slug 都装配到同一（或指定）桩会话。"""

    async def factory(workspace: StoryWorkspace) -> WritingSession:
        del workspace
        return session if session is not None else StubSession()

    return WorkspaceRegistry(root=root, session_factory=factory)


def test_health_reports_ok(tmp_path: Path) -> None:
    body = TestClient(create_app(stub_registry(tmp_path))).get("/api/health").json()
    assert body == {"status": "ok"}


def test_serves_spa_dist_when_present(tmp_path: Path) -> None:
    """前端 build 产物经 StaticFiles 托管；/api 优先于静态挂载。"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>hastings</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    client = TestClient(create_app(stub_registry(tmp_path), static_dir=dist))
    assert client.get("/").text == "<html>hastings</html>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/health").status_code == 200


def test_without_static_dir_root_is_404(tmp_path: Path) -> None:
    """static_dir 缺省（目录不存在或未传）时根路径 404，API 照常。"""
    client = TestClient(create_app(stub_registry(tmp_path)))
    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200


def test_state_resolves_via_registry_per_slug(tmp_path: Path) -> None:
    """state 端点按路径 slug 从 registry 解析会话（多工作区路由语义）。"""
    client = TestClient(create_app(stub_registry(tmp_path)))
    body = client.get(f"/api/{DEFAULT_SLUG}/state").json()
    assert body["slug"] == DEFAULT_SLUG
    assert (tmp_path / DEFAULT_SLUG).is_dir()  # 懒装配落盘建目录
