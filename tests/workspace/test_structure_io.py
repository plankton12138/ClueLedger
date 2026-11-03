"""v1.1 Q1 M1/M2：结构文档读写门禁与受管路径扩展。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import STRUCTURE_PATH, Workspace, managed_kind
from hastings.workspace.structure_document import STRUCTURE_FILENAME, split_iron_rules
from hastings.workspace.structure_io import read_structure_state, write_structure_document

GOOD = "## 铁律\n第三人称限制视角。\n\n## 路线\n暴雪之夜逐步收束。\n"


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    ws = Workspace.create(tmp_path / "书")

    class NoPolicy:
        """结构文件不经全书构思门禁；若被调用即测试失败。"""

        def require_write(self, path: str) -> None:  # pragma: no cover - 不应被调用
            raise AssertionError(f"结构写入不应触发构思门禁：{path}")

    ws.chapter_policy = NoPolicy()  # type: ignore[assignment]
    return ws


def test_structure_path_constants_agree() -> None:
    assert STRUCTURE_FILENAME == STRUCTURE_PATH
    assert managed_kind(STRUCTURE_PATH) == "structure"


def test_read_structure_state_missing_is_empty(workspace: Workspace) -> None:
    state = read_structure_state(workspace)
    assert state.exists is False
    assert state.text == "" and state.digest is None


def test_write_structure_create_requires_marker(workspace: Workspace) -> None:
    with pytest.raises(WorkspaceError) as exc:
        write_structure_document(
            workspace, text="# 我的作品\n## 铁律\n规则\n", expected_digest=None, creating=True
        )
    assert exc.value.code == "STRUCTURE_MARKER_REQUIRED"
    assert read_structure_state(workspace).exists is False


def test_write_structure_create_roundtrip(workspace: Workspace) -> None:
    view = write_structure_document(workspace, text=GOOD, expected_digest=None, creating=True)
    assert view.path == STRUCTURE_PATH
    state = read_structure_state(workspace)
    assert state.exists and state.digest == view.digest
    assert split_iron_rules(state.text).ok


def test_write_structure_update_requires_digest(workspace: Workspace) -> None:
    first = write_structure_document(workspace, text=GOOD, expected_digest=None, creating=True)
    with pytest.raises(WorkspaceError) as exc:
        write_structure_document(workspace, text=GOOD, expected_digest=None, creating=False)
    assert exc.value.code == "FILE_EXISTS_CONFLICT"
    updated = write_structure_document(
        workspace,
        text=GOOD + "\n## 骨架\n1. 抵达旅馆。\n",
        expected_digest=first.digest,
        creating=False,
    )
    assert updated.digest != first.digest


def test_write_structure_budget_conflict(workspace: Workspace) -> None:
    first = write_structure_document(workspace, text=GOOD, expected_digest=None, creating=True)
    with pytest.raises(WorkspaceError) as exc:
        write_structure_document(
            workspace,
            text="## 铁律\n规则\n" + "x" * 1700,
            expected_digest=first.digest,
            creating=False,
        )
    assert exc.value.code == "STRUCTURE_BUDGET_CONFLICT"
    assert read_structure_state(workspace).digest == first.digest


def test_write_structure_empty_rejected(workspace: Workspace) -> None:
    with pytest.raises(WorkspaceError) as exc:
        write_structure_document(workspace, text="   ", expected_digest=None, creating=True)
    assert exc.value.code == "STRUCTURE_EMPTY"


def test_write_structure_update_allows_lost_marker(workspace: Workspace) -> None:
    """外部编辑弄丢标记时更新如实落盘；降级注入是装配层的事。"""
    first = write_structure_document(workspace, text=GOOD, expected_digest=None, creating=True)
    broken = "# 手改的开头\n没有铁律了\n"
    view = write_structure_document(
        workspace, text=broken, expected_digest=first.digest, creating=False
    )
    assert split_iron_rules(read_structure_state(workspace).text).ok is False
    assert view.digest
