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
from hastings.workspace.legacy_files import require_legacy_file_authorization

__all__ = [
    "ClueDesignAction",
    "ClueDesignGrant",
    "ModuleAction",
    "ModuleChangeAction",
    "ModuleChangeTarget",
    "ModuleContentGrant",
    "ModuleGrant",
    "ModuleReference",
    "Mutation",
    "MutationAction",
    "ScopeController",
    "ScopeDraft",
    "TaskScope",
    "WriteScope",
]

MutationAction = Literal["create", "update", "delete", "move"]
ModuleAction = Literal["attach", "detach"]
ModuleChangeAction = Literal["create", "update"]
ClueDesignAction = Literal["link", "inference"]
ScopeMode = Literal["read_only", "edit"]

_ACTION_LABEL: dict[str, str] = {
    "create": "新建",
    "update": "修改",
    "delete": "删除",
    "move": "移动",
}

#: 明确指向某类模块操作的短语；只用来核对作者是否真的提出了要求，不推断任务意图。
_MODULE_PHRASES: dict[str, tuple[str, ...]] = {
    "attach": ("装入", "装上", "装进", "放进", "加载", "挂上", "装配", "绑定"),
    "detach": ("卸下", "移除", "拆下", "卸载", "解绑"),
}
_PUBLISH_PHRASES = ("发布", "存成模块", "保存到模块库", "存进模块库", "放进模块库", "另存为模块")
_CLUE_DESIGN_PHRASES: dict[str, tuple[str, ...]] = {
    "link": ("关联计划", "关联作者计划", "挂到线索", "接到线索", "设计关联", "关联到线索"),
    "inference": ("推理", "目标", "结论", "构思", "设想"),
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
class ModuleGrant:
    """一次装配操作：装入必须精确到模块版本，卸下必须精确到绑定。

    库与作品独立：这里只允许改本作品的装配清单，不包含库发布；发布另由
    ``library_publish`` 登记，并复用作者的发布原话。
    """

    action: ModuleAction
    module_id: str = ""
    revision_id: str = ""
    binding_id: str = ""

    def to_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {"action": self.action}
        if self.module_id:
            payload["module_id"] = self.module_id
        if self.revision_id:
            payload["revision_id"] = self.revision_id
        if self.binding_id:
            payload["binding_id"] = self.binding_id
        return payload

    def describe(self) -> str:
        if self.action == "attach":
            return f"装入 {self.module_id}@{self.revision_id}"
        return f"卸下 {self.binding_id}"


@dataclass(frozen=True, slots=True)
class ModuleReference:
    """必读的已装模块条目；名称只供展示，不作为定位依据。"""

    module_id: str
    revision_id: str
    entry_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "module_id": self.module_id,
            "revision_id": self.revision_id,
            "entry_id": self.entry_id,
        }


@dataclass(frozen=True, slots=True)
class ModuleContentGrant:
    """一次作品内模块内容操作。

    新建还没有真实 ID，用本任务稳定的 ``slot`` 表示目标，由应用在提交时分配；
    已有内容更新必须精确到绑定、模块与基础版本，避免只凭名称改错对象。
    """

    action: ModuleChangeAction
    slot: str = ""
    purpose: str = ""
    binding_id: str = ""
    module_id: str = ""
    base_revision: str = ""

    def to_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {"action": self.action}
        if self.action == "create":
            payload["slot"] = self.slot
            if self.purpose:
                payload["purpose"] = self.purpose
            return payload
        payload.update(
            {
                "binding_id": self.binding_id,
                "module_id": self.module_id,
                "base_revision": self.base_revision,
            }
        )
        if self.purpose:
            payload["purpose"] = self.purpose
        return payload

    def describe(self) -> str:
        if self.action == "create":
            return f"新建模块 {self.slot}"
        return f"修改绑定 {self.binding_id}（{self.module_id}@{self.base_revision}）"


@dataclass(frozen=True, slots=True)
class ClueDesignGrant:
    """一次线索设计关系操作：关联作者计划或保存推理目标。

    关系授权与模块／正文写入分开：它只允许改账本中的计划与目标，不授予任何内容写权限。
    """

    action: ClueDesignAction
    clue_id: str = ""
    inference_id: str = ""
    clue_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"action": self.action}
        if self.clue_id:
            payload["clue_id"] = self.clue_id
        if self.inference_id:
            payload["inference_id"] = self.inference_id
        if self.clue_ids:
            payload["clue_ids"] = list(self.clue_ids)
        return payload

    def describe(self) -> str:
        if self.action == "link":
            return f"关联线索 {self.clue_id} 的作者计划"
        target = self.inference_id or "新建推理目标"
        return f"保存推理目标（{target}）"


