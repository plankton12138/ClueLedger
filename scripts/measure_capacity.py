"""Phase 17 容量测量（T17.8 / plan §7）：100 章 × 每章约 5000 中文字。

测量项（plan §7 逐条对应）：
- V2 项目构造与章节提交；
- 打开项目不读全书正文：ManuscriptCatalog 元数据（不拼正文单元文本）；
- 读取单章（units 全文拼接）延迟；
- 章基础（chapter_base）计算延迟；
- 一次人工保存 Commit（V2 写链，经 CommitManualEdit）延迟；
- Commit 历史读取与两快照比较延迟；
- 导出（export_commit + zip）延迟；
- 重启恢复：重新打开 sqlite 后 head 可用，不重放对话/正文。

预算口径（plan §7：不先写死毫秒门槛，测量值随构建笔记归档并解释选择）：
目标硬件为开发笔记本；单章读 / 章基础 / 保存为交互路径，应显著低于秒级；
项目构造与导出为批量路径，秒级可接受。

运行：``uv run python scripts/measure_capacity.py``（临时目录，跑完自清理）。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hastings.application.handlers.manuscript_commands import (
    ManuscriptCommandHandler,
    manuscript_command,
)
from hastings.application.workflows.manual_edit import CommitManualEdit, ManualEditIntent
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import import_chapter
from hastings.domain.repository.ids import Actor, ProjectId
from hastings.infrastructure.repository.sqlite import build_sqlite_repository
from hastings.infrastructure.runtime import SystemClock, UuidIdGenerator
from hastings.interfaces.exporters.v2_package_exporter import export_commit, zip_package

CHAPTERS = 100
PARAGRAPHS_PER_CHAPTER = 10
PARAGRAPH_CHARS = 500  # 10 段 × 500 字 ≈ 5000 字/章
ACTOR = Actor(kind="human", id="capacity")
SENTENCE = "侦探沿着回廊走向尽头的书房，雨水敲打着高窗，烛光在墙上投下摇晃的影子。"


def _build_v2_project(service: Any, ids: UuidIdGenerator) -> ProjectId:
    """直接构造 100 章 V2 正文，通过现有命令入口提交。"""
    project = service.create_project("容量测量").id
    handler = ManuscriptCommandHandler(service, service, ids, ACTOR)
    paragraph = (SENTENCE * (PARAGRAPH_CHARS // len(SENTENCE) + 1))[:PARAGRAPH_CHARS]
    text = "\n\n".join(paragraph for _ in range(PARAGRAPHS_PER_CHAPTER))
    for index in range(1, CHAPTERS + 1):
        handler.execute(
            manuscript_command(
                import_chapter,
                chapter_id=f"chapter-{index:03d}",
                title=f"第{index:03d}章 迷雾-{index}",
                text=text,
                order_key=f"{index:04d}",
            ),
            project_id=project,
            idempotency_key=f"chapter-{index}",
            summary=f"构造第{index}章",
        )
    return project


def _timed(fn: Callable[[], Any], *, repeat: int = 1) -> dict[str, float | Any]:
    """计时并返回 ms 统计；repeat>1 时取均值/最大值（交互路径抖动观察）。"""
    samples: list[float] = []
    result: Any = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - start) * 1000)
    return {
        "ms_mean": round(statistics.mean(samples), 2),
        "ms_max": round(max(samples), 2),
        "result": result,
    }


def _strip(timed: dict[str, float | Any]) -> dict[str, float]:
    return {k: v for k, v in timed.items() if k != "result"}  # type: ignore[misc]


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="h17-capacity-"))
    db_path = workdir / "v2.db"
    measurements: dict[str, Any] = {
        "dataset": {
            "chapters": CHAPTERS,
            "chars_per_chapter": PARAGRAPHS_PER_CHAPTER * PARAGRAPH_CHARS,
        }
    }
    try:
        # 直接使用 V2 命令构造项目，批量构造耗时与交互路径分开记录。
        ids = UuidIdGenerator()
        service = build_sqlite_repository(str(db_path), SystemClock(), ids).service
        built = _timed(lambda: _build_v2_project(service, ids))
        project = built["result"]
        measurements["build_v2_project"] = _strip(built)
        head = service.get_branch(project, "main").head_commit_id
        assert head is not None

        # 3) 打开项目不读全书：章元数据目录
        opened = _timed(
            lambda: len(
                ManuscriptCatalog.from_snapshot(service.get_snapshot(project, head)).chapters()
            ),
            repeat=20,
        )
        assert opened["result"] == CHAPTERS
        measurements["open_project_metadata"] = _strip(opened)

        # 4) 读单章全文
        catalog = ManuscriptCatalog.from_snapshot(service.get_snapshot(project, head))
        first_id = catalog.chapters()[0].artifact_id
        read_one = _timed(
            lambda: "\n\n".join(u.model().text for u in catalog.units_of(first_id)), repeat=50
        )
        measurements["read_single_chapter"] = _strip(read_one)

        # 5) 章基础（乐观并发基础）
        base = _timed(lambda: catalog.chapter_base(first_id), repeat=50)
        measurements["chapter_base"] = _strip(base)

        # 6) 人工保存（V2 写链，经 CommitManualEdit）
        from hastings.application.handlers.manuscript_commands import ManuscriptCommandHandler

        handler = ManuscriptCommandHandler(service, service, UuidIdGenerator(), ACTOR)
        workflow = CommitManualEdit(handler)
        counter = {"n": 0}

        async def _save_once():
            counter["n"] += 1
            text = (
                "\n\n".join(u.model().text for u in catalog.units_of(first_id))
                + f"\n\n第{counter['n']}次容量测量修订。"
            )
            return await workflow.save(
                ManualEditIntent(
                    project_id=project,
                    chapter_id=first_id,
                    idempotency_key=f"capacity-save-{counter['n']}",
                    summary=f"容量测量保存 {counter['n']}",
                    full_text=text,
                )
            )

        save = _timed(lambda: asyncio.run(_save_once()), repeat=5)
        measurements["manual_save_commit"] = _strip(save)

        # 7) Commit 历史与两快照比较
        head_now = service.get_branch(project, "main").head_commit_id
        assert head_now is not None
        history = _timed(lambda: len(service.commit_history(project, head_now)), repeat=10)
        measurements["commit_history"] = _strip(history)

        commits = service.commit_history(project, head_now)
        old_head = commits[-1].id  # 最早（导入）commit

        def _compare():
            new_ms = ManuscriptCatalog.from_snapshot(
                service.get_snapshot(project, head_now)
            ).chapters()
            old_ms = ManuscriptCatalog.from_snapshot(
                service.get_snapshot(project, old_head)
            ).chapters()
            return len(new_ms), len(old_ms)

        diff = _timed(_compare, repeat=10)
        measurements["two_snapshot_compare"] = _strip(diff)

        # 8) 导出（快照 → 包 + zip）
        export = _timed(lambda: zip_package(export_commit(service, project, head_now)), repeat=3)
        measurements["export_package"] = _strip(export)

        # 9) 重启恢复：重开 sqlite 即 head 可用
        def _reopen() -> bool:
            reopened = build_sqlite_repository(str(db_path), SystemClock(), UuidIdGenerator())
            return reopened.service.get_branch(project, "main").head_commit_id is not None

        reopen = _timed(_reopen, repeat=3)
        assert reopen["result"] is True
        measurements["restart_recovery"] = _strip(reopen)

        print(json.dumps(measurements, ensure_ascii=False, indent=2))
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
