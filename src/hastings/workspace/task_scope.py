"""本轮任务的修改范围（0.6 V06-B1）。

普通任务起初没有作品写权限：可以列目录、搜索、读取和回答。模型定位目标后，用
``set_task_scope`` 登记「这次要做什么、凭什么、允许动哪些确切路径、哪些明确禁止」，
写工具才可能通过。这是同一个 Agent 的工具约束，不是另起一轮分类调用，也不强制
先提交一份计划。

范围只接受规范化的确切路径，不接受目录前缀或通配符；材料选择不产生任何操作授权。
授权依据只来自作者消息：助手计划、作品内容和旧范围都不能单独构成授权。应用能证明
「操作没有超出已记录范围」，不能证明「范围解释符合自然语言意图」——后者属于任务理解，
需要单独验收，不能宣传成完整的授权证明。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import is_managed_path

__all__ = [
    "Mutation",
    "MutationAction",
    "ScopeController",
    "ScopeDraft",
    "TaskScope",
    "WriteScope",
]

MutationAction = Literal["create", "update", "delete", "move"]
ScopeMode = Literal["read_only", "edit"]

_ACTION_LABEL: dict[str, str] = {
    "create": "新建",
    "update": "修改",
    "delete": "删除",
    "move": "移动",
}


@dataclass(frozen=True, slots=True)
class Mutation:
    """一次作品变更：确切路径 + 操作类别；移动另带目标路径。"""

    action: MutationAction
    path: str
    target: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"action": self.action, "path": self.path}
        if self.target is not None:
            payload["target"] = self.target
        return payload

    def describe(self) -> str:
        if self.target is not None:
            return f"{_ACTION_LABEL[self.action]} {self.path} → {self.target}"
        return f"{_ACTION_LABEL[self.action]} {self.path}"


@dataclass(frozen=True, slots=True)
class WriteScope:
    """校验用的不可变范围：模式、允许的操作集合、禁止路径。"""

    mode: ScopeMode
    grants: frozenset[Mutation]
    denied_paths: frozenset[str]

    def permits(self, operation: Mutation) -> bool:
        touched = {operation.path}
        if operation.target is not None:
            touched.add(operation.target)
        return (
            self.mode == "edit"
            and touched.isdisjoint(self.denied_paths)
            and operation in self.grants
        )


@dataclass(frozen=True, slots=True)
class ScopeDraft:
    """模型登记的范围内容；字段同时用于事件展示与校验。

    ``authorization`` 是作者原话里的短引文，``authorization_seq`` 定位那条消息；
    承接旧要求时 ``carried_from_seq`` 记被承接的作者请求，两者都要能在本轮可见的
    作者消息里找到。``extendable`` 对应「同步相关章节」这类可发现范围。
    """

    mode: ScopeMode
    grants: tuple[Mutation, ...] = ()
    denied_paths: tuple[str, ...] = ()
    summary: str = ""
    authorization: str = ""
    authorization_seq: int | None = None
    carried_from_seq: int | None = None
    required_references: tuple[str, ...] = ()
    extendable: bool = False
    extend_actions: tuple[MutationAction, ...] = ()
    note: str = ""


@dataclass(frozen=True, slots=True)
class TaskScope:
    """已生效的范围：版本号、原始登记内容与校验视图。"""

    version: int
    draft: ScopeDraft
    scope: WriteScope

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "mode": self.scope.mode,
            "summary": self.draft.summary,
            "authorization": self.draft.authorization,
            "authorization_seq": self.draft.authorization_seq,
            "carried_from_seq": self.draft.carried_from_seq,
            "grants": [item.to_dict() for item in self.draft.grants],
            "denied_paths": list(self.draft.denied_paths),
            "required_references": list(self.draft.required_references),
            "extendable": self.draft.extendable,
            "extend_actions": list(self.draft.extend_actions),
        }


class ScopeController:
    """本轮范围的状态机：登记、追加与操作检查。

    一个 ``RunContext`` 一份，不跨轮继承。旧版本不能覆盖新版本；追加事件在写入前生效，
    不追认已经发生的越界写入。
    """

    def __init__(self) -> None:
        self._current: TaskScope | None = None
        self._history: list[TaskScope] = []

    @property
    def current(self) -> TaskScope | None:
        return self._current

    def set_scope(
        self,
        draft: ScopeDraft,
        *,
        author_texts: Sequence[tuple[int, str]],
    ) -> TaskScope:
        """登记本轮范围；重复登记生成新版本，不修改旧版本记录。

        重复登记只能收窄：去掉已登记的路径与操作、补充禁止项、改成只读。扩大范围必须走
        ``extend``——它要求可发现范围、操作类别白名单与关联证据；否则先登记一个小范围、
        再换一个大范围，就能绕开固定范围与禁止项。
        """
        _validate_draft(draft, author_texts)
        if self._current is not None:
            _validate_reregistration(self._current, draft)
        scope = TaskScope(
            version=(self._current.version + 1) if self._current is not None else 1,
            draft=draft,
            scope=WriteScope(
                mode=draft.mode,
                grants=frozenset(draft.grants),
                denied_paths=frozenset(draft.denied_paths),
            ),
        )
        if self._current is not None:
            self._history.append(self._current)
        self._current = scope
        return scope

    def extend(
        self,
        *,
        version: int,
        grants: Sequence[Mutation],
        evidence: str,
        author_texts: Sequence[tuple[int, str]],
    ) -> TaskScope:
        """在可发现范围内追加具体路径；保留原禁止项与操作类别限制。"""
        current = self._require_scope()
        if version != current.version:
            raise WorkspaceError(
                code="TASK_SCOPE_STALE_CONFLICT",
                where="task_scope.extend",
                why=f"这次追加基于范围版本 {version}，当前已经是第 {current.version} 版",
                fix="读取当前范围后按最新版本重新追加",
                details={"expected": current.version, "given": version},
            )
        if not current.draft.extendable:
            raise WorkspaceError(
                code="TASK_SCOPE_DENIED_CONFLICT",
                where="task_scope.extend",
                why="本轮登记的是固定范围，不能自行追加文件",
                fix="在回复里说明还需要修改哪些文件，由作者明确要求后再改",
                details={"version": current.version},
            )
        if not grants:
            raise WorkspaceError(
                code="TASK_SCOPE_DENIED_CONFLICT",
                where="task_scope.extend",
                why="追加范围至少要给一个确切路径",
                fix="给出与原目标有关的具体文件路径与操作类别",
            )
        allowed = set(current.draft.extend_actions)
        for grant in grants:
            _validate_mutation(grant, where="task_scope.extend")
            if grant.action not in allowed:
                raise WorkspaceError(
                    code="TASK_SCOPE_DENIED_CONFLICT",
                    where="task_scope.extend",
                    why=f"本轮没有授权「{_ACTION_LABEL[grant.action]}」这类操作",
                    fix="只追加原任务已允许的操作类别；其他改动等作者明确要求",
                    details={"action": grant.action, "allowed": sorted(allowed)},
                )
            if grant.path in current.scope.denied_paths or (
                grant.target is not None and grant.target in current.scope.denied_paths
            ):
                raise WorkspaceError(
                    code="TASK_SCOPE_DENIED_CONFLICT",
                    where="task_scope.extend",
                    why=f"这个路径在本轮被明确禁止：{grant.path}",
                    fix="保留该文件，并在回复里说明需要作者决定的关联修改",
                    details={"path": grant.path},
                )
        if not evidence.strip():
            raise WorkspaceError(
                code="TASK_SCOPE_EVIDENCE_INVALID",
                where="task_scope.extend",
                why="追加范围需要说明实际读到的关联证据",
                fix="先读取要追加的文件，再用其中的短引文说明为什么它属于本次任务",
            )
        merged = tuple(dict.fromkeys((*current.draft.grants, *grants)))
        draft = ScopeDraft(
            mode=current.draft.mode,
            grants=merged,
            denied_paths=current.draft.denied_paths,
            summary=current.draft.summary,
            authorization=current.draft.authorization,
            authorization_seq=current.draft.authorization_seq,
            carried_from_seq=current.draft.carried_from_seq,
            required_references=current.draft.required_references,
            extendable=True,
            extend_actions=current.draft.extend_actions,
            note=evidence.strip()[:200],
        )
        _validate_draft(draft, author_texts)
        scope = TaskScope(
            version=current.version + 1,
            draft=draft,
            scope=WriteScope(
                mode=draft.mode,
                grants=frozenset(draft.grants),
                denied_paths=frozenset(draft.denied_paths),
            ),
        )
        self._history.append(current)
        self._current = scope
        return scope

    def require(self, operation: Mutation) -> None:
        """写工具的统一入口：没有范围、只读、越界都给出可修正的具体错误。"""
        current = self._current
        if current is None:
            raise WorkspaceError(
                code="TASK_SCOPE_REQUIRED_CONFLICT",
                where=f"task_scope:{operation.path}",
                why="这一轮还没有登记修改范围，写工具不能用",
                fix=(
                    "先用 set_task_scope 登记任务理解、作者授权引文与确切路径；只是讨论时不必写文件"
                ),
                details={"operation": operation.to_dict()},
            )
        if current.scope.mode == "read_only":
            raise WorkspaceError(
                code="TASK_SCOPE_DENIED_CONFLICT",
                where=f"task_scope:{operation.path}",
                why="这一轮登记为只读任务",
                fix="只在回复里说明结论；需要改动时由作者提出新的修改要求",
                details={"operation": operation.to_dict(), "version": current.version},
            )
        if current.scope.permits(operation):
            return
        touched = [operation.path]
        if operation.target is not None:
            touched.append(operation.target)
        blocked = [path for path in touched if path in current.scope.denied_paths]
        if blocked:
            raise WorkspaceError(
                code="TASK_SCOPE_DENIED_CONFLICT",
                where=f"task_scope:{operation.path}",
                why=f"这个路径在本轮被明确禁止修改：{blocked[0]}",
                fix="保留该文件，并在回复里说明需要作者决定的关联修改",
                details={"operation": operation.to_dict(), "denied_paths": blocked},
            )
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where=f"task_scope:{operation.path}",
            why=f"本轮范围不包含这次操作：{operation.describe()}",
            fix=(
                "只改已登记的路径与操作类别；需要扩大范围时说明理由并请作者明确要求"
                "（可发现范围可用 extend 追加具体文件）"
            ),
            details={
                "operation": operation.to_dict(),
                "grants": [item.to_dict() for item in current.draft.grants],
                "version": current.version,
            },
        )

    def _require_scope(self) -> TaskScope:
        if self._current is None:
            raise WorkspaceError(
                code="TASK_SCOPE_REQUIRED_CONFLICT",
                where="task_scope.extend",
                why="这一轮还没有登记修改范围",
                fix="先用 set_task_scope 登记本轮范围",
            )
        return self._current


def _validate_draft(draft: ScopeDraft, author_texts: Sequence[tuple[int, str]]) -> None:
    """结构与证据校验：路径必须确切，授权引文必须来自作者消息。"""
    if not draft.summary.strip():
        raise _evidence_error("没有写清这一轮要做什么")
    if draft.mode == "read_only":
        if draft.grants:
            raise _evidence_error("只读任务不能登记写操作")
    elif not draft.grants:
        raise _evidence_error("修改任务至少要登记一个确切的路径与操作")
    for grant in draft.grants:
        _validate_mutation(grant, where="task_scope.set")
    for path in draft.denied_paths:
        _validate_path(path, where="task_scope.set")
    for path in draft.required_references:
        _validate_path(path, where="task_scope.set")
    if not set(draft.extend_actions) <= {"create", "update", "delete", "move"}:
        raise _evidence_error("可追加的操作类别不合法")
    if draft.extendable and not draft.extend_actions:
        raise _evidence_error("可发现范围要写明允许追加哪类操作")
    _validate_authorization(draft, author_texts)


def _validate_reregistration(current: TaskScope, draft: ScopeDraft) -> None:
    """重复登记只能收窄：不新增允许操作、不撤禁止项、不打开可追加开关。

    改成只读时禁止项随之失去意义，不再要求保留；但只读范围没有写权限，重新登记回
    修改范围仍要过「不新增操作」这一关。
    """
    added = [grant for grant in draft.grants if grant not in current.draft.grants]
    if added:
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where="task_scope.set",
            why=f"重新登记只能收窄范围，这次新增了：{added[0].describe()}",
            fix=(
                "这一轮要改的路径一次登记完整；可发现范围用 extend_task_scope 追加已读到的"
                "关联文件，固定范围要由作者明确要求新的改动"
            ),
            details={
                "added": [grant.to_dict() for grant in added],
                "version": current.version,
            },
        )
    if draft.extendable and not current.draft.extendable:
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where="task_scope.set",
            why="这次登记把固定范围改成了可追加范围",
            fix="固定范围不因重新登记而放宽；需要追加时请作者明确要求",
            details={"version": current.version},
        )
    extra_actions = sorted(set(draft.extend_actions) - set(current.draft.extend_actions))
    if extra_actions:
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where="task_scope.set",
            why=(
                "这次登记新增了可追加的操作类别："
                f"{'、'.join(_ACTION_LABEL[item] for item in extra_actions)}"
            ),
            fix="只保留原登记里允许追加的操作类别",
            details={"added_actions": extra_actions, "version": current.version},
        )
    removed = sorted(current.scope.denied_paths - set(draft.denied_paths))
    if removed and draft.mode == "edit":
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where="task_scope.set",
            why=f"这次登记撤掉了本轮已明确的禁止项：{removed[0]}",
            fix="保留已登记的禁止项；确实要改这个文件时请作者明确说明",
            details={"removed_denied_paths": removed, "version": current.version},
        )


def _validate_mutation(grant: Mutation, *, where: str) -> None:
    _validate_path(grant.path, where=where)
    if grant.action == "move":
        if grant.target is None:
            raise _evidence_error("移动操作必须给出目标路径", where=where)
        _validate_path(grant.target, where=where)
        if grant.target == grant.path:
            raise _evidence_error("移动的源与目标是同一个文件", where=where)
    elif grant.target is not None:
        raise _evidence_error("只有移动操作可以带目标路径", where=where)


def _validate_path(path: str, *, where: str) -> None:
    if not is_managed_path(path):
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where=where,
            why=f"范围只能登记作品内的确切文件路径：{path}",
            fix="用 正文/xxx.md、设定集/角色小传/姓名.md、设定集/线索.md 或 设定集/真相.md",
            details={"path": path},
        )


def _validate_authorization(draft: ScopeDraft, author_texts: Sequence[tuple[int, str]]) -> None:
    quote = " ".join(draft.authorization.split())
    if not quote:
        raise _evidence_error("没有给出作者的授权原话")
    if not author_texts:
        raise _evidence_error("这一轮没有可核对的作者消息，不能登记写权限")
    known = {seq for seq, _ in author_texts}
    if draft.authorization_seq is not None and draft.authorization_seq not in known:
        raise _evidence_error("授权引文定位到的不是本轮可见的作者消息")
    if draft.carried_from_seq is not None:
        if draft.carried_from_seq not in known:
            raise _evidence_error("承接的作者请求不在本轮可见的作者消息里")
        if draft.carried_from_seq == draft.authorization_seq:
            raise _evidence_error("承接对象与授权语句是同一条消息，说明「继续」没有真正承接旧要求")
    for _, text in author_texts:
        if quote in " ".join(text.split()):
            return
    raise _evidence_error("授权引文没有出现在本轮可见的作者消息里")


def _evidence_error(why: str, *, where: str = "task_scope.set") -> WorkspaceError:
    return WorkspaceError(
        code="TASK_SCOPE_EVIDENCE_INVALID",
        where=where,
        why=why,
        fix="按作者原话与确切路径重新登记；引用作者消息时保持文字一致",
    )
