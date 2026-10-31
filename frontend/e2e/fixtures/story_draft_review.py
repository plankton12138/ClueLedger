"""隔离浏览器夹具：无模块来源的约定等待作者审阅，不调用模型。"""

import sys
from pathlib import Path

from hastings.workspace.operations import Operation, OperationStore
from hastings.workspace.state import ConversationStore, conversation_root
from hastings.workspace.story_drafts import StoryDraftStore
from hastings.workspace.story_models import StoryVariant

state, wid = Path(sys.argv[1]), sys.argv[2]
root = conversation_root(state, wid)
store = ConversationStore(root)
info = store.create_with_first_turn(text="创建构思", request_key="fixture", turn_id="t-review")
operation = Operation(
    id="op-review", owner=f"workspace:{wid}", workspace_id=wid,
    conversation_id=info.conversation_id, turn_id="t-review", purpose="chat",
    status="needs_attention", resumable=True, recovery_request={"fixture": True},
)
OperationStore(state).create(operation)
store.append(info.conversation_id, {"type": "run_finished", "turn_id": "t-review",
    "operation_id": operation.id, "generation": 1, "status": "partial"})
StoryDraftStore(root, operation.id, "main").prepare(
    variant=StoryVariant(id="main", name="构思", revision="empty"),
    operation_id=operation.id, generation=1, replacements={}, operations=[{
        "op": "add_constraint", "constraint": {
            "id": "c1", "text": "至少六名嫌疑人", "strength": "required",
            "scope": {"kind": "book"}, "revision": "r1",
        },
    }],
)
