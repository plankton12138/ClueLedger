"""V03-8.7 0.3 真模型走查驱动（eval/v2/desk_driver.py）。

进程内启动 V2 应用（隔离 SQLite）+ 真实 Provider（默认 config.yaml 的本地
Ollama 档案），经 HTTP 走查 0.3 的真模型路径与证据分层：

S1 讨论流式：增量分片、终态到达、**不产生 Job**（讨论无写权限）；
S2 试写候选 → 采用：真实模型产出候选（预览哈希），采用走统一发布产生 Commit；
S3 修订任务预算与覆盖：开任务时预算可缩小、读取与覆盖结论入库、停止留痕；
S4 环境失败记录：不存在的模型必须落成真实失败（不静默、不伪成功）。

证据分层（计划 §9.1、执行指导 §2.2）：
- 真模型证据只来自本驱动（真实 Provider 调用）；模拟/FakeProvider 证据另计；
- 模型输出不合规时如实记失败与原因，不用 FakeProvider 替代；
- S4 的环境失败是「失败留记录」的证据，不是通过项。

运行::

    uv run python eval/v2/desk_driver.py \\
        --out eval/runs/2026-10-19-v03-8.7-real-model --model qwen3:8b

报告：``<out>/README.md``（入库）；逐场景细节写 ``<out>/evidence.json``（入库）。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
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
    """收集证据行；``ok=None`` 表示「失败证据」（按设计记录，不计通过）。"""

    def __init__(self, out_dir: Path, model: str) -> None:
        self.out_dir = out_dir
        self.model = model
        self.started = datetime.now()
        self.findings: list[dict[str, Any]] = []

    def record(self, scenario: str, ok: bool | None, detail: str, **extra: Any) -> None:
        self.findings.append({"scenario": scenario, "ok": ok, "detail": detail, **extra})
        mark = "EVID" if ok is None else ("PASS" if ok else "FAIL")
        print(f"[{mark}] {scenario}: {detail}", flush=True)

    def write(self) -> None:
        passed = sum(1 for f in self.findings if f["ok"] is True)
        failed = sum(1 for f in self.findings if f["ok"] is False)
        evidence = sum(1 for f in self.findings if f["ok"] is None)
        lines = [
            "# V03-8.7 0.3 真模型走查报告",
            "",
            f"- 时间：{self.started.isoformat()}",
            f"- 模型：{self.model}（本地 Ollama，config.yaml ollama 档案）",
            "- 证据性质：真实 Provider 调用（本地、无收费 API）；模拟/FakeProvider 证据不在本表。",
            f"- 结果：通过 {passed} / 失败 {failed} / 失败证据 {evidence}"
            "（失败证据按设计记录，不计通过）。",
            "",
            "| 场景 | 结果 | 说明 |",
            "|---|---|---|",
        ]
        for f in self.findings:
            mark = "通过" if f["ok"] is True else ("失败" if f["ok"] is False else "失败证据")
            lines.append(f"| {f['scenario']} | {mark} | {f['detail']} |")
        (self.out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (self.out_dir / "evidence.json").write_text(
            json.dumps(
                {
                    "started": self.started.isoformat(),
                    "model": self.model,
                    "findings": self.findings,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


class Client:
    """进程内 ASGI 客户端：与生产同一 FastAPI 应用，不经网络栈。"""

    def __init__(self, runtime: V2Runtime) -> None:
        self.runtime = runtime
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_v2_app(runtime)),
            base_url="http://walkthrough.local",
            timeout=httpx.Timeout(900.0),
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def create_project(self, name: str = "8.7 走查") -> str:
        return (await self.http.post("/api/v2/projects", json={"name": name})).json()["project_id"]

    async def import_chapter(self, project: str, title: str, text: str, key: str) -> dict[str, Any]:
        response = await self.http.post(
            f"/api/v2/projects/{project}/chapters/import",
            json={"idempotency_key": key, "title": title, "text": text},
        )
        response.raise_for_status()
        return response.json()

    async def turn(self, project: str, conversation: str, text: str, key: str) -> str:
        response = await self.http.post(
            f"/api/v2/projects/{project}/conversations/{conversation}/turns",
            json={"text": text, "refs": [], "idempotency_key": key},
        )
        response.raise_for_status()
        return str(response.json()["turn"]["turn_id"])

    async def events(self, project: str, conversation: str) -> list[dict[str, Any]]:
        """SSE 快照流（tail=0）：返回当前积压后服务端关闭。"""
        rows: list[dict[str, Any]] = []
        async with self.http.stream(
            "GET",
            f"/api/v2/projects/{project}/conversations/{conversation}/events?after=0&tail=0",
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    rows.append(json.loads(line.removeprefix("data: ")))
        return rows

    async def wait_terminal(
        self, project: str, conversation: str, turn_id: str, timeout: float
    ) -> list[dict[str, Any]]:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            rows = await self.events(project, conversation)
            terminal = [
                e
                for e in rows
                if e["event"].get("type")
                in ("message.completed", "turn.cancelled", "turn.failed", "turn.interrupted")
                and e["event"].get("turn_id") == turn_id
            ]
            if terminal:
                return rows
            await asyncio.sleep(0.3)
        return await self.events(project, conversation)

    async def job_count(self, project: str) -> int:
        jobs = (await self.http.get(f"/api/v2/projects/{project}/agent-jobs")).json()
        return len(jobs["jobs"])


def _deltas(rows: list[dict[str, Any]], turn_id: str) -> list[str]:
    return [
        e["event"]["text"]
        for e in rows
        if e["event"].get("type") == "message.delta" and e["event"]["turn_id"] == turn_id
    ]


async def scenario_discussion(walk: Walkthrough, client: Client) -> tuple[str, str]:
    """S1：讨论真模型——增量、终态、无 Job。返回 conversation_id 供后续复用。"""
    project = await client.create_project()
    await client.import_chapter(
        project, "第一章 雨夜", "雨下了一整夜。艾琳把钥匙推过柜台。", "imp-1"
    )
    conversation = (
        await client.http.post(f"/api/v2/projects/{project}/conversations", json={})
    ).json()["conversation_id"]
    before_jobs = await client.job_count(project)
    turn = await client.turn(
        project, conversation, "用一句半话说说：开篇让雨停不下来有什么作用？", "walk-s1"
    )
    rows = await client.wait_terminal(project, conversation, turn, timeout=600.0)
    deltas = _deltas(rows, turn)
    text = "".join(deltas)
    completed = any(
        e["event"].get("type") == "message.completed" and e["event"]["turn_id"] == turn
        for e in rows
    )
    after_jobs = await client.job_count(project)
    walk.record(
        "S1 讨论流式（真模型）",
        completed and len(deltas) >= 2 and len(text) > 5 and after_jobs == before_jobs,
        f"{len(deltas)} 个增量、{len(text)} 字、终态{'到达' if completed else '未到达'}；"
        f"Job {before_jobs}→{after_jobs}（讨论无写权限，期望不变）",
        deltas=len(deltas),
        chars=len(text),
        sample=text[:120],
    )
    return project, conversation


async def scenario_draft_adopt(walk: Walkthrough, client: Client, project: str) -> None:
    """S2：真实模型试写候选 → 按预览哈希采用 → 统一发布产生 Commit。"""
    head_before = (await client.http.get(f"/api/v2/projects/{project}/manuscript")).json()[
        "commit_id"
    ]
    created = await client.http.post(
        f"/api/v2/projects/{project}/drafts",
        json={
            "target": "new",
            "chapter_title": "第二章 潮汐时刻表",
            "instruction": "写第二章开头约 200 字：潮汐时刻表上多了一行新墨迹。",
            "idempotency_key": "walk-draft-1",
        },
    )
    if created.status_code != 200:
        walk.record("S2 试写候选（真模型）", False, f"试写请求失败：{created.text[:300]}")
        return
    candidate = created.json()
    candidate_id = candidate.get("candidate_id") or candidate.get("id")
    detail = (await client.http.get(f"/api/v2/projects/{project}/drafts/{candidate_id}")).json()
    chosen = detail.get("candidate") or detail
    preview_hash = chosen.get("preview_hash")
    revision = chosen.get("revision")
    text = chosen.get("text") or ""
    walk.record(
        "S2.1 试写候选（真模型）",
        bool(preview_hash) and len(text) > 50,
        f"候选 {candidate_id}：{len(text)} 字，预览哈希 {str(preview_hash)[:12]}…",
        candidate_id=candidate_id,
        chars=len(text),
        sample=text[:160],
    )
    adopted = await client.http.post(
        f"/api/v2/projects/{project}/drafts/{candidate_id}/adopt",
        json={
            "preview_hash": preview_hash,
            "revision": revision,
            "idempotency_key": "walk-adopt-1",
        },
    )
    head_after = (await client.http.get(f"/api/v2/projects/{project}/manuscript")).json()[
        "commit_id"
    ]
    body = adopted.json() if adopted.status_code == 200 else {}
    walk.record(
        "S2.2 采用产生 Commit（统一发布）",
        adopted.status_code == 200 and head_after != head_before,
        f"采用 HTTP {adopted.status_code}；operation {(body.get('operation_id') or '')[:18]}…；"
        f"Commit {'已落盘' if head_after != head_before else '未变化'}",
        status=adopted.status_code,
        operation_id=body.get("operation_id"),
    )


async def scenario_revision_budget_coverage(
    walk: Walkthrough, client: Client, project: str
) -> None:
    """S3：修订任务预算可缩小、覆盖台账可回读、停止留痕（不调用模型）。"""
    jobs_before = await client.job_count(project)
    started = await client.http.post(
        f"/api/v2/projects/{project}/revision-tasks",
        json={"request_id": "walk-task-1", "call_budget": 1},
    )
    if started.status_code != 200:
        walk.record("S3.1 开修订任务", False, f"开任务失败：{started.text[:300]}")
        return
    task = started.json()
    task_id = task.get("task_id")
    budget = task.get("budget") or {}
    # 预算口径（V03-7.3）：call_limit 是本次上限，remaining 是可用次数；
    # 开任务不消耗调用（calls_used=0），同作品同时只允许一个进行中任务。
    walk.record(
        "S3.1 预算可缩小（开任务不调用模型）",
        budget.get("call_limit") == 1
        and budget.get("calls_used") == 0
        and budget.get("remaining") == 1
        and (await client.job_count(project)) == jobs_before,
        f"任务 {task_id}：call_limit={budget.get('call_limit')}、"
        f"calls_used={budget.get('calls_used')}、remaining={budget.get('remaining')}；"
        f"Job 数不变（开任务零模型调用）",
        budget=budget,
    )

    chapters = (await client.http.get(f"/api/v2/projects/{project}/manuscript")).json()["chapters"]
    # 全书覆盖：每章都登记摘要读取并下结论（试写采用的第二章也在内）。
    statuses: list[int] = []
    for chapter in chapters:
        chapter_id = chapter["chapter_id"]
        read = await client.http.post(
            f"/api/v2/projects/{project}/revision-work/reads",
            json={"chapter_id": chapter_id, "kind": "summary"},
        )
        covered = await client.http.post(
            f"/api/v2/projects/{project}/revision-work/coverage/{chapter_id}",
            json={"action": "changed", "reason": "试写采用后需要重读本章"},
        )
        statuses.extend([read.status_code, covered.status_code])
    ledger = (await client.http.get(f"/api/v2/projects/{project}/revision-work")).json()
    # 覆盖口径（V03-7.2）：每章一个 status（read/changed/confirmed_unchanged/blocked），
    # complete 只在全部章节下过结论后为真。
    coverage = {row.get("chapter_id"): row.get("status") for row in ledger.get("coverage", [])}
    walk.record(
        "S3.2 覆盖台账（读取登记 + 结论）",
        statuses and all(code == 200 for code in statuses) and bool(ledger.get("complete")),
        f"{len(chapters)} 章登记读取与结论（HTTP {set(statuses)}）；"
        f"覆盖 {ledger.get('covered_count')}/{ledger.get('total_count')}、"
        f"complete={ledger.get('complete')}；状态 {sorted(set(coverage.values()))}",
        coverage=coverage,
    )

    stopped = await client.http.post(f"/api/v2/projects/{project}/revision-tasks/{task_id}/stop")
    view = (await client.http.get(f"/api/v2/projects/{project}/revision-tasks/{task_id}")).json()
    walk.record(
        "S3.3 停止留痕",
        stopped.status_code == 200 and view.get("state") == "cancelled",
        f"停止 HTTP {stopped.status_code}；任务状态 {view.get('state')}",
    )


async def scenario_environment_failure(walk: Walkthrough) -> None:
    """S4：不存在的模型 → 真实失败留记录（失败证据，不计通过）。"""
    with tempfile.TemporaryDirectory(prefix="v0387-fail-", ignore_cleanup_errors=True) as tmp:
        runtime = V2Runtime.sqlite(str(Path(tmp) / "fail.db"))
        config = load_server_config()
        profile = next((p for p in config.providers if p.name == "ollama" and p.available), None)
        if profile is None:
            walk.record(
                "S4 环境失败记录",
                None,
                "config.yaml 无可用 ollama 档案，无法构造失败证据",
                skipped=True,
            )
            return
        book = ProviderBook(config, boot_name=profile.name, boot_model="model-not-installed:v9")
        runtime.worker.runner.book = book
        worker_task = asyncio.create_task(runtime.worker_loop())
        client = Client(runtime)
        try:
            project = await client.create_project("8.7 失败证据")
            await client.import_chapter(project, "第一章", "钟停了。", "fail-imp")
            conversation = (
                await client.http.post(f"/api/v2/projects/{project}/conversations", json={})
            ).json()["conversation_id"]
            turn = await client.turn(project, conversation, "随便说一句。", "walk-fail-1")
            rows = await client.wait_terminal(project, conversation, turn, timeout=120.0)
            failed = [
                e
                for e in rows
                if e["event"].get("type") == "turn.failed" and e["event"]["turn_id"] == turn
            ]
            detail = failed[0]["event"].get("error") if failed else None
            walk.record(
                "S4 环境失败记录（不存在的模型）",
                None,
                ("轮次终态 turn.failed" if failed else "轮次未失败（异常！）")
                + f"；错误：{str(detail)[:220]}",
                failed=bool(failed),
                error=detail,
            )
        finally:
            await client.aclose()
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="报告目录 eval/runs/<日期>-v03-8.7")
    parser.add_argument("--model", default="", help="覆盖档案模型名（如 qwen3:8b）")
    args = parser.parse_args()

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    config = load_server_config()
    profile = next((p for p in config.providers if p.name == "ollama" and p.available), None)
    if profile is None:
        print("config.yaml 没有可用的 ollama 档案；真模型走查需要本地模型。", file=sys.stderr)
        return 2
    model = args.model or profile.default_model
    book = ProviderBook(config, boot_name=profile.name, boot_model=model)

    with tempfile.TemporaryDirectory(prefix="v0387-walk-", ignore_cleanup_errors=True) as tmp:
        runtime = V2Runtime.sqlite(str(Path(tmp) / "walk.db"))
        runtime.worker.runner.book = book
        worker_task = asyncio.create_task(runtime.worker_loop())
        walk = Walkthrough(out_dir, model)
        client = Client(runtime)
        try:
            project, _conversation = await scenario_discussion(walk, client)
            await scenario_draft_adopt(walk, client, project)
            await scenario_revision_budget_coverage(walk, client, project)
        finally:
            await client.aclose()
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
    await scenario_environment_failure(walk)

    walk.write()
    failed = [f for f in walk.findings if f["ok"] is False]
    print(f"\n报告：{out_dir / 'README.md'}；失败 {len(failed)} 项（失败证据不计通过）")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
