"""V03-0.7 性能基线测量：按计划 §9.6 的基线样本构建真实数据并测量。

用法（仓库根）：
    uv run python scripts/bench_v03_baseline.py [--db PATH] [--json PATH]

基线样本（§9.6）：100 章、约 30 万汉字、500 个账本对象、1000 条关系。
测量项（当前已实现部分；未建成能力在报告中标记 pending，不伪造数字）：
    - 单章保存提交延迟（服务层，P50/P95，替代尚未建成的副本自动保存）
    - 章节回读（暖查询 P95）
    - 全文目录读取（manuscript catalog）
    - 故事快照查询（get_snapshot，图谱/上下文的数据源）
    - 执行日志/图谱首屏：对应存储未建成 → pending
结果写入 JSON（默认 scripts/bench-v03-baseline.json）并打印摘要。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from hastings.domain.repository.ids import Actor, ArtifactId
from hastings.domain.repository.models import Project
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange
from hastings.server.v2 import V2Runtime

CHAPTERS = 100
CHARS_PER_CHAPTER = 3000  # 100 × 3000 ≈ 30 万汉字
LEDGER_OBJECTS = 500
RELATIONS = 1000
SAVE_ROUNDS = 30
READ_ROUNDS = 30


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


def _build_baseline(runtime: V2Runtime) -> tuple[Project, list[str]]:
    """构建基线样本：100 章正文（import 命令）+ 500 账本对象 + 1000 关系。"""
    from hastings.application.handlers.manuscript_commands import manuscript_command
    from hastings.domain.manuscript.commands import import_chapter

    service = runtime.service
    project = service.create_project("bench-baseline")
    actor = Actor(kind="system", id="bench")
    chapter_ids: list[str] = []
    for index in range(CHAPTERS):
        chapter_id = f"art_bench_ch{index:04d}"
        chapter_ids.append(chapter_id)
        text = ("雪落在旅馆的台阶上。" * (CHARS_PER_CHAPTER // 11))[:CHARS_PER_CHAPTER]
        runtime.handler.execute(
            manuscript_command(
                import_chapter,
                chapter_id=chapter_id,
                title=f"第{index + 1}章",
                text=text,
                order_key=f"{index:04d}",
            ),
            project_id=project.id,
            idempotency_key=f"bench-import-{index}",
            summary=f"基线导入第{index + 1}章",
        )
    branch = service.get_branch(project.id, "main")
    assert branch is not None
    head = branch.head_commit_id
    # 500 个账本对象
    for start in range(0, LEDGER_OBJECTS, 50):
        changes = tuple(
            ArtifactChange(
                artifact_id=ArtifactId(f"art_bench_obj{i:04d}"),
                content={"name": f"对象{i}", "role": "support"},
                kind="story.character",
            )
            for i in range(start, min(start + 50, LEDGER_OBJECTS))
        )
        result = service.commit(
            CommitRequest(
                project_id=project.id,
                branch_id=branch.id,
                expected_head=head,
                actor=actor,
                idempotency_key=f"bench-objects-{start}",
                summary=f"基线账本对象 {start}",
                changes=changes,
            )
        )
        head = result.new_head
    # 1000 条关系（对象间 follows）
    rels: list[RelationChange] = []
    for index in range(RELATIONS):
        source = ArtifactId(f"art_bench_obj{index % LEDGER_OBJECTS:04d}")
        target = ArtifactId(f"art_bench_obj{(index * 7 + 3) % LEDGER_OBJECTS:04d}")
        if source == target:
            continue
        rels.append(
            RelationChange(action="add", source=source, relation_type="follows", target=target)
        )
    for start in range(0, len(rels), 200):
        result = service.commit(
            CommitRequest(
                project_id=project.id,
                branch_id=branch.id,
                expected_head=head,
                actor=actor,
                idempotency_key=f"bench-relations-{start}",
                summary=f"基线关系 {start}",
                relations=tuple(rels[start : start + 200]),
            )
        )
        head = result.new_head
    return project, chapter_ids


def _measure(runtime: V2Runtime, project: Project, chapter_ids: list[str]) -> dict[str, object]:
    service = runtime.service
    queries = runtime.queries
    project_id = project.id
    head = service.get_branch(project_id, "main").head_commit_id
    assert head is not None

    # 单章保存（v2 /chapters/{id}/save 同路径：ManualEditIntent → workflow.save）
    from hastings.application.workflows.manual_edit import ManualEditIntent

    save_ms: list[float] = []
    for round_index in range(SAVE_ROUNDS):
        chapter = chapter_ids[round_index % len(chapter_ids)]
        current = queries.catalog(project_id, head)
        unit = current.units_of(chapter)[0]
        started = time.perf_counter()
        asyncio.run(
            runtime.workflow.save(
                ManualEditIntent(
                    project_id=project_id,
                    chapter_id=chapter,
                    idempotency_key=f"bench-save-{round_index}",
                    full_text=unit.model().text + "改。",
                    summary="基线单章保存",
                )
            )
        )
        save_ms.append((time.perf_counter() - started) * 1000)
        head = service.get_branch(project_id, "main").head_commit_id or head

    # 章节回读（暖查询）
    read_ms: list[float] = []
    for round_index in range(READ_ROUNDS):
        chapter = chapter_ids[round_index % len(chapter_ids)]
        started = time.perf_counter()
        catalog = queries.catalog(project_id, head)
        units = catalog.units_of(chapter)
        assert units
        read_ms.append((time.perf_counter() - started) * 1000)

    # 全文目录读取
    catalog_ms: list[float] = []
    for _ in range(READ_ROUNDS):
        started = time.perf_counter()
        catalog = queries.catalog(project_id, head)
        assert catalog.chapters()
        catalog_ms.append((time.perf_counter() - started) * 1000)

    # 故事快照（图谱/上下文数据源）
    snapshot_ms: list[float] = []
    for _ in range(READ_ROUNDS):
        started = time.perf_counter()
        snapshot = service.get_snapshot(project_id, head)
        assert snapshot.artifacts
        snapshot_ms.append((time.perf_counter() - started) * 1000)

    return {
        "sample": {
            "chapters": CHAPTERS,
            "approx_chars": CHAPTERS * CHARS_PER_CHAPTER,
            "ledger_objects": LEDGER_OBJECTS,
            "relations": RELATIONS,
        },
        "single_chapter_save_ms": {
            "p50": round(statistics.median(save_ms), 2),
            "p95": round(_percentile(save_ms, 95), 2),
            "max": round(max(save_ms), 2),
            "rounds": SAVE_ROUNDS,
        },
        "chapter_read_ms": {
            "p50": round(statistics.median(read_ms), 2),
            "p95": round(_percentile(read_ms, 95), 2),
            "max": round(max(read_ms), 2),
            "rounds": READ_ROUNDS,
        },
        "catalog_read_ms": {
            "p50": round(statistics.median(catalog_ms), 2),
            "p95": round(_percentile(catalog_ms, 95), 2),
            "max": round(max(catalog_ms), 2),
        },
        "story_snapshot_ms": {
            "p50": round(statistics.median(snapshot_ms), 2),
            "p95": round(_percentile(snapshot_ms, 95), 2),
            "max": round(max(snapshot_ms), 2),
        },
        "pending": [
            "edit_session_autosave（V03-2.4 建成后测防抖+确认延迟）",
            "manual_finish_two_chapters（V03-2.4/5.1）",
            "graph_first_screen（V03-6）",
            "execution_log_paging（V03-1.6）",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="V03-0.7 性能基线测量")
    parser.add_argument("--db", default=":memory:")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    runtime = V2Runtime.sqlite(args.db)
    project, chapter_ids = _build_baseline(runtime)
    report = _measure(runtime, project, chapter_ids)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
