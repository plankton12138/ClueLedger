"""线簿上下文评测：列题、在线运行、离线复核评分与同题变体比较。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from eval.harness.grading import grade_attempt
from eval.harness.models import load_model
from eval.harness.reporting import build_report, write_report
from eval.harness.runtime import run_attempt, write_json
from eval.harness.schema import ROOT, VARIANTS, Case, fixture_files, load_cases

REPO = ROOT.parent
SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True, encoding="utf-8").strip()


def source_hash() -> str:
    digest = hashlib.sha256()
    paths = list((REPO / "src").rglob("*.py"))
    paths += list((ROOT / "harness").glob("*.py")) + [ROOT / "__main__.py"]
    for path in sorted(paths):
        digest.update(path.relative_to(REPO).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def selection(args: argparse.Namespace) -> tuple[list[Case], str]:
    cases, digest = load_cases()
    suite = json.loads((ROOT / "suites" / f"{args.suite}.json").read_text(encoding="utf-8"))
    keys = args.case or suite["cases"]
    unknown = set(keys) - {c.id for c in cases}
    if unknown:
        raise ValueError(f"不存在的案例：{sorted(unknown)}")
    selected = [c for key in dict.fromkeys(keys) for c in cases if c.id == key]
    for case in selected:
        fixture_files(case)
    return selected, digest


class CallBudget:
    def __init__(self, limit: int) -> None:
        self.limit, self.used = limit, 0

    def wrap(self, provider: Any) -> Any:
        budget = self

        class Provider:
            async def stream_response(self, **kwargs: Any) -> Any:
                if budget.used >= budget.limit:
                    raise RuntimeError(
                        "[EVAL_REQUEST_BUDGET] eval.run：模型请求预算已用完。"
                        "建议：检查报告后另建运行。"
                    )
                budget.used += 1
                async for event in provider.stream_response(**kwargs):
                    yield event

            async def aclose(self) -> None:
                close = getattr(provider, "aclose", None)
                if close:
                    await close()

        return Provider()


async def run(args: argparse.Namespace) -> int:
    cases, digest = selection(args)
    selected = load_model(
        env_file=args.env_file or REPO / ".env",
        config=args.config,
        model=args.model,
    )
    variants = list(dict.fromkeys(args.variant or ["current"]))
    jobs = []
    # 同题各方案交错运行；重复轮换起始方案，避免方案顺序完全固定。
    for repeat in range(1, args.repeat + 1):
        ordered = (
            variants[(repeat - 1) % len(variants) :] + variants[: (repeat - 1) % len(variants)]
        )
        for case in cases:
            for variant in ordered:
                jobs.append(
                    {
                        "key": f"{case.id}--{variant}--{repeat}",
                        "case": case.id,
                        "family": case.family,
                        "layer": case.layer,
                        "variant": variant,
                        "repeat": repeat,
                    }
                )
    run_id = "context-" + datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S-%f")
    out = (args.out or ROOT / "runs" / run_id).resolve()
    out.mkdir(parents=True, exist_ok=False)
    work = (args.work_root / run_id).resolve()
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": datetime.now(SHANGHAI).isoformat(),
        "model": selected.model,
        "connection_id": selected.connection_id,
        "provider_api": selected.api,
        "model_source": selected.source,
        "provider_mode": "product_worker",
        "model_sampling_parameters": "未单独覆盖；使用当前产品 provider 默认值，见源代码指纹",
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain")),
        "source_sha256": source_hash(),
        "cases_sha256": digest,
        "suite": args.suite,
        "jobs": jobs,
        "max_turns": args.max_turns,
        "step_timeout_seconds": args.timeout,
        "max_provider_calls": args.max_requests,
        "runner_retries": 0,
        "work_root": str(work),
        "evidence_format": 1,
        "history": "案例可使用明确标注的合成公开历史，不是实际执行轨迹",
        "variant_policy": (
            "提示分区变体仅移除所选分区；no_compaction 禁用手动与自动摘要，"
            "保留轮内裁剪和溢出恢复；公开对话和按需读取工具保留"
        ),
    }
    write_json(out / "manifest.json", manifest)
    cases_by_id = {c.id: c for c in cases}
    factory = selected.factory
    budget = CallBudget(args.max_requests)

    def counted_factory() -> Any:
        snapshot = factory()
        provider_config = getattr(snapshot.provider, "_config", None)
        manifest["observed_provider_parameters"] = {
            name: getattr(provider_config, name)
            for name in (
                "max_tokens",
                "temperature",
                "top_p",
                "seed",
                "timeout_seconds",
                "max_retries",
            )
            if provider_config is not None and hasattr(provider_config, name)
        }
        return replace(snapshot, provider=budget.wrap(snapshot.provider))

    results: list[dict[str, Any]] = []
    write_report(out, build_report(manifest, results))
    print(f"[run] {out}", flush=True)
    for job in jobs:
        if budget.used >= budget.limit:
            print("[stop] 模型请求预算用完，剩余案例保留为未运行。", flush=True)
            break
        print(f"[case] {job['key']}", flush=True)
        result = await run_attempt(
            cases_by_id[job["case"]],
            job["variant"],
            job["repeat"],
            factory=counted_factory,
            work=work,
            out=out,
            max_turns=args.max_turns,
            timeout=args.timeout,
        )
        results.append(result)
        manifest["provider_calls_used"] = budget.used
        write_json(out / "manifest.json", manifest)
        write_report(out, build_report(manifest, results))
        print(
            f"[case] {job['key']} -> {result['status']} ({result['duration_seconds']}s)", flush=True
        )
        if result["model_error"]:
            print("[stop] 模型请求失败，停止本批；剩余案例保留为未运行。", flush=True)
            break
    return 0 if len(results) == len(jobs) and all(r["status"] == "pass" for r in results) else 1


def rescore(args: argparse.Namespace) -> int:
    report = json.loads((args.run / "report.json").read_text(encoding="utf-8"))
    reviews = json.loads(args.reviews.read_text(encoding="utf-8")) if args.reviews else {}
    results = []
    valid_keys: set[str] = set()
    for previous in report["results"]:
        folder = args.run / previous["evidence"]
        case = Case.model_validate_json((folder / "case.json").read_text(encoding="utf-8"))
        evidence = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(
                folder.glob("step-*.json"),
                key=lambda p: int(p.stem.split("-")[-1]),
            )
        ]
        valid_keys.update(
            f"{previous['key']}/step-{i + 1}/{c.id}"
            for i, step in enumerate(case.steps)
            for c in step.checks
            if c.kind == "manual"
        )
        result = {**previous, **grade_attempt(case, evidence, previous["key"], reviews)}
        if previous.get("error"):
            result["status"] = "error"
        results.append(result)
    if set(reviews) - valid_keys:
        raise ValueError(f"复核记录包含未知检查：{sorted(set(reviews) - valid_keys)}")
    manifest = dict(report["manifest"])
    manifest["rescored_at"] = datetime.now(SHANGHAI).isoformat()
    manifest["scorer_source_sha256"] = source_hash()
    manifest["reviews_sha256"] = (
        hashlib.sha256(args.reviews.read_bytes()).hexdigest() if args.reviews else None
    )
    rescored = build_report(manifest, results)
    write_report(args.run, rescored, "rescored")
    print(f"[score] {args.run / 'rescored.md'}")
    return 0 if all(r["status"] == "pass" for r in results) and not rescored["unexecuted"] else 1


def compare(args: argparse.Namespace) -> int:
    report = json.loads(args.report.read_text(encoding="utf-8"))
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for row in report["results"]:
        grouped.setdefault((row["case"], row["repeat"]), {})[row["variant"]] = row
    print("case\trepeat\tbaseline\tcandidate\tbaseline_status\tcandidate_status\tinput_token_delta")
    for (case, repeat), rows in grouped.items():
        baseline = rows.get(args.baseline)
        for name, row in rows.items():
            if name == args.baseline:
                continue
            a = baseline["metrics"]["input_tokens"] if baseline else None
            b = row["metrics"]["input_tokens"]
            delta = b - a if a is not None and b is not None else None
            print(
                f"{case}\t{repeat}\t{args.baseline}\t{name}\t"
                f"{baseline['status'] if baseline else 'not_run'}\t{row['status']}\t{delta}"
            )
    return 0


def positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="离线校验案例与材料并列出清单")
    running = sub.add_parser("run", help="通过配置文件中的在线模型执行")
    suite_names = tuple(sorted(path.stem for path in (ROOT / "suites").glob("*.json")))
    for command in (listing, running):
        command.add_argument("--suite", choices=suite_names, default="core")
        command.add_argument("--case", action="append", default=[])
    running.add_argument("--variant", choices=VARIANTS, action="append")
    running.add_argument("--repeat", type=positive, default=1)
    sources = running.add_mutually_exclusive_group()
    sources.add_argument("--env-file", type=Path, help="在线模型 .env，默认仓库根目录 .env")
    sources.add_argument("--config", type=Path, help="显式改用产品模型配置文件")
    running.add_argument("--model")
    running.add_argument("--out", type=Path)
    running.add_argument("--work-root", type=Path, default=REPO / "data" / "eval")
    running.add_argument("--max-turns", type=positive, default=24)
    running.add_argument("--timeout", type=positive, default=240)
    running.add_argument("--max-requests", type=positive, default=200)
    scoring = sub.add_parser("score", help="读取已保存证据重新判分，不调用模型")
    scoring.add_argument("run", type=Path)
    scoring.add_argument("--reviews", type=Path)
    comparing = sub.add_parser("compare", help="对比同一运行中的同题、同次变体")
    comparing.add_argument("report", type=Path)
    comparing.add_argument("--baseline", choices=VARIANTS, default="current")
    args = parser.parse_args()
    try:
        if args.command == "list":
            cases, digest = selection(args)
            for case in cases:
                print(f"{case.id}\t{case.layer}\t{case.family}\t{case.title}")
            print(f"cases_sha256={digest}")
            return 0
        if args.command == "run":
            return asyncio.run(run(args))
        if args.command == "score":
            return rescore(args)
        return compare(args)
    except (OSError, ValueError) as exc:
        print(
            f"[EVAL_CONFIG_INVALID] eval.{args.command}：{type(exc).__name__}。"
            "建议：检查案例、配置与运行目录。"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
