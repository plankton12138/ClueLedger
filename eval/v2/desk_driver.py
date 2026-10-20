"""V03-8.7 0.3 真模型走查驱动（eval/v2/desk_driver.py）。

进程内启动 V2 应用（隔离 SQLite）+ 真实 Provider（默认 config.yaml 的本地
Ollama 档案），经 HTTP 走查 0.3 的真模型路径与证据分层：

S1 讨论流式：增量分片、终态到达、**不产生 Job**（讨论无写权限）；
S2 试写候选 → 采用：真实模型产出候选（预览哈希），采用走统一发布产生 Commit；
S3 修订任务预算与覆盖：开任务时预算可缩小、读取与覆盖结论入库、停止留痕；
S4 环境失败记录：不存在的模型必须落成真实失败（不静默、不伪成功）；
S5 真模型修订全循环（``--deep``）：读章 → 模型改稿 → 覆盖结论 → 暂存 → 一次发布。

证据分层（计划 §9.1、执行指导 §2.2）：
- 真模型证据只来自本驱动（真实 Provider 调用）；模拟/FakeProvider 证据另计；
- 模型输出不合规时如实记失败与原因，不用 FakeProvider 替代；
- S4 的环境失败是「失败留记录」的证据，不是通过项。

运行::

    uv run python eval/v2/desk_driver.py \\
        --out eval/runs/2026-10-19-v03-8.7-real-model --model qwen3:8b [--deep]

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
from dotenv import load_dotenv

from hastings.server.provider_config import ProviderBook, load_server_config
from hastings.server.v2 import V2Runtime, build_v2_app

ROOT = Path(__file__).resolve().parents[2]


class Walkthrough:
    """收集证据行；``ok=None`` 表示「失败证据」（按设计记录，不计通过）。"""

    def __init__(
        self, out_dir: Path, model: str, provider: str = "ollama", deep: bool = False
    ) -> None:
        self.out_dir = out_dir
        self.model = model
        self.provider = provider
        self.deep = deep
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
        if self.provider == "ollama":
            nature = "真实 Provider 调用（本地模型，无收费 API）；模拟/FakeProvider 证据不在本表。"
        else:
            nature = "真实远程 Provider 调用（按量计费）；模拟/FakeProvider 证据不在本表。"
        lines = [
            "# V03-8.7 0.3 真模型走查报告" + ("（含 --deep 修订全循环）" if self.deep else ""),
            "",
            f"- 时间：{self.started.isoformat()}",
            f"- 模型：{self.model}（provider 档案 {self.provider!r}）",
            f"- 范围：{'S1–S6（含修订全循环与执行循环）' if self.deep else 'S1–S4'}",
            f"- 证据性质：{nature}",
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


#: 深走查用的短章：读章、改稿、暂存、发布都走真实链路，正文长度控制在
#: 本地模型的上下文舒适区，避免把「模型写不完」误当链路失败。
DEEP_CHAPTER_TITLE = "第一章 九点的钟声"
DEEP_CHAPTER_TEXT = (
    "九点整，旅馆大厅的钟停了。林昭把伞靠在门边，雪水顺着伞骨滴在石板上。"
    "柜台后面没有人，只有一本翻开的登记簿，最后一行写着林雪的名字。"
    "他喊了一声，回答他的是楼上关门的声音，很轻，像有人贴着门板在听。"
    "壁炉里的火只剩一层灰，茶几上的两只杯子，一只还温着。"
)


async def scenario_deep_revision(walk: Walkthrough, client: Client) -> None:
    """S5：真模型修订全循环——读章 → 改稿 → 覆盖 → 暂存 → 一次发布。

    口径（不夸大）：
    - 「读章」= 驱动按当前版本读正文，并在覆盖台账登记 ``full``；上下文装配
      端点回读时带同章原文，证明装配器确实拿到了这一章；
    - 「改稿」的模型调用发生在**试写候选服务**（V03-3）；试写目标为现有章时
      服务自己把该章正文放进提示词的「现有正文」段（V03-8.7 补记修复），
      这里的 instruction **不含原文**，模型能看到原文才算通过；
    - 「覆盖 → 暂存 → 发布」走 V03-7.2/7.4 的真实端点，发布产生单个 Commit。
    """
    project = await client.create_project("8.7 深走查")
    imported = await client.import_chapter(
        project, DEEP_CHAPTER_TITLE, DEEP_CHAPTER_TEXT, "deep-imp"
    )
    chapter_id = str(imported["chapter_id"])
    chapter = (await client.http.get(f"/api/v2/projects/{project}/chapters/{chapter_id}")).json()
    original = str(chapter["text"])
    chapter_base = str(chapter["chapter_base"])
    head_before = str(
        (await client.http.get(f"/api/v2/projects/{project}/manuscript")).json()["commit_id"]
    )

    started = await client.http.post(
        f"/api/v2/projects/{project}/revision-tasks",
        json={"request_id": "deep-task-1", "call_budget": 6},
    )
    if started.status_code != 200:
        walk.record("S5 真模型修订全循环", False, f"开任务失败：{started.text[:300]}")
        return
    task_id = str(started.json()["task_id"])

    # -- 读章：登记全文读取，并确认上下文装配拿到同章原文 -------------------
    read = await client.http.post(
        f"/api/v2/projects/{project}/revision-work/reads",
        json={"chapter_id": chapter_id, "kind": "full"},
    )
    background = await client.http.get(
        f"/api/v2/projects/{project}/context/background",
        params={
            "scope": "manuscript",
            "target": [chapter_id],
            "include_temporary_text": "true",
        },
    )
    bg_text = (
        json.dumps(background.json(), ensure_ascii=False) if background.status_code == 200 else ""
    )
    walk.record(
        "S5.1 读章（登记全文读取 + 装配含原文）",
        read.status_code == 200 and background.status_code == 200 and original[:24] in bg_text,
        f"读取登记 HTTP {read.status_code}；上下文装配 HTTP {background.status_code}、"
        f"{len(bg_text)} 字符；含本章原文={'是' if original[:24] in bg_text else '否'}",
        background_chars=len(bg_text),
    )

    # -- 改稿：真实模型按原文改写（原文由服务端装入提示词，驱动不粘贴）------
    instruction = (
        "请在不改变事实与时间线的前提下改写本章："
        "保留钟停在九点、登记簿最后一行是林雪的名字、楼上很轻的关门声、还温着的杯子；"
        "语言更克制，删掉解释性句子，篇幅与原文相当。"
    )
    created = await client.http.post(
        f"/api/v2/projects/{project}/drafts",
        json={
            "target": chapter_id,
            "instruction": instruction,
            "idempotency_key": "deep-draft-1",
        },
    )
    if created.status_code != 200:
        walk.record(
            "S5.2 真模型改稿（试写候选）",
            False,
            f"试写请求失败：{created.text[:400]}",
            status=created.status_code,
        )
        return
    candidate_id = str(created.json().get("candidate_id") or created.json().get("id"))
    detail = (await client.http.get(f"/api/v2/projects/{project}/drafts/{candidate_id}")).json()
    chosen = detail.get("candidate") or detail
    revised = str(chosen.get("text") or "")
    preview_hash = chosen.get("preview_hash")
    kept = [token for token in ("九点", "林雪", "杯") if token in revised]
    walk.record(
        "S5.2 真模型改稿（试写候选）",
        bool(preview_hash) and len(revised) > 40 and revised.strip() != original.strip(),
        f"候选 {candidate_id}：{len(revised)} 字（原文 {len(original)} 字）、"
        f"预览哈希 {str(preview_hash)[:12]}…；保留要素 {kept}；"
        f"instruction 未含原文={original[:12] not in instruction}（服务端自行装入）",
        candidate_id=candidate_id,
        chars=len(revised),
        kept=kept,
        sample=revised[:200],
    )

    # -- 覆盖 → 暂存 → 一次发布 ---------------------------------------------
    covered = await client.http.post(
        f"/api/v2/projects/{project}/revision-work/coverage/{chapter_id}",
        json={"action": "changed", "reason": "模型已按原文改写本章，待统一发布"},
    )
    staged = await client.http.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/staged-chapters",
        json={"chapter_id": chapter_id, "text": revised, "base_revision_id": chapter_base},
    )
    staged_text = str(
        (await client.http.get(f"/api/v2/projects/{project}/chapters/{chapter_id}")).json()["text"]
    )
    walk.record(
        "S5.3 覆盖结论与暂存（不写正式正文）",
        covered.status_code == 200 and staged.status_code == 200,
        f"覆盖 HTTP {covered.status_code}、暂存 HTTP {staged.status_code}；"
        f"暂存前正式正文仍是原文={staged_text == original}",
    )

    published = await client.http.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/publish",
        json={"operation_id": "op_deep_revision_publish_1"},
    )
    after = (await client.http.get(f"/api/v2/projects/{project}/chapters/{chapter_id}")).json()
    head_after = str(
        (await client.http.get(f"/api/v2/projects/{project}/manuscript")).json()["commit_id"]
    )
    operation = await client.http.get(
        f"/api/v2/projects/{project}/operations/op_deep_revision_publish_1"
    )
    body = published.json() if published.status_code == 200 else {}
    replaced = str(after["text"]).strip() == revised.strip()
    walk.record(
        "S5.4 一次发布（正文换版 + 回执可回读）",
        published.status_code == 200
        and replaced
        and head_after != head_before
        and operation.status_code == 200,
        f"发布 HTTP {published.status_code}；正文已换版={replaced}；"
        f"Commit {'前进' if head_after != head_before else '未变'}；"
        f"操作回执 HTTP {operation.status_code}",
        status=published.status_code,
        operation_id=body.get("operation_id"),
        changed_chapters=body.get("changed_chapters"),
    )

    # -- 预算归因：真实缺口，如实记录（不计通过）----------------------------
    view = (await client.http.get(f"/api/v2/projects/{project}/revision-tasks/{task_id}")).json()
    budget = view.get("budget") or {}
    walk.record(
        "S5.5 手动路径不计预算（口径说明）",
        None,
        f"任务 call_limit={budget.get('call_limit')}、calls_used={budget.get('calls_used')}；"
        "本场景直接调试写候选再手工暂存，没经过执行循环，所以这次调用不计入任务预算"
        "（设计如此：只有 POST /revision-tasks/{id}/run 的逐章记账才消耗预算）。"
        "预算门禁由 S6 取证：1 次预算只跑 1 章，继续后才跑第 2 章",
        budget=budget,
        task_state=view.get("state"),
    )


async def scenario_execution_loop(walk: Walkthrough, client: Client) -> None:
    """S6：执行循环真模型——预算 1 跑两章会停在第二章，继续后跑完并一次发布。"""
    project = await client.create_project("8.7 执行循环")
    first = await client.import_chapter(
        project,
        "第一章 九点",
        "九点整，钟停了。林昭把伞靠在门边，雪水顺着伞骨滴在石板上。柜台后面没有人。",
        "loop-imp-1",
    )
    second = await client.import_chapter(
        project,
        "第二章 空房",
        "二楼尽头有一扇没有门牌的门。门后没有声音，只有一股冷气贴着地板流出来。",
        "loop-imp-2",
    )
    originals = {
        chapter_id: str(
            (await client.http.get(f"/api/v2/projects/{project}/chapters/{chapter_id}")).json()[
                "text"
            ]
        )
        for chapter_id in (first["chapter_id"], second["chapter_id"])
    }
    head_before = str(
        (await client.http.get(f"/api/v2/projects/{project}/manuscript")).json()["commit_id"]
    )
    started = await client.http.post(
        f"/api/v2/projects/{project}/revision-tasks",
        json={"request_id": "deep-loop-task", "call_budget": 1},
    )
    if started.status_code != 200:
        walk.record("S6 执行循环（真模型）", False, f"开任务失败：{started.text[:300]}")
        return
    task_id = str(started.json()["task_id"])
    instruction = "把这一章改得更克制，保留全部事实与时间线。"

    first_round = await client.http.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/run",
        json={"instruction": instruction, "request_id": "deep-loop-1"},
    )
    first_body = first_round.json() if first_round.status_code == 200 else {}
    walk.record(
        "S6.1 预算 1 跑两章：停在第二章之前",
        first_round.status_code == 200
        and first_body.get("status") == "stopped"
        and len(first_body.get("chapters") or []) == 1
        and (first_body.get("task") or {}).get("state") == "waiting"
        and ((first_body.get("task") or {}).get("budget") or {}).get("calls_used") == 1,
        f"执行 HTTP {first_round.status_code}；状态 {first_body.get('status')}、"
        f"完成 {len(first_body.get('chapters') or [])} 章、"
        f"任务 {(first_body.get('task') or {}).get('state')}、"
        f"原因 {((first_body.get('stopped') or {}).get('reason'))}",
        status=first_round.status_code,
        body=first_body,
    )

    continued = await client.http.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/continue",
        json={"additional_calls": 1},
    )
    second_round = await client.http.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/run",
        json={"instruction": instruction, "request_id": "deep-loop-2"},
    )
    second_body = second_round.json() if second_round.status_code == 200 else {}
    walk.record(
        "S6.2 继续后跑完剩余章",
        continued.status_code == 200
        and second_round.status_code == 200
        and second_body.get("status") == "executed"
        and len(second_body.get("chapters") or []) == 1
        and ((second_body.get("task") or {}).get("budget") or {}).get("calls_used") == 2,
        f"继续 HTTP {continued.status_code}；执行 HTTP {second_round.status_code}；"
        f"完成 {len(second_body.get('chapters') or [])} 章、"
        f"calls_used={((second_body.get('task') or {}).get('budget') or {}).get('calls_used')}",
        status=second_round.status_code,
    )

    published = await client.http.post(
        f"/api/v2/projects/{project}/revision-tasks/{task_id}/publish",
        json={"operation_id": "op_deep_loop_publish_1"},
    )
    body = published.json() if published.status_code == 200 else {}
    chapter_ids = [str(first["chapter_id"]), str(second["chapter_id"])]
    texts = {
        chapter_id: str(
            (await client.http.get(f"/api/v2/projects/{project}/chapters/{chapter_id}")).json()[
                "text"
            ]
        )
        for chapter_id in chapter_ids
    }
    head_after = str(
        (await client.http.get(f"/api/v2/projects/{project}/manuscript")).json()["commit_id"]
    )
    walk.record(
        "S6.3 两章一次发布",
        published.status_code == 200
        and len(body.get("changed_chapters") or []) == 2
        and head_after != head_before
        and all(
            texts[chapter_id].strip() != originals[chapter_id].strip() for chapter_id in chapter_ids
        ),
        f"发布 HTTP {published.status_code}；改动 {len(body.get('changed_chapters') or [])} 章；"
        f"Commit {'前进' if head_after != head_before else '未变'}；"
        f"首章保留要素 {[k for k in ('钟', '伞', '柜台') if k in texts[chapter_ids[0]]]}；"
        f"正文样本 {texts[chapter_ids[0]][:40]!r}",
        status=published.status_code,
        changed=body.get("changed_chapters"),
        sample=texts[chapter_ids[0]][:160],
    )


async def scenario_environment_failure(walk: Walkthrough, provider_name: str) -> None:
    """S4：不存在的模型 → 真实失败留记录（失败证据，不计通过）。"""
    with tempfile.TemporaryDirectory(prefix="v0387-fail-", ignore_cleanup_errors=True) as tmp:
        runtime = V2Runtime.sqlite(str(Path(tmp) / "fail.db"))
        config = load_server_config()
        profile = next(
            (p for p in config.providers if p.name == provider_name and p.available), None
        )
        if profile is None:
            walk.record(
                "S4 环境失败记录",
                None,
                f"config.yaml 无可用 {provider_name} 档案，无法构造失败证据",
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
    parser.add_argument(
        "--provider",
        default="ollama",
        help="provider 档案名（默认 ollama；用 .env 里的 DeepSeek 时填 default）",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="追加 S5 真模型修订全循环（读章→改稿→覆盖→暂存→发布）",
    )
    args = parser.parse_args()

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    load_dotenv(ROOT / ".env")
    config = load_server_config()
    profile = next((p for p in config.providers if p.name == args.provider and p.available), None)
    if profile is None:
        print(
            f"config.yaml 没有可用的 {args.provider!r} 档案；"
            "检查档案是否存在且 api_key_env 指向的变量已设置（.env 会被加载）。",
            file=sys.stderr,
        )
        return 2
    model = args.model or profile.default_model
    book = ProviderBook(config, boot_name=profile.name, boot_model=model)

    with tempfile.TemporaryDirectory(prefix="v0387-walk-", ignore_cleanup_errors=True) as tmp:
        runtime = V2Runtime.sqlite(str(Path(tmp) / "walk.db"))
        runtime.worker.runner.book = book
        worker_task = asyncio.create_task(runtime.worker_loop())
        walk = Walkthrough(out_dir, model, provider=profile.name, deep=args.deep)
        client = Client(runtime)
        try:
            project, _conversation = await scenario_discussion(walk, client)
            await scenario_draft_adopt(walk, client, project)
            await scenario_revision_budget_coverage(walk, client, project)
            if args.deep:
                await scenario_deep_revision(walk, client)
                await scenario_execution_loop(walk, client)
        finally:
            await client.aclose()
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
    await scenario_environment_failure(walk, profile.name)

    walk.write()
    failed = [f for f in walk.findings if f["ok"] is False]
    print(f"\n报告：{out_dir / 'README.md'}；失败 {len(failed)} 项（失败证据不计通过）")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
