"""应用状态里的工作目录登记（0.4 M05 的状态部分）。

0.4 的作品内容只在作品目录里。应用自己需要记住的只有「最近打开过哪些目录」这类
定位信息，它放在应用状态目录，不复制正文，也不成为第二份当前稿。

绑定 ID 由规范化后的绝对路径算出：同一目录重复打开得到同一个 ID，界面的选择、
会话与事件因此不需要另一套映射；目录被移动后 ID 变化，属于「换了一个目录」而不是
「作品改了名」。登记文件损坏时按空列表处理——宁可让作者重新选择目录，也不让启动
失败。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace

__all__ = [
    "DEFAULT_STATE_ROOT",
    "RecentWorkspaces",
    "WorkspaceBinding",
    "default_state_root",
]

#: 应用状态目录：聊天、最近目录、运行事件都在这里；不含作品正文。
DEFAULT_STATE_ROOT = Path.home() / ".hastings"

_STATE_ENV = "HASTINGS_STATE_DIR"
_REGISTRY_FILE = "workspaces.json"
_REGISTRY_VERSION = 1
#: 最近目录保留条数；超出后丢弃最旧的登记，不删除磁盘上的作品。
_MAX_ENTRIES = 20


def default_state_root() -> Path:
    """状态目录：``HASTINGS_STATE_DIR`` 优先，否则用户主目录下的 ``.hastings``。"""
    override = os.environ.get(_STATE_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_STATE_ROOT


@dataclass(frozen=True, slots=True)
class WorkspaceBinding:
    """界面与接口使用的本地绑定：ID 只用于定位，不写入作品内容。"""

    id: str
    name: str
    path: str
    opened_at: str

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "name": self.name, "path": self.path, "opened_at": self.opened_at}

    @property
    def exists(self) -> bool:
        return Path(self.path).is_dir()


class RecentWorkspaces:
    """最近目录登记：JSON 单文件，读写都加锁，写入用临时文件替换。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root is not None else default_state_root()
        self.registry_file = self.root / _REGISTRY_FILE
        self._lock = threading.Lock()

    # -- 查询 ---------------------------------------------------------------

    def recent(self) -> tuple[WorkspaceBinding, ...]:
        with self._lock:
            return tuple(self._load())

    def get(self, workspace_id: str) -> WorkspaceBinding:
        for binding in self.recent():
            if binding.id == workspace_id:
                return binding
        raise _unknown_workspace(workspace_id)

    def workspace(self, workspace_id: str) -> Workspace:
        """按绑定 ID 打开作品；目录已被移动或删除时给出可理解的失败。"""
        binding = self.get(workspace_id)
        try:
            return Workspace.open(Path(binding.path))
        except WorkspaceError as exc:
            raise WorkspaceError(
                code="WORKSPACE_NOT_FOUND",
                where="workspace.resolve",
                why=f"已登记的目录当前不可用：{binding.path}（{exc.why}）",
                fix="确认目录仍在原位置，或在顶栏重新打开目录",
                details={"id": workspace_id, "path": binding.path},
            ) from exc

    # -- 登记 ---------------------------------------------------------------

    def bind(self, root: Path) -> WorkspaceBinding:
        """登记一个已存在的目录并放到最前；同一目录不会产生第二条记录。"""
        resolved = Path(root).expanduser().resolve()
        if not resolved.exists():
            raise WorkspaceError(
                code="WORKSPACE_NOT_FOUND",
                where="workspace.bind",
                why=f"目录不存在：{resolved}",
                fix="确认路径拼写，或先用「新建」创建作品目录",
            )
        if not resolved.is_dir():
            raise WorkspaceError(
                code="WORKSPACE_NOT_DIRECTORY",
                where="workspace.bind",
                why=f"路径不是目录：{resolved}",
                fix="作品目录必须是一个文件夹",
            )
        binding = WorkspaceBinding(
            id=binding_id(resolved),
            name=resolved.name or str(resolved),
            path=str(resolved),
            opened_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        with self._lock:
            entries = [item for item in self._load() if item.id != binding.id]
            self._save([binding, *entries][:_MAX_ENTRIES])
        return binding

    # -- 内部 ---------------------------------------------------------------

    def _load(self) -> list[WorkspaceBinding]:
        try:
            raw = json.loads(self.registry_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            # 文件缺失、不可读或内容损坏：按空登记处理，不阻断打开目录。
            return []
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        bindings: list[WorkspaceBinding] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            workspace_id = item.get("id")
            path = item.get("path")
            name = item.get("name")
            opened_at = item.get("opened_at")
            if not isinstance(workspace_id, str) or not workspace_id:
                continue
            if not isinstance(path, str) or not path:
                continue
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(opened_at, str) or not opened_at:
                continue
            bindings.append(
                WorkspaceBinding(id=workspace_id, name=name, path=path, opened_at=opened_at)
            )
        return bindings

    def _save(self, bindings: list[WorkspaceBinding]) -> None:
        payload = {
            "version": _REGISTRY_VERSION,
            "items": [binding.to_dict() for binding in bindings],
        }
        handle: int | None = None
        temp_name: str | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                dir=str(self.root), prefix=".workspaces-", suffix=".json"
            )
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                handle = None
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temp_name, self.registry_file)
            temp_name = None
        except OSError as exc:
            raise WorkspaceError(
                code="STATE_WRITE_FAILED",
                where="workspace.registry",
                why=f"无法保存最近目录登记：{exc.strerror or exc}",
                fix="检查应用状态目录是否可写（HASTINGS_STATE_DIR 或 ~/.hastings）",
            ) from exc
        finally:
            if handle is not None:
                os.close(handle)
            if temp_name is not None:
                with suppress(OSError):
                    os.unlink(temp_name)


def binding_id(path: Path) -> str:
    """路径散列作为绑定 ID：规范化大小写，同一目录稳定得到同一 ID。"""
    key = os.path.normcase(str(path))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _unknown_workspace(workspace_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="WORKSPACE_NOT_FOUND",
        where="workspace.resolve",
        why=f"没有这个目录绑定：{workspace_id}",
        fix="在顶栏重新打开目录；绑定只记录最近打开过的本地路径",
        details={"id": workspace_id},
    )
