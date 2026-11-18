"""Suite / Env / Evaluator 资产加载。"""

from __future__ import annotations

from pathlib import Path

import yaml
from evaluation.harness.assets import (
    load_env_by_id,
    load_evaluator_by_id,
    load_suite_by_id,
)
from evaluation.harness.runner_api import resolve_run_env_knobs
from evaluation.harness.schema import load_case

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "evaluation"


def test_load_repo_assets() -> None:
    suite = load_suite_by_id("smoke_revise", suites_dir=EVAL / "suites")
    assert suite.case_ids == ("revise_ch1_ending_farm_tonight",)
    assert suite.default_evaluator == "revise_v1"
    env = load_env_by_id("local_default", envs_dir=EVAL / "envs")
    assert env.budget == 30
    pack = load_evaluator_by_id("revise_v1", evaluators_dir=EVAL / "evaluators")
    assert pack.version == 1
    assert any(c.id == "committed_via_candidate" for c in pack.checks)


def test_case_resolves_evaluator_pack() -> None:
    case = load_case(
        EVAL / "cases" / "revise_ch1_ending_farm_tonight.yaml",
        works_root=EVAL / "works",
        evaluators_dir=EVAL / "evaluators",
    )
    assert case.evaluator_id == "revise_v1"
    assert case.evaluator_version == 1
    assert [c.id for c in case.checks][0] == "only_region_changed"
    assert all(c.role == "required" for c in case.checks)


def test_checks_extra_overrides(tmp_path: Path) -> None:
    pack_dir = tmp_path / "evaluators"
    pack_dir.mkdir()
    (pack_dir / "demo.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "demo",
                "version": 1,
                "checks": [
                    {"id": "only_region_changed", "level": "must", "role": "required"},
                    {
                        "id": "chapter_in_model_input",
                        "level": "quality",
                        "role": "diagnostic",
                    },
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    work = tmp_path / "works" / "base" / "inn"
    chapter = work / "正文" / "a.md"
    chapter.parent.mkdir(parents=True)
    chapter.write_text("前文。\n目标句。\n", encoding="utf-8")
    case_path = tmp_path / "case.yaml"
    case_path.write_text(
        yaml.safe_dump(
            {
                "id": "demo_case",
                "version": 1,
                "work": "base/inn",
                "history": "short",
                "evaluator": "demo",
                "checks_extra": [
                    {
                        "id": "chapter_in_model_input",
                        "level": "quality",
                        "role": "required",
                    }
                ],
                "steps": [{"author": "改", "focus": "正文/a.md"}],
                "expect": {
                    "region": {"path": "正文/a.md", "from": "目标句", "to": "END"},
                    "preserve": [],
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    case = load_case(case_path, works_root=tmp_path / "works", evaluators_dir=pack_dir)
    by_id = {c.id: c for c in case.checks}
    assert by_id["chapter_in_model_input"].role == "required"


def test_resolve_run_env_knobs_uses_override_env(tmp_path: Path) -> None:
    envs = tmp_path / "envs"
    envs.mkdir()
    (envs / "tight.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "tight",
                "version": 1,
                "capture_inputs": False,
                "budget": 7,
                "timeout_s": 12.5,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    env_id, budget, timeout_s, capture = resolve_run_env_knobs(
        "tight",
        max_requests=None,
        timeout_s=None,
        envs_dir=envs,
    )
    assert env_id == "tight"
    assert budget == 7
    assert timeout_s == 12.5
    assert capture is False

    env_id2, budget2, timeout2, capture2 = resolve_run_env_knobs(
        "tight",
        max_requests=99,
        timeout_s=1.0,
        envs_dir=envs,
    )
    assert env_id2 == "tight"
    assert budget2 == 99
    assert timeout2 == 1.0
    assert capture2 is False
