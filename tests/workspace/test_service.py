"""一轮写入的记录协调（0.4 V04-2，计划 M05 的版本部分）。

契约：首次真正写文件前才准备基线；外部改动先单独记一笔；结束时按实际变化提交一次；
没有变化不提交；停止或失败仍记录已写入内容并标明「部分完成」；Git 失败不掩盖文件
已保存，结果记为 unrecorded。执行状态与记录状态是两条独立的轴。

真实临时仓库，不涉及模型与旧数据库。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="需要本机安装 git")


def _git(root: Path, *args: str) -> str:
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
    if completed.returncode:
        raise AssertionError(f"git {' '.join(args)} 失败：{completed.stderr}")
    return completed.stdout


def _setup(tmp_path: Path) -> tuple[Workspace, GitRepo]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    repo = GitRepo(workspace.root)
    repo.init()
    return workspace, repo


def _subjects(repo: GitRepo) -> list[str]:
    return [commit.subject for commit in repo.log()]


# -- 基线 -----------------------------------------------------------------


def test_read_only_session_never_prepares_or_commits(tmp_path: Path) -> None:
    workspace, repo = _setup(tmp_path)
    head = repo.head()
    session = WriteSession(workspace, instruction="这个故事适合什么视角？")

    result = session.finish()

    assert result.recording == "no_change"
    assert result.changed_paths == ()
    assert repo.head() == head
    assert session.prepared is False


def test_prepare_records_external_changes_before_the_turn(tmp_path: Path) -> None:
    workspace, repo = _setup(tmp_path)
    path = "正文/0001 雨夜.md"
    workspace.write(path, "# 雨夜\n\n初稿。\n", expected_digest=None)
    repo.record("初稿")
    # 作者在应用之外改过这一章
    (workspace.root / path).write_text("# 雨夜\n\n作者改过。\n", encoding="utf-8")

    session = WriteSession(workspace, instruction="改写第一章的开头")
    baseline = session.prepare()

    assert session.external_commit is not None
    assert baseline == repo.head() == session.external_commit
    assert _subjects(repo)[0].startswith("外部修改")

    digest = workspace.read(path).digest
    workspace.write(path, "# 雨夜\n\n作者改过。\n\n模型补了一段。\n", expected_digest=digest)
    result = session.finish()

    assert result.recording == "committed"
    assert result.changed_paths == (path,)
    subjects = _subjects(repo)
    assert subjects[0].startswith("写作：改写第一章的开头")
    assert subjects[1].startswith("外部修改")
    # 作者改动与模型改动分属两个提交：模型提交只含自己补的那一段。
    model_diff = repo.commit_diff(repo.head() or "")
    external_diff = repo.commit_diff(session.external_commit or "")
    assert "模型补了一段" in model_diff
    assert "+作者改过" not in model_diff
    assert "+作者改过" in external_diff


def test_prepare_with_clean_tree_creates_no_external_commit(tmp_path: Path) -> None:
    workspace, repo = _setup(tmp_path)
    head = repo.head()

    session = WriteSession(workspace)
    baseline = session.prepare()

    assert session.external_commit is None
    assert baseline == head
    assert len(repo.log()) == 1


# -- 结束 -----------------------------------------------------------------


def test_finish_without_changes_makes_no_commit(tmp_path: Path) -> None:
    workspace, repo = _setup(tmp_path)
    head = repo.head()
    session = WriteSession(workspace, instruction="先讨论")
    session.prepare()

    result = session.finish()

    assert result.recording == "no_change"
    assert repo.head() == head


def test_finish_stopped_records_partial_commit(tmp_path: Path) -> None:
    workspace, repo = _setup(tmp_path)
    session = WriteSession(workspace, instruction="写第一章")
    session.prepare()
    workspace.create_chapter("雨夜", "# 雨夜\n\n写到一半。\n")

    result = session.finish("stopped")

    assert result.status == "stopped"
    assert result.recording == "committed"
    assert result.changed_paths == ("正文/0001 雨夜.md",)
    assert _subjects(repo)[0] == "写作（已停止）：写第一章"
    assert (workspace.root / "正文/0001 雨夜.md").is_file()


def test_finish_failed_keeps_written_files_and_records_them(tmp_path: Path) -> None:
    workspace, repo = _setup(tmp_path)
    session = WriteSession(workspace, instruction="改角色小传")
    session.prepare()
    workspace.write("设定集/角色小传/管家.md", "# 管家\n\n管家，五十岁。\n", expected_digest=None)

    result = session.finish("failed")

    # 两条轴：执行失败，但内容已经写入并记录。
    assert result.status == "failed"
    assert result.recording == "committed"
    assert _subjects(repo)[0] == "写作（失败）：改角色小传"
    assert "管家" in (workspace.root / "设定集/角色小传/管家.md").read_text(encoding="utf-8")


def test_finish_reports_unrecorded_when_git_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, repo = _setup(tmp_path)
    session = WriteSession(workspace, instruction="写第一章")
    session.prepare()
    workspace.create_chapter("雨夜", "# 雨夜\n")

    def broken(*args: object, **kwargs: object) -> str | None:
        raise WorkspaceError(
            code="GIT_COMMAND_FAILED",
            where="git.record",
            why="git 命令失败：模拟",
            fix="稍后单独补记",
        )

    monkeypatch.setattr(GitRepo, "record", broken)

    result = session.finish()

    assert result.recording == "unrecorded"
    assert result.commit is None
    assert result.changed_paths == ("正文/0001 雨夜.md",)
    assert "文件已保存" in result.note
    assert (workspace.root / "正文/0001 雨夜.md").is_file()


def test_finish_reports_unrecorded_when_history_moves(tmp_path: Path) -> None:
    workspace, repo = _setup(tmp_path)
    session = WriteSession(workspace, instruction="写第一章")
    session.prepare()
    workspace.create_chapter("雨夜", "# 雨夜\n")
    # 另一进程改了别的文件并提交：HEAD 前进，但本轮改动还没记录。
    digest = None
    workspace.write("设定集/线索.md", "# 线索\n\n门口有雪。\n", expected_digest=digest)
    _git(workspace.root, "add", "-A", "--", "设定集")
    _git(workspace.root, "commit", "-m", "外部提交")

    result = session.finish()

    assert result.recording == "unrecorded"
    assert "历史被外部改动" in result.note
    assert result.changed_paths == ("正文/0001 雨夜.md",)


def test_prepare_refuses_staged_work(tmp_path: Path) -> None:
    workspace, repo = _setup(tmp_path)
    head = repo.head()
    (workspace.root / "正文/0001 雨夜.md").write_text("# 雨夜\n", encoding="utf-8")
    _git(workspace.root, "add", "--", "正文/0001 雨夜.md")
    session = WriteSession(workspace, instruction="写第一章")

    with pytest.raises(WorkspaceError) as staged:
        session.prepare()

    assert staged.value.code == "GIT_STAGED_CHANGES_CONFLICT"
    assert session.prepared is False
    assert repo.head() == head


def test_record_pending_records_without_running_a_turn(tmp_path: Path) -> None:
    workspace, repo = _setup(tmp_path)
    session = WriteSession(workspace)
    workspace.create_chapter("雨夜", "# 雨夜\n")

    commit = session.record_pending()

    assert commit is not None
    assert repo.changes() == ()
    assert session.record_pending() is None


def test_result_dict_matches_api_contract(tmp_path: Path) -> None:
    workspace, _ = _setup(tmp_path)
    session = WriteSession(workspace)
    session.prepare()
    workspace.create_chapter("雨夜", "# 雨夜\n")

    body = session.finish().to_dict()

    assert set(body) == {"status", "recording", "changed_paths", "commit", "note"}
    assert body["status"] == "completed"
    assert body["recording"] == "committed"
    assert isinstance(body["changed_paths"], list)
