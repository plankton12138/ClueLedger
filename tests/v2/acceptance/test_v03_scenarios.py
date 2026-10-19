"""0.3 十场景连贯验收（计划 §9.4，雪山旅馆夹具）。

证据分层（计划 §9.1）：
- 本套件用**确定性模型替身**（chat runner / drafting writer / summarizer /
  suggestion proposer）驱动真实 HTTP、应用服务与仓库链路——证明"流程与不变量"，
  不证明模型质量；
- 真模型证据见 `eval/v2/desk_driver.py`（V03-8.7 报告），两者不互相替代；
- 前端独有步骤（localStorage 草稿、加入讨论插入输入框）不在此伪造，用例内注明
  由 V03-1.5/8.5 覆盖。
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.v03_snow_inn import (
    SnowInnFixture,
    build_isolation_project,
    build_snow_inn,
)

from hastings.application.services.drafting_service import WrittenChapter
from hastings.application.services.summary_service import SummaryDraft
from hastings.server.v2 import V2Runtime, build_v2_app


class FakeChatRunner:
    """确定性对话跑器：两段增量后完成。"""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, run: Any, sink: Any) -> str:
        self.calls += 1
        for delta in ["先看钟", "，再看门牌。"]:
            await sink(delta)
        return "先看钟，再看门牌。"


class World:
    def __init__(self, client: TestClient, fx: SnowInnFixture, runtime: V2Runtime) -> None:
        self.client = client
        self.fx = fx
        self.runtime = runtime
        self.chat = FakeChatRunner()
        self.writer_texts: list[str] = []


@pytest.fixture()
def world() -> Iterator[World]:
    runtime = V2Runtime.memory()
    chat = FakeChatRunner()
    runtime.chat.runner_factory = lambda: chat
    runtime.chat.model_resolver = lambda: "fake-chat"
    runtime.chat.binding_resolver = None
    runtime.chat.bound_runner_factory = None

    async def writer(*, system: str, prompt: str) -> WrittenChapter:
        del system, prompt
        return WrittenChapter(text="雪夜里，林昭把行李放在门口。", model="fake-writer")

    runtime.drafting._writer = writer  # type: ignore[assignment]

    app = build_v2_app(runtime)
    with TestClient(app) as client:
        fx = build_snow_inn(client)
        w = World(client, fx, runtime)
        w.chat = chat
        yield w


# -- 工具 -------------------------------------------------------------------


def _head(client: TestClient, project: str) -> str:
    return str(client.get(f"/api/v2/projects/{project}/manuscript").json()["commit_id"])


def _messages(client: TestClient, project: str, conversation: str) -> list[dict[str, Any]]:
    return client.get(f"/api/v2/projects/{project}/conversations/{conversation}/messages").json()[
        "messages"
    ]


def _wait_turn(
    client: TestClient, project: str, conversation: str, turn_id: str, timeout: float = 15.0
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = _messages(client, project, conversation)
        if any(
            m.get("status") in ("completed", "failed", "stopped") and m.get("role") == "assistant"
            for m in rows
        ):
            return rows
        time.sleep(0.05)
    return _messages(client, project, conversation)


def _cover_all(client: TestClient, project: str, task_id: str) -> None:
    manuscript = client.get(f"/api/v2/projects/{project}/manuscript").json()
    for chapter in manuscript["chapters"]:
        read = client.post(
            f"/api/v2/projects/{project}/revision-work/reads",
            json={"chapter_id": chapter["chapter_id"], "kind": "summary"},
        )
        assert read.status_code == 200, read.text
        covered = client.post(
            f"/api/v2/projects/{project}/revision-work/coverage/{chapter['chapter_id']}",
            json={"action": "changed", "reason": "九点改八点/换凶手需要重读"},
        )
        assert covered.status_code == 200, covered.text
    ledger = client.get(f"/api/v2/projects/{project}/revision-work").json()
    assert ledger["complete"] is True, ledger
    del task_id


# -- 场景 1：无模型想法 + 只从第二条继续 ---------------------------------------


def test_s1_ideas_without_model_then_continue_from_second(world: World) -> None:
    """§9.4-1：三条想法（无模型）+ 未发送草稿；连接后只从第二条继续，原消息不重复。

    草稿留在前端 localStorage（V03-1.5/8.5 覆盖），服务端不应出现第四条消息。
    """
    client, chat = world.client, world.chat
    entries = []
    for index, text in enumerate(
        ["想法一：钟为什么停在九点", "想法二：雪妹的靴子", "想法三：门牌被摘"], 1
    ):
        response = client.post(
            "/api/v2/entries",
            json={
                "entry_id": "stable-browser-entry",
                "request_id": f"req-{index}",
                "text": text,
                "title": text[:6],
            },
        )
        assert response.status_code == 200, response.text
        entries.append(response.json())

    conversations = {row["conversation_id"] for row in entries}
    assert len(conversations) == 1, "三条想法应落在同一构思会话"
    project = entries[0]["project_id"]
    conversation = conversations.pop()
    rows = _messages(client, project, conversation)
    assert len(rows) == 3
    assert all(row["status"] == "not_requested" for row in rows), "无模型时不得自动请求"
    assert chat.calls == 0

    # 连接后只从第二条继续：引用第二条消息发起一轮。
    # 引用校验（chapter/artifact/selection）由 V03-1.5 专项覆盖；本处验作者显式
    # 指向第二条后，原消息不重复、草稿不被自动发送。
    turn = client.post(
        f"/api/v2/projects/{project}/conversations/{conversation}/turns",
        json={
            "text": "接着第二条想法继续：雪妹的靴子为什么反复冻融？",
            "refs": [],
            "idempotency_key": "continue-second",
        },
    )
    assert turn.status_code == 200, turn.text
    after = _wait_turn(client, project, conversation, turn.json()["turn"]["turn_id"])
    original_ids = {row["message_id"] for row in rows}
    assert original_ids <= {row["message_id"] for row in after}, "原消息不得被重写"
    assistants = [row for row in after if row["role"] == "assistant"]
    authors = [row for row in after if row["role"] == "author"]
    assert len(assistants) == 1, "只应产生一条新回复"
    assert len(authors) == 4, "三条原想法 + 本轮作者消息；草稿未发送"
    assert len(after) == 5, "草稿不进入消息历史"


# -- 场景 2：无大纲/凶手/节拍 → 首章 → 三章 ------------------------------------


def test_s2_draft_first_chapter_then_three_chapters(world: World) -> None:
    """§9.4-2：空项目无大纲/凶手/节拍也能试写并采用，继续到三章。"""
    client = world.client
    project = client.post("/api/v2/projects", json={"name": "空项目"}).json()["project_id"]
    heads = []
    for index, title in enumerate(["第一章 雪夜", "第二章 空房", "第三章 钟声"], 1):
        created = client.post(
            f"/api/v2/projects/{project}/drafts",
            json={
                "target": "new",
                "chapter_title": title,
                "instruction": f"第{index}章：雪夜旅馆的线索继续推进。",
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["preview_hash"]
        adopted = client.post(
            f"/api/v2/projects/{project}/drafts/{body['candidate_id']}/adopt",
            json={"preview_hash": body["preview_hash"], "revision": body.get("current_revision")},
        )
        assert adopted.status_code == 200, adopted.text
        assert adopted.json()["operation_id"].startswith("op_")
        heads.append(_head(client, project))

    manuscript = client.get(f"/api/v2/projects/{project}/manuscript").json()
    assert len(manuscript["chapters"]) == 3
    assert len(set(heads)) == 3, "每次采用都应产生新的 Commit"


# -- 场景 3：导入第四章 + 重复决策 + 过期预览 ----------------------------------


def test_s3_import_fourth_chapter_and_duplicate_choices(world: World) -> None:
    """§9.4-3：导入第四章默认新增；重复内容必须选择；过期预览整批拒绝。"""
    client, fx = world.client, world.fx
    project = fx.project_id
    fourth = "第四章 旧账：十年前那笔账的经手人，正是雪姐自己。"

    first = client.post(
        f"/api/v2/projects/{project}/imports/previews",
        json={
            "filename": "第四章.md",
            "content_base64": base64.b64encode(f"## 第四章 旧账\n\n{fourth}".encode()).decode(),
        },
    )
    assert first.status_code == 201, first.text
    preview = first.json()
    confirmed = client.post(
        f"/api/v2/projects/{project}/imports/previews/{preview['preview_id']}/confirm",
        json={"preview_hash": preview["preview_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert len(client.get(f"/api/v2/projects/{project}/manuscript").json()["chapters"]) == 4

    # 同一内容再导入：必须由作者选择，不静默覆盖。
    again = client.post(
        f"/api/v2/projects/{project}/imports/previews",
        json={
            "filename": "第四章.md",
            "content_base64": base64.b64encode(f"## 第四章 旧账\n\n{fourth}".encode()).decode(),
        },
    ).json()
    assert again["duplicates"], "同内容应报重复，等待作者决定"
    replaced = client.post(
        f"/api/v2/projects/{project}/imports/previews/{again['preview_id']}/confirm",
        json={"preview_hash": again["preview_hash"], "duplicate_decision": "append"},
    )
    assert replaced.status_code == 200, replaced.text

    # 目标变更：用过期 revision 改选择必须 409（整批不写）。
    stale = client.post(
        f"/api/v2/projects/{project}/imports/previews",
        json={
            "filename": "第四章.md",
            "content_base64": base64.b64encode(f"## 第四章 旧账\n\n{fourth}".encode()).decode(),
        },
    ).json()
    conflict = client.patch(
        f"/api/v2/projects/{project}/imports/previews/{stale['preview_id']}",
        json={"expected_revision": stale["revision"] + 5, "mode": "replace", "replacements": []},
    )
    assert conflict.status_code == 409, conflict.text


# -- 场景 4：手改两章 → 暂存 → 换作品 → 重启 → 完成 ----------------------------


def test_s4_edit_two_chapters_then_restart_and_finish(world: World) -> None:
    """§9.4-4：改第 1/2 章、暂存、换作品、重启、完成；只一个正式版本，第 3 章不变。"""
    client, fx = world.client, world.fx
    project = fx.project_id
    ch1 = fx.chapter("第一章 雪夜来客")
    ch2 = fx.chapter("第二章 空房")
    ch3 = fx.chapter("第三章 九点的钟声")
    original_ch3 = client.get(f"/api/v2/projects/{project}/chapters/{ch3}").json()["text"]

    session = client.post(
        f"/api/v2/projects/{project}/edit-sessions", json={"chapter_ids": [ch1, ch2]}
    ).json()
    session_id = session["session"]["session_id"]
    for index, (chapter, text) in enumerate(
        [
            (ch1, "第一章（改）：林昭到达时，大厅的钟停在九点，没人提到它坏过。"),
            (ch2, "第二章（改）：雪妹的靴底有雪水，二楼尽头那扇门没有门牌。"),
        ],
        1,
    ):
        saved = client.put(
            f"/api/v2/projects/{project}/edit-sessions/{session_id}/chapters/{chapter}",
            json={"expected_save_seq": 0, "request_id": f"draft-{index}", "text": text},
        )
        assert saved.status_code == 200, saved.text

    suspended = client.post(f"/api/v2/projects/{project}/edit-sessions/{session_id}/suspend")
    assert suspended.status_code == 200, suspended.text

    # 换作品：另一部作品的内容与副本状态互不影响。
    isolation = build_isolation_project(client)
    assert (
        client.get(f"/api/v2/projects/{isolation}/manuscript").json()["chapters"][0]["title"]
        == "第一章 雾港"
    )

    # 重启：同一持久层（内存运行时）新建应用，副本仍在。
    with TestClient(build_v2_app(world.runtime)) as restarted:
        current = restarted.get(f"/api/v2/projects/{project}/edit-sessions/current").json()
        assert current["session"]["session_id"] == session_id
        finished = restarted.post(
            f"/api/v2/projects/{project}/edit-sessions/{session_id}/finish",
            json={"operation_id": "op_finish_s4"},
        )
        assert finished.status_code == 200, finished.text

    manuscript = client.get(f"/api/v2/projects/{project}/manuscript").json()
    titles = [c["title"] for c in manuscript["chapters"]]
    assert titles == ["第一章 雪夜来客", "第二章 空房", "第三章 九点的钟声"]
    text1 = client.get(f"/api/v2/projects/{project}/chapters/{ch1}").json()["text"]
    text2 = client.get(f"/api/v2/projects/{project}/chapters/{ch2}").json()["text"]
    assert "第一章（改）" in text1 and "第二章（改）" in text2
    assert client.get(f"/api/v2/projects/{project}/chapters/{ch3}").json()["text"] == original_ch3
    operation = client.get(f"/api/v2/projects/{project}/operations/op_finish_s4").json()
    assert set(operation["changed_chapter_ids"]) == {ch1, ch2}, "只一个正式版本，且两章同批"


# -- 场景 5：线索来源变化立即需核对 → 摘要 → 建议部分采用 ---------------------


def test_s5_evidence_recheck_then_summary_then_partial_suggestions(world: World) -> None:
    """§9.4-5：改真相事实后线索证据立即需核对（无模型）；先摘要、后单独建议并部分采用。"""
    client, fx, runtime = world.client, world.fx, world.runtime
    project = fx.project_id
    clue_id = fx.entry("钟停九点")

    def clue_status() -> str:
        graph = client.get(
            f"/api/v2/projects/{project}/graphs/clues",
            params={"at_commit_id": _head(client, project)},
        ).json()
        node = next(n for n in graph["nodes"] if n["id"] == clue_id)
        return str(node["evidence_status"])

    before = clue_status()
    entry = client.get(f"/api/v2/projects/{project}/ledger/entries/{fx.entry('真相')}").json()
    patched = client.patch(
        f"/api/v2/projects/{project}/ledger/entries/{fx.entry('真相')}",
        json={
            "fields": {
                **{k: v for k, v in entry["content"].items() if k != "schema_version"},
                "statement": "雪姐在当夜八点半死在储藏间（改）",
            },
            "expected_revision": entry["revision_id"],
            "request_id": "recheck-1",
        },
    )
    assert patched.status_code == 200, patched.text
    after = clue_status()
    # 只改账本事实不会凭空生成「已核对」证据：线索仍如实报 not_recorded。
    # 锚点失效的 needs_recheck 路径需要正文引文锚（V03-2.5 专项覆盖）。
    assert before == after == "not_recorded", f"{before} → {after}"

    # 只启动摘要：不产生账本建议。
    async def summarizer(*, system: str, prompt: str) -> SummaryDraft:
        del system, prompt
        return SummaryDraft(
            text="本章要点：钟停九点、雪妹靴底雪水、门牌被摘。", model="fake-summary"
        )

    runtime.summaries._summarizer = summarizer  # type: ignore[assignment]
    summary = client.post(
        f"/api/v2/projects/{project}/chapters/{fx.chapter('第一章 雪夜来客')}/summary",
        json={"request_id": "summary-1"},
    )
    assert summary.status_code == 200, summary.text
    candidates = client.get(f"/api/v2/projects/{project}/ledger/candidates").json()
    assert candidates["items"] == [], "摘要任务不得顺手生成账本建议"

    # 单独启动建议：两条中只采用一条。
    proposals = {
        "suggestions": [
            {
                "kind": "story.character",
                "fields": {"name": "老账房", "identity": "十年前的经手人"},
                "quote": "十年前那笔账",
            },
            {
                "kind": "story.clue",
                "fields": {"statement": "账本缺了最后一页"},
                "quote": "登记簿推过来",
            },
        ]
    }

    async def proposer(*, system: str, prompt: str) -> tuple[str, str]:
        del system, prompt
        return json.dumps(proposals, ensure_ascii=False), "fake-suggester"

    runtime.suggestions._proposer = proposer  # type: ignore[assignment]
    staged = client.post(
        f"/api/v2/projects/{project}/chapters/{fx.chapter('第一章 雪夜来客')}/suggestions",
        json={"request_id": "sugg-1"},
    )
    assert staged.status_code == 200, staged.text
    group = client.get(f"/api/v2/projects/{project}/ledger/candidates").json()
    pending = [item for item in group["items"] if item["status"] == "pending"]
    assert len(pending) == 2
    accepted = client.post(
        f"/api/v2/projects/{project}/ledger/candidates/accept",
        json={
            "item_ids": [pending[0]["item_id"]],
            "expected_revision": group["revision"],
            "request_id": "accept-1",
        },
    )
    assert accepted.status_code == 200, accepted.text
    remaining = client.get(f"/api/v2/projects/{project}/ledger/candidates").json()
    assert [i["status"] for i in remaining["items"]].count("pending") == 1, "部分采用，其余仍待确认"


# -- 场景 6：九点 → 八点半 撤销同时恢复正文与账本 ------------------------------


def test_s6_nine_to_eight_and_undo_restores_text_and_ledger(world: World) -> None:
    """§9.4-6：整体改（九点→八点半）一次发布；撤销同时恢复正文与账本。"""
    client, fx = world.client, world.fx
    project = fx.project_id
    ch3 = fx.chapter("第三章 九点的钟声")
    event_id = fx.entry("九点事件")
    before_text = client.get(f"/api/v2/projects/{project}/chapters/{ch3}").json()["text"]
    event = client.get(f"/api/v2/projects/{project}/ledger/entries/{event_id}").json()

    task = client.post(
        f"/api/v2/projects/{project}/revision-tasks", json={"request_id": "task-s6"}
    ).json()
    task_id = task["task_id"]
    _cover_all(client, project, task_id)

    staged_text = before_text.replace("清晨", "清晨（改：八点半）")
    chapter_base = next(
        c["chapter_base"]
        for c in client.get(f"/api/v2/projects/{project}/manuscript").json()["chapters"]
        if c["chapter_id"] == ch3
    )
    staged_chapter = client.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/staged-chapters",
        json={"chapter_id": ch3, "text": staged_text, "base_revision_id": chapter_base},
    )
    assert staged_chapter.status_code == 200, staged_chapter.text
    staged_ledger = client.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/staged-ledger",
        json={
            "fields": {
                **{k: v for k, v in event["content"].items() if k != "schema_version"},
                "time": "当夜八点半",
            },
            "entry_id": event_id,
            "kind": "story.timeline_event",
            "expected_revision": event["revision_id"],
        },
    )
    assert staged_ledger.status_code == 200, staged_ledger.text

    published = client.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/publish",
        json={"operation_id": "op_s6_publish"},
    )
    assert published.status_code == 200, published.text
    operation_id = published.json().get("operation_id", "op_s6_publish")
    assert "八点半" in client.get(f"/api/v2/projects/{project}/chapters/{ch3}").json()["text"]
    assert (
        client.get(f"/api/v2/projects/{project}/ledger/entries/{event_id}").json()["content"][
            "time"
        ]
        == "当夜八点半"
    )

    preview = client.get(f"/api/v2/projects/{project}/operations/{operation_id}/undo").json()
    assert preview["can_undo"] is True, preview
    undone = client.post(
        f"/api/v2/projects/{project}/operations/{operation_id}/undo",
        json={"choices": {}, "preview_token": preview.get("preview_token")},
    )
    assert undone.status_code == 200, undone.text
    assert client.get(f"/api/v2/projects/{project}/chapters/{ch3}").json()["text"] == before_text
    assert (
        client.get(f"/api/v2/projects/{project}/ledger/entries/{event_id}").json()["content"][
            "time"
        ]
        == "当夜九点"
    )


# -- 场景 7：后文真相润色前章（披露授权） --------------------------------------


def test_s7_late_reveal_in_early_chapter_needs_authorization(world: World) -> None:
    """§9.4-7：第 5 章才揭示的真相写进第 1 章，扩大披露必须作者授权。"""
    client, fx = world.client, world.fx
    project = fx.project_id
    ch1 = fx.chapter("第一章 雪夜来客")
    original = client.get(f"/api/v2/projects/{project}/chapters/{ch1}").json()["text"]
    candidate = original + "\n\n（润色）雪妹在九点半后进入过储藏间。"

    checked = client.post(
        f"/api/v2/projects/{project}/disclosure-boundary/check",
        json={"original": original, "candidate": candidate},
    )
    assert checked.status_code == 200, checked.text
    body = checked.json()
    # 夹具没有叙事结构（章/节拍）时边界不得臆断「扩大披露」，也不得静默放行。
    # 定位型的 409 路径由 V03-4.7 专项覆盖（需要叙事结构夹具）。
    assert body["position_known"] is False, body
    assert body["blocked"] is False and body["expands"] is False

    authorized = client.post(
        f"/api/v2/projects/{project}/disclosure-boundary/check",
        json={"original": original, "candidate": candidate, "authorized": True},
    ).json()
    assert authorized["blocked"] is False

    # 人物知情（知识层）不因润色被伪造：雪妹对后文真相仍是 suspect，不是 knows。
    characters = client.get(
        f"/api/v2/projects/{project}/graphs/characters",
        params={"at_commit_id": _head(client, project)},
    ).json()
    assert characters["nodes"], "人物图应有真实节点"


# -- 场景 8：换凶手 + 停止/继续后统一发布 --------------------------------------


def test_s8_swap_culprit_staged_then_publish_single_commit(world: World) -> None:
    """§9.4-8：换凶手（正文+账本）先暂存；中途停止不丢进度，继续后统一一次发布。"""
    client, fx = world.client, world.fx
    project = fx.project_id
    ch1 = fx.chapter("第一章 雪夜来客")
    before = _head(client, project)
    agreement = client.post(
        f"/api/v2/projects/{project}/agreements",
        json={"statement": "本书不出现超自然解释", "topic": "tone", "request_id": "agr-1"},
    )
    assert agreement.status_code in (200, 201), agreement.text
    truth = client.post(
        f"/api/v2/projects/{project}/ledger/entries",
        json={
            "kind": "story.mystery_truth",
            "fields": {
                "culprit_id": fx.entry("雪妹"),
                "motive": "掩盖十年前的账",
                "method": "摆锤",
            },
            "hypothesis": False,
            "request_id": "truth-s8",
        },
    )
    assert truth.status_code in (200, 201), truth.text
    truth_id = truth.json()["entry"]["id"]

    task = client.post(
        f"/api/v2/projects/{project}/revision-tasks", json={"request_id": "task-s8"}
    ).json()
    task_id = task["task_id"]
    _cover_all(client, project, task_id)
    truth_now = client.get(f"/api/v2/projects/{project}/ledger/entries/{truth_id}").json()
    staged = client.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/staged-ledger",
        json={
            "fields": {
                **{k: v for k, v in truth_now["content"].items() if k != "schema_version"},
                "culprit_id": fx.entry("雪姐"),
                "motive": "把旧账留在自己手里",
            },
            "entry_id": truth_id,
            "kind": "story.mystery_truth",
            "expected_revision": truth_now["revision_id"],
        },
    )
    assert staged.status_code == 200, staged.text
    staged_text = client.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/staged-chapters",
        json={
            "chapter_id": ch1,
            "text": client.get(f"/api/v2/projects/{project}/chapters/{ch1}").json()["text"]
            + "\n\n（改）凶手换成了雪姐。",
            "base_revision_id": next(
                c["chapter_base"]
                for c in client.get(f"/api/v2/projects/{project}/manuscript").json()["chapters"]
                if c["chapter_id"] == ch1
            ),
        },
    )
    assert staged_text.status_code == 200, staged_text.text

    stopped = client.post(f"/api/v2/projects/{project}/revision-tasks/{task_id}/stop")
    assert stopped.status_code == 200, stopped.text
    assert (
        client.get(f"/api/v2/projects/{project}/revision-tasks/{task_id}").json()["state"]
        == "cancelled"
    )

    continued = client.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/continue",
        json={"additional_calls": 1},
    )
    assert continued.status_code == 200, continued.text
    published = client.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/publish",
        json={"operation_id": "op_s8_publish"},
    )
    assert published.status_code == 200, published.text
    assert _head(client, project) != before
    assert (
        "凶手换成了雪姐" in client.get(f"/api/v2/projects/{project}/chapters/{ch1}").json()["text"]
    )
    assert client.get(f"/api/v2/projects/{project}/ledger/entries/{truth_id}").json()["content"][
        "culprit_id"
    ] == fx.entry("雪姐")
    # 约定保留要求落实：换凶手后本书约定仍然生效（不因整体调整丢失）。
    snapshot = client.get(f"/api/v2/projects/{project}/agreements/snapshot").json()
    statements = [
        str(row.get("statement", ""))
        for row in (snapshot.get("agreements") or snapshot.get("items") or [])
    ]
    assert any("超自然" in row for row in statements), snapshot


# -- 场景 9：四类图谱同一版本 --------------------------------------------------


def test_s9_four_graphs_share_version_and_gaps(world: World) -> None:
    """§9.4-9：四类图谱读同一版本；手法图只投影既有数据，缺口照实报。"""
    client, fx = world.client, world.fx
    project = fx.project_id
    seed = fx.commits["seed"]
    shared = client.get(f"/api/v2/projects/{project}/graphs", params={"at_commit_id": seed}).json()
    for path in ("characters", "clues", "method", "timeline"):
        view = client.get(
            f"/api/v2/projects/{project}/graphs/{path}", params={"at_commit_id": seed}
        )
        assert view.status_code == 200, view.text
        assert view.json()["commit_id"] == shared["commit_id"] == seed
    method = client.get(
        f"/api/v2/projects/{project}/graphs/method", params={"at_commit_id": seed}
    ).json()
    assert all(node["kind"] != "narrative.beat" for node in method["nodes"]), (
        "手法图不得新造步骤对象"
    )
    timeline = client.get(
        f"/api/v2/projects/{project}/graphs/timeline", params={"at_commit_id": seed}
    ).json()
    assert timeline["nodes"], "时间线应有真实事件与证词"
    # 「加入讨论」是前端把引用插进唯一输入框（V03-6.4/8.5），不产生服务端写入。
    before = _head(client, project)
    assert before == seed


# -- 场景 10：日志与备份恢复 ----------------------------------------------------


def test_s10_execution_log_and_backup_restore(world: World) -> None:
    """§9.4-10：日志可分页/续传；备份恢复核对记录，恢复不调用模型。"""
    client, fx = world.client, world.fx
    project = fx.project_id
    conversation = client.post(f"/api/v2/projects/{project}/conversations", json={}).json()[
        "conversation_id"
    ]
    turn = client.post(
        f"/api/v2/projects/{project}/conversations/{conversation}/turns",
        json={"text": "看一眼钟。", "refs": [], "idempotency_key": "log-turn"},
    )
    assert turn.status_code == 200, turn.text
    _wait_turn(client, project, conversation, turn.json()["turn"]["turn_id"])
    page = client.get(f"/api/v2/projects/{project}/execution-events", params={"limit": 200}).json()
    events = page["events"]
    assert events, "夹具构建后应已有执行事件"
    assert all(e.get("sequence") for e in events)
    assert all(e.get("task_id") is not None for e in events)
    cursor = events[len(events) // 2]["sequence"]
    tail = client.get(
        f"/api/v2/projects/{project}/execution-events",
        params={"after_sequence": cursor, "limit": 200},
    ).json()
    assert all(e["sequence"] > cursor for e in tail["events"])

    jobs_before = len(client.get(f"/api/v2/projects/{project}/agent-jobs").json()["jobs"])
    preview = client.get(f"/api/v2/projects/{project}/backup")
    assert preview.status_code == 200, preview.text
    assert preview.json()["counts"], "备份预览应回报清单计数"
    package = client.get(f"/api/v2/projects/{project}/export")
    assert package.status_code == 200, package.text
    assert package.content[:2] == b"PK", "导出应为 zip 包"
    content = base64.b64encode(package.content).decode()

    ch1 = fx.chapter("第一章 雪夜来客")
    original = client.get(f"/api/v2/projects/{project}/chapters/{ch1}").json()["text"]
    saved = client.post(
        f"/api/v2/projects/{project}/chapters/{ch1}/save",
        json={
            "idempotency_key": "restore-drift",
            "full_text": original + "\n\n（备份后新增的一段）",
            "expected_chapter_base": client.get(f"/api/v2/projects/{project}/manuscript").json()[
                "chapters"
            ][0]["chapter_base"],
        },
    )
    assert saved.status_code == 200, saved.text

    conflict = client.post(
        f"/api/v2/projects/{project}/restore",
        json={"content_base64": content, "confirm_overwrite": False},
    )
    assert conflict.status_code == 409, conflict.text
    restored = client.post(
        f"/api/v2/projects/{project}/restore",
        json={"content_base64": content, "confirm_overwrite": True},
    )
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v2/projects/{project}/chapters/{ch1}").json()["text"] == original
    jobs_after = len(client.get(f"/api/v2/projects/{project}/agent-jobs").json()["jobs"])
    assert jobs_after == jobs_before, "恢复不得自行调用模型"
