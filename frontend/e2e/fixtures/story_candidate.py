"""仅为浏览器验证创建持久候选，不运行模型、不修改正式正文。"""
import sys
from pathlib import Path

from hastings.workspace.files import Workspace
from hastings.workspace.story_candidate import StoryCandidate
from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_store import StoryStore

workspace = Workspace.open(Path(sys.argv[1]))
root = Path(sys.argv[2])
variant = StoryStore(root, workspace).require_variant("main")
item = StoryCandidate(workspace, root,
    StoryContextSnapshot(variant_id="main", revision=variant.revision,
                         purpose="write", node_ids=("n1",)),
    "browser-fixture", 1, lambda: None, lambda kind, payload: None, "改写雨夜场景")
path = "正文/0001 雨夜.md"
item.propose(path, workspace.read(path).digest, "# 雨夜\n\n候选里侦探收起了湿伞。")
item.status = "needs_input"
item.save()
