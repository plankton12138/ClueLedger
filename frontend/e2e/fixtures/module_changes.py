"""为模块变更的浏览器验收预置一轮会话与真实变更集；不调用模型。

用法：
    python module_changes.py <作品目录> <状态根> <作品ID> <模式> [模块ID] [版本ID] [绑定ID]

模式：
- ``create_applied``：准备并应用两份新建内容，会话里留下准备、逐项保存、应用与结束事件；
- ``update_prepared``：只准备一份对已有模块的修改，留下准备事件与尚未结束本轮的状态。

事件结构与模型工具路径一致，界面据此投影，不靠回复正文猜结果。
"""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

from hastings.library import ModuleLibrary
from hastings.library.changes import ChangeEdit, ChangeOperation, ModuleChangeService
from hastings.workspace.files import Workspace
from hastings.workspace.state import ConversationStore, conversation_root

workspace_root = Path(sys.argv[1])
state_root = Path(sys.argv[2])
workspace_id = sys.argv[3]
mode = sys.argv[4]
module_id = sys.argv[5] if len(sys.argv) > 5 else ""
revision_id = sys.argv[6] if len(sys.argv) > 6 else ""
binding_id = sys.argv[7] if len(sys.argv) > 7 else ""

store = ConversationStore(conversation_root(state_root, workspace_id))
turn_id = f"t-e2e-{mode}-{secrets.token_hex(3)}"
accepted = store.create_with_first_turn(
    text="请整理当前作品的人物与聚集前提。",
    request_key=f"e2e-module-changes-{secrets.token_hex(8)}",
    turn_id=turn_id,
)
conversation_id = accepted.conversation_id

library = ModuleLibrary(state_root.parent / "library", cache_file=state_root / "library-catalog.json")
service = ModuleChangeService(library, Workspace(workspace_root))

if mode == "create_applied":
    prepared = service.prepare(
        turn_id=turn_id,
        operations=(
            ChangeOperation(
                operation_id="cast",
                action="create",
                name="雾港人物小传",
                body="# 核心人物\n\n陈默在码头修钟，台风夜没有登船。",
            ),
            ChangeOperation(
                operation_id="premise",
                action="create",
                name="聚集缘由",
                body="# 聚集\n\n台风封港，旅客被困在候船室。",
            ),
        ),
        base_digest=service.assembly.store.digest(),
    )
    result = service.apply(change_set_id=str(prepared["change_set_id"]), turn_id=turn_id)
    if not result.get("applied"):
        raise SystemExit(f"预置变更没有应用：{result.get('error')}")
elif mode == "update_prepared":
    if not (module_id and revision_id and binding_id):
        raise SystemExit("update_prepared 需要模块、版本与绑定")
    _raw, digest = library.storage.read_entry(module_id, revision_id, "content")
    prepared = service.prepare(
        turn_id=turn_id,
        operations=(
            ChangeOperation(
                operation_id="cast",
                action="update",
                name=library.module(module_id).name,
                binding_id=binding_id,
                module_id=module_id,
                base_revision=revision_id,
                edits=(
                    ChangeEdit(
                        entry_id="content",
                        base_digest=digest,
                        find="陈默修理码头的钟。",
                        replace="陈默改行做了渡轮检修员。",
                    ),
                ),
            ),
        ),
        base_digest=service.assembly.store.digest(),
    )
    result = None
else:
    raise SystemExit(f"未知模式：{mode}")

store.append(conversation_id, {"type": "module_changes_prepared", "turn_id": turn_id, **prepared})
if result is not None:
    for saved in result.get("saved_events") or []:
        store.append(
            conversation_id, {"type": "module_content_saved", "turn_id": turn_id, **saved}
        )
    store.append(
        conversation_id,
        {
            "type": "module_changes_applied",
            "turn_id": turn_id,
            "change_set_id": result.get("change_set_id"),
            "assembly": result.get("assembly"),
            "operations": result.get("operations"),
            "saved": result.get("saved"),
            "undo": result.get("undo"),
        },
    )
    status = "completed"
else:
    status = "partial"
store.append(
    conversation_id,
    {
        "type": "run_finished",
        "turn_id": turn_id,
        "status": status,
        "recording": "no_change",
        "changed_paths": [],
    },
)

print(
    json.dumps(
        {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "change_set_id": prepared["change_set_id"],
            "base_digest": prepared.get("base_assembly_digest"),
            "module_names": [row.get("name") for row in prepared["operations"]],
        },
        ensure_ascii=False,
    )
)
