"""WorkspaceRegistry 测试（P4 T4，计划 D4）：多工作区懒装配注册表。

钉住的行为：
- get_or_create 懒装配 + 常驻缓存（同 slug 只装配一次）；
- 同 slug 并发首访不重复装配（per-slug 锁 + 双重检查，R3-③ 关注点）；
- slug 过 P2 穿越防护闸（../、Windows 保留名）；
- list_slugs 扫 stories/ 根目录，非法目录名跳过；
- create_workspace 落盘建目录、撞名拒绝（POST /api/workspaces 的后端语义）。

装配细节（provider/harness/restore）不在 registry——测试注入桩工厂即可。
"""

import asyncio
from pathlib import Path

import pytest

from hastings.bible.store import InvalidEntityNameError
from hastings.server.registry import WorkspaceExistsError, WorkspaceRegistry
from test_server_app import StubSession


def _registry(root: Path, calls: list[str] | None = None) -> WorkspaceRegistry:
    """桩工厂注册表：装配即记录 slug（可注入 calls 观察装配次数）。"""

    async def factory(workspace: object) -> StubSession:
        if calls is not None:
            calls.append(workspace.slug)  # type: ignore[attr-defined]
        return StubSession()

    return WorkspaceRegistry(root=root, session_factory=factory)  # type: ignore[arg-type]


# --- get_or_create：懒装配 + 缓存 ---


def test_get_or_create_assembles_once_and_caches(tmp_path: Path) -> None:
    calls: list[str] = []
    registry = _registry(tmp_path, calls)

    first = asyncio.run(registry.get_or_create("orient-express"))
    second = asyncio.run(registry.get_or_create("orient-express"))

    assert first is second
    assert calls == ["orient-express"]  # 第二次命中缓存，不再装配
    assert (tmp_path / "orient-express" / "bible" / "characters").is_dir()
    assert (tmp_path / "orient-express" / "chapters").is_dir()


def test_get_or_create_separate_slugs_separate_sessions(tmp_path: Path) -> None:
    calls: list[str] = []
    registry = _registry(tmp_path, calls)

    a = asyncio.run(registry.get_or_create("orient-express"))
    b = asyncio.run(registry.get_or_create("ghost-train"))

    assert a is not b
    assert sorted(calls) == ["ghost-train", "orient-express"]


def test_get_or_create_concurrent_first_visit_assembles_once(tmp_path: Path) -> None:
    """同 slug 双连接同时首访：锁 + 双重检查保证只装配一次（R3-③ 回归钉）。"""

    async def slow_factory(workspace: object) -> StubSession:
        await asyncio.sleep(0)  # 让出事件循环，制造并发首访窗口
        return StubSession()

    registry = WorkspaceRegistry(root=tmp_path, session_factory=slow_factory)  # type: ignore[arg-type]

    async def gather() -> tuple[object, object]:
        return await asyncio.gather(
            registry.get_or_create("orient-express"),
            registry.get_or_create("orient-express"),
        )

    a, b = asyncio.run(gather())
    assert a is b


def test_rebuild_marks_old_session_as_stale(tmp_path: Path) -> None:
    """重建后，旧 WebSocket 持有的 session 不再被视为当前实例。"""
    registry = _registry(tmp_path)

    first = asyncio.run(registry.get_or_create("orient-express"))
    assert registry.is_current("orient-express", first)

    rebuilt = asyncio.run(registry.rebuild("orient-express"))

    assert rebuilt is not first
    assert not registry.is_current("orient-express", first)
    assert registry.is_current("orient-express", rebuilt)


# --- slug 防护：穿越与保留名（P2 闸门复用） ---


@pytest.mark.parametrize("bad_slug", ["../escape", "..", "CON", "a/b", ""])
def test_get_or_create_rejects_invalid_slug(tmp_path: Path, bad_slug: str) -> None:
    registry = _registry(tmp_path)
    with pytest.raises(InvalidEntityNameError):
        asyncio.run(registry.get_or_create(bad_slug))
    assert not (tmp_path / "escape").exists()  # 穿越未遂不留痕


# --- list_slugs：扫根目录 ---


def test_list_slugs_scans_root_skips_invalid(tmp_path: Path) -> None:
    (tmp_path / "orient-express").mkdir()
    (tmp_path / "ghost-train").mkdir()
    (tmp_path / "notes.txt").write_text("不是目录", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    registry = _registry(tmp_path)

    assert registry.list_slugs() == ["ghost-train", "orient-express"]


def test_list_slugs_missing_root_is_empty(tmp_path: Path) -> None:
    registry = _registry(tmp_path / "stories")
    assert registry.list_slugs() == []


# --- create_workspace：POST /api/workspaces 的后端语义 ---


def test_create_workspace_makes_dirs_and_appears_in_list(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    registry.create_workspace("fresh-story")

    assert (tmp_path / "fresh-story" / "bible" / "characters").is_dir()
    assert (tmp_path / "fresh-story" / "chapters").is_dir()
    assert registry.list_slugs() == ["fresh-story"]


def test_create_workspace_rejects_duplicate(tmp_path: Path) -> None:
    (tmp_path / "existing").mkdir()
    registry = _registry(tmp_path)

    with pytest.raises(WorkspaceExistsError):
        registry.create_workspace("existing")


@pytest.mark.parametrize("bad_slug", ["../escape", "NUL"])
def test_create_workspace_rejects_invalid_slug(tmp_path: Path, bad_slug: str) -> None:
    registry = _registry(tmp_path)
    with pytest.raises(InvalidEntityNameError):
        registry.create_workspace(bad_slug)
