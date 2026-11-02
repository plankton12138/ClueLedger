"""P7 性能观察：200 章 / 300 条信息线的首屏投影。

构造一个隔离样本，测全书与单章工作台投影的服务端耗时与响应体大小。
样本用直接写入状态文件的方式构造，不经过 200 次提交，避免把构造时间混进测量。

运行：.venv/Scripts/python.exe dev-notes/v1.0.9/artifacts/perf/profile_workbench.py
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from hastings.workspace.chapter_write_policy import ChapterWritePolicy
from hastings.workspace.files import Workspace
from hastings.workspace.story_store import (
    EMPTY_REVISION,
    MAIN_VARIANT_ID,
    StoryStore,
    new_id,
)
from hastings.workspace.writing_actions import create_chapter, save_book_intent
from hastings.workspace.writing_view import build_workbench

ROOT = Path("data/test-tmp/p7-perf")
BOOK = ROOT / "雪夜旅馆"
STATE = ROOT / "state"
CHAPTERS = 200
THREADS = 300
RUNS = 5


def build_sample() -> tuple[Workspace, StoryStore]:
    if ROOT.exists():
        import shutil

        shutil.rmtree(ROOT)
    workspace = Workspace.create(BOOK)
    store = StoryStore(STATE, workspace, format_version=4)
    workspace.chapter_policy = ChapterWritePolicy(store)
    save_book_intent(
        store,
        text="暴雪封山的旅馆发生命案，林砚从湿手套开始怀疑管家。",
        expected_plan_revision=EMPTY_REVISION,
        request_key="perf:intent",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    first = create_chapter(
        store,
        workspace,
        title="第1章",
        after_chapter_id=None,
        expected_plan_revision=variant.revision,
        request_key="perf:chapter:1",
    )
    assert first.order == 0

    document = json.loads(store.file.read_text(encoding="utf-8"))
    current = document["variants"][MAIN_VARIANT_ID]
    existing = next(iter(current["chapter_plans"].values()))
    plans: dict[str, object] = {}
    threads: dict[str, object] = {}
    moves: dict[str, object] = {}
    bindings: dict[str, object] = {}
    chapter_ids: dict[int, str] = {}

    for index in range(CHAPTERS):
        number = index + 1
        chapter_id = existing["id"] if index == 0 else f"ch_bench_{number:04d}"
        path = existing["prose_path"] if index == 0 else f"正文/{number:04d} 第{number}章.md"
        chapter_ids[number] = chapter_id
        plans[chapter_id] = {
            "id": chapter_id,
            "order": index,
            "title": f"第{number}章",
            "prose_path": path,
        }
        text = (
            f"# 第{number}章\n\n"
            f"第{number}章的第一句。\n\n"
            f"第{number}章的第二个线索。\n\n"
            + "雪落在窗上，屋里的人都没有说话。" * 12
            + "\n"
        )
        if index != 0:
            workspace.write(path, text, expected_digest=None)
        else:
            current_text = workspace.read(path).text
            if len(current_text) < 40:
                workspace.write(path, text, expected_digest=workspace.read(path).digest)

    for index in range(THREADS):
        number = index + 1
        chapter_number = (index % CHAPTERS) + 1
        chapter_id = chapter_ids[chapter_number]
        thread_id = f"thread_{number:04d}"
        move_id = f"move_{number:04d}"
        binding_id = f"binding_{number:04d}"
        quote = (
            f"第{chapter_number}章的第一句。"
            if index < CHAPTERS
            else f"第{chapter_number}章的第二个线索。"
        )
        path = f"正文/{chapter_number:04d} 第{chapter_number}章.md"
        prose = workspace.read(path).text
        start = prose.find(quote)
        threads[thread_id] = {
            "id": thread_id,
            "summary": f"第{number}条信息线：雪夜旅馆的线索",
        }
        moves[move_id] = {
            "id": move_id,
            "thread_id": thread_id,
            "chapter_id": chapter_id,
            "statement": f"第{number}条信息：{quote}",
            "effect": None,
        }
        bindings[binding_id] = {
            "id": binding_id,
            "node_id": None,
            "clue_id": None,
            "move_id": move_id,
            "document_id": chapter_id,
            "path": path,
            "digest": workspace.read(path).digest,
            "span_digest": "",
            "source": "manual",
            "start": start,
            "end": start + len(quote),
            "quote": quote,
            "observation": "",
            "assessment_id": None,
            "contract_digest": None,
        }

    current["chapter_plans"] = plans
    current["information_threads"] = threads
    current["information_moves"] = moves
    current["bindings"] = bindings
    current["revision"] = new_id("rev")
    document["revision"] = new_id("rev")
    store.file.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return workspace, store


def measure(workspace: Workspace, store: StoryStore, chapter_id: str | None) -> dict[str, object]:
    variant = store.require_variant(MAIN_VARIANT_ID)
    timings: list[float] = []
    payload = ""
    for _ in range(RUNS):
        started = time.perf_counter()
        view = build_workbench(
            workspace,
            variant,
            workspace_id="perf",
            chapter_id=chapter_id,
        )
        timings.append((time.perf_counter() - started) * 1000)
        payload = view.model_dump_json()
    return {
        "median_ms": round(statistics.median(timings), 1),
        "min_ms": round(min(timings), 1),
        "max_ms": round(max(timings), 1),
        "bytes": len(payload.encode()),
    }


def main() -> None:
    workspace, store = build_sample()
    variant = store.require_variant(MAIN_VARIANT_ID)
    chapter_id = next(iter(sorted(variant.chapter_plans.values(), key=lambda row: row.order))).id
    result = {
        "sample": {
            "chapters": len(variant.chapter_plans),
            "threads": len(variant.information_threads),
            "moves": len(variant.information_moves),
            "bindings": len(variant.bindings),
            "runs": RUNS,
        },
        "book": measure(workspace, store, None),
        "chapter": measure(workspace, store, chapter_id),
    }
    out = Path(__file__).with_name("workbench-200.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
