"""WorkspaceRegistry：多工作区注册表（P4 T4，计划 D4）。

一个 slug 一份会话装配（workspace + session 常驻缓存，不驱逐——单用户
个位数故事），registry 只管生命周期与 slug 防护，不认识 provider/harness：
装配细节在 session factory（server 入口用 default_session_factory 接线
restore_and_attach，测试注入桩工厂）。

并发语义：单线程事件循环内 get_or_create 用 per-slug asyncio.Lock +
双重检查——同 slug 双连接同时首访只装配一次；不同 slug 互不阻塞。

slug 是目录名来源，全部入口过 P2 的 validate_slug 穿越防护闸。
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from hastings.bible.store import (
    InvalidEntityNameError,
    StoryWorkspace,
    validate_slug,
)
from hastings.server.session import WritingSession

__all__ = [
    "SessionFactory",
    "WorkspaceExistsError",
    "WorkspaceRegistry",
]

SessionFactory = Callable[[StoryWorkspace], Awaitable[WritingSession]]


class WorkspaceExistsError(ValueError):
    """新建工作区撞名（POST /api/workspaces，资源已存在）。"""


class WorkspaceRegistry:
    """slug → (StoryWorkspace, WritingSession) 的懒加载注册表。"""

    def __init__(self, root: Path, session_factory: SessionFactory) -> None:
        self._root = root
        self._factory = session_factory
        self._sessions: dict[str, WritingSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def root(self) -> Path:
        return self._root

    async def get_or_create(self, slug: str) -> WritingSession:
        """按 slug 取会话，首次访问时装配（含 ensure_dirs 落盘建目录）。

        slug 不合法抛 InvalidEntityNameError（穿越/保留名），HTTP 层转 422。
        """
        cached = self._sessions.get(slug)
        if cached is not None:
            return cached
        workspace = StoryWorkspace(root=self._root, slug=slug)  # 构造即校验 slug
        lock = self._locks.setdefault(slug, asyncio.Lock())
        async with lock:
            session = self._sessions.get(slug)
            if session is not None:
                return session
            workspace.ensure_dirs()
            self._sessions[slug] = await self._factory(workspace)
            return self._sessions[slug]

    async def rebuild(self, slug: str) -> WritingSession:
        """按当前工厂重新装配 slug 的会话（P6 T2 跨档案切换）。

        工厂每次调用都从磁盘读取工作区设置并解析 provider 档案，因此「落盘
        新档案 → 重跑工厂」即得到新 provider 的新会话（对话历史由工厂
        restore_and_attach 从 session.jsonl 恢复）。provider 实例由
        ProviderBook 按档案名缓存复用，此处只需丢弃旧会话引用、无需关闭。
        调用方负责运行态检查（is_running → 409）——registry 不读会话状态。
        """
        workspace = StoryWorkspace(root=self._root, slug=slug)
        lock = self._locks.setdefault(slug, asyncio.Lock())
        async with lock:
            self._sessions.pop(slug, None)
            workspace.ensure_dirs()
            self._sessions[slug] = await self._factory(workspace)
            return self._sessions[slug]

    def create_workspace(self, slug: str) -> StoryWorkspace:
        """新建工作区并落盘建目录；撞名或 slug 不合法抛错。"""
        workspace = StoryWorkspace(root=self._root, slug=slug)
        if workspace.story_dir.exists():
            msg = (
                f"工作区已存在：{slug}。"
                f"为什么错：{workspace.story_dir} 目录已在盘上。"
                "怎样算对：直接切换到该工作区，或换一个新 slug。"
            )
            raise WorkspaceExistsError(msg)
        workspace.ensure_dirs()
        return workspace

    def list_slugs(self) -> list[str]:
        """扫描根目录下的工作区（目录名过 slug 校验，非法名跳过）。"""
        if not self._root.exists():
            return []
        slugs: list[str] = []
        for path in sorted(self._root.iterdir()):
            if not path.is_dir():
                continue
            try:
                validate_slug(path.name)
            except InvalidEntityNameError:
                continue
            slugs.append(path.name)
        return slugs

    def peek(self, slug: str) -> WritingSession | None:
        """已装配会话（不触发装配，P7 T13）：删除前的 running 检查专用。

        未装配过 → None（不可能在运行：会话在运行态必然已装配）。
        """
        return self._sessions.get(slug)

    def delete(self, slug: str) -> None:
        """删除工作区（P7 T13）：丢会话/锁缓存 + 整目录移除。

        调用方（REST 层）负责 confirm 校验、存在性 404 与 running 409；
        这里只保证缓存不悬空。rmtree OSError 上抛，由调用方归因环境故障。
        """
        workspace = StoryWorkspace(root=self._root, slug=slug)  # 构造即校验 slug
        self._sessions.pop(slug, None)
        self._locks.pop(slug, None)
        shutil.rmtree(workspace.story_dir)
