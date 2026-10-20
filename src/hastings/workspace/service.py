"""一轮写入的记录协调（0.4 M05 的版本部分）。

作者发出一次修改请求，可能写零个、一个或多个文件，也可能中途停止或失败。这一层
只负责把「这一轮实际发生了什么」翻译成文件和 Git 的两件事：

- **内容基线**：首次真正写文件之前才准备。受管文件已有未记录的外部改动时，先单独
  记一笔「外部修改」，让作者改动和模型改动不混在同一个提交里；只读讨论不准备基线，
  也不要求仓库可写。
- **结果记录**：结束时按磁盘上的实际变化提交一次；没有变化不提交。停止或失败时
  已写入的内容仍然记一笔并标明「部分完成」。Git 失败不掩盖文件已保存的事实，
  结果记为 ``unrecorded``，可以单独补记，不重跑模型。

执行状态（完成/部分/失败/停止/中断）与记录状态（已提交/无变化/未记录）是两条轴：
「文件写进去了但历史没记上」必须能同时表达，不能合并成一个成功/失败。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo

__all__ = [
    "EXTERNAL_CHANGE_MESSAGE",
    "Recording",
    "RunResult",
    "RunStatus",
    "WriteSession",
]

RunStatus = Literal["completed", "partial", "failed", "stopped", "interrupted"]
Recording = Literal["committed", "no_change", "unrecorded"]

#: 外部编辑形成的独立提交：让作者的改动先有归属，再开始这一轮。
EXTERNAL_CHANGE_MESSAGE = "外部修改：应用之外保存的内容"

_STATUS_LABEL: dict[str, str] = {
    "partial": "部分完成",
    "failed": "失败",
    "stopped": "已停止",
    "interrupted": "中断",
}

_MAX_SUMMARY = 40


@dataclass(frozen=True, slots=True)
class RunResult:
    """一轮的结果：执行状态、记录状态、实际改动路径与提交。"""

    status: RunStatus
    recording: Recording
    changed_paths: tuple[str, ...]
    commit: str | None
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "recording": self.recording,
            "changed_paths": list(self.changed_paths),
            "commit": self.commit,
            "note": self.note,
        }


class WriteSession:
    """一轮写入的 Git 记录。调用顺序：``prepare()`` → 写文件 → ``finish(status)``。"""

    def __init__(self, workspace: Workspace, *, instruction: str = "") -> None:
        self.workspace = workspace
        self.repo = GitRepo(workspace.root)
        self.instruction = instruction
        self.baseline_head: str | None = None
        self.external_commit: str | None = None
        self.prepared = False

    def prepare(self) -> str | None:
        """首次实际修改前调用，返回这一轮的内容基线（提交 ID）。

        仓库不可写、有冲突或作者预暂存内容时抛错：此时还没有写任何文件，
        调用方应当停止写入，但阅读与讨论仍然可用。
        """
        self.repo.require_ready()
        self.baseline_head = self.repo.head()
        pending = self.repo.changes()
        if pending:
            # 作者在应用之外改过：先单独记一笔，避免混进本轮模型改动。
            self.external_commit = self.repo.record(EXTERNAL_CHANGE_MESSAGE)
            if self.external_commit is not None:
                self.baseline_head = self.external_commit
        self.prepared = True
        return self.baseline_head

    def finish(self, status: RunStatus = "completed") -> RunResult:
        """结束一轮：按实际变化提交一次，并如实报告记录状态。"""
        if not self.prepared:
            # 只读讨论：没有准备基线，也就没有要记录的内容。
            return RunResult(status=status, recording="no_change", changed_paths=(), commit=None)

        changes = self.repo.changes()
        paths = tuple(sorted({change.path for change in changes}))
        if not paths:
            return RunResult(status=status, recording="no_change", changed_paths=(), commit=None)

        head = self.repo.head()
        if head != self.baseline_head:
            # 运行期间历史被别人推进：不把未知改动并进本轮提交。
            return RunResult(
                status=status,
                recording="unrecorded",
                changed_paths=paths,
                commit=None,
                note="运行期间历史被外部改动，未自动提交；文件已保存，可单独补记",
            )

        try:
            commit = self.repo.record(self._message(status), paths)
        except WorkspaceError as exc:
            return RunResult(
                status=status,
                recording="unrecorded",
                changed_paths=paths,
                commit=None,
                note=f"文件已保存，历史尚未记录：{exc.why}",
            )
        if commit is None:
            return RunResult(status=status, recording="no_change", changed_paths=(), commit=None)
        return RunResult(
            status=status,
            recording="committed",
            changed_paths=paths,
            commit=commit,
        )

    def record_pending(self, message: str = EXTERNAL_CHANGE_MESSAGE) -> str | None:
        """单独补记当前未记录的改动：只执行 Git，不调用模型。"""
        self.repo.require_ready()
        return self.repo.record(message)

    def _message(self, status: RunStatus) -> str:
        summary = _summary(self.instruction)
        if status == "completed":
            return f"写作：{summary}" if summary else "写作：记录本轮修改"
        label = _STATUS_LABEL.get(status, status)
        return f"写作（{label}）：{summary}" if summary else f"写作（{label}）：记录已写入的内容"


def _summary(instruction: str) -> str:
    collapsed = " ".join(instruction.split())
    return collapsed[:_MAX_SUMMARY]
