"""会话日志、界面状态与上下文回放（0.4 V04-10）。

契约：正式会话从「第一条非空提示被受理」开始存在；日志是受理成立的依据，索引可以重建；
同一请求键不会产生第二个会话或第二轮；界面草稿按会话分开保存，过期版本不得覆盖新内容；
跨轮只回放作者提示与公开回复，执行事实由应用单独说明。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hastings.workspace.conversation_context import execution_facts, replay_public_messages
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.state import (
    META_TYPE,
    ConversationStore,
    ConversationUiStore,
    conversation_root,
)
from tau_agent.messages import AssistantMessage, UserMessage


def _store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(conversation_root(tmp_path / "state", "w1"))


# -- 受理与日志 -----------------------------------------------------------


def test_first_turn_publishes_meta_and_turn_together(tmp_path: Path) -> None:
    store = _store(tmp_path)

    accepted = store.create_with_first_turn(text="  写第一章  ", request_key="k1", turn_id="t1")

    assert accepted.last_seq == 1
    assert accepted.title == "写第一章"
    lines = store.events_file(accepted.conversation_id).read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    event = json.loads(lines[1])
    assert (meta["type"], meta["version"], meta["seq"]) == (META_TYPE, 2, 0)
    assert event["type"] == "turn_started"
    # 界面事件流看不到元信息行：它只服务于会话重建。
    assert [item["type"] for item in store.events(accepted.conversation_id)] == ["turn_started"]
    assert store.conversations()[0].title == "写第一章"


def test_same_request_key_reuses_the_accepted_turn(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t1")

    again = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t9")

    assert again.turn_id == first.turn_id
    assert again.conversation_id == first.conversation_id
    assert len(store.conversations()) == 1
    assert (
        len(
            [item for item in store.events(first.conversation_id) if item["type"] == "turn_started"]
        )
        == 1
    )


def test_same_request_key_with_other_text_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t1")

    with pytest.raises(WorkspaceError) as conflict:
        store.create_with_first_turn(text="写第二章", request_key="k1", turn_id="t2")

    assert conflict.value.code == "REQUEST_KEY_CONFLICT"
    assert len(store.conversations()) == 1


def test_request_key_cannot_move_to_another_conversation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create_with_first_turn(text="A", request_key="k1", turn_id="t1")
    second = store.create_with_first_turn(text="B", request_key="k2", turn_id="t2")

    with pytest.raises(WorkspaceError) as conflict:
        store.accept_turn(second.conversation_id, text="A", request_key="k1", turn_id="t3")

    assert conflict.value.code == "REQUEST_KEY_CONFLICT"
    assert first.conversation_id != second.conversation_id


def test_accept_turn_appends_with_model_facts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t1")

    store.accept_turn(
        accepted.conversation_id,
        text="继续",
        request_key="k2",
        turn_id="t2",
        facts={"model": "m1", "connection_name": "我的连接"},
    )

    last = store.events(accepted.conversation_id)[-1]
    assert (last["type"], last["text"], last["model"]) == ("turn_started", "继续", "m1")
    assert last["connection_name"] == "我的连接"


def test_blank_prompt_creates_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(WorkspaceError) as blank:
        store.create_with_first_turn(text="   ", request_key="k1", turn_id="t1")

    assert blank.value.code == "PROMPT_EMPTY"
    assert store.conversations() == ()
    assert not list(store.root.glob("*.jsonl"))


def test_index_is_rebuilt_from_logs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t1")
    store.registry_file.unlink()

    rebuilt = _store(tmp_path).conversations()

    assert [item.id for item in rebuilt] == [accepted.conversation_id]
    assert rebuilt[0].title == "写第一章"
    # 重建后索引被写回：下一次读取不再依赖扫描。
    assert store.registry_file.is_file()


def test_legacy_log_without_meta_is_still_readable(tmp_path: Path) -> None:
    """旧日志没有元信息行：按首条提示补出会话，不要求重写旧记录。"""
    store = _store(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "c_legacy.jsonl").write_text(
        json.dumps(
            {
                "seq": 1,
                "at": "2026-10-01T10:00:00+08:00",
                "type": "turn_started",
                "turn_id": "t_old",
                "text": "旧对话",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    conversations = store.conversations()

    assert [item.id for item in conversations] == ["c_legacy"]
    assert conversations[0].title == "旧对话"


def test_unfinished_turns_are_paired_per_turn(tmp_path: Path) -> None:
    store = _store(tmp_path)
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t1")
    conversation_id = accepted.conversation_id
    # 崩在工具阶段：最后一条不是 turn_started。
    store.append(conversation_id, {"type": "tool_started", "turn_id": "t1", "tool": "read_file"})
    store.append(conversation_id, {"type": "run_finished", "turn_id": "t1", "status": "completed"})
    store.append(conversation_id, {"type": "turn_started", "turn_id": "t2", "text": "继续"})
    store.append(conversation_id, {"type": "tool_finished", "turn_id": "t2", "tool": "read_file"})

    assert store.unfinished_turns(conversation_id) == ["t2"]


def test_rename_rejects_blank_and_bumps_activity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t1")
    before = store.get(accepted.conversation_id)

    with pytest.raises(WorkspaceError) as blank:
        store.rename(accepted.conversation_id, "   ")
    assert blank.value.code == "CONVERSATION_TITLE_INVALID"

    renamed = store.rename(accepted.conversation_id, " 人物动机 ")
    assert renamed.title == "人物动机"
    assert renamed.created_at == before.created_at


def test_revision_changes_with_the_conversation_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    empty = store.revision()
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t1")

    assert store.revision() != empty
    store.rename(accepted.conversation_id, "改个名")
    assert store.revision() != empty


# -- 界面状态 -------------------------------------------------------------


def test_ui_state_starts_empty_and_persists_drafts(tmp_path: Path) -> None:
    ui = ConversationUiStore(conversation_root(tmp_path / "state", "w1"))

    initial = ui.read()
    assert (initial.recent_id, initial.drafts, initial.revision) == (None, {}, "0")

    saved = ui.update(
        expected_revision="0",
        set_draft=True,
        draft_target="c1",
        draft_text="  先写开头  ",
    )
    assert saved.drafts == {"c1": "先写开头"}
    assert ConversationUiStore(ui.root).read().drafts == {"c1": "先写开头"}


def test_ui_state_rejects_stale_revision(tmp_path: Path) -> None:
    ui = ConversationUiStore(conversation_root(tmp_path / "state", "w1"))
    first = ui.update(expected_revision="0", set_draft=True, draft_target="new", draft_text="A")

    with pytest.raises(WorkspaceError) as conflict:
        ui.update(expected_revision="0", set_draft=True, draft_target="new", draft_text="B")

    assert conflict.value.code == "CONVERSATION_UI_CONFLICT"
    assert ui.read().drafts == {"new": "A"}
    assert first.revision != "0"


def test_ui_state_merges_only_the_given_target(tmp_path: Path) -> None:
    ui = ConversationUiStore(conversation_root(tmp_path / "state", "w1"))
    first = ui.update(expected_revision="0", set_draft=True, draft_target="c1", draft_text="一")
    second = ui.update(
        expected_revision=first.revision,
        set_draft=True,
        draft_target="c2",
        draft_text="二",
        set_recent=True,
        recent_id="c2",
    )

    assert second.drafts == {"c1": "一", "c2": "二"}
    assert second.recent_id == "c2"

    cleared = ui.update(expected_revision=second.revision, set_draft=True, draft_target="c1")
    assert cleared.drafts == {"c2": "二"}
    assert cleared.recent_id == "c2"


def test_ui_state_clears_recent(tmp_path: Path) -> None:
    ui = ConversationUiStore(conversation_root(tmp_path / "state", "w1"))
    saved = ui.update(expected_revision="0", set_recent=True, recent_id="c1")

    cleared = ui.update(expected_revision=saved.revision, set_recent=True, recent_id=None)

    assert cleared.recent_id is None


def test_corrupt_ui_state_is_treated_as_empty(tmp_path: Path) -> None:
    root = conversation_root(tmp_path / "state", "w1")
    root.mkdir(parents=True)
    (root / "conversation-ui.json").write_text("{ not json", encoding="utf-8")

    ui = ConversationUiStore(root).read()

    assert (ui.recent_id, ui.drafts) == (None, {})


# -- 上下文回放 -----------------------------------------------------------


def _events() -> list[dict[str, object]]:
    return [
        {"seq": 1, "type": "turn_started", "turn_id": "t1", "text": "记住：侦探不能是凶手"},
        {"seq": 2, "type": "tool_started", "turn_id": "t1", "tool": "read_file"},
        {"seq": 3, "type": "tool_finished", "turn_id": "t1", "tool": "read_file"},
        {"seq": 4, "type": "assistant_message", "turn_id": "t1", "text": "记住了。"},
        {
            "seq": 5,
            "type": "run_finished",
            "turn_id": "t1",
            "status": "completed",
            "recording": "no_change",
            "changed_paths": [],
        },
        {"seq": 6, "type": "turn_started", "turn_id": "t2", "text": "写第一章"},
    ]


def test_replay_keeps_only_prompts_and_public_replies() -> None:
    messages = replay_public_messages(_events())

    assert [type(message) for message in messages] == [UserMessage, AssistantMessage, UserMessage]
    assert [message.text for message in messages] == [
        "记住：侦探不能是凶手",
        "记住了。",
        "写第一章",
    ]


def test_execution_facts_describe_actual_outcome() -> None:
    facts = execution_facts(_events())

    assert "「记住：侦探不能是凶手」" in facts
    assert "完成" in facts
    assert "没有改动文件" in facts
    assert "没有改动" in facts


def test_execution_facts_skip_unfinished_turns() -> None:
    events = [
        {"seq": 1, "type": "turn_started", "turn_id": "t1", "text": "写第一章"},
        {"seq": 2, "type": "tool_started", "turn_id": "t1", "tool": "read_file"},
    ]

    assert execution_facts(events) == ""
