"""评测 CLI 入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.harness.report import (
    apply_reviews,
    compare_to_baseline,
    gate_decision,
    regrade_report,
    write_report,
)
from evaluation.harness.runner_api import RunBatchError, run_batch
from evaluation.harness.schema import CaseLoadError, load_cases

ROOT = Path(__file__).resolve().parent


def _add_shared_case_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cases", type=Path, default=ROOT / "cases", help="案例目录")
    parser.add_argument("--works", type=Path, default=ROOT / "works", help="基础作品目录")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evaluation", description="线簿离线评测")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="列出案例")
    _add_shared_case_args(list_parser)

    run_parser = sub.add_parser("run", help="对本地模式服务跑案例（需已启动服务）")
    _add_shared_case_args(run_parser)
    run_parser.add_argument(
        "--base-url",
        required=True,
        help="本地模式服务根地址，如 http://127.0.0.1:8000",
    )
    run_parser.add_argument("--case", action="append", default=[], help="只跑指定案例 id，可重复")
    run_parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="每案试验次数（冒烟 1，完整门禁 3）",
    )
    run_parser.add_argument("--max-requests", type=int, default=30, help="本批模型请求上限")
    run_parser.add_argument("--out", type=Path, required=True, help="尚不存在的输出目录")
    run_parser.add_argument("--timeout", type=float, default=300.0, help="单轮等待秒数")
    run_parser.add_argument(
        "--mode",
        choices=("smoke", "gate"),
        default="smoke",
        help="报告模式标签",
    )
    run_parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="基线 report.json，用于质量退化对比",
    )

    score_parser = sub.add_parser(
        "score",
        help="用证据包重跑程序判分，并可选合并 reviews.json",
    )
    score_parser.add_argument("run_dir", type=Path, help="含 report.json 的运行目录")
    _add_shared_case_args(score_parser)
    score_parser.add_argument(
        "--reviews",
        type=Path,
        default=None,
        help="复核文件；默认若存在则读 run_dir/reviews.json",
    )
    score_parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="可选基线 report.json",
    )
    score_parser.add_argument(
        "--no-regrade",
        action="store_true",
        help="跳过证据包重评，只合并复核",
    )

    wb_parser = sub.add_parser("workbench", help="打开本地评测工作台")
    _add_shared_case_args(wb_parser)
    wb_parser.add_argument(
        "--run",
        type=Path,
        default=None,
        help="可选：直接打开某次运行（目录路径，或 runs 根下的名字）",
    )
    wb_parser.add_argument(
        "--runs-root",
        type=Path,
        default=None,
        help="运行根目录；默认 data/evaluation/runs",
    )
    wb_parser.add_argument("--host", default="127.0.0.1")
    wb_parser.add_argument("--port", type=int, default=8765)
    wb_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="不自动打开浏览器",
    )

    args = parser.parse_args(argv)

    if args.command == "list":
        try:
            cases = load_cases(args.cases, works_root=args.works)
        except CaseLoadError as exc:
            print(f"[EVAL_CASE] evaluation.list：{exc}。建议：修正案例或作品初态后重试。")
            return 1
        if not cases:
            print("（无案例）")
            return 0
        for case in cases:
            print(f"{case.id}\tv{case.version}\t{case.work}\t{case.history}")
        return 0

    if args.command == "run":
        if args.mode == "gate" and args.repeat < 3:
            print(
                "[EVAL_MODE] evaluation.run：完整门禁建议 --repeat 3。"
                "建议：提高试验次数，或改用 --mode smoke。"
            )

        def _on_progress(result, _results, _report) -> None:  # type: ignore[no-untyped-def]
            outcome = result.grade.outcome if result.grade else "-"
            print(
                f"{result.case_id}#{result.attempt}\t{result.status}\t"
                f"{outcome}\t{result.error or ''}".rstrip()
            )

        try:
            batch = run_batch(
                base_url=args.base_url,
                out_dir=args.out,
                cases_dir=args.cases,
                works_dir=args.works,
                case_ids=args.case or None,
                mode=args.mode,
                repeat=args.repeat,
                max_requests=args.max_requests,
                timeout_s=args.timeout,
                baseline=args.baseline,
                on_progress=_on_progress,
                strict_gate_repeat=False,
            )
        except RunBatchError as exc:
            print(str(exc))
            return 1
        gate_label = "通过" if batch.gate_passed else "不通过"
        print(f"写入 {args.out / 'report.json'}；门禁={gate_label}")
        if batch.status == "failed":
            if batch.error:
                print(batch.error)
            return 1
        return 0 if batch.gate_passed else 2

    if args.command == "score":
        report_path = args.run_dir / "report.json"
        if not report_path.is_file():
            print(
                f"[EVAL_SCORE] evaluation.score：找不到 {report_path}。"
                "建议：先完成 evaluation run。"
            )
            return 1
        report = json.loads(report_path.read_text(encoding="utf-8"))
        updated = report
        if not args.no_regrade:
            try:
                cases = load_cases(args.cases, works_root=args.works)
            except CaseLoadError as exc:
                print(f"[EVAL_CASE] evaluation.score：{exc}。建议：修正案例后重试。")
                return 1
            case_map = {case.id: case for case in cases}
            updated = regrade_report(updated, args.run_dir, cases=case_map)
        reviews_path = args.reviews or (args.run_dir / "reviews.json")
        if reviews_path.is_file():
            reviews_raw = json.loads(reviews_path.read_text(encoding="utf-8"))
            if isinstance(reviews_raw, list):
                reviews, attributions = reviews_raw, []
            else:
                reviews = reviews_raw.get("reviews", [])
                attributions = reviews_raw.get("attributions", [])
            updated = apply_reviews(updated, reviews)
            if isinstance(attributions, list):
                updated["attributions"] = attributions
        elif args.reviews is not None:
            print(
                f"[EVAL_SCORE] evaluation.score：找不到 {reviews_path}。"
                "建议：先写 reviews.json 或省略 --reviews。"
            )
            return 1
        if args.baseline:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            updated = compare_to_baseline(updated, baseline)
        elif report.get("baseline_compare"):
            updated["baseline_compare"] = report.get("baseline_compare")
        updated["gate"] = gate_decision(updated)
        write_report(args.run_dir, updated)
        print(
            f"已{'重评并' if not args.no_regrade else ''}写入报告；"
            f"门禁={'通过' if updated['gate']['passed'] else '不通过'}"
        )
        return 0 if updated["gate"]["passed"] else 2

    if args.command == "workbench":
        from evaluation.workbench.server import DEFAULT_RUNS_ROOT, serve

        runs_root = args.runs_root or DEFAULT_RUNS_ROOT
        initial_run: str | None = None
        if args.run is not None:
            run_arg = args.run
            if run_arg.is_dir() and (run_arg / "report.json").is_file():
                runs_root = run_arg.parent
                initial_run = run_arg.name
            elif (runs_root / run_arg).is_dir() and (
                (runs_root / run_arg) / "report.json"
            ).is_file():
                initial_run = run_arg.name
            elif (DEFAULT_RUNS_ROOT / run_arg).is_dir() and (
                (DEFAULT_RUNS_ROOT / run_arg) / "report.json"
            ).is_file():
                runs_root = DEFAULT_RUNS_ROOT
                initial_run = run_arg.name
            else:
                print(
                    f"[EVAL_WB] evaluation.workbench：找不到运行 {run_arg}。"
                    "建议：省略 --run，在工作台列表里选；或先完成 evaluation run。"
                )
                return 1
        serve(
            runs_root,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
            initial_run=initial_run,
            cases_root=args.cases,
            works_root=args.works,
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
