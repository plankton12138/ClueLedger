"""分角色真 Provider 走查；场景基线由作者夹具提交，不冒充模型产出。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from hastings.application.contexts import ContextBuilder
from hastings.application.workflows.manual_edit import ManualEditIntent
from hastings.domain.manuscript.models import Chapter, TextUnit
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange
from hastings.domain.story.canon.models import Character
from hastings.domain.story.narrative.models import Beat, NarrativeNode
from hastings.infrastructure.agents.tau_runner import TauAgentRunner
from hastings.server.provider_config import env_fallback_config
from hastings.server.v2 import V2Runtime
from hastings.server.v2_agents import ConfiguredV2Runner

ROOT = Path(__file__).resolve().parents[2]


def seed(runtime):
    pid = runtime.create_project("Phase16 真模型评测夹具")
    branch = runtime.service.get_branch(pid, "main")
    ids = {
        key: f"{pid.value}:{key}"
        for key in (
            "helper",
            "series",
            "book",
            "arc",
            "nchapter",
            "scene",
            "beat",
            "chapter",
            "unit",
            "outside",
        )
    }
    objects = {
        "helper": (
            "story.character",
            Character("林岚", role="protagonist", goal="帮助侦探调查").to_content(),
        ),
        "beat": (
            "narrative.beat",
            Beat(
                ids["beat"],
                parent_id=ids["scene"],
                title="怀表停走",
                purpose="递出怀表，只展示指针停在3点",
                participants=(ids["helper"],),
                constraints=(),
            ).to_content(),
        ),
        "chapter": (
            "manuscript.chapter",
            Chapter(ids["chapter"], "第一章", "1", (ids["unit"], ids["outside"])).to_content(),
        ),
        "unit": (
            "manuscript.text_unit",
            TextUnit(
                ids["unit"],
                ids["chapter"],
                "林岚非常缓慢地递出怀表。侦探早已知道林岚调慢怀表，指针停在3点。",
            ).to_content(),
        ),
        "outside": (
            "manuscript.text_unit",
            TextUnit(ids["outside"], ids["chapter"], "窗外落着细雨。").to_content(),
        ),
    }
    previous = None
    for key, kind in (
        ("series", "series"),
        ("book", "book"),
        ("arc", "arc"),
        ("nchapter", "chapter"),
        ("scene", "scene"),
    ):
        objects[key] = (
            f"narrative.{kind}",
            NarrativeNode(
                ids[key], f"narrative.{kind}", title=key, parent_id=previous
            ).to_content(),
        )
        previous = ids[key]
    result = runtime.service.commit(
        CommitRequest(
            pid,
            branch.id,
            None,
            Actor("human", "eval-author"),
            "fixture",
            "作者提供评测基线",
            changes=tuple(
                ArtifactChange(ArtifactId(ids[k]), body, kind=kind)
                for k, (kind, body) in objects.items()
            ),
            relations=(
                RelationChange(
                    "add", ArtifactId(ids["helper"]), "motivates", ArtifactId(ids["beat"])
                ),
                RelationChange("add", ArtifactId(ids["beat"]), "renders", ArtifactId(ids["unit"])),
            ),
        )
    )
    return pid, result.commit_id.value, ids


async def context(runtime, pid, base, ids, case):
    role = case["role"]
    app = runtime.agent_workflows
    if role == "planner":
        jid = await app.plan(pid, base, (ids["helper"],), "eval-plan", case["instruction"])
    elif role == "writer":
        jid = await app.draft(
            pid, base, (ids["beat"],), f"{pid.value}:new-chapter", "eval-draft", case["instruction"]
        )
    elif role == "auditor":
        jid = await app.audit(pid, base, (ids["unit"], ids["beat"]), "eval-audit")
    elif role == "style_observer":
        catalog = runtime.queries.catalog(pid, CommitId(base))
        text = "\n\n".join(e.model().text for e in catalog.units_of(ids["chapter"]))
        changed = await runtime.workflow.save(
            ManualEditIntent(
                project_id=pid,
                chapter_id=ids["chapter"],
                idempotency_key="edit-fact",
                full_text=text.replace("3点", "4点"),
            )
        )
        jid = await app.style(pid, base, changed.commit_id, ids["chapter"], "eval-style")
    else:
        return ContextBuilder(runtime.service).build(
            role,
            pid,
            base,
            (ids["beat"], ids["unit"]) if role == "coordinator" else (ids["unit"],),
            instruction=case["instruction"],
            writable_ids=(ids["unit"],) if role == "reviser" else (),
            evidence={
                "approval": {"target_ids": [ids["unit"]], "source": "评测中人工指定的修订范围"}
            }
            if role == "reviser"
            else {},
        )
    from hastings.domain.worker import ContextPackage

    ctx = ContextPackage.model_validate((await runtime.jobs.get(jid))["payload"]["context"])
    # 评测指令属于任务参数；重新确定 context_id。
    ctx.instruction = case["instruction"]
    from hastings.domain.repository.serialization import content_hash

    ctx.context_id = content_hash(ctx.model_dump(exclude={"context_id"}))
    await runtime.worker.cancel(jid)
    return ctx


def assess(case, output):
    if output["kind"] != case["expected_kind"]:
        return False, f"expected {case['expected_kind']}, got {output['kind']}"
    role = case["role"]
    if role == "planner":
        ok = bool(output["items"]) and all(
            i["status"] == "proposed" and i["kind"] == "story.character" for i in output["items"]
        )
    elif role == "writer":
        text = output.get("text", "") or ""
        ok = bool(text) and "早已知道" not in text and "林岚调慢" not in text
    elif role == "reviser":
        ok = bool(output["patches"]) and all(
            "非常缓慢地" not in p["content"].get("text", "") for p in output["patches"]
        )
    elif role == "auditor":
        ok = bool(output["findings"])  # 版本、scope、原文 quote 已由应用守卫逐项验证。
    elif role == "style_observer":
        ok = bool(output["observations"]) and all(
            o["category"] == "fact" for o in output["observations"]
        )
    else:
        ok = output["intent_kind"] == "draft" and not output["items"]
    return ok, "角色约束与应用守卫通过" if ok else "结构可解析，但角色任务语义未满足"


async def run(args):
    load_dotenv(ROOT / ".env")
    destination = Path(args.out)
    destination.mkdir(parents=True, exist_ok=True)
    reports = []
    cases = [Path(args.case)] if args.case else sorted(Path(__file__).parent.glob("*.json"))
    for casefile in cases:
        case = json.loads(casefile.read_text(encoding="utf-8"))
        runtime = V2Runtime.memory()
        pid, base, ids = seed(runtime)
        ctx = await context(runtime, pid, base, ids, case)
        adapter = ConfiguredV2Runner(
            runtime.service, env_fallback_config() if args.provider == "env" else None
        ).adapter()
        providers = []
        factory = adapter.factory

        def tracked(factory=factory, providers=providers):
            provider = factory()
            providers.append(provider)
            return provider

        adapter.factory = tracked
        runtime.configure_agent_runner(adapter)
        jid = await runtime.worker.submit(ctx, "real-eval")
        try:
            job = await runtime.worker.execute(jid)
            output = job.get("output") or {}
            passed, reason = assess(case, output) if output else (False, "Provider 未返回输出")
        finally:
            for provider in providers:
                await provider.aclose()
        report = {
            "case": casefile.name,
            "role": case["role"],
            "model": adapter.model,
            "passed": passed,
            "reason": reason,
            "state": job["state"],
            "context": ctx.model_dump(mode="json"),
            "output": output,
            "events": await runtime.jobs.events(jid),
        }
        (destination / f"{case['role']}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        reports.append({k: report[k] for k in ("role", "model", "passed", "state", "reason")})
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
    if args.interruption:
        reports.append(await interruption(destination, args.provider))
    (destination / "report.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def interruption(destination, provider_source):
    """在真实响应收到文本后注入断线，再从同一磁盘库/同一 Job 恢复。"""
    path = str(destination / "interruption.sqlite")
    runtime = V2Runtime.sqlite(path)
    pid, base, ids = seed(runtime)
    case = {
        "role": "coordinator",
        "instruction": "请按现有节拍生成一章，只返回 draft 用例选择的 story_proposal，items 为空。",
    }
    ctx = await context(runtime, pid, base, ids, case)
    configured = ConfiguredV2Runner(
        runtime.service, env_fallback_config() if provider_source == "env" else None
    ).adapter()
    real = configured.factory()
    observed = {"real_text_received": False}

    class Interrupted:
        async def stream_response(self, **kwargs):
            async for event in real.stream_response(**kwargs):
                if event.type in ("text_delta", "toolcall_delta", "toolcall_end"):
                    observed["real_text_received"] = True
                    raise OSError("评测注入：真实 Provider 流中断")
                yield event

    runtime.configure_agent_runner(
        TauAgentRunner(lambda: Interrupted(), configured.model, runtime.service)
    )
    jid = await runtime.worker.submit(ctx, "interrupt-real")
    try:
        first = await runtime.worker.execute(jid)
    finally:
        await real.aclose()
    restarted = V2Runtime.sqlite(path)
    # 到达真实退避时间后重试，重启不重建用户意图。
    if first.get("retry_at"):
        from datetime import datetime

        delay = (datetime.fromisoformat(first["retry_at"]) - restarted.clock.now()).total_seconds()
        await asyncio.sleep(max(0, delay) + 0.05)
    adapter = ConfiguredV2Runner(
        restarted.service, env_fallback_config() if provider_source == "env" else None
    ).adapter()
    recovery_provider = adapter.factory()
    adapter.factory = lambda: recovery_provider
    restarted.configure_agent_runner(adapter)
    try:
        final = await restarted.worker.execute(jid)
    finally:
        await recovery_provider.aclose()
    report = {
        "role": "provider_recovery",
        **observed,
        "first_state": first["state"],
        "final_state": final["state"],
        "same_job_id": final["job_id"] == jid,
        "same_context_id": final["payload"]["context"]["context_id"] == ctx.context_id,
        "output": final["output"],
        "events": await restarted.jobs.events(jid),
    }
    report["passed"] = (
        observed["real_text_received"]
        and first["state"] == "retryable_failed"
        and final["state"] == "waiting"
    )
    (destination / "provider-recovery.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({k: report[k] for k in ("role", "passed", "first_state", "final_state")}),
        flush=True,
    )
    return {k: report[k] for k in ("role", "passed", "first_state", "final_state")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case")
    parser.add_argument("--out", required=True)
    parser.add_argument("--interruption", action="store_true")
    parser.add_argument("--provider", choices=("env", "config"), default="env")
    asyncio.run(run(parser.parse_args()))
