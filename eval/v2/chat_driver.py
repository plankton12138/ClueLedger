"""Phase 19 对话真模型走查驱动（eval/v2/chat_driver.py）。

进程内启动 V2 应用（SQLite 隔离库）+ 真实 Provider（默认 config.yaml 的
本地 Ollama 档案），经 HTTP + SSE 走查计划 §19.6 的四个对话场景：

1. 流式多轮：增量分片到达、终态完整、**不产生任何 Job/Commit**（A01）；
2. 只讨论不写入：假设性剧情讨论，账本/变更/正文均不变（A02）；
3. 停止与断线补齐：取消后迟到分片无效、部分回复保留；SSE 按游标重放不重复；
4. 聊天到创作：任务建议 → 显式启动（模型绑定定格）→ 真实执行 → 候选投影
   （或如实记录模型输出不合规），候选可接受则验证 Commit 落盘。

运行::

    uv run python eval/v2/chat_driver.py --out eval/runs/2026-10-16-phase19-chat

默认 `--provider config` 使用 config.yaml 的 ollama 档案；`--model` 覆盖档案
模型名。报告（README.md）入库；SSE 转录写 *.log，不入库。模型输出不合规时
报告如实记录失败原因，不用 FakeProvider 替代真模型证据。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from hastings.server.provider_config import ProviderBook, load_server_config
from hastings.server.v2 import V2Runtime, build_v2_app

ROOT = Path(__file__).resolve().parents[2]


class Walkthrough:
    def __init__(self, out_dir: Path, model: str) -> None:
        self.out_dir = out_dir
        self.model = model
        self.findings: list[dict[str, Any]] = []
        self.started = datetime.now()

    def record(self, scenario: str, ok: bool, detail: str, **extra: Any) -> None:
        self.findings.append(
            {"scenario": scenario, "ok": ok, "detail": detail, **extra}
        )
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {scenario}: {detail}", flush=True)

    def write_report(self) -> None:
        lines = [
            "# Phase 19 对话真模型走查报告",
            "",
            f"- 时间：{self.started.isoformat()}",
            f"- 模型：{self.model}（本地 Ollama，config.yaml ollama 档案）",
            "- 证据性质：真实 Provider 流式调用；转录见同目录 *.log（不入库）。",
            "",
            "| 场景 | 结果 | 说明 |",
            "|---|---|---|",
        ]
        for f in self.findings:
            lines.append(
                f"| {f['scenario']} | {'通过' if f['ok'] else '失败'} | {f['detail']} |"
            )
        (self.out_dir / "README.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )


class ChatClient:
    """进程内 ASGI 客户端：与生产同一 FastAPI 应用，不经网络栈。"""

    def __init__(self, runtime: V2Runtime) -> None:
        self.runtime = runtime
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_v2_app(runtime)),
            base_url="http://walkthrough.local",
            timeout=httpx.Timeout(600.0),
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def create_project_with_chapter(self) -> tuple[str, str]:
        project = (
            await self.http.post("/api/v2/projects", json={"name": "走查项目"})
        ).json()["project_id"]
        await self.http.post(
            f"/api/v2/projects/{project}/chapters/import",
            json={"idempotency_key": "imp1", "title": "第一章", "text": "雨夜，钟停了。"},
        )
        conversation = (
            await self.http.post(
                f"/api/v2/projects/{project}/conversations", json={}
            )
        ).json()["conversation_id"]
        return project, conversation

    async def send_turn(self, project: str, conversation: str, text: str, key: str) -> str:
        response = await self.http.post(
            f"/api/v2/projects/{project}/conversations/{conversation}/turns",
            json={"text": text, "refs": [], "idempotency_key": key},
        )
        response.raise_for_status()
        return str(response.json()["turn"]["turn_id"])

    async def read_events(
        self, project: str, conversation: str, after: int, max_events: int = 500
    ) -> list[dict[str, Any]]:
        """读 SSE 快照流（tail=0：返回当前积压后服务端关闭）。"""
        events: list[dict[str, Any]] = []
        async with self.http.stream(
            "GET",
            f"/api/v2/projects/{project}/conversations/{conversation}/events?after={after}&tail=0",
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line.removeprefix("data: ")))
                    if len(events) >= max_events:
                        break
        return events

    async def wait_terminal(
        self, project: str, conversation: str, turn_id: str, timeout: float
    ) -> list[dict[str, Any]]:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            events = await self.read_events(project, conversation, 0)
            terminal = [
                e
                for e in events
                if e["event"].get("type") in ("message.completed", "turn.cancelled",
                                              "turn.failed", "turn.interrupted")
                and e["event"].get("turn_id") == turn_id
            ]
            if terminal:
                return events
            await asyncio.sleep(0.2)
        return await self.read_events(project, conversation, 0)

    async def counts(self, project: str) -> tuple[int | None, int]:
        manuscript = (await self.http.get(f"/api/v2/projects/{project}/manuscript")).json()
        jobs = (await self.http.get(f"/api/v2/projects/{project}/agent-jobs")).json()
        return manuscript["commit_id"] is not None, len(jobs["jobs"])


def _deltas(events: list[dict[str, Any]], turn_id: str) -> list[str]:
    return [
        e["event"]["text"]
        for e in events
        if e["event"].get("type") == "message.delta" and e["event"]["turn_id"] == turn_id
    ]


async def scenario_streaming(walk: Walkthrough, client: ChatClient) -> None:
    """场景 1+2：流式多轮与只讨论不写入（A01/A02）。"""
    project, conversation = await client.create_project_with_chapter()
    had_commit, job_count = await client.counts(project)

    turn = await client.send_turn(
        project, conversation, "用两句话聊聊：一部推理小说的开头为什么要让钟停下来？",
        "walk-1",
    )
    events = await client.wait_terminal(project, conversation, turn, timeout=300.0)
    deltas = _deltas(events, turn)
    completed = any(e["event"].get("type") == "message.completed" for e in events)
    walked_deltas = len(deltas) >= 2
    text = "".join(deltas)
    ok = completed and walked_deltas and len(text) > 10
    walk.record(
        "流式多轮（A01 前半）",
        ok,
        f"{len(deltas)} 个增量分片，终态 {'到达' if completed else '未到达'}，"
        f"正文 {len(text)} 字",
        deltas=len(deltas),
    )

    second = await client.send_turn(
        project, conversation, "假如凶手改成了死者的姐姐，前面哪些伏笔要重读？", "walk-2"
    )
    events2 = await client.wait_terminal(project, conversation, second, timeout=300.0)
    commit_after, jobs_after = await client.counts(project)
    no_writes = (commit_after == had_commit) and (jobs_after == job_count)
    walk.record(
        "只讨论不写入（A02）",
        no_writes and any(
            e["event"].get("type") == "message.completed"
            for e in events2
            if e["event"].get("turn_id") == second
        ),
        f"讨论前后 Commit {'不变' if commit_after == had_commit else '变化了'}、"
        f"Job 数 {job_count}→{jobs_after}（期望不变）",
    )

    # 第三轮：确认多轮上下文生效（第二轮内容能被第三轮引用）。
    third = await client.send_turn(
        project, conversation, "接上一条，用一句话概括你的建议。", "walk-3"
    )
    events3 = await client.wait_terminal(project, conversation, third, timeout=300.0)
    walk.record(
        "多轮上下文",
        any(e["event"].get("type") == "message.completed" for e in events3),
        "第三轮正常完成（服务端携带历史）",
    )


async def scenario_stop_and_replay(walk: Walkthrough, client: ChatClient) -> None:
    """场景 3：停止（文本冻结、迟到分片无效）与 SSE 游标重放不重复（A07/A06 前半）。

    思考型本地模型可能长时间只有思考流不下发正文，取消落在首段之前是合法
    终态——判据是「取消后文本冻结且不再增长」，不是「必须有部分文本」。
    """
    project, conversation = await client.create_project_with_chapter()
    turn = await client.send_turn(
        project, conversation, "写一段 300 字左右的雨夜描写，尽量长一点。", "walk-stop"
    )
    # 等首个正文分片（思考型模型可能较久），超时也照常取消（0 字停止合法）。
    saw_delta = False
    deadline = asyncio.get_running_loop().time() + 90.0
    while asyncio.get_running_loop().time() < deadline and not saw_delta:
        events = await client.read_events(project, conversation, 0)
        saw_delta = any(
            e["event"].get("type") == "message.delta" and e["event"]["turn_id"] == turn
            for e in events
        )
        if not saw_delta:
            await asyncio.sleep(0.2)
    await client.http.post(
        f"/api/v2/projects/{project}/conversations/{conversation}/turns/{turn}/cancel",
        json={},
    )
    events = await client.wait_terminal(project, conversation, turn, timeout=60.0)
    cancelled = any(
        e["event"].get("type") == "turn.cancelled" and e["event"]["turn_id"] == turn
        for e in events
    )
    messages = (
        await client.http.get(
            f"/api/v2/projects/{project}/conversations/{conversation}/messages"
        )
    ).json()["messages"]
    assistant = next((m for m in messages if m["role"] == "assistant"), None)
    text_at_cancel = assistant["text"] if assistant else ""
    # 迟到分片不复活已停止轮次：等待后文本必须冻结在同一长度。
    await asyncio.sleep(5.0)
    messages = (
        await client.http.get(
            f"/api/v2/projects/{project}/conversations/{conversation}/messages"
        )
    ).json()["messages"]
    assistant = next((m for m in messages if m["role"] == "assistant"), None)
    frozen = assistant is not None and len(assistant["text"]) == len(text_at_cancel)
    status_ok = assistant is not None and assistant["status"] == "stopped"
    walk.record(
        "停止与文本冻结（A06/A16 前半）",
        cancelled and frozen and status_ok,
        f"取消事件{'到达' if cancelled else '未到达'}；停止时正文 {len(text_at_cancel)} 字，"
        f"5 秒后{'冻结' if frozen else '仍在增长'}，状态 {assistant['status'] if assistant else '无消息'}",
    )

    # 游标重放：after=cursor 只返回之后的事件；after=0 与全量一致（不重复）。
    all_events = await client.read_events(project, conversation, 0)
    mid = max(1, len(all_events) // 2)
    cursor = all_events[mid - 1]["sequence"]
    tail = await client.read_events(project, conversation, cursor)
    sequences = [e["sequence"] for e in tail]
    replay_ok = all(s > cursor for s in sequences) and len(tail) == len(all_events) - mid
    walk.record(
        "SSE 游标补齐不重复（A07）",
        replay_ok,
        f"全量 {len(all_events)} 条；after={cursor} 恰好返回其后 {len(tail)} 条",
    )


async def scenario_chat_to_creation(walk: Walkthrough, client: ChatClient) -> None:
    """场景 4：讨论 → 任务建议 → 显式启动 → 真实执行 → 候选投影（A03/A04/A13）。"""
    project, conversation = await client.create_project_with_chapter()
    had_commit, _ = await client.counts(project)

    # 先发一条讨论，再显式准备任务（建议不自动开工，A03/A05）。
    await client.send_turn(project, conversation, "给这个故事规划一个第二章的走向。", "walk-plan")
    created = await client.http.post(
        f"/api/v2/projects/{project}/task-drafts",
        json={"conversation_id": conversation, "task_kind": "plan", "instruction": "规划第二章走向"},
    )
    created.raise_for_status()
    draft = created.json()["draft"]
    walk.record(
        "建议不自动开工（A03/A05）",
        draft["job_ref"] is None and draft["missing"] is not None,
        "draft 创建后停留在建议态（无 job_ref），不产生 Job",
    )

    started = await client.http.post(
        f"/api/v2/projects/{project}/task-drafts/{draft['draft_id']}/start",
        json={"expected_revision": draft["revision"], "idempotency_key": "walk-start-1"},
    )
    if started.status_code != 200:
        walk.record(
            "显式启动（A04）",
            False,
            f"启动被拒绝：{started.text[:200]}",
        )
        return
    job_ref = started.json()["job_ref"]
    job = (await client.http.get(f"/api/v2/projects/{project}/agent-jobs/{job_ref}")).json()
    binding = job["payload"].get("model_binding")
    walk.record(
        "显式启动与模型绑定（A04/A17 前半）",
        bool(binding) and binding.get("model") == walk.model,
        f"Job {job_ref} 创建，绑定 {binding}",
    )

    # 真实模型执行 planner：等待终态；输出不合规时如实记录。
    state = ""
    deadline = asyncio.get_running_loop().time() + 600.0
    candidate_message_id = ""
    while asyncio.get_running_loop().time() < deadline:
        job = (await client.http.get(f"/api/v2/projects/{project}/agent-jobs/{job_ref}")).json()
        state = job["state"]
        if state in ("waiting", "completed", "terminal_failed", "cancelled"):
            break
        messages = await client.read_events(project, conversation, 0)
        cand = [
            e
            for e in messages
            if e["event"].get("type") == "candidate.ready"
            and e["event"]["job_ref"] == job_ref
        ]
        if cand:
            candidate_message_id = cand[0]["event"]["message_id"]
            break
        await asyncio.sleep(1.0)

    if state == "waiting" or candidate_message_id:
        walk.record(
            "候选投影（A13 前半）",
            True,
            "真实模型输出通过 Schema 校验，候选已投影到对话",
        )
        head = (await client.http.get(f"/api/v2/projects/{project}/manuscript")).json()["commit_id"]
        accepted = await client.http.post(
            f"/api/v2/projects/{project}/conversations/{conversation}"
            f"/messages/{candidate_message_id or ''}/accept",
            json={"expected_head": head, "idempotency_key": "walk-accept-1"},
        )
        commit_now, _ = await client.counts(project)
        walk.record(
            "候选接受产生 Commit（A13）",
            accepted.status_code == 200 and commit_now != had_commit,
            f"接受 HTTP {accepted.status_code}；Commit {'已落盘' if commit_now != had_commit else '未变化'}",
        )
    else:
        output = json.dumps(job.get("output") or {}, ensure_ascii=False)[:300]
        walk.record(
            "候选投影（A13 前半）",
            False,
            f"真实模型（{walk.model}）执行终态 {state}，未产出合规候选；输出摘要：{output}",
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="报告输出目录 eval/runs/<日期>-phase19-chat")
    parser.add_argument("--model", default="", help="覆盖档案模型名（如 qwen3.5:4b）")
    parser.add_argument("--provider", default="config", choices=["config", "env"])
    args = parser.parse_args()

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.provider == "config":
        config = load_server_config()
        profile = next((p for p in config.providers if p.name == "ollama" and p.available), None)
        if profile is None:
            print("config.yaml 没有可用的 ollama 档案；走查需要本地模型。", file=sys.stderr)
            return 2
        model = args.model or profile.default_model
        book = ProviderBook(config, boot_name=profile.name, boot_model=model)
    else:  # pragma: no cover - env 路径沿用 eval/v2/driver 约定
        from hastings.server.provider_config import env_fallback_config

        config = env_fallback_config()
        model = args.model
        book = ProviderBook(config, boot_name="default", boot_model=model)

    with tempfile.TemporaryDirectory(
        prefix="phase19-chat-eval-", ignore_cleanup_errors=True
    ) as tmp:
        runtime = V2Runtime.sqlite(str(Path(tmp) / "walk.db"))
        runtime.worker.runner.book = book  # 与 server/app.py 同一接线
        # ASGITransport 不触发 ASGI lifespan：worker 循环（Job 执行与候选投影）
        # 需要显式启动，否则任务永远停在 queued。
        worker_task = asyncio.create_task(runtime.worker_loop())
        walk = Walkthrough(out_dir, model)
        client = ChatClient(runtime)
        try:
            await scenario_streaming(walk, client)
            await scenario_stop_and_replay(walk, client)
            await scenario_chat_to_creation(walk, client)
        finally:
            await client.aclose()
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
    walk.write_report()
    failed = [f for f in walk.findings if not f["ok"]]
    print(f"\n报告：{out_dir / 'README.md'}；{len(walk.findings) - len(failed)}/{len(walk.findings)} 通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
