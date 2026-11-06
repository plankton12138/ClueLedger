"""server 模式的作品注册：目录由服务器分配，归属来自当前账号。

浏览器只提交作品名；真实目录由 ``storage_key`` 在服务器存储根下解析。列表、打开、
改名与删除都以 ``owner_user_id`` 为条件，猜 ID 或换账号都得到同一个 404。
"""

from __future__ import annotations

import secrets
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from hastings.accounts.db import AccountDatabase
from hastings.identity import current_user_id
from hastings.workspace.deletion import delete_workspace_files
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.state import (
    RecentWorkspaces,
    WorkspaceBinding,
    clean_workspace_name,
    workspace_state_root,
)

__all__ = ["UserWorkspaces"]


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


class UserWorkspaces(RecentWorkspaces):
    """按账号存放作品；``root`` 仍是应用状态目录，``storage_root`` 是作品内容根。"""

    managed = True

    def __init__(self, state_root: Path, database: AccountDatabase, storage_root: Path) -> None:
        super().__init__(state_root)
        self._database = database
        self.storage_root = Path(storage_root).expanduser().resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)

    # -- 查询 ---------------------------------------------------------------

    def recent(self) -> tuple[WorkspaceBinding, ...]:
        with self._database.read() as conn:
            rows = conn.execute(
                "SELECT id, name, storage_key, opened_at FROM workspaces "
                "WHERE owner_user_id = ? ORDER BY opened_at DESC",
                (self._owner(),),
            ).fetchall()
        return tuple(self._binding(row) for row in rows)

    def get(self, workspace_id: str) -> WorkspaceBinding:
        with self._database.read() as conn:
            row = conn.execute(
                "SELECT id, name, storage_key, opened_at FROM workspaces "
                "WHERE id = ? AND owner_user_id = ?",
                (workspace_id, self._owner()),
            ).fetchone()
        if row is None:
            raise self._not_found(workspace_id)
        return self._binding(row)

    def disk_root(self, workspace_id: str) -> Path:
        with self._database.read() as conn:
            row = conn.execute(
                "SELECT storage_key FROM workspaces WHERE id = ? AND owner_user_id = ?",
                (workspace_id, self._owner()),
            ).fetchone()
        if row is None:
            raise self._not_found(workspace_id)
        candidate = (self.storage_root / str(row["storage_key"])).resolve()
        if candidate == self.storage_root or not candidate.is_relative_to(self.storage_root):
            raise WorkspaceError(
                code="WORKSPACE_PATH_CONFLICT",
                where="workspace.resolve",
                why="作品存储位置超出服务器作品根",
                fix="检查作品注册与存储目录；不要手工改写数据库中的存储键",
                details={"id": workspace_id},
            )
        return candidate

    # -- 登记 ---------------------------------------------------------------

    def bind(self, root: Path) -> WorkspaceBinding:
        raise WorkspaceError(
            code="WORKSPACE_SERVER_MANAGED",
            where="workspace.bind",
            why="server 模式的作品目录由服务器分配，不能登记本地路径",
            fix="通过新建作品或作品列表打开",
        )

    def open_managed(self, *, name: str, create: bool) -> WorkspaceBinding:
        owner = self._owner()
        clean = clean_workspace_name(name)
        now = int(time.time())
        if not create:
            return self._touch_by_name(owner, clean, now)
        self._reject_duplicate_name(owner, clean)
        workspace_id = "ws_" + secrets.token_hex(16)
        storage_key = f"{owner}/{workspace_id}"
        target = self.storage_root / storage_key
        try:
            Workspace.create(target, exclusive=True)
            GitRepo(target).init()
            with self._database.write() as conn:
                conn.execute(
                    "INSERT INTO workspaces "
                    "(id, owner_user_id, storage_key, name, created_at, updated_at, opened_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (workspace_id, owner, storage_key, clean, now, now, now),
                )
        except sqlite3.IntegrityError as exc:
            shutil.rmtree(target, ignore_errors=True)
            raise WorkspaceError(
                code="WORKSPACE_NAME_CONFLICT",
                where="workspace.create",
                why=f"已经有同名作品：{clean}",
                fix="换一个名称，或从作品列表打开已有作品",
            ) from exc
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return WorkspaceBinding(
            id=workspace_id,
            name=clean,
            path="",
            opened_at=_iso(now),
            exists_override=True,
        )

    def remove_managed(self, workspace_id: str) -> WorkspaceBinding:
        binding = self.get(workspace_id)
        root = self.disk_root(workspace_id)
        state_root = workspace_state_root(self.root, workspace_id)
        delete_workspace_files(
            root,
            state_root,
            confirmed_path=str(root),
            protected_roots=[self.storage_root],
        )
        with self._database.write() as conn:
            conn.execute(
                "DELETE FROM workspaces WHERE id = ? AND owner_user_id = ?",
                (workspace_id, self._owner()),
            )
        return WorkspaceBinding(
            id=binding.id,
            name=binding.name,
            path="",
            opened_at=binding.opened_at,
            exists_override=False,
        )

    def rename(self, workspace_id: str, name: str) -> WorkspaceBinding:
        clean = clean_workspace_name(name)
        owner = self._owner()
        now = int(time.time())
        self._reject_duplicate_name(owner, clean, excluding=workspace_id)
        try:
            with self._database.write() as conn:
                changed = conn.execute(
                    "UPDATE workspaces SET name = ?, updated_at = ? "
                    "WHERE id = ? AND owner_user_id = ?",
                    (clean, now, workspace_id, owner),
                ).rowcount
        except sqlite3.IntegrityError as exc:
            raise WorkspaceError(
                code="WORKSPACE_NAME_CONFLICT",
                where="workspace.rename",
                why=f"已经有同名作品：{clean}",
                fix="换一个名称",
            ) from exc
        if changed == 0:
            raise self._not_found(workspace_id)
        with self._database.read() as conn:
            row = conn.execute(
                "SELECT id, name, storage_key, opened_at FROM workspaces "
                "WHERE id = ? AND owner_user_id = ?",
                (workspace_id, owner),
            ).fetchone()
        assert row is not None
        return self._binding(row)

    def unbind(self, workspace_id: str) -> WorkspaceBinding:
        raise WorkspaceError(
            code="WORKSPACE_SERVER_MANAGED",
            where="workspace.unbind",
            why="server 模式请使用删除作品",
            fix="从作品菜单删除作品",
        )

    def prune_missing(self) -> tuple[WorkspaceBinding, ...]:
        # 服务器存储缺失属于数据故障；不自动删除登记，列表以 exists=false 呈现。
        return ()

    # -- 内部 ---------------------------------------------------------------

    def _owner(self) -> str:
        user_id = current_user_id()
        if not user_id:
            raise WorkspaceError(
                code="WORKSPACE_ACCESS_DENIED",
                where="workspace.owner",
                why="当前请求没有账号身份",
                fix="重新登录后再试",
            )
        return user_id

    def _binding(self, row: sqlite3.Row) -> WorkspaceBinding:
        storage = (self.storage_root / str(row["storage_key"])).resolve()
        return WorkspaceBinding(
            id=str(row["id"]),
            name=str(row["name"]),
            path="",
            opened_at=_iso(int(row["opened_at"])),
            exists_override=storage.is_dir(),
        )

    def _touch_by_name(self, owner: str, name: str, now: int) -> WorkspaceBinding:
        with self._database.write() as conn:
            row = conn.execute(
                "SELECT id, name, storage_key, opened_at FROM workspaces "
                "WHERE owner_user_id = ? AND name = ?",
                (owner, name),
            ).fetchone()
            if row is None:
                raise self._not_found(name)
            conn.execute(
                "UPDATE workspaces SET opened_at = ? WHERE id = ? AND owner_user_id = ?",
                (now, str(row["id"]), owner),
            )
        return WorkspaceBinding(
            id=str(row["id"]),
            name=str(row["name"]),
            path="",
            opened_at=_iso(now),
            exists_override=(self.storage_root / str(row["storage_key"])).resolve().is_dir(),
        )

    def _reject_duplicate_name(self, owner: str, name: str, *, excluding: str = "") -> None:
        with self._database.read() as conn:
            row = conn.execute(
                "SELECT id FROM workspaces WHERE owner_user_id = ? AND name = ? AND id != ?",
                (owner, name, excluding),
            ).fetchone()
        if row is not None:
            raise WorkspaceError(
                code="WORKSPACE_NAME_CONFLICT",
                where="workspace.rename" if excluding else "workspace.create",
                why=f"已经有同名作品：{name}",
                fix="换一个名称，或从作品列表打开已有作品",
            )

    @staticmethod
    def _not_found(workspace_id: str) -> WorkspaceError:
        return WorkspaceError(
            code="WORKSPACE_NOT_FOUND",
            where="workspace.resolve",
            why="作品不存在或不可访问",
            fix="返回作品列表重新选择",
            details={"id": workspace_id},
        )
