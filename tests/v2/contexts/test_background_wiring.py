"""背景接线（V03-4.2 后半）：讨论与试写共用同一份背景。

三条验收：
- 试写提示词里出现本书约定，且排在任务要求之后；
- 同一部作品的两个讨论拿到完全相同的背景，换作品不串；
- 背景按任务的基础版本取，不随当前 head 漂移。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from hastings.application.services.conversation_service import CHAT_SYSTEM_PROMPT
from hastings.application.services.drafting_service import WrittenChapter
from hastings.domain.manuscript.drafting import DraftBrief, render_user_prompt
from hastings.server.v2 import V2Runtime, build_v2_app


class ScriptedChatRunner:
    """记录每次收到的 ChatRun，返回固定回复；不经真模型。"""

    def __init__(self) -> None:
        self.runs: list[Any] = []

    async def stream(self, run: Any, sink: Any) -> str:
        self.runs.append(run)
        await sink("好，我们继续。")
        return "好，我们继续。"


def _chat_client() -> tuple[TestClient, V2Runtime, ScriptedChatRunner]:
    runtime = V2Runtime.memory()
    runner = ScriptedChatRunner()
    runtime.chat.runner_factory = lambda: runner
    runtime.chat.model_resolver = lambda: "test-model"
    runtime.chat.bound_runner_factory = None
    return TestClient(build_v2_app(runtime)), runtime, runner


def _project(client: TestClient, name: str = "背景接线") -> str:
    return client.post("/api/v2/projects", json={"name": name}).json()["project_id"]


def _agreement(client: TestClient, pid: str, statement: str, *, request_id: str) -> None:
    response = client.post(
        f"/api/v2/projects/{pid}/agreements",
        json={"statement": statement, "topic": "narrative_person", "request_id": request_id},
    )
    assert response.status_code == 201, response.text


def _conversation(client: TestClient, pid: str, title: str = "讨论") -> str:
    response = client.post(f"/api/v2/projects/{pid}/conversations", json={"title": title})
    assert response.status_code in (200, 201), response.text
    return response.json()["conversation_id"]


def _send(client: TestClient, pid: str, cid: str, key: str) -> None:
    response = client.post(
        f"/api/v2/projects/{pid}/conversations/{cid}/turns",
        json={"text": "我们聊聊这一章", "idempotency_key": key},
    )
    assert response.status_code == 200, response.text


def test_chat_system_message_carries_the_book_agreements() -> None:
    client, _, runner = _chat_client()
    pid = _project(client)
    _agreement(client, pid, "全书第一人称", request_id="req-1")
    cid = _conversation(client, pid)

    _send(client, pid, cid, "turn-1")

    assert len(runner.runs) == 1
    system = runner.runs[0].system
    assert system.startswith(CHAT_SYSTEM_PROMPT)
    assert "全书第一人称" in system
    assert "## 本书约定" in system
    # 无写权限的边界不能被背景淹没
    assert "没有修改项目内容的" in system


def test_two_conversations_in_one_project_see_the_same_background() -> None:
    client, runtime, runner = _chat_client()
    pid = _project(client)
    _agreement(client, pid, "全书第一人称", request_id="req-1")
    first = _conversation(client, pid, "讨论甲")
    second = _conversation(client, pid, "讨论乙")

    _send(client, pid, first, "turn-1")
    _send(client, pid, second, "turn-2")

    backgrounds = [run.system.split(CHAT_SYSTEM_PROMPT, 1)[1] for run in runner.runs[:2]]
    assert backgrounds[0] == backgrounds[1]
    assert backgrounds[0].strip()
    # 同一个渲染入口：接口读到的文本与提示词里的一致
    assert runtime.background.render(pid, scope="discussion") in runner.runs[0].system


def test_background_does_not_leak_across_projects() -> None:
    client, _, runner = _chat_client()
    alpha = _project(client, "甲作品")
    beta = _project(client, "乙作品")
    _agreement(client, alpha, "甲作品用第一人称", request_id="req-a")
    _agreement(client, beta, "乙作品用第三人称", request_id="req-b")

    _send(client, alpha, _conversation(client, alpha), "turn-a")
    _send(client, beta, _conversation(client, beta), "turn-b")

    alpha_system, beta_system = runner.runs[0].system, runner.runs[1].system
    assert "甲作品用第一人称" in alpha_system
    assert "乙作品用第三人称" not in alpha_system
    assert "乙作品用第三人称" in beta_system
    assert "甲作品用第一人称" not in beta_system


def test_draft_prompt_includes_background_after_task_requirements() -> None:
    runtime = V2Runtime.memory()
    prompts: list[str] = []

    async def writer(*, system: str, prompt: str) -> WrittenChapter:
        prompts.append(prompt)
        return WrittenChapter(text="雪夜里，林昭推开旅馆的门。", model="test-model")

    runtime.drafting._writer = writer  # type: ignore[assignment]
    client = TestClient(build_v2_app(runtime))
    pid = _project(client, "试写背景")
    _agreement(client, pid, "全书第一人称，不写他人内心", request_id="req-1")

    response = client.post(
        f"/api/v2/projects/{pid}/drafts",
        json={"instruction": "林昭在雪夜住进旅馆", "chapter_title": "雪山旅馆"},
    )
    assert response.status_code == 200, response.text
    assert len(prompts) == 1
    prompt = prompts[0]
    assert "全书第一人称，不写他人内心" in prompt
    assert "作品背景" in prompt
    # 本次明确要求在前，常驻背景在后
    assert prompt.index("作者要求") < prompt.index("作品背景")


def test_render_user_prompt_keeps_background_between_task_and_refs() -> None:
    brief = DraftBrief(
        brief_id="drf_1",
        project_id="prj_1",
        target="new",
        chapter_title="第一章",
        instruction="写开场",
    )
    prompt = render_user_prompt(
        brief,
        background="## 本书约定\n- 全书第一人称",
        ledger=[
            {"kind": "story.character", "kind_label": "人物", "title": "姐姐", "summary": "寡言"}
        ],
    )
    assert prompt.index("作者要求") < prompt.index("作品背景")
    assert prompt.index("作品背景") < prompt.index("本书设定")
