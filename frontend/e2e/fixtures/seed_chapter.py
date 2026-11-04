"""E2E 建章夹具：作者端建章接口已移除，测试直接走内部事务。

用法：state_root workspace_id workspace_path title request_key expected_plan_revision
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from hastings.workspace.files import Workspace
from hastings.workspace.state import conversation_root
from hastings.workspace.story_models import FORMAT_VERSION
from hastings.workspace.story_store import StoryStore
from hastings.workspace.writing_actions import create_chapter


def main() -> None:
    state_root, workspace_id, workspace_path, title, request_key, expected = sys.argv[1:7]
    workspace = Workspace.open(Path(workspace_path))
    structure = workspace.root / "STRUCTURE.md"
    if not structure.exists():
        structure.write_text("## 铁律\n\n测试用结构。\n", encoding="utf-8")
    store = StoryStore(
        conversation_root(Path(state_root), workspace_id),
        workspace,
        format_version=FORMAT_VERSION,
    )
    result = create_chapter(
        store,
        workspace,
        title=title,
        after_chapter_id=None,
        expected_plan_revision=expected,
        request_key=request_key,
    )
    sys.stdout.write(
        json.dumps(
            {
                "chapter_id": result.chapter_id,
                "order": result.order,
                "path": result.path,
                "prose_digest": result.prose_digest,
                "plan_revision": result.plan_revision,
            }
        )
    )


if __name__ == "__main__":
    main()
