"""作品目录的 Git 历史（0.4 V04-2，计划 M02）。

全部用例跑真实临时仓库，不用假 Git：这一层的价值就在于「Git 实际会怎样」，
替身只能证明我们自己的假设。覆盖初始化与基线、外部改动识别、受管范围、
预暂存与合并冲突的拒绝、提交、差异、日志、最新撤销，以及 git 缺失/超时。

不涉及旧工作台数据库：作品目录自带 .git，与应用数据无关。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="需要本机安装 git")


def _git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "-c",
            "user.name=测试",
            "-c",
            "user.email=test@local",
            "-C",
            str(root),
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and completed.returncode:
        raise AssertionError(f"git {' '.join(args)} 失败：{completed.stderr}")
    return completed.stdout


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace.create(tmp_path / "雪山旅馆")


def _repo(tmp_path: Path) -> tuple[Workspace, GitRepo]:
    workspace = _workspace(tmp_path)
    repo = GitRepo(workspace.root)
    repo.init()
    return workspace, repo


def _code(exc_info: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return exc_info.value.code


# -- 初始化 ---------------------------------------------------------------


def test_init_creates_repository_and_baseline(tmp_path: Path) -> None:
    workspace, repo = _repo(tmp_path)

    assert (workspace.root / ".git").is_dir()
    assert repo.is_repository() is True
    assert repo.branch() is not None
    assert repo.head() is not None
    assert [commit.subject for commit in repo.log()] == ["初始化作品目录"]
    assert repo.status().clean is True

    gitignore = (workspace.root / ".gitignore").read_text(encoding="utf-8")
    assert ".obsidian/" in gitignore
    assert ".hastings-*.tmp" in gitignore


def test_init_commits_existing_content_as_baseline(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n侦探抵达旅馆。\n", expected_digest=None)
    workspace.write("设定集/真相.md", "# 真相\n\n凶手是管家。\n", expected_digest=None)
    repo = GitRepo(workspace.root)

    repo.init()

    assert repo.status().clean is True
    assert repo.head() is not None
    tracked = _git(workspace.root, "ls-files")
    assert "正文/0001 雨夜.md" in tracked
    assert "设定集/真相.md" in tracked


def test_init_is_idempotent(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    head = repo.head()

    repo.init()

    assert repo.head() == head
    assert len(repo.log()) == 1


def test_uninitialized_workspace_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    repo = GitRepo(workspace.root)

    assert repo.is_repository() is False
    with pytest.raises(WorkspaceError) as missing:
        repo.require_repository()
    assert _code(missing) == "GIT_NOT_REPOSITORY"


def test_parent_repository_is_not_adopted(tmp_path: Path) -> None:
    parent = tmp_path / "上级仓库"
    parent.mkdir()
    _git(parent, "init", "-q")
    workspace = Workspace.create(parent / "作品")
    repo = GitRepo(workspace.root)

    with pytest.raises(WorkspaceError) as missing:
        repo.require_repository()
    assert _code(missing) == "GIT_NOT_REPOSITORY"

    repo.init()
    assert repo.is_repository() is True
    # 上级仓库仍是空的：作品没有把内容写进别人的历史。
    assert _git(parent, "status", "--porcelain").strip() == "?? 作品/"


def test_repository_pointing_elsewhere_is_rejected(tmp_path: Path, workspace_dir: Path) -> None:
    elsewhere = tmp_path / "别处"
    elsewhere.mkdir()
    _git(workspace_dir, "init", "-q")
    _git(workspace_dir, "config", "core.worktree", str(elsewhere))

    with pytest.raises(WorkspaceError) as mismatch:
        GitRepo(workspace_dir).require_repository()
    assert _code(mismatch) == "GIT_ROOT_MISMATCH"


@pytest.fixture()
def workspace_dir(tmp_path: Path) -> Path:
    target = tmp_path / "作品"
    target.mkdir()
    return target


# -- 写入前提 -------------------------------------------------------------


def test_staged_changes_block_writes(tmp_path: Path) -> None:
    workspace, repo = _repo(tmp_path)
    (workspace.root / "正文/0001 雨夜.md").write_text("# 雨夜\n", encoding="utf-8")
    _git(workspace.root, "add", "--", "正文/0001 雨夜.md")

    with pytest.raises(WorkspaceError) as staged:
        repo.require_ready()
    assert _code(staged) == "GIT_STAGED_CHANGES_CONFLICT"
    assert staged.value.details["paths"] == ["正文/0001 雨夜.md"]


def test_merge_conflict_blocks_writes(tmp_path: Path) -> None:
    workspace, repo = _repo(tmp_path)
    path = "正文/0001 雨夜.md"
    workspace.write(path, "# 雨夜\n\n第一版。\n", expected_digest=None)
    repo.record("写入第一章")
    base = repo.branch()
    assert base is not None

    _git(workspace.root, "checkout", "-q", "-b", "side")
    digest = workspace.read(path).digest
    workspace.write(path, "# 雨夜\n\n分支改动。\n", expected_digest=digest)
    repo.record("分支修改")

    _git(workspace.root, "checkout", "-q", base)
    digest = workspace.read(path).digest
    workspace.write(path, "# 雨夜\n\n主线改动。\n", expected_digest=digest)
    repo.record("主线修改")
    _git(workspace.root, "merge", "side", check=False)

    assert repo.conflicts() == (path,)
    with pytest.raises(WorkspaceError) as conflict:
        repo.require_ready()
    assert _code(conflict) == "GIT_MERGE_CONFLICT"


def test_detached_head_blocks_writes(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    _git(repo.root, "checkout", "-q", "--detach", "HEAD")

    assert repo.branch() is None
    with pytest.raises(WorkspaceError) as detached:
        repo.require_ready()
    assert _code(detached) == "GIT_NO_BRANCH"


def test_missing_git_reports_environment_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)

    def missing(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)

    with pytest.raises(WorkspaceError) as unavailable:
        GitRepo(workspace.root).head()
    assert _code(unavailable) == "GIT_UNAVAILABLE_FAILED"


def test_git_timeout_reports_environment_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)

    def slow(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(subprocess, "run", slow)

    with pytest.raises(WorkspaceError) as timeout:
        GitRepo(workspace.root).head()
    assert _code(timeout) == "GIT_TIMEOUT_FAILED"


# -- 受管范围与提交 -------------------------------------------------------


def test_unmanaged_files_do_not_count_as_changes(tmp_path: Path) -> None:
    workspace, repo = _repo(tmp_path)
    (workspace.root / ".obsidian").mkdir()
    (workspace.root / ".obsidian/app.json").write_text("{}", encoding="utf-8")
    (workspace.root / "正文/随手笔记.txt").write_text("不属于作品", encoding="utf-8")

    assert repo.changes() == ()
    assert repo.status().clean is True


def test_record_commits_only_managed_paths(tmp_path: Path) -> None:
    workspace, repo = _repo(tmp_path)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n", expected_digest=None)
    (workspace.root / "笔记.txt").write_text("临时", encoding="utf-8")

    commit = repo.record("写入第一章")

    assert commit is not None
    changed = _git(workspace.root, "show", "--name-only", "--format=", commit).splitlines()
    assert changed == ["正文/0001 雨夜.md"]
    # 非受管文件保持未跟踪，不因提交被带进去。
    assert "笔记.txt" in _git(workspace.root, "status", "--porcelain")


def test_record_without_changes_makes_no_commit(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    head = repo.head()

    assert repo.record("空提交") is None
    assert repo.head() == head
    assert len(repo.log()) == 1


def test_commit_uses_command_identity_only(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)

    author = _git(repo.root, "log", "-1", "--format=%an <%ae>").strip()

    assert author == "Hastings <hastings@local>"


# -- 差异与日志 -----------------------------------------------------------


def test_diff_shows_uncommitted_changes_and_commit_diff(tmp_path: Path) -> None:
    workspace, repo = _repo(tmp_path)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n侦探抵达旅馆。\n", expected_digest=None)
    commit = repo.record("写入第一章")
    assert commit is not None

    digest = workspace.read("正文/0001 雨夜.md").digest
    workspace.write(
        "正文/0001 雨夜.md", "# 雨夜\n\n侦探抵达旅馆。\n\n雪更大了。\n", expected_digest=digest
    )

    assert "+雪更大了。" in repo.diff()

    # 提交差异包含新增文件：新章节的完整内容在这里可见。
    assert "+侦探抵达旅馆。" in repo.commit_diff(commit)


def test_working_diff_leaves_untracked_files_to_status(tmp_path: Path) -> None:
    """Git 的 diff 不含未跟踪文件：新章节在提交差异里看，工作区只标「未跟踪」。"""
    workspace, repo = _repo(tmp_path)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n", expected_digest=None)

    assert repo.diff() == ""
    assert [(change.code, change.path) for change in repo.changes()] == [
        ("??", "正文/0001 雨夜.md")
    ]


def test_commit_diff_rejects_unknown_or_unsafe_revision(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)

    for revision in ("deadbeef", "../../etc/passwd", "--help"):
        with pytest.raises(WorkspaceError) as unknown:
            repo.commit_diff(revision)
        assert _code(unknown) == "GIT_COMMIT_NOT_FOUND"


def test_log_lists_newest_first(tmp_path: Path) -> None:
    workspace, repo = _repo(tmp_path)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n", expected_digest=None)
    repo.record("写入第一章")
    workspace.write("正文/0002 来信.md", "# 来信\n", expected_digest=None)
    repo.record("写入第二章")

    subjects = [commit.subject for commit in repo.log()]

    assert subjects == ["写入第二章", "写入第一章", "初始化作品目录"]
    assert all(commit.committed_at for commit in repo.log())


# -- 撤销 -----------------------------------------------------------------


def test_revert_latest_creates_opposite_commit(tmp_path: Path) -> None:
    workspace, repo = _repo(tmp_path)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n", expected_digest=None)
    commit = repo.record("写入第一章")
    assert commit is not None

    revert_commit = repo.revert_latest(commit)

    assert revert_commit
    assert not (workspace.root / "正文/0001 雨夜.md").exists()
    assert repo.log()[0].subject.startswith('Revert "写入第一章"')
    assert repo.status().clean is True


def test_revert_requires_matching_head(tmp_path: Path) -> None:
    workspace, repo = _repo(tmp_path)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n", expected_digest=None)
    repo.record("写入第一章")
    older = repo.head()

    workspace.write("正文/0002 来信.md", "# 来信\n", expected_digest=None)
    repo.record("写入第二章")

    with pytest.raises(WorkspaceError) as moved:
        repo.revert_latest(older or "")
    assert _code(moved) == "GIT_HEAD_MOVED_CONFLICT"


def test_revert_requires_clean_working_tree(tmp_path: Path) -> None:
    workspace, repo = _repo(tmp_path)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n", expected_digest=None)
    commit = repo.record("写入第一章")
    assert commit is not None
    target = workspace.root / "正文/0001 雨夜.md"
    target.write_text("# 雨夜\n\n未记录的改动。\n", encoding="utf-8")

    with pytest.raises(WorkspaceError) as dirty:
        repo.revert_latest(commit)
    assert _code(dirty) == "GIT_DIRTY_CONFLICT"
    assert "未记录的改动" in target.read_text(encoding="utf-8")
