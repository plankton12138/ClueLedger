"""作品目录的 Git 历史（0.4 M02）。

作品目录是一个独立的 Git 仓库，历史由真实 ``git`` 命令产生：本模块只负责调用、
校验和把结果整理成可判断的结构，不自己维护版本对象。这样作者仍可以在应用之外用
自己的 Git 工具，应用也不必解释一份平行历史。

几条硬边界写在代码里而不是靠调用方自觉：

- 参数一律列表传入、``shell=False``，禁止 ``git add .``、``reset --hard``、
  ``clean``、强推和改写历史；
- 提交身份只用 ``-c`` 对本次命令生效，不读也不写作者的全局配置；
- 仓库根必须等于作品根：作品目录套在别的仓库里时不会「顺便」使用上级仓库；
- 作者已经暂存过内容时拒绝自动写入与提交，不消费他的暂存区。

差异、日志与撤销都直接读 Git 的输出。撤销用 ``git revert`` 生成反向提交，不移动
分支指针；存在后续改动时明确拒绝，不做三方合并。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import BODY_DIR, SETTINGS_DIR, Workspace, is_managed_path

__all__ = [
    "Change",
    "CommitInfo",
    "GitRepo",
    "GitStatus",
    "MANAGED_DIRS",
]

#: 参与版本记录的目录：只有正文与设定集，``.gitignore`` 等技术文件单独处理。
MANAGED_DIRS = (BODY_DIR, SETTINGS_DIR)

GIT_TIMEOUT_SECONDS = 30

#: 只对本次命令生效的配置：身份不落全局，非 ASCII 路径不做引号转义。
_CONFIG = (
    "-c",
    "core.quotepath=false",
    "-c",
    "user.name=Hastings",
    "-c",
    "user.email=hastings@local",
)

_SHA = re.compile(r"^[0-9a-f]{4,40}$")
#: porcelain v1 中表示未合并的两位状态码。
_CONFLICT_CODES = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})

#: 外部工具与写入临时文件不属于作品内容，初始化时写入。
GITIGNORE = """\
# Hastings 作品目录：外部工具文件与写入临时文件不属于作品内容
.obsidian/
.DS_Store
Thumbs.db
.hastings-*.tmp
"""


@dataclass(frozen=True, slots=True)
class Change:
    """工作区相对 HEAD 的一处改动；重命名同时记录原路径。"""

    path: str
    code: str
    orig_path: str | None = None


@dataclass(frozen=True, slots=True)
class GitStatus:
    """当前仓库状态：分支、HEAD、暂存区、受管改动与未合并路径。"""

    branch: str | None
    head: str | None
    staged: tuple[str, ...]
    changes: tuple[Change, ...]
    conflicts: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.staged and not self.changes and not self.conflicts


@dataclass(frozen=True, slots=True)
class CommitInfo:
    sha: str
    committed_at: str
    subject: str


class GitRepo:
    """一个作品目录对应的 Git 仓库。所有命令都以作品根为工作目录。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    # -- 环境 ---------------------------------------------------------------

    @property
    def available(self) -> bool:
        """系统上是否有可执行的 git；没有时界面应提示安装而不是报未知错误。"""
        return shutil.which("git") is not None

    def is_repository(self) -> bool:
        if not (self.root / ".git").exists():
            return False
        code, out, _ = self._exec("rev-parse", "--show-toplevel")
        if code:
            return False
        return _same_path(_decode(out).strip(), self.root)

    def require_repository(self) -> None:
        """确认仓库存在且根目录就是作品目录；套在上级仓库里不算。"""
        if not (self.root / ".git").exists():
            raise WorkspaceError(
                code="GIT_NOT_REPOSITORY",
                where="git.repository",
                why=f"作品目录还没有版本记录：{self.root}",
                fix="先执行「启用版本记录」，应用会初始化仓库并记录当前内容",
                details={"root": str(self.root)},
            )
        code, out, _ = self._exec("rev-parse", "--show-toplevel")
        if code:
            raise WorkspaceError(
                code="GIT_NOT_REPOSITORY",
                where="git.repository",
                why="git 无法识别这个仓库",
                fix="检查作品目录下的 .git 是否完整；必要时重新初始化",
            )
        toplevel = _decode(out).strip()
        if not _same_path(toplevel, self.root):
            raise WorkspaceError(
                code="GIT_ROOT_MISMATCH",
                where="git.repository",
                why=f"作品目录位于另一个仓库中：{toplevel}",
                fix="在作品目录内单独初始化版本记录；应用不会使用上级仓库",
                details={"repository": toplevel, "root": str(self.root)},
            )

    def require_ready(self) -> None:
        """自动写入与提交的前提：有分支、没有未合并路径、没有作者预暂存内容。"""
        self.require_repository()
        if self.branch() is None:
            raise WorkspaceError(
                code="GIT_NO_BRANCH",
                where="git.ready",
                why="仓库处于分离头指针状态，没有可用分支",
                fix="切换到一个分支后再修改作品；应用不替你创建分支",
            )
        conflicts = self.conflicts()
        if conflicts:
            raise WorkspaceError(
                code="GIT_MERGE_CONFLICT",
                where="git.ready",
                why=f"仓库有未解决的合并冲突：{', '.join(conflicts[:5])}",
                fix="先在 Git 里解决冲突并提交，再回到应用修改",
                details={"paths": list(conflicts)},
            )
        staged = self.staged_paths()
        if staged:
            raise WorkspaceError(
                code="GIT_STAGED_CHANGES_CONFLICT",
                where="git.ready",
                why=f"暂存区里已有作者准备的改动：{', '.join(staged[:5])}",
                fix="先提交或取消这些暂存内容；应用不会替你消费暂存区",
                details={"paths": list(staged)},
            )

    # -- 初始化 -------------------------------------------------------------

    def init(self) -> None:
        """初始化仓库并把当前内容记成第一个提交；已初始化时只补缺失的基线。"""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(
                code="GIT_COMMAND_FAILED",
                where="git.init",
                why=f"无法创建作品目录：{exc.strerror or exc}",
                fix="检查父目录是否存在、是否有写权限",
            ) from exc
        if not (self.root / ".git").exists():
            self._git("init")
        self.require_repository()

        gitignore = self.root / ".gitignore"
        if not gitignore.exists():
            try:
                gitignore.write_text(GITIGNORE, encoding="utf-8", newline="\n")
            except OSError as exc:
                raise WorkspaceError(
                    code="GIT_COMMAND_FAILED",
                    where="git.init",
                    why=f"无法写入 .gitignore：{exc.strerror or exc}",
                    fix="检查作品目录写权限",
                ) from exc

        if self.head() is None:
            # 首次基线：把 .gitignore 与已有的受管文件一起记下来，
            # 之后的差异与撤销才有可比的起点。
            self._git("add", "--", ".gitignore")
            existing = self._managed_existing_paths()
            if existing:
                self._git("add", "-A", "--", *existing)
            self._git("commit", "-m", "初始化作品目录")

    # -- 读取 ---------------------------------------------------------------

    def head(self) -> str | None:
        code, out, _ = self._exec("rev-parse", "-q", "--verify", "HEAD")
        return _decode(out).strip() if code == 0 else None

    def branch(self) -> str | None:
        code, out, _ = self._exec("symbolic-ref", "--short", "-q", "HEAD")
        return _decode(out).strip() if code == 0 else None

    def staged_paths(self) -> tuple[str, ...]:
        """暂存区里的全部路径（不限受管范围）：作者预暂存会因此被识别。"""
        code, out, _ = self._exec("diff", "--cached", "--name-only", "-z")
        if code:
            return ()
        return tuple(item for item in _decode(out).split("\0") if item)

    def conflicts(self) -> tuple[str, ...]:
        """未合并路径：存在时应用不写、不提交，也不自动解决。"""
        code, out, _ = self._exec("ls-files", "-u", "-z")
        if code:
            return ()
        paths: set[str] = set()
        for entry in _decode(out).split("\0"):
            if not entry:
                continue
            _, _, path = entry.partition("\t")
            if path:
                paths.add(path)
        return tuple(sorted(paths))

    def changes(self) -> tuple[Change, ...]:
        """受管范围内相对 HEAD 的改动；非受管文件（如 .obsidian/）不参与。"""
        code, out, _ = self._exec(
            "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *MANAGED_DIRS
        )
        if code:
            return ()
        entries = _parse_porcelain(out)
        return tuple(
            change
            for change in entries
            if change.code not in _CONFLICT_CODES and _managed(change.path)
        )

    def status(self) -> GitStatus:
        self.require_repository()
        return GitStatus(
            branch=self.branch(),
            head=self.head(),
            staged=self.staged_paths(),
            changes=self.changes(),
            conflicts=self.conflicts(),
        )

    def diff(self, base: str = "HEAD") -> str:
        """工作区相对某个提交的文本差异；还没有提交时返回空串。"""
        self.require_repository()
        if base == "HEAD" and self.head() is None:
            return ""
        return _decode(self._git("diff", "--no-color", "--no-ext-diff", base, "--", *MANAGED_DIRS))

    def commit_diff(self, sha: str) -> str:
        """指定提交的差异；未知提交返回可理解的失败而不是空串。"""
        self.require_repository()
        if not _SHA.match(sha):
            raise _unknown_commit(sha)
        code, _, _ = self._exec("cat-file", "-e", f"{sha}^{{commit}}")
        if code:
            raise _unknown_commit(sha)
        return _decode(
            self._git("show", "--no-color", "--no-ext-diff", "--format=", sha, "--", *MANAGED_DIRS)
        )

    def log(self, limit: int = 50) -> tuple[CommitInfo, ...]:
        """作品仓库的全部提交，最新在前。

        不按路径过滤：作品仓库只记录这部作品，初始化提交（只含 .gitignore）也是
        作者能看到的历史起点，不应因为没触及正文就不显示。
        """
        self.require_repository()
        if self.head() is None:
            return ()
        out = self._git("log", f"--max-count={limit}", "--format=%H%x1f%aI%x1f%s")
        commits: list[CommitInfo] = []
        for line in _decode(out).splitlines():
            sha, _, rest = line.partition("\x1f")
            committed_at, _, subject = rest.partition("\x1f")
            if sha:
                commits.append(CommitInfo(sha=sha, committed_at=committed_at, subject=subject))
        return tuple(commits)

    # -- 写入 ---------------------------------------------------------------

    def record(
        self, message: str, paths: tuple[str, ...] | None = None, *, allow_empty: bool = False
    ) -> str | None:
        """把受管改动记成一个提交；没有变化时不生成空提交。"""
        self.require_ready()
        changes = self.changes()
        if paths is not None:
            wanted = set(paths)
            changes = tuple(
                change
                for change in changes
                if change.path in wanted
                or (change.orig_path is not None and change.orig_path in wanted)
            )
        if not changes:
            if not allow_empty:
                return None
            self._git("commit", "--allow-empty", "-m", message)
            return self.head()
        stage = sorted(
            {path for change in changes for path in (change.path, change.orig_path) if path}
        )
        self._git("add", "-A", "--", *stage)
        unexpected = set(self.staged_paths()) - set(stage)
        if unexpected:
            # 暂存区在准备之后又出现别的路径：说明有人同时操作，不能把未知改动混进本次提交。
            raise WorkspaceError(
                code="GIT_STAGED_CHANGES_CONFLICT",
                where="git.record",
                why=f"暂存区出现本次之外的改动：{', '.join(sorted(unexpected)[:5])}",
                fix="先处理这些暂存内容；本次改动仍保留在磁盘上，可以单独补记",
                details={"paths": sorted(unexpected)},
            )
        self._git("commit", "-m", message)
        return self.head()

    def revert_latest(self, expected_head: str) -> str:
        """用反向提交撤销最新提交；HEAD 变了或工作区不干净时拒绝。"""
        self.require_ready()
        head = self.head()
        if head is None:
            raise WorkspaceError(
                code="GIT_REVERT_CONFLICT",
                where="git.revert",
                why="仓库还没有任何提交，没有可撤销的内容",
                fix="先完成一次修改并记录历史",
            )
        if head != expected_head:
            raise WorkspaceError(
                code="GIT_HEAD_MOVED_CONFLICT",
                where="git.revert",
                why="最新提交已经变化，不能按旧位置撤销",
                fix="刷新历史后确认要撤销的那一条再操作",
                details={"expected": expected_head, "current": head},
            )
        pending = self.changes()
        if pending:
            raise WorkspaceError(
                code="GIT_DIRTY_CONFLICT",
                where="git.revert",
                why=f"还有未记录的改动：{', '.join(change.path for change in pending[:5])}",
                fix="先把当前改动记录下来或手工处理，再撤销上一次修改",
                details={"paths": [change.path for change in pending]},
            )
        code, _, err = self._exec("revert", "--no-edit", expected_head)
        if code:
            # 冲突时清掉 revert 的中间状态，工作文件保持原样。
            self._exec("revert", "--abort")
            raise WorkspaceError(
                code="GIT_REVERT_CONFLICT",
                where="git.revert",
                why=f"撤销产生冲突：{_excerpt(err)}",
                fix="在 Git 里手工处理这次撤销；应用不会自动合并",
                details={"commit": expected_head},
            )
        return self.head() or ""

    # -- 内部 ---------------------------------------------------------------

    def _managed_existing_paths(self) -> list[str]:
        catalog = Workspace(self.root).catalog()
        return [
            entry.path
            for entry in (*catalog.chapters, *catalog.characters, *catalog.settings)
            if entry.exists
        ]

    def _exec(self, *args: str) -> tuple[int, bytes, bytes]:
        command = ["git", *_CONFIG, "--literal-pathspecs", "-C", str(self.root), *args]
        try:
            completed = subprocess.run(
                command,
                shell=False,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise WorkspaceError(
                code="GIT_UNAVAILABLE_FAILED",
                where="git.exec",
                why="找不到 git 可执行文件",
                fix="安装 Git 并确认它在 PATH 中，然后重启应用",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError(
                code="GIT_TIMEOUT_FAILED",
                where="git.exec",
                why=f"git 命令超过 {GIT_TIMEOUT_SECONDS} 秒未返回",
                fix="确认磁盘或仓库没有被其他程序长时间占用后重试",
            ) from exc
        return completed.returncode, completed.stdout, completed.stderr

    def _git(self, *args: str) -> bytes:
        code, out, err = self._exec(*args)
        if code:
            raise WorkspaceError(
                code="GIT_COMMAND_FAILED",
                where=f"git.{args[0]}",
                why=f"git 命令失败：{_excerpt(err) or f'退出码 {code}'}",
                fix="检查仓库状态；文件本身没有被改动，可以稍后单独补记历史",
            )
        return out


def _parse_porcelain(data: bytes) -> list[Change]:
    """解析 ``status --porcelain=v1 -z``；重命名条目会带一个原路径。"""
    parts = _decode(data).split("\0")
    changes: list[Change] = []
    index = 0
    while index < len(parts):
        entry = parts[index]
        index += 1
        if len(entry) < 4:
            continue
        code = entry[:2]
        path = entry[3:]
        orig_path: str | None = None
        if code[0] in ("R", "C") and index < len(parts) and parts[index]:
            orig_path = parts[index]
            index += 1
        changes.append(Change(path=path, code=code, orig_path=orig_path))
    return changes


def _managed(path: str) -> bool:
    return is_managed_path(path)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _excerpt(data: bytes, limit: int = 200) -> str:
    text = " ".join(_decode(data).split())
    return text[:limit]


def _same_path(left: str, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(str(right))


def _unknown_commit(sha: str) -> WorkspaceError:
    return WorkspaceError(
        code="GIT_COMMIT_NOT_FOUND",
        where="git.commit",
        why=f"没有这个提交：{sha}",
        fix="刷新历史列表后重新选择；提交可能已被其他 Git 操作移除",
        details={"commit": sha},
    )
