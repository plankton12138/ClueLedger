"""给浏览器准备一个最小构思方案，让全书线索视图可达；不调用模型。

用法：python e2e/fixtures/story_variant_seed.py <状态根目录> <作品 id>
"""

from __future__ import annotations

import sys
from pathlib import Path

from hastings.workspace.state import conversation_root
from hastings.workspace.story_models import StoryChangeSet
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore

state, workspace_id = Path(sys.argv[1]), sys.argv[2]
store = StoryStore(conversation_root(state, workspace_id))
if store.read().document is None:
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(
            id="seed_variant",
            base_revision="empty",
            operations=[
                {
                    "op": "add_node",
                    "node": {"id": "n1", "title": "抵达", "action": "侦探抵达港口"},
                }
            ],
        ),
        request_key="seed-variant",
    )
