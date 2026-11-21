import json
from pathlib import Path

import pytest
from eval.benchmark import workbench_index as index

BENCHMARK = Path(__file__).resolve().parents[1] / "eval" / "benchmark"
T01 = BENCHMARK / "tasks" / "t01.json"


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def task_variant(path, task_id, revision):
    task = json.loads(T01.read_bytes())
    task["scenario"].update(id=task_id, revision=revision, title=f"示例 {task_id}")
    save(path, task)


def test_catalog_lists_repository_t01_without_fake_entries():
    catalog = index.Catalog.load({"writing": T01.parent})
    assert [(row.library_id, row.task_id, row.revision) for row in catalog.list()] == [
        ("writing", "T01", 1)
    ]
    assert catalog.detail("writing", "T01", 1)["scenario"]["cases"]["main"]["steps"]


def test_catalog_searches_and_pages_latest_revision_per_library(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    for name, task_id, rev in [("x", "T02", 1), ("a", "T01", 2), ("z", "T01", 1)]:
        task_variant(a / f"{name}.json", task_id, rev)
    task_variant(b / "x.json", "T01", 1)
    task_variant(b / "y.json", "T03", 1)
    catalog = index.Catalog.load({"b": b, "a": a})
    page = catalog.list(limit=2)
    assert [(x.library_id, x.task_id, x.revision) for x in page] == [
        ("a", "T01", 2),
        ("a", "T02", 1),
    ]
    assert [(x.library_id, x.task_id) for x in catalog.list(offset=2, limit=2)] == [
        ("b", "T01"),
        ("b", "T03"),
    ]
    assert catalog.revisions("a", "T01") == (2, 1)
    assert catalog.detail("a", "T01", 1)["scenario"]["revision"] == 1
    assert [x.task_id for x in catalog.list(query="示例 t02")] == ["T02"]
    assert [x.task_id for x in catalog.list(library_id="b", query="t01")] == ["T01"]


def test_catalog_rejects_dangling_message(tmp_path):
    task = json.loads(T01.read_bytes())
    task["scenario"]["cases"]["main"]["steps"][0]["message"] = "missing"
    save(tmp_path / "broken.json", task)
    with pytest.raises(ValueError, match="missing.*message|message.*missing"):
        index.Catalog.load({"writing": tmp_path})


def test_catalog_rejects_schema_error(tmp_path):
    task = json.loads(T01.read_bytes())
    del task["llm_graders"]
    save(tmp_path / "broken.json", task)
    with pytest.raises(ValueError, match="llm_graders"):
        index.Catalog.load({"writing": tmp_path})


def test_archive_filters_before_paging_and_orders_by_actual_start(tmp_path):
    path = tmp_path / "index.json"
    task = {"library_id": "writing", "task_id": "T01", "revision": 1}
    rows = [
        dict(
            run_id="prose",
            kind="regression",
            started_at="2026-11-21T17:15:48+08:00",
            verdict="pass",
        ),
        dict(
            run_id="retry",
            task=task,
            started_at="2026-11-21T17:08:05+08:00",
            verdict="inconclusive",
        ),
        dict(run_id="baseline", task=task, started_at="2026-11-21T04:10:22Z", verdict="pass"),
        dict(
            run_id="provider",
            task=task,
            started_at="2026-11-21T11:07:33+08:00",
            execution_status="failed",
            verdict="invalid",
        ),
        dict(run_id="baseline-z", task=task, started_at="2026-11-21T12:10:22+08:00"),
        dict(run_id="missing", task=task),
        dict(run_id="preflight", task=task, execution_status="not_started"),
    ]
    for row in rows:
        row.setdefault("kind", "task")
        row.setdefault("execution_status", "completed")
    save(path, {"archive_version": 1, "runs": rows})
    archive = index.Archive.load(path)
    ref = index.TaskRef("writing", "T01", 1)
    assert [r.run_id for r in archive.list()] == [
        "provider",
        "baseline",
        "baseline-z",
        "retry",
        "prose",
    ]
    assert [r.run_id for r in archive.list(task=ref, limit=2)] == ["provider", "baseline"]
    assert [r.run_id for r in archive.list(task=ref, offset=2, limit=2)] == ["baseline-z", "retry"]
    assert [r.run_id for r in archive.list(kind="regression")] == ["prose"]
    assert [r.run_id for r in archive.list(timing="unknown")] == ["missing"]
    assert [r.run_id for r in archive.list(timing="not_started")] == ["preflight"]
    assert archive.detail("baseline").verdict == "pass"

    # Updating a verdict on the same run must not turn it into a new execution.
    rows[2].update(verdict="inconclusive")
    save(path, {"archive_version": 1, "runs": rows})
    regraded = index.Archive.load(path)
    assert regraded.detail("baseline").verdict == "inconclusive"
    assert [r.run_id for r in regraded.list()] == [r.run_id for r in archive.list()]


def test_archive_rejects_unzoned_start_timestamp(tmp_path):
    path = tmp_path / "index.json"
    save(
        path,
        {
            "archive_version": 1,
            "runs": [
                {
                    "run_id": "bad",
                    "kind": "regression",
                    "execution_status": "completed",
                    "started_at": "2026-11-21T09:00:00",
                }
            ],
        },
    )
    with pytest.raises(ValueError, match="timezone"):
        index.Archive.load(path)
