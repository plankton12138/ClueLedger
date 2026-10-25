"""为隔离浏览器样例准备一份原文已变化的章节观察，不调用模型。"""

import sys
from pathlib import Path

from hastings.workspace.activity import ActivityStore
from hastings.workspace.files import Workspace
from hastings.workspace.story_index import ChapterProjection, StoryIndexStore

workspace = Workspace(Path(sys.argv[1]))
state_root = Path(sys.argv[2])
path = "正文/0001 雨夜.md"
source = workspace.read(path)
StoryIndexStore(state_root).publish(
    ChapterProjection(path=path, digest=source.digest, chapter=1, summary="侦探抵达港口。"),
    expected_digest=source.digest,
)
workspace.write(path, source.text + "\n原文新增了一份潮汐表。\n", expected_digest=source.digest)

# 明确标为隔离夹具的操作日志，供活动读取和导出验证；不创建模型请求。
with ActivityStore(state_root).operation("git_record", path=path) as operation:
    operation.result["message"] = "隔离样例：记录章节改动"
