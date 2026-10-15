"""Phase 17 容量测量（T17.8 / plan §7）：100 章 × 每章约 5000 中文字。

测量项（plan §7 逐条对应）：
- V1 源构造 + 幂等导入（迁移耗时）；
- 打开项目不读全书正文：ManuscriptCatalog 元数据（不拼正文单元文本）；
- 读取单章（units 全文拼接）延迟；
- 章基础（chapter_base）计算延迟；
- 一次人工保存 Commit（V2 写链，经 CommitManualEdit）延迟；
- Commit 历史读取与两快照比较延迟；
- 导出（export_commit + zip）延迟；
- 重启恢复：重新打开 sqlite 后 head 可用，不重放对话/正文。

预算口径（plan §7：不先写死毫秒门槛，测量值随构建笔记归档并解释选择）：
目标硬件为开发笔记本；单章读 / 章基础 / 保存为交互路径，应显著低于秒级；
导入与导出为一次性路径，秒级可接受。

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

from hastings.application.workflows.manual_edit import CommitManualEdit, ManualEditIntent
from hastings.bible.models import Clue, OutlineEntry
from hastings.bible.store import StoryWorkspace, save_ledger
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import Actor, CommitId
from hastings.infrastructure.repository.sqlite import build_sqlite_repository
from hastings.infrastructure.runtime import SystemClock, UuidIdGenerator
from hastings.interfaces.exporters.v2_package_exporter import export_commit, zip_package
from hastings.interfaces.importers.v1_workspace_importer import V1WorkspaceImporter

CHAPTERS = 100
PARAGRAPHS_PER_CHAPTER = 10
PARAGRAPH_CHARS = 500  # 10 段 × 500 字 ≈ 5000 字/章
ACTOR = Actor(kind="human", id="capacity")
SENTENCE = "侦探沿着回廊走向尽头的书房，雨水敲打着高窗，烛光在墙上投下摇晃的影子。"


def _build_v1_workspace(root: Path) -> StoryWorkspace:
    """构造 100 章的 V1 工作区（bible + chapters）。"""
    workspace = StoryWorkspace(root=root, slug="capacity-100")
    workspace.ensure_dirs()
    save_ledger(
        workspace.outline_path,
        [
            OutlineEntry(
                name=f"第{i:03d}章 迷雾-{i}",
                chapter=i,
                act="第一幕",
                beats=["开场", "推进", "收束"],
                plant_plans=[],
                end_hook="",
                status="planned",
            )
            for i in range(1, CHAPTERS + 1)
        ],
    )
    save_ledger(
        workspace.clues_path,
        [
            Clue(
                name="怀表停摆",
                content="怀表停在被害时刻",
                chapter="ch050",
                points_to="管家",
                is_red_herring=False,
                is_genuine=True,
                status="planted",
            )
        ],
    )
    paragraph = "".join(SENTENCE for _ in range(PARAGRAPH_CHARS // len(SENTENCE) + 1))[
        :PARAGRAPH_CHARS
    ]
    for i in range(1, CHAPTERS + 1):
        text = "\n\n".join(paragraph for _ in range(PARAGRAPHS_PER_CHAPTER))
        (workspace.chapters_dir / f"ch{i:02d}.md").write_text(text, encoding="utf-8")
    return workspace


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
    root = workdir / "stories"
    root.mkdir()
    measurements: dict[str, Any] = {
        "dataset": {
            "chapters": CHAPTERS,
            "chars_per_chapter": PARAGRAPHS_PER_CHAPTER * PARAGRAPH_CHARS,
        }
    }
    try:
        # 1) V1 源构造
        built = _timed(lambda: _build_v1_workspace(root))
        workspace: StoryWorkspace = built["result"]
        measurements["build_v1_workspace"] = _strip(built)

        # 2) 幂等导入（迁移主路径，一次性）
        repo = build_sqlite_repository(str(db_path), SystemClock(), UuidIdGenerator())
        service = repo.service
        importer = V1WorkspaceImporter(service, service, actor=ACTOR)
        report = importer.dry_run(workspace)
        if not report.ok:
            print(json.dumps({"error": "报告未通过"}, ensure_ascii=False))
            return 1
        project = service.create_project("容量测量").id
        applied = _timed(
            lambda: importer.apply(workspace, project_id=project, confirm_run_id=report.run_id)
        )
        result = applied["result"]
        assert result is not None and result.validation.ok, "导入校验未通过"
        measurements["import_commit"] = _strip(applied)
        head = CommitId(result.new_head)

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
