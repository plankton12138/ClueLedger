from typing import Any

import pytest

from hastings.application.errors import ViolationError
from hastings.application.handlers.change_commands import ChangeSetCommandHandler

from .conftest import AUTHOR, head_of, make_open_command
from .test_branch_executor import _seed_chain


def test_new_handler_replays_exact_original_result(changes_env: Any) -> None:
    env = changes_env
    project, base = _seed_chain(env)
    command = make_open_command(base_commit_id=base)
    first = env.handler.execute(
        command, project_id=project.id, idempotency_key="open", summary="打开"
    )
    before = head_of(env, project.id)
    restarted = ChangeSetCommandHandler(env.service, env.service, env.ids, AUTHOR)
    again = restarted.execute(
        command, project_id=project.id, idempotency_key="open", summary="打开"
    )
    assert again == first
    assert head_of(env, project.id) == before
    with pytest.raises(ViolationError):
        restarted.execute(
            make_open_command(base_commit_id=base, intent="另一件事"),
            project_id=project.id,
            idempotency_key="open",
            summary="打开",
        )