@dataclass(frozen=True, slots=True)
class ModuleChangeTarget:
    """变更集里实际提交的一项操作目标；校验时与登记内容逐项对照。"""

    operation_id: str
    action: ModuleChangeAction
    binding_id: str = ""
    module_id: str = ""
    base_revision: str = ""


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
    作者消息里找到。``extendable`` 对应「同步相关章节」这类可发现范围；追加时
    ``evidence_path`` 与 ``note`` 记下依据来自哪份文件的哪句原文。
    """

    mode: ScopeMode
    grants: tuple[Mutation, ...] = ()
    denied_paths: tuple[str, ...] = ()
    summary: str = ""
    authorization: str = ""
    authorization_seq: int | None = None
    carried_from_seq: int | None = None
    required_references: tuple[str, ...] = ()
    required_module_references: tuple[ModuleReference, ...] = ()
    #: 作者明确要求从全局模块库查找时登记；只开搜索，不自动装入或发布。
    library_search: bool = False
    #: 本作品装配操作：装入精确版本或卸下确切绑定；只在专用装配轮使用。
    module_grants: tuple[ModuleGrant, ...] = ()
    #: 本作品模块内容操作：新建槽位或更新精确版本；普通创作任务用它登记。
    module_content_grants: tuple[ModuleContentGrant, ...] = ()
    #: 线索计划与推理目标操作；只改关系，不授予模块、正文或其他内容写入。
    clue_design_grants: tuple[ClueDesignGrant, ...] = ()
    #: 作者明确要求把内容发布到全局模块库；不隐含装入或改正文。
    library_publish: bool = False
    extendable: bool = False
    extend_actions: tuple[MutationAction, ...] = ()
    evidence_path: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class TaskScope:
    """已生效的范围：版本号、原始登记内容与校验视图。"""

    version: int
    draft: ScopeDraft
    scope: WriteScope

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": self.version,
            "mode": self.scope.mode,
            "summary": self.draft.summary,
            "authorization": self.draft.authorization,
            "authorization_seq": self.draft.authorization_seq,
            "carried_from_seq": self.draft.carried_from_seq,
            "grants": [item.to_dict() for item in self.draft.grants],
            "denied_paths": list(self.draft.denied_paths),
            "required_references": list(self.draft.required_references),
            "required_module_references": [
                item.to_dict() for item in self.draft.required_module_references
            ],
            "library_search": self.draft.library_search,
            "module_grants": [item.to_dict() for item in self.draft.module_grants],
            "module_content_grants": [item.to_dict() for item in self.draft.module_content_grants],
            "clue_design_grants": [item.to_dict() for item in self.draft.clue_design_grants],
            "library_publish": self.draft.library_publish,
            "extendable": self.draft.extendable,
            "extend_actions": list(self.draft.extend_actions),
        }
        # 依据只在追加时存在：登记事件保持原来的字段，不写两个空值。
        if self.draft.evidence_path:
            payload["evidence_path"] = self.draft.evidence_path
            payload["evidence"] = self.draft.note
        return payload


class ScopeController:
    """本轮范围的状态机：登记、追加与操作检查。

    一个 ``RunContext`` 一份，不跨轮继承。旧版本不能覆盖新版本；追加事件在写入前生效，
    不追认已经发生的越界写入。
    """

    def __init__(self) -> None:
        self._current: TaskScope | None = None
        self._history: list[TaskScope] = []
        self._created_chapters: set[str] = set()

    @property
    def current(self) -> TaskScope | None:
        return self._current

    def set_scope(
        self,
        draft: ScopeDraft,
        *,
        author_texts: Sequence[tuple[int, str]],
        module_content_available: bool = False,
    ) -> TaskScope:
        """登记本轮范围；重复登记生成新版本，不修改旧版本记录。

        重复登记只能收窄：去掉已登记的路径与操作、补充禁止项、改成只读。扩大范围必须走
        ``extend``——它要求可发现范围、操作类别白名单与关联证据；否则先登记一个小范围、
        再换一个大范围，就能绕开固定范围与禁止项。
        """
        _validate_draft(draft, author_texts)
        if self._current is not None:
            _validate_reregistration(self._current, draft)
        if module_content_available:
            require_legacy_file_authorization(
                draft.grants,
                author_texts=author_texts,
                carried_from_seq=draft.carried_from_seq,
                where="task_scope.set",
            )
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
        evidence_path: str,
        evidence: str,
        author_texts: Sequence[tuple[int, str]],
        module_content_available: bool = False,
    ) -> TaskScope:
        """在可发现范围内追加具体路径；保留原禁止项与操作类别限制。

        依据必须是「哪份文件 + 哪句原文」。这里只核对结构；这句原文是否真的在本轮读过、
        是否落在提供给模型的区间里，由工具入口用读取记录核对（本模块不持有材料状态）。
        """
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
        if not evidence_path.strip():
            raise WorkspaceError(
                code="TASK_SCOPE_EVIDENCE_INVALID",
                where="task_scope.extend",
                why="追加范围要说明这句原文来自哪份文件",
                fix="带上实际读过并引用的文件路径，与 evidence 一起给出",
            )
        _validate_path(evidence_path, where="task_scope.extend")
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
            required_module_references=current.draft.required_module_references,
            library_search=current.draft.library_search,
            module_grants=current.draft.module_grants,
            module_content_grants=current.draft.module_content_grants,
            library_publish=current.draft.library_publish,
            extendable=True,
            extend_actions=current.draft.extend_actions,
            evidence_path=evidence_path,
            note=evidence.strip()[:200],
        )
        _validate_draft(draft, author_texts)
        if module_content_available:
            require_legacy_file_authorization(
                draft.grants,
                author_texts=author_texts,
                carried_from_seq=draft.carried_from_seq,
                where="task_scope.extend",
            )
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

    def require(
        self,
        operation: Mutation,
        *,
        author_texts: Sequence[tuple[int, str]] = (),
        module_content_available: bool = False,
    ) -> None:
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
        created_chapter_update = (
            operation.action == "update"
            and operation.path in self._created_chapters
            and current.scope.permits(Mutation("create", operation.path))
        )
        if current.scope.permits(operation) or created_chapter_update:
            if module_content_available:
                require_legacy_file_authorization(
                    (operation,),
                    author_texts=author_texts,
                    carried_from_seq=current.draft.carried_from_seq,
                    where=f"task_scope:{operation.path}",
                )
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

    def record_created_chapter(self, path: str) -> None:
        """仅在新建实际成功后调用；当前范围仍需保留该章的新建授权。"""
        if path.startswith("正文/"):
            self._created_chapters.add(path)

    def check_module_changes(self, targets: Sequence[ModuleChangeTarget]) -> None:
        """变更集提交前逐项核对：新建槽位或更新的绑定、模块与基础版本都必须已登记。"""
        current = self._require_scope()
        if current.scope.mode == "read_only":
            raise WorkspaceError(
                code="TASK_SCOPE_DENIED_CONFLICT",
                where="task_scope.module_changes",
                why="这一轮登记为只读任务",
                fix="只在回复里说明结论；需要保存内容时由作者提出新的创作要求",
                details={"version": current.version},
            )
        grants = current.draft.module_content_grants
        for target in targets:
            matched = any(
                (
                    grant.action == "create"
                    and target.action == "create"
                    and grant.slot == target.operation_id
                )
                or (
                    grant.action == "update"
                    and target.action == "update"
                    and grant.binding_id == target.binding_id
                    and grant.module_id == target.module_id
                    and grant.base_revision == target.base_revision
                )
                for grant in grants
            )
            if matched:
                continue
            raise WorkspaceError(
                code="TASK_SCOPE_DENIED_CONFLICT",
                where=f"task_scope.module_changes:{target.operation_id}",
                why=f"本轮范围不包含这次内容操作：{target.action} {target.operation_id}",
                fix=(
                    "新建按登记过的 slot 提交；更新按登记过的绑定、模块与基础版本提交。"
                    "需要新增对象时说明理由并请作者明确要求"
                ),
                details={
                    "operation_id": target.operation_id,
                    "action": target.action,
                    "binding_id": target.binding_id,
                    "module_id": target.module_id,
                    "base_revision": target.base_revision,
                    "granted": [item.to_dict() for item in grants],
                    "version": current.version,
                },
            )

    def require_clue_design(self, grant: ClueDesignGrant) -> None:
        """关系写工具的统一入口：只允许本轮登记过的身份、关联或目标。"""
        current = self._require_scope()
        if current.scope.mode == "read_only":
            raise WorkspaceError(
                code="TASK_SCOPE_DENIED_CONFLICT",
                where="task_scope.clue_design",
                why="这一轮登记为只读任务",
                fix="只在回复里说明构思方案；作者明确要求保存时再登记关系操作",
                details={"version": current.version},
            )
        matched = any(
            self._clue_design_matches(grant, item) for item in current.draft.clue_design_grants
        )
        if matched:
            return
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where="task_scope.clue_design",
            why=f"本轮范围不包含这次关系操作：{grant.describe()}",
            fix=(
                "作者明确要求创建、关联或保存构思后，在 set_task_scope 的 "
                "clue_design_grants 一次登记完整；讨论本身不自动保存"
            ),
            details={
                "operation": grant.to_dict(),
                "granted": [item.to_dict() for item in current.draft.clue_design_grants],
                "version": current.version,
            },
        )

    @staticmethod
    def _clue_design_matches(actual: ClueDesignGrant, granted: ClueDesignGrant) -> bool:
        if actual.action != granted.action:
            return False
        if actual.action == "link":
            return actual.clue_id == granted.clue_id
        if granted.inference_id and granted.inference_id != actual.inference_id:
            return False
        return set(granted.clue_ids) == set(actual.clue_ids)

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
        if (
            draft.grants
            or draft.module_grants
            or draft.module_content_grants
            or draft.clue_design_grants
            or draft.library_publish
        ):
            raise _evidence_error("只读任务不能登记写操作、装配变更、内容保存、关系操作或库发布")
    elif (
        not draft.grants
        and not draft.module_grants
        and not draft.module_content_grants
        and not draft.clue_design_grants
        and not draft.library_publish
    ):
        raise _evidence_error(
            "修改任务至少要登记一个确切的路径、装配操作、内容保存、关系操作或库发布"
        )
    for grant in draft.grants:
        _validate_mutation(grant, where="task_scope.set")
    for module_grant in draft.module_grants:
        _validate_module_grant(module_grant)
    seen_slots: set[str] = set()
    for content_grant in draft.module_content_grants:
        _validate_module_content_grant(content_grant)
        if content_grant.action == "create" and content_grant.slot in seen_slots:
            raise _evidence_error(f"新建模块的槽位重复：{content_grant.slot}")
        if content_grant.action == "create":
            seen_slots.add(content_grant.slot)
    for design_grant in draft.clue_design_grants:
        _validate_clue_design_grant(design_grant)
    for path in draft.denied_paths:
        _validate_path(path, where="task_scope.set")
    for path in draft.required_references:
        _validate_path(path, where="task_scope.set")
    for reference in draft.required_module_references:
        if not all(
            (reference.module_id.strip(), reference.revision_id.strip(), reference.entry_id.strip())
        ):
            raise _evidence_error("必读模块条目要写清模块 ID、精确版本与条目 ID")
    if not set(draft.extend_actions) <= {"create", "update", "delete", "move"}:
        raise _evidence_error("可追加的操作类别不合法")
    if draft.extendable and not draft.extend_actions:
        raise _evidence_error("可发现范围要写明允许追加哪类操作")
    _validate_authorization(draft, author_texts)
    _validate_module_authorization(draft, author_texts)
    _validate_clue_design_authorization(draft, author_texts)
    if draft.library_search and not _library_search_authorized(draft, author_texts):
        raise _evidence_error("作者没有明确要求从全局模块库查找，不能登记库搜索权限")


def _validate_clue_design_grant(grant: ClueDesignGrant) -> None:
    """关系操作必须给到精确身份或目标，不接受「保存所有构思」这种模糊授权。"""
    if grant.action == "link":
        if not grant.clue_id.strip():
            raise _evidence_error("关联线索计划要写清线索 ID")
        return
    if not 1 <= len(grant.clue_ids) <= 10:
        raise _evidence_error("推理目标要关联 1—10 条线索")
    if len(set(grant.clue_ids)) != len(grant.clue_ids):
        raise _evidence_error("推理目标的线索不能重复")


def _validate_clue_design_authorization(
    draft: ScopeDraft, author_texts: Sequence[tuple[int, str]]
) -> None:
    text = _quoted_author_text(draft, author_texts)
    for grant in draft.clue_design_grants:
        phrases = _CLUE_DESIGN_PHRASES[grant.action]
        if not any(phrase in text for phrase in phrases):
            raise _evidence_error(
                f"作者没有明确要求「{grant.describe()}」；普通写作授权不能代替计划或目标操作"
            )


def _validate_module_grant(grant: ModuleGrant) -> None:
    """装配操作必须精确到版本或绑定；不接受「随便装一个」这种模糊授权。"""
    if grant.action == "attach":
        if not grant.module_id.strip() or not grant.revision_id.strip():
            raise _evidence_error("装入模块要写清模块 ID 与精确版本")
    elif not grant.binding_id.strip():
        raise _evidence_error("卸下模块要写清本作品的绑定 ID")


def _validate_module_content_grant(grant: ModuleContentGrant) -> None:
    """新建用稳定槽位；更新必须精确到绑定、模块与基础版本，不接受只给名称。"""
    if grant.action == "create":
        if not grant.slot.strip():
            raise _evidence_error("新建模块要给出本任务稳定的槽位名称")
        if len(grant.slot) > 64 or not all(char.isalnum() or char in "-_" for char in grant.slot):
            raise _evidence_error("新建模块的槽位只能用字母、数字、连字符与下划线，最长 64 字符")
        return
    if (
        not grant.binding_id.strip()
        or not grant.module_id.strip()
        or not grant.base_revision.strip()
    ):
        raise _evidence_error("修改已有模块要写清绑定 ID、模块 ID 与基础版本")


def _validate_module_authorization(
    draft: ScopeDraft, author_texts: Sequence[tuple[int, str]]
) -> None:
    """装配与发布要求作者原话里确实出现对应动词，而不是只给一句通用授权。"""
    text = _quoted_author_text(draft, author_texts)
    for grant in draft.module_grants:
        phrases = _MODULE_PHRASES[grant.action]
        if not any(phrase in text for phrase in phrases):
            raise _evidence_error(
                f"作者没有明确要求「{grant.describe()}」；库搜索或一般写作授权不能代替装卸指令"
            )
    if draft.library_publish and not any(phrase in text for phrase in _PUBLISH_PHRASES):
        raise _evidence_error("作者没有明确要求发布到模块库")


def _quoted_author_text(draft: ScopeDraft, author_texts: Sequence[tuple[int, str]]) -> str:
    quote = " ".join(draft.authorization.split())
    for _, text in author_texts:
        normalized = " ".join(text.split())
        if quote and quote in normalized:
            return normalized
    return ""


def _validate_reregistration(current: TaskScope, draft: ScopeDraft) -> None:
    """重复登记只能收窄：不新增允许操作、不撤禁止项、不打开可追加开关。

    改成只读时禁止项随之失去意义，不再要求保留；但只读范围没有写权限，重新登记回
    修改范围仍要过「不新增操作」这一关。
    """
    if draft.mode == "edit" and not set(current.draft.required_module_references) <= set(
        draft.required_module_references
    ):
        raise _evidence_error("重新登记不能撤掉本轮已明确的必读模块参考")
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
    if draft.library_search and not current.draft.library_search:
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where="task_scope.set",
            why="重新登记不能新开全局模块库搜索",
            fix="需要库搜索时由作者明确要求后重新登记；不要在这一轮中途扩大读取范围",
            details={"version": current.version},
        )
    added_modules = [
        grant for grant in draft.module_grants if grant not in current.draft.module_grants
    ]
    if added_modules:
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where="task_scope.set",
            why=f"重新登记只能收窄范围，这次新增了装配操作：{added_modules[0].describe()}",
            fix="装配变更一次登记完整；中途扩大装卸范围需要作者新的明确要求",
            details={
                "added": [grant.to_dict() for grant in added_modules],
                "version": current.version,
            },
        )
    added_content = [
        grant
        for grant in draft.module_content_grants
        if grant not in current.draft.module_content_grants
    ]
    if added_content:
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where="task_scope.set",
            why=f"重新登记只能收窄范围，这次新增了内容操作：{added_content[0].describe()}",
            fix="本轮要保存的内容一次登记完整；中途新增模块需要作者新的明确要求",
            details={
                "added": [item.to_dict() for item in added_content],
                "version": current.version,
            },
        )
    added_design = [
        grant
        for grant in draft.clue_design_grants
        if grant not in current.draft.clue_design_grants
    ]
    if added_design:
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where="task_scope.set",
            why=f"重新登记只能收窄范围，这次新增了关系操作：{added_design[0].describe()}",
            fix="本轮要保存的计划与目标一次登记完整；中途扩大范围需要作者新的明确要求",
            details={
                "added": [item.to_dict() for item in added_design],
                "version": current.version,
            },
        )
    if draft.library_publish and not current.draft.library_publish:
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where="task_scope.set",
            why="重新登记不能新开模块库发布",
            fix="需要发布时由作者明确要求后重新登记",
            details={"version": current.version},
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
            raise WorkspaceError(
                code="TASK_SCOPE_EVIDENCE_INVALID",
                where=where,
                why="移动操作必须给出目标路径",
                fix="给这次移动补上 target：移动后的新路径；其余操作只登记 path",
                details={"path": grant.path},
            )
        _validate_path(grant.target, where=where)
        if grant.target == grant.path:
            raise _evidence_error("移动的源与目标是同一个文件", where=where)
    elif grant.target is not None:
        # 提示要指明「删掉哪个字段」，否则模型会去掉值却留着字段，反复被拒。
        raise WorkspaceError(
            code="TASK_SCOPE_EVIDENCE_INVALID",
            where=where,
            why="只有移动操作可以带目标路径",
            fix=f"去掉这次登记里的 target，{_ACTION_LABEL[grant.action]}只登记 path",
            details={"path": grant.path, "target": grant.target},
        )


def _validate_path(path: str, *, where: str) -> None:
    if not is_managed_path(path):
        raise WorkspaceError(
            code="TASK_SCOPE_DENIED_CONFLICT",
            where=where,
            why=f"范围只能登记作品内的确切文件路径：{path}",
            fix=(
                "文件填写 正文/xxx.md、设定集/角色小传/姓名.md、设定集/线索.md 或 "
                "设定集/真相.md；必读模块请在 required_module_references 中填写"
                "模块 ID、版本与条目 ID"
            ),
            details={"path": path},
        )


#: 明确指向全局模块库的短语；只用来判断作者是否真的提出了库搜索，不推断任务意图。
_LIBRARY_SEARCH_PHRASES = ("模块库", "库里", "全局", "挑选模块", "找模块")


def _library_search_authorized(draft: ScopeDraft, author_texts: Sequence[tuple[int, str]]) -> bool:
    quote = " ".join(draft.authorization.split())
    for _, text in author_texts:
        normalized = " ".join(text.split())
        if quote and quote in normalized:
            return any(phrase in normalized for phrase in _LIBRARY_SEARCH_PHRASES)
    return False


def _validate_authorization(draft: ScopeDraft, author_texts: Sequence[tuple[int, str]]) -> None:
    quote = " ".join(draft.authorization.split())
    if not quote:
        raise _evidence_error("没有给出作者的授权原话")
    if not author_texts:
        raise _evidence_error("这一轮没有可核对的作者消息，不能登记写权限")
    known = {seq for seq, _ in author_texts}
    seqs = "、".join(str(seq) for seq in sorted(known))
    if draft.authorization_seq is not None and draft.authorization_seq not in known:
        # 引文本身已经能定位消息，这个字段填错只会白跑一趟：直接告诉模型可用的序号，
        # 并说明可以不填，否则它会继续猜。
        raise WorkspaceError(
            code="TASK_SCOPE_EVIDENCE_INVALID",
            where="task_scope.set",
            why="授权引文定位到的不是本轮可见的作者消息",
            fix=f"省略 authorization_seq（引文已能定位）；确需填写时用作者消息的 seq {seqs}",
            details={"seq": draft.authorization_seq, "author_seq": sorted(known)},
        )
    if draft.carried_from_seq is not None:
        if draft.carried_from_seq not in known:
            raise WorkspaceError(
                code="TASK_SCOPE_EVIDENCE_INVALID",
                where="task_scope.set",
                why="承接的作者请求不在本轮可见的作者消息里",
                fix=(
                    f"carried_from_seq 只填作者消息的 seq {seqs}；"
                    "不确定就省略，找不到承接对象先向作者确认"
                ),
                details={"seq": draft.carried_from_seq, "author_seq": sorted(known)},
            )
        if draft.carried_from_seq == draft.authorization_seq:
            raise WorkspaceError(
                code="TASK_SCOPE_EVIDENCE_INVALID",
                where="task_scope.set",
                why="承接对象与授权语句是同一条消息，说明「继续」没有真正承接旧要求",
                fix=f"承接对象要选更早的那条作者请求；本轮作者消息的 seq 是 {seqs}",
                details={"seq": draft.carried_from_seq, "author_seq": sorted(known)},
            )
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
