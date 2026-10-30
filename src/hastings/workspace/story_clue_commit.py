"""构思与线索账本的联合提交准备：先解析身份，再生成可恢复的账本目标。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from hastings.workspace.atomic_io import locked
from hastings.workspace.clue_models import ClueIdentity, ClueLedger
from hastings.workspace.clue_store import ClueLedgerStore, new_id
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_changes import StoryInvalid, apply_change_set
from hastings.workspace.story_models import (
    AddNode,
    CreateClueIdentity,
    RemoveClueIdentity,
    RenameClueIdentity,
    StoryChangeSet,
    StoryOperation,
    StoryVariant,
    UpdateNode,
    UpsertCluePlan,
)
from hastings.workspace.story_store import (
    EMPTY_REVISION,
    MAIN_VARIANT_ID,
    JointTarget,
    StoryStore,
)

__all__ = ["prepare_clue_joint", "prepare_clue_undo"]


def _encoded(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode()


def _digest_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def _resolve_change(
    change: StoryChangeSet, mapping: dict[str, str]
) -> StoryChangeSet:
    operations: list[StoryOperation] = []
    for operation in change.operations:
        if isinstance(operation, UpsertCluePlan):
            operations.append(
                operation.model_copy(
                    update={"clue_id": mapping.get(operation.clue_id, operation.clue_id)}
                )
            )
        elif isinstance(operation, (AddNode, UpdateNode)):
            uses = [
                use.model_copy(update={"clue_id": mapping.get(use.clue_id, use.clue_id)})
                for use in operation.node.clue_uses
            ]
            node = operation.node.model_copy(update={"clue_uses": uses})
            operations.append(operation.model_copy(update={"node": node}))
        else:
            operations.append(operation)
    return change.model_copy(update={"operations": operations})


def _apply_identity_operations(
    ledger: ClueLedger,
    change: StoryChangeSet,
    mapping: dict[str, str],
    *,
    created_by: str,
) -> list[dict[str, Any]]:
    inverse: list[dict[str, Any]] = []
    for operation in change.operations:
        if isinstance(operation, CreateClueIdentity):
            clue_id = mapping[operation.temp_id]
            ledger.clues[clue_id] = ClueIdentity(
                clue_id=clue_id,
                title=operation.title.strip(),
                clue_revision=new_id("rev"),
                created_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
                created_by=created_by,
                first_chapter=None,
            )
            inverse.append({"op": "inverse_remove", "clue_id": clue_id})
        elif isinstance(operation, RenameClueIdentity):
            identity = ledger.clues.get(operation.clue_id)
            if identity is None:
                raise _reference_error(operation.clue_id)
            inverse.append(
                {
                    "op": "inverse_rename",
                    "clue_id": operation.clue_id,
                    "from_title": operation.title.strip(),
                    "to_title": identity.title,
                }
            )
            ledger.clues[operation.clue_id] = identity.model_copy(
                update={
                    "title": operation.title.strip(),
                    "clue_revision": new_id("rev"),
                }
            )
        elif isinstance(operation, RemoveClueIdentity):
            identity = ledger.clues.get(operation.clue_id)
            if identity is None:
                raise _reference_error(operation.clue_id)
            if any(
                row.clue_id == operation.clue_id
                for row in ledger.contributions.values()
            ):
                raise WorkspaceError(
                    code="STORY_CLUE_REFERENCE_INVALID",
                    where="story.clue",
                    why="这条线索仍有正文贡献，不能删除身份",
                    fix="先处理正文贡献，再删除身份",
                    details={"clue_id": operation.clue_id},
                )
            inverse.append(
                {
                    "op": "inverse_restore",
                    "clue_id": operation.clue_id,
                    "title": identity.title,
                    "clue_revision": identity.clue_revision,
                }
            )
            del ledger.clues[operation.clue_id]
    return inverse


def _reference_error(clue_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_CLUE_REFERENCE_INVALID",
        where="story.clue",
        why=f"线索身份不存在：{clue_id}",
        fix="先在同一变更中创建身份，或重新读取线索后选择已有身份",
        details={"clue_id": clue_id},
    )


def _validate_final_refs(variant: StoryVariant, ledger: ClueLedger | None) -> None:
    identities = set(ledger.clues) if ledger is not None else set()
    for node in variant.nodes.values():
        for use in node.clue_uses:
            if use.clue_id not in identities:
                raise _reference_error(use.clue_id)
    for plan in variant.clue_plans.values():
        if plan.clue_id not in identities:
            raise _reference_error(plan.clue_id)
    for binding in variant.bindings.values():
        if binding.clue_id is not None and binding.clue_id not in identities:
            raise _reference_error(binding.clue_id)


def prepare_clue_joint(
    state_root: Path,
    *,
    variant_id: str,
    change: StoryChangeSet,
    expected_ledger_revision: str = "",
    created_by: str = "story_change",
) -> Any:
    """返回 ``StoryStore.commit_change`` 使用的联合准备回调。"""

    def prepare(document: Any) -> tuple[StoryChangeSet, JointTarget | None, dict[str, Any]]:
        if document is not None:
            current = document.variants.get(variant_id)
            if current is not None and current.revision != change.base_revision:
                raise WorkspaceError(
                    code="STORY_REVISION_CONFLICT",
                    where="story.commit",
                    why="结构版本已经变化",
                    fix="刷新当前结构后重新发起这次修改",
                    details={
                        "expected_revision": change.base_revision,
                        "current_revision": current.revision,
                    },
                )
        ledger_store = ClueLedgerStore(state_root)
        with locked(ledger_store.lock_file):
            snapshot = ledger_store.read()
            if (
                expected_ledger_revision
                and snapshot.revision != expected_ledger_revision
            ):
                raise WorkspaceError(
                    code="STORY_CLUE_REVISION_CONFLICT",
                    where="story.commit",
                    why="线索账本在受理之后发生了变化",
                    fix="重新读取线索，核对后再提交",
                    details={
                        "expected_ledger_revision": expected_ledger_revision,
                        "current_ledger_revision": snapshot.revision,
                    },
                )
            ledger = (
                snapshot.ledger.model_copy(deep=True)
                if snapshot.ledger is not None
                else None
            )
            mapping: dict[str, str] = {}
            for operation in change.operations:
                if isinstance(operation, CreateClueIdentity):
                    if operation.temp_id in mapping:
                        raise WorkspaceError(
                            code="STORY_CHANGE_INVALID",
                            where="story.clue",
                            why=f"同一请求里的临时引用重复：{operation.temp_id}",
                            fix="为每条新线索使用不同的临时引用",
                        )
                    mapping[operation.temp_id] = new_id("clue")
            resolved = _resolve_change(change, mapping)
            has_identity_ops = any(
                isinstance(operation, CreateClueIdentity)
                for operation in change.operations
            ) or any(
                isinstance(operation, RemoveClueIdentity)
                or isinstance(operation, RenameClueIdentity)
                for operation in change.operations
            )
            if has_identity_ops and ledger is None:
                ledger = ClueLedger(
                    workspace_lineage=new_id("ws"), ledger_revision=new_id("rev")
                )
            inverse_ledger: list[dict[str, Any]] = []
            if ledger is not None:
                inverse_ledger = _apply_identity_operations(
                    ledger, change, mapping, created_by=created_by
                )

            variant = (
                document.variants.get(variant_id)
                if document is not None
                else StoryVariant(
                    id=variant_id or MAIN_VARIANT_ID,
                    name="主方案",
                    revision=EMPTY_REVISION,
                )
            )
            if variant is None:
                raise WorkspaceError(
                    code="STORY_VARIANT_NOT_FOUND",
                    where="story.commit",
                    why=f"找不到方案：{variant_id}",
                    fix="刷新构思后重新选择",
                )
            try:
                final_variant = apply_change_set(variant, resolved)
            except StoryInvalid as exc:
                raise WorkspaceError(
                    code="STORY_GRAPH_INVALID",
                    where="story.change",
                    why=str(exc),
                    fix="按提示补齐目标或关系后重新提交",
                ) from exc
            _validate_final_refs(final_variant, ledger)

            if not has_identity_ops or ledger is None:
                return resolved, None, {"ledger_revision": snapshot.revision}

            ledger.ledger_revision = new_id("rev")
            payload = ledger.model_dump(mode="json")
            target = JointTarget(
                path=ledger_store.file,
                lock_path=ledger_store.lock_file,
                before=_digest_file(ledger_store.file),
                after=hashlib.sha256(_encoded(payload)).hexdigest(),
                payload=payload,
            )
            return resolved, target, {
                "ledger_revision": ledger.ledger_revision,
                "id_map": mapping,
                "inverse_ledger": inverse_ledger,
            }

    return prepare


def _apply_inverse_ledger(
    ledger: ClueLedger, inverse: list[dict[str, Any]]
) -> bool:
    changed = False
    for row in inverse:
        op = str(row.get("op") or "")
        clue_id = str(row.get("clue_id") or "")
        if op == "inverse_remove":
            identity = ledger.clues.get(clue_id)
            if identity is None:
                raise _reference_error(clue_id)
            if any(item.clue_id == clue_id for item in ledger.contributions.values()):
                raise WorkspaceError(
                    code="STORY_CLUE_REFERENCE_INVALID",
                    where="story.undo",
                    why="这条线索后来新增了正文贡献，不能撤销这次创建",
                    fix="保留这次创建，改为单独调整线索",
                    details={"clue_id": clue_id},
                )
            del ledger.clues[clue_id]
            changed = True
        elif op == "inverse_rename":
            identity = ledger.clues.get(clue_id)
            if identity is None:
                raise _reference_error(clue_id)
            if identity.title != str(row.get("from_title") or ""):
                raise WorkspaceError(
                    code="STORY_CLUE_REVISION_CONFLICT",
                    where="story.undo",
                    why="线索标题在这次提交之后又被修改",
                    fix="保留当前标题，重新选择要撤销的改动",
                    details={"clue_id": clue_id},
                )
            ledger.clues[clue_id] = identity.model_copy(
                update={
                    "title": str(row.get("to_title") or ""),
                    "clue_revision": new_id("rev"),
                }
            )
            changed = True
        elif op == "inverse_restore":
            if clue_id in ledger.clues:
                raise WorkspaceError(
                    code="STORY_CLUE_REVISION_CONFLICT",
                    where="story.undo",
                    why="线索身份后来已经被重新创建",
                    fix="保留当前线索，重新选择要撤销的改动",
                    details={"clue_id": clue_id},
                )
            ledger.clues[clue_id] = ClueIdentity(
                clue_id=clue_id,
                title=str(row.get("title") or ""),
                clue_revision=str(row.get("clue_revision") or new_id("rev")),
                created_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
                created_by="undo",
                first_chapter=None,
            )
            changed = True
        else:
            raise WorkspaceError(
                code="STORY_CHANGE_INVALID",
                where="story.undo",
                why="撤销记录里的账本操作无法识别",
                fix="从改动记录中重新选择撤销目标",
            )
    return changed


def prepare_clue_undo(
    state_root: Path,
    *,
    variant_id: str,
    inverse_ledger: list[dict[str, Any]],
) -> Any:
    """撤销时把账本反向操作与结构反向操作一起提交。"""

    def prepare(
        document: Any, change: StoryChangeSet
    ) -> tuple[StoryChangeSet, JointTarget | None, dict[str, Any]]:
        ledger_store = ClueLedgerStore(state_root)
        with locked(ledger_store.lock_file):
            snapshot = ledger_store.read()
            ledger = (
                snapshot.ledger.model_copy(deep=True)
                if snapshot.ledger is not None
                else None
            )
            if inverse_ledger and ledger is None:
                raise WorkspaceError(
                    code="STORY_CLUE_REFERENCE_INVALID",
                    where="story.undo",
                    why="线索账本不存在，无法撤销这次身份操作",
                    fix="检查状态目录后重试",
                )
            changed = (
                _apply_inverse_ledger(ledger, inverse_ledger)
                if ledger is not None and inverse_ledger
                else False
            )
            variant = (
                document.variants.get(variant_id)
                if document is not None
                else StoryVariant(
                    id=variant_id or MAIN_VARIANT_ID,
                    name="主方案",
                    revision=EMPTY_REVISION,
                )
            )
            if variant is None:
                raise WorkspaceError(
                    code="STORY_VARIANT_NOT_FOUND",
                    where="story.undo",
                    why=f"找不到方案：{variant_id}",
                    fix="刷新构思后重新选择",
                )
            try:
                final_variant = apply_change_set(variant, change)
            except StoryInvalid as exc:
                raise WorkspaceError(
                    code="STORY_GRAPH_INVALID",
                    where="story.undo",
                    why=str(exc),
                    fix="按提示处理后再撤销",
                ) from exc
            _validate_final_refs(final_variant, ledger)
            if not changed or ledger is None:
                return change, None, {"ledger_revision": snapshot.revision}
            ledger.ledger_revision = new_id("rev")
            payload = ledger.model_dump(mode="json")
            return change, JointTarget(
                path=ledger_store.file,
                lock_path=ledger_store.lock_file,
                before=_digest_file(ledger_store.file),
                after=hashlib.sha256(_encoded(payload)).hexdigest(),
                payload=payload,
            ), {"ledger_revision": ledger.ledger_revision}

    return prepare
