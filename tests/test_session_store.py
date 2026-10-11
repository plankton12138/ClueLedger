"""多会话存储层测试（P10 T14，D13）：索引 CRUD + 旧单会话迁移 + 损坏重建。

纯函数 + dataclass 层，全部同步（无 harness/asyncio）——REST/CLI/工厂共用的
唯一会话解析点，这里钉住存储语义。
"""

import json
from pathlib import Path

import pytest

from hastings.bible.store import StoryWorkspace
from hastings.session_store import (
    SessionNotFoundError,
    activate_session,
    active_session_id,
    active_session_path,
    create_session,
    delete_session,
    legacy_session_file,
    list_sessions,
    load_index,
    rename_session,
    session_index_file,
    session_path,
    sessions_dir,
    touch_session,
)

SLUG = "orient-express"


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    ws = StoryWorkspace(root=tmp_path, slug=SLUG)
    ws.ensure_dirs()
    return ws


def test_load_index_creates_first_session_for_new_workspace(workspace: StoryWorkspace) -> None:
    index = load_index(workspace)
    assert len(index.sessions) == 1
    assert index.active_id == index.sessions[0].id
    assert index.sessions[0].message_count == 0
    # 索引落盘、活跃路径指向 sessions/ 下
    assert session_index_file(workspace).exists()
    assert sessions_dir(workspace).is_dir()
    assert active_session_path(workspace) == session_path(workspace, active_session_id(workspace))


def test_load_index_is_stable_across_calls(workspace: StoryWorkspace) -> None:
    first = load_index(workspace)
    second = load_index(workspace)
    assert first == second  # 不重复迁移/新建


def test_load_index_migrates_legacy_single_session(workspace: StoryWorkspace) -> None:
    legacy = legacy_session_file(workspace)
    legacy.write_text('{"a":1}\n\n{"b":2}\n', encoding="utf-8")  # 空行不计

    index = load_index(workspace)

    assert len(index.sessions) == 1
    meta = index.sessions[0]
    assert meta.message_count == 2
    assert not legacy.exists()
    assert session_path(workspace, meta.id).read_text(encoding="utf-8").startswith('{"a":1}')


def test_load_index_rebuilds_from_disk_when_corrupt(workspace: StoryWorkspace) -> None:
    """索引损坏 → 备份 + 从 sessions/ 磁盘文件重建，不静默丢会话。"""
    directory = sessions_dir(workspace)
    directory.mkdir(parents=True, exist_ok=True)
    kept = directory / "session-20260101-000000-abcd.jsonl"
    kept.write_text('{"a":1}\n', encoding="utf-8")
    session_index_file(workspace).write_text("{ not json", encoding="utf-8")

    index = load_index(workspace)

    assert [meta.id for meta in index.sessions] == ["session-20260101-000000-abcd"]
    assert index.active_id == "session-20260101-000000-abcd"
    assert index.sessions[0].message_count == 1
    assert list(workspace.story_dir.glob("sessions.json.bad-*"))  # 损坏索引已备份


def test_create_session_adds_and_activates(workspace: StoryWorkspace) -> None:
    first = load_index(workspace).active_id
    created = create_session(workspace, title="第二稿")

    assert created.id != first
    assert created.title == "第二稿"
    assert active_session_id(workspace) == created.id
    assert {meta.id for meta in list_sessions(workspace)} == {first, created.id}


def test_create_session_default_title_when_absent(workspace: StoryWorkspace) -> None:
    assert create_session(workspace).title == "会话 2"  # 首个为「会话 1」


def test_activate_session_switches_and_touches(workspace: StoryWorkspace) -> None:
    first = load_index(workspace).active_id
    create_session(workspace)

    meta = activate_session(workspace, first)

    assert meta.id == first
    assert active_session_id(workspace) == first


def test_rename_session_changes_only_title(workspace: StoryWorkspace) -> None:
    target = load_index(workspace).active_id
    renamed = rename_session(workspace, target, "改名")
    assert renamed.title == "改名"
    assert active_session_id(workspace) == target  # 活跃不变
    assert list_sessions(workspace)[0].title == "改名"


def test_delete_non_active_keeps_active(workspace: StoryWorkspace) -> None:
    first = load_index(workspace).active_id
    created = create_session(workspace).id

    active = delete_session(workspace, first)

    assert active == created  # 活跃未被删，保持
    assert [meta.id for meta in list_sessions(workspace)] == [created]


def test_delete_active_switches_to_remaining(workspace: StoryWorkspace) -> None:
    first = load_index(workspace).active_id
    created = create_session(workspace).id
    session_path(workspace, created).write_text("x\n", encoding="utf-8")

    active = delete_session(workspace, created)

    assert active == first
    assert not session_path(workspace, created).exists()
    assert [meta.id for meta in list_sessions(workspace)] == [first]


def test_delete_last_session_creates_empty_replacement(workspace: StoryWorkspace) -> None:
    only = load_index(workspace).active_id
    active = delete_session(workspace, only)
    assert active != only
    assert [meta.id for meta in list_sessions(workspace)] == [active]


def test_missing_session_raises(workspace: StoryWorkspace) -> None:
    with pytest.raises(SessionNotFoundError) as excinfo:
        activate_session(workspace, "session-00000000-000000-0000")
    message = str(excinfo.value)
    assert "会话不存在" in message
    assert "怎样算对" in message


def test_touch_session_increments_count(workspace: StoryWorkspace) -> None:
    target = load_index(workspace).active_id
    touch_session(workspace, target)
    touch_session(workspace, target)
    assert list_sessions(workspace)[0].message_count == 2


def test_index_file_is_valid_json(workspace: StoryWorkspace) -> None:
    load_index(workspace)
    data = json.loads(session_index_file(workspace).read_text(encoding="utf-8"))
    assert data["active_id"]
    assert isinstance(data["sessions"], list)
