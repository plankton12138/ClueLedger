"""按案例、层次和变体报告；未运行项进入覆盖分母，待复核项不计成功。"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any

from eval.harness.grading import SCORER_VERSION
from eval.harness.runtime import write_json


def aggregate(results: list[dict[str, Any]], planned: int) -> dict[str, Any]:
    counts = Counter(r["status"] for r in results)
    counts["not_run"] += planned - len(results)
    successes = counts["pass"]
    durations = sorted(r["duration_seconds"] for r in results)
    complete_usage = bool(results) and all(r["metrics"]["usage_complete"] for r in results)
    total_input = (
        sum(r["metrics"]["input_tokens"] or 0 for r in results) if complete_usage else None
    )
    total_output = (
        sum(r["metrics"]["output_tokens"] or 0 for r in results) if complete_usage else None
    )
    return {
        "planned": planned,
        "executed": len(results),
        "counts": dict(counts),
        "success_rate": successes / planned if planned else None,
        "duration_p50_seconds": durations[math.ceil(len(durations) * 0.5) - 1]
        if durations
        else None,
        "duration_p95_seconds": durations[math.ceil(len(durations) * 0.95) - 1]
        if durations
        else None,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "tokens_per_success": (total_input + total_output) / successes
        if successes and total_input is not None and total_output is not None
        else None,
        "cost_per_success": None,
    }


def build_report(manifest: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    jobs = manifest["jobs"]
    groups: dict[str, Any] = {}
    for axis in ("variant", "family", "layer", "case"):
        groups[axis] = {}
        for value in dict.fromkeys(j[axis] for j in jobs):
            selected = [r for r in results if r[axis] == value]
            groups[axis][value] = aggregate(selected, sum(j[axis] == value for j in jobs))
    return {
        "schema_version": 1,
        "scorer_version": SCORER_VERSION,
        "manifest": manifest,
        "summary": aggregate(results, len(jobs)),
        "groups": groups,
        "results": results,
        "unexecuted": [j["key"] for j in jobs if j["key"] not in {r["key"] for r in results}],
    }


def write_report(out: Path, report: dict[str, Any], stem: str = "report") -> None:
    write_json(out / f"{stem}.json", report)
    summary = report["summary"]
    lines = [
        "# 线簿上下文评测",
        "",
        f"运行：`{report['manifest']['run_id']}`；模型：`{report['manifest']['model']}`。",
        "",
        f"计划 {summary['planned']} 次，执行 {summary['executed']} 次；结果：{summary['counts']}。",
        "",
        "成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。",
        "",
        "| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |",
        "|---|---|---:|---|---:|---:|---:|",
    ]
    for result in report["results"]:
        metric = result["metrics"]
        lines.append(
            f"| {result['case']} | {result['variant']} | {result['repeat']} | {result['status']} "
            f"| {result['duration_seconds']} "
            f"| {metric['model_requests']} | {metric['tool_calls']} |"
        )
    if report["unexecuted"]:
        lines += ["", "未运行：" + "、".join(report["unexecuted"]) + "。"]
    for result in report["results"]:
        lines += ["", f"## {result['key']}", "", f"证据目录：`{result['evidence']}`。", ""]
        if result.get("error"):
            lines.append(result["error"])
        for row in result["checks"]:
            if row["status"] != "pass":
                lines.append(f"- {row['status']}：`{row['id']}`：{row['detail']}")
    lines += [
        "",
        "用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。",
        "费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。",
        "人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。",
        "",
    ]
    (out / f"{stem}.md").write_text("\n".join(lines), encoding="utf-8")
