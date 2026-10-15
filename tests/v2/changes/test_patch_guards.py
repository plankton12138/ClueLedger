from typing import Any

from hastings.application.workflows.revision import ExecuteIntent
from hastings.domain.changes.execution import PatchUnit

from .test_branch_executor import FakeWorker, _approved, _executor, _full_patches


def test_worker_cannot_move_text_unit_to_another_chapter(changes_env: Any) -> None:
    env = changes_env
    project, _base, cs = _approved(env)
    patches = _full_patches()
    unit = patches["step_manuscript"][0]
    patches["step_manuscript"] = (
        PatchUnit(unit.artifact_id, {**unit.content, "chapter_id": "stolen"}),
    )
    result = _executor(env).execute(ExecuteIntent(project.id, cs, "guard"), FakeWorker(patches))
    assert result.status == "failed"
    assert result.error_code == "CHANGES_PROPOSAL_OUT_OF_SCOPE"
