from typing import Any

from hastings.application.workflows.change_analysis import AnalyzeChangeImpact, AnalyzeIntent
from hastings.domain.changes.impact import ImpactItem

from .conftest import AUTHOR, catalog_of
from .test_branch_executor import _open, _seed_chain


def test_semantic_failure_preserves_deterministic_report(changes_env: Any) -> None:
    env = changes_env
    project, base = _seed_chain(env)
    cs = _open(env, project, base)

    def unavailable(context: dict[str, Any]) -> tuple[ImpactItem, ...]:
        raise OSError("semantic provider offline")

    flow = AnalyzeChangeImpact(env.handler, env.service, env.clock, AUTHOR, semantic=unavailable)
    result = flow.analyze(AnalyzeIntent(project.id, cs, "semantic"))
    report = catalog_of(env, project.id).get(result.report_id).content
    assert {i["target_artifact_id"] for i in report["items"]} == {"art_b", "art_c", "tu_x"}
    assert report["coverage"] == "deterministic_only"
    assert "offline" in report["warnings"][0]
