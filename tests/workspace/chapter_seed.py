"""测试用建章夹具：作者端建章接口已移除，测试直接走内部事务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hastings.workspace.files import Workspace
from hastings.workspace.state import conversation_root
from hastings.workspace.story_models import FORMAT_VERSION
from hastings.workspace.story_store import StoryStore
from hastings.workspace.writing_actions import create_chapter


def seed_chapter(
    state_root: Path,
    workspace_id: str,
    root: Path,
    *,
    title: str | None,
    expected_plan_revision: str = "empty",
    request_key: str = "chapter:seed",
) -> dict[str, Any]:
    workspace = Workspace.open(root)
    store = StoryStore(
        conversation_root(state_root, workspace_id),
        workspace,
        format_version=FORMAT_VERSION,
    )
    result = create_chapter(
        store,
        workspace,
        title=title,
        after_chapter_id=None,
        expected_plan_revision=expected_plan_revision,
        request_key=request_key,
    )
    return {
        "chapter_id": result.chapter_id,
        "order": result.order,
        "path": result.path,
        "prose_digest": result.prose_digest,
        "plan_revision": result.plan_revision,
    }
