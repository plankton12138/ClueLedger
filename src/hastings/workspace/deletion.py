"""确认后删除单部作品；共享库、应用根目录和其他作品不在删除范围内。"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Sequence
from pathlib import Path

from hastings.workspace.errors import WorkspaceError


def delete_workspace_files(
    root: Path,
    state_root: Path,
    *,
    confirmed_path: str,
    protected_roots: Sequence[Path],
) -> None:
    """先核对确认路径与删除边界，再删文件；失败时由调用方保留登记以便重试。"""
    if confirmed_path != str(root):
        raise WorkspaceError(
            code="WORKSPACE_CONFIRMATION_CONFLICT",
            where="workspace.delete",
            why="确认的目录与当前作品目录不一致",
            fix="刷新作品列表，核对完整路径后重新确认删除",
        )
    resolved = root.resolve()
    if root.is_symlink() or root.is_junction() or resolved != root.absolute():
        raise WorkspaceError(
            code="WORKSPACE_PATH_CONFLICT",
            where="workspace.delete",
            why="作品路径已经变成链接或指向其他目录",
            fix="核对磁盘目录后重新打开作品，再确认删除",
        )
    if state_root.is_symlink() or state_root.is_junction() or state_root.resolve() != state_root:
        raise WorkspaceError(
            code="WORKSPACE_PATH_CONFLICT",
            where="workspace.delete",
            why="作品应用记录的路径已指向其他目录",
            fix="恢复应用记录目录后重试删除",
        )
    for directory in (resolved, state_root):
        for protected in (Path(root.anchor), *protected_roots):
            absolute = protected.expanduser().absolute()
            for target in {absolute, absolute.resolve()}:
                if directory == target or directory in target.parents:
                    raise WorkspaceError(
                        code="WORKSPACE_DELETE_PROTECTED",
                        where="workspace.delete",
                        why=f"待删除目录包含受保护的位置：{target}",
                        fix="选择单部作品的文件夹；不能删除总目录、模块库或包含其他作品的目录",
                    )
    if root.exists() and not (root / "正文").is_dir():
        raise WorkspaceError(
            code="WORKSPACE_DELETE_PROTECTED",
            where="workspace.delete",
            why="这个目录没有正文文件夹，无法确认为作品目录",
            fix="核对完整路径，选择单部作品的文件夹后再删除",
        )

    # Windows 的 Git 对象可能带只读属性。仅在权限错误时解除属性并重试当前操作。
    def retry_readonly(function: Callable[..., object], path: str, exc: BaseException) -> None:
        entry = Path(path)
        if not isinstance(exc, PermissionError) or entry.is_symlink() or entry.is_junction():
            raise exc
        os.chmod(entry, entry.stat().st_mode | stat.S_IWRITE)
        function(path)

    for directory in (root, state_root):
        try:
            # 缺失目录允许继续，支持外部已删除或上次部分完成后的清理重试。
            if directory.exists():
                shutil.rmtree(directory, onexc=retry_readonly)
        except OSError as exc:
            raise WorkspaceError(
                code="WORKSPACE_DELETE_FAILED",
                where="workspace.delete",
                why=f"无法完成目录删除：{directory}（{exc.strerror or exc}）",
                fix="关闭占用文件的程序并检查目录权限后重试；部分文件可能已经删除",
                details={"path": str(directory)},
            ) from exc
