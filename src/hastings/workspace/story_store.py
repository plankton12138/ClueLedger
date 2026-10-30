"""构思结构的状态文件：严格读取、两阶段提交、恢复与撤销（1.0.1 P1）。

布局（作品状态目录下）：

``story/story.json``
    当前状态。所有方案、约定、正文关联、变更记录与请求收据都在这里。
``story/revisions/<revision>.json``
    每次提交后的不可变快照，用于追查与恢复；内容与当次 head 完全一致。
``story/commits.jsonl``
    提交日志：一行一次变更，只追加。
``story/pending.json`` / ``story/pending.decided``
    两阶段提交的中间物：先落待提交内容，再落提交决定，最后改 head。

为什么不是一次 ``os.replace``：结构、快照与日志是三个文件，物理上无法一起替换。
这里保证的是「应用逻辑可见性」——没有提交决定就回到原状态，有决定就补完剩余步骤，
不会让界面或模型看到半提交结构。外部编辑器能否看到中间文件不在保证范围内。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from pydantic import JsonValue, ValidationError

from hastings.workspace.atomic_io import atomic_write_bytes, atomic_write_json, locked
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, digest_bytes, managed_kind, normalize_newlines
from hastings.workspace.story_adoption import AdoptionPlan, adoption_plan
from hastings.workspace.story_changes import (
    StoryInvalid,
    apply_change_set,
    diff_variant,
    touched_objects,
    variant_digests,
)
from hastings.workspace.story_models import (
    ProsePatch,
    StoryChangeRecord,
    StoryChangeSet,
    StoryDocument,
    StoryOperation,
    StoryReceipt,
    StoryVariant,
)

if TYPE_CHECKING:
    from hastings.library.modules import ModuleLibrary

__all__ = [
    "EMPTY_REVISION",
    "MAIN_VARIANT_ID",
    "StorySnapshot",
    "StoryStore",
    "new_id",
]

#: 还没写过任何结构时的修订号；只用于「新建作品第一次提交」，不写进文件。
EMPTY_REVISION = "empty"
MAIN_VARIANT_ID = "main"

_MAX_LOG_TAIL = 2_000_000


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class StorySnapshot:
    """一次读取的结果；``document`` 为空表示这本作品还没有构思结构。"""

    revision: str
    document: StoryDocument | None = None

    @property
    def initialized(self) -> bool:
        return self.document is not None

    def variant(self, variant_id: str) -> StoryVariant | None:
        if self.document is None:
            return None
        return self.document.variants.get(variant_id)

    def primary(self) -> StoryVariant | None:
        if self.document is None:
            return None
        return self.document.variants.get(self.document.primary_variant_id)


class StoryStore:
    def __init__(
        self,
        root: Path,
        workspace: Workspace | None = None,
        *,
        library: ModuleLibrary | None = None,
        conversation_seq: Callable[[str], str | None] | None = None,
    ) -> None:
        """``root`` 是作品状态目录，与 ``workspace_state_root`` 一致。

        ``library`` 与 ``conversation_seq`` 只用于核对读取依赖：模块是否仍存在、
        会话当前事件序号。没有提供时，相应的依赖无法核对，按冲突拒绝。
        """
        self.workspace = workspace
        self.library = library
        self.conversation_seq = conversation_seq
        self.root = Path(root)
        self.dir = self.root / "story"
        self.file = self.dir / "story.json"
        self.lock_file = self.dir / ".story.lock"
        self.pending = self.dir / "pending.json"
        self.decision = self.dir / "pending.decided"
        self.log = self.dir / "commits.jsonl"
        self.revisions = self.dir / "revisions"

    # -- 读取 ---------------------------------------------------------------

    def read(self) -> StorySnapshot:
        """无副作用读取（除确定性恢复）；损坏的新格式绝不回退到猜测内容。"""
        if self.pending.exists() or self.decision.exists():
            with locked(self.lock_file):
                self._recover_locked()
        document = self._read_head()
        if document is None:
            return StorySnapshot(EMPTY_REVISION)
        return StorySnapshot(document.revision, document)

    def require_variant(self, variant_id: str) -> StoryVariant:
        snapshot = self.read()
        variant = snapshot.variant(variant_id)
        if variant is None:
            raise WorkspaceError(
                code="STORY_VARIANT_NOT_FOUND",
                where="story.variant",
                why=f"找不到方案：{variant_id}",
                fix="刷新构思列表后重新选择方案",
                details={"variant_id": variant_id},
            )
        return variant

    def require_unused_request_key(self, request_key: str) -> None:
        """尚未支持的动作也不能复用已经属于其他请求的键。"""
        with locked(self.lock_file):
            self._recover_locked()
            document = self._read_head()
            if document is not None and request_key in document.requests:
                raise WorkspaceError(
                    code="STORY_REQUEST_CONFLICT",
                    where="story.request",
                    why="同一请求键的内容发生了变化",
                    fix="使用新的请求键提交新操作",
                    details={"request_key": request_key},
                )

    # -- 写入 ---------------------------------------------------------------

    def commit_change(
        self,
        *,
        variant_id: str,
        change: StoryChangeSet,
        request_key: str,
        kind: str = "change",
        require_eligible: Callable[[str, int], None] | None = None,
        decision_guard: Callable[[], AbstractContextManager[None]] | None = None,
        prose_shift: bool = False,
    ) -> StoryReceipt:
        """应用一次结构变更；同键同负载返回同一收据，同键异负载报冲突。

        ``require_eligible`` 由服务层给：带上操作与代次时，提交决定必须由仍在运行
        的那一代做出。放在查重之后——已经提交过的同一请求返回收据，不因暂停而消失。
        ``prose_shift`` 供撤销使用：正文其他段落有后续编辑时，按唯一原文重新定位，
        不要求整份文件摘要不变。
        """
        fingerprint = _fingerprint(
            {
                "kind": kind,
                "variant_id": variant_id,
                "change": change.model_dump(mode="json", exclude={"id"}),
            }
        )
        with locked(self.lock_file):
            self._recover_locked()
            head_digest = self._head_digest()
            document = self._read_head()
            prior = self._receipt(document, request_key, fingerprint)
            if prior is not None:
                return prior
            if (change.prose_patches or change.read_dependencies) and self.workspace is None:
                raise WorkspaceError(
                    code="STORY_CHANGE_UNAVAILABLE",
                    where="story.commit",
                    why="正文补丁与读取依赖的联合提交尚未接通，本次没有写入",
                    fix="先单独修改正文或结构；联合修改接通后再提交",
                )
            if require_eligible is not None:
                require_eligible(change.operation_id, change.generation)
            created = False
            if document is None:
                if variant_id != MAIN_VARIANT_ID:
                    raise _variant_missing(variant_id)
                document = _new_document()
                created = True
            elif change.id in document.changes:
                raise WorkspaceError(
                    code="STORY_CHANGE_CONFLICT",
                    where="story.commit",
                    why="变更标识已经属于另一笔变更",
                    fix="为新的变更生成新的标识后重试",
                    details={"change_id": change.id},
                )
            variant = document.variants.get(variant_id)
            if variant is None:
                raise _variant_missing(variant_id)
            if created:
                if change.base_revision != EMPTY_REVISION:
                    raise _revision_conflict(change.base_revision, EMPTY_REVISION)
            elif variant.revision != change.base_revision:
                raise _revision_conflict(change.base_revision, variant.revision)
            return self._commit_locked(
                document,
                variant_id=variant_id,
                change=change,
                request_key=request_key,
                kind=kind,
                fingerprint=fingerprint,
                expected_head_digest=head_digest,
                require_eligible=require_eligible,
                decision_guard=decision_guard,
                prose_shift=prose_shift,
            )

    def create_variant(
        self,
        *,
        name: str,
        source_variant_id: str,
        source_revision: str,
        request_key: str,
    ) -> StoryReceipt:
        """从确定基线复制一个备选；只复制结构，不带正文关联与核对记录。"""
        fingerprint = _fingerprint(
            {
                "kind": "create_variant",
                "name": name,
                "source_variant_id": source_variant_id,
                "source_revision": source_revision,
            }
        )
        with locked(self.lock_file):
            self._recover_locked()
            head_digest = self._head_digest()
            document = self._read_head()
            prior = self._receipt(document, request_key, fingerprint)
            if prior is not None:
                return prior
            if document is None:
                raise _variant_missing(source_variant_id)
            source = document.variants.get(source_variant_id)
            if source is None:
                raise _variant_missing(source_variant_id)
            if source.revision != source_revision:
                raise _revision_conflict(source_revision, source.revision)
            variant = StoryVariant(
                id=new_id("variant"),
                name=name,
                base_variant_id=source.id,
                base_revision=source.revision,
                revision=new_id("rev"),
                nodes=dict(source.nodes),
                clues=dict(source.clues),
                threads=dict(source.threads),
                constraints=dict(source.constraints),
                edges=dict(source.edges),
                event_times=list(source.event_times),
            )
            # 备选与来源不共享可变对象：上面只复制了字典，对象还是同一批。
            variant = variant.model_copy(deep=True)
            document = document.model_copy(deep=True)
            document.variants[variant.id] = variant
            document.revision = new_id("rev")
            receipt = StoryReceipt(
                fingerprint=fingerprint,
                kind="create_variant",
                variant_id=variant.id,
                base_revision=source.revision,
                revision=variant.revision,
                request_key=request_key,
                change_id="",
                result={
                    "variant_id": variant.id,
                    "name": variant.name,
                    "base_variant_id": source.id,
                },
            )
            document.requests[request_key] = receipt
            try:
                self._copy_prose_baseline(source.id, variant.id)
            except OSError as exc:
                raise WorkspaceError(
                    code="STORY_WRITE_FAILED",
                    where="story.variant",
                    why="备选的正文基线无法写入",
                    fix="检查状态目录权限与可用空间后重新创建备选",
                    details={"variant_id": variant.id},
                ) from exc
            self._persist_locked(document, None, expected_head_digest=head_digest)
            return receipt

    def adopt(
        self,
        *,
        source_variant_id: str,
        source_revision: str,
        target_variant_id: str,
        target_revision: str,
        node_ids: tuple[str, ...],
        replace: bool,
        request_key: str,
        mode: str = "exact",
        prepare: Callable[[AdoptionPlan, StoryVariant, StoryVariant], list[StoryOperation]]
        | None = None,
        require_eligible: Callable[[str, int], None] | None = None,
        decision_guard: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> StoryReceipt:
        """在同一锁内查重、检查两端修订、计算并提交采用计划。

        ``prepare`` 由模型入口提供：拿到锁内算出的计划后，按普通结构变更的规则检查
        对象、真相与约定权限，并返回真正要提交的操作。界面发起的采用不需要它。
        """
        fingerprint = _fingerprint({
            "kind": "adoption",
            "source_variant_id": source_variant_id,
            "source_revision": source_revision,
            "target_variant_id": target_variant_id,
            "target_revision": target_revision,
            "node_ids": list(node_ids),
            "replace": replace,
            "mode": mode,
        })
        with locked(self.lock_file):
            self._recover_locked()
            head_digest = self._head_digest()
            document = self._read_head()
            prior = self._receipt(document, request_key, fingerprint)
            if prior is not None:
                return prior
            if document is None:
                raise _variant_missing(source_variant_id)
            source = document.variants.get(source_variant_id)
            target = document.variants.get(target_variant_id)
            if source is None:
                raise _variant_missing(source_variant_id)
            if target is None:
                raise _variant_missing(target_variant_id)
            if source.revision != source_revision:
                raise _revision_conflict(source_revision, source.revision)
            if target.revision != target_revision:
                raise _revision_conflict(target_revision, target.revision)
            plan = adoption_plan(source, target, node_ids=node_ids, replace=replace)
            summary = cast(dict[str, JsonValue], plan.summary())
            if plan.conflicts:
                raise WorkspaceError(
                    code="STORY_ADOPTION_CONFLICT",
                    where="story.adoption",
                    why=f"目标方案里已有 {len(plan.conflicts)} 个同 ID 但内容不同的对象",
                    fix="确认要覆盖时勾选覆盖重试，或先改名再采用",
                    details={**summary, "note": "只复制结构；正文关联需要单独绑定。"},
                )
            document = document.model_copy(deep=True)
            operations = (
                prepare(plan, source, target) if prepare is not None else list(plan.operations)
            )
            if operations:
                change = StoryChangeSet(
                    id=new_id("adopt"),
                    base_revision=target_revision,
                    operations=operations,
                    requested_scope=f"采用 {source.name}"[:500],
                )
                return self._commit_locked(
                    document,
                    variant_id=target_variant_id,
                    change=change,
                    request_key=request_key,
                    kind="adoption",
                    fingerprint=fingerprint,
                    result_extra={"adoption_plan": summary},
                    expected_head_digest=head_digest,
                    require_eligible=require_eligible,
                    decision_guard=decision_guard,
                )
            # 即使无需改动也登记请求，后续不同负载不能复用这个键。
            document.revision = new_id("rev")
            receipt = StoryReceipt(
                fingerprint=fingerprint,
                kind="adoption",
                variant_id=target_variant_id,
                base_revision=target_revision,
                revision=target_revision,
                request_key=request_key,
                result={"adoption_plan": summary},
            )
            document.requests[request_key] = receipt
            self._persist_locked(document, None, expected_head_digest=head_digest)
            return receipt

    def undo(self, *, change_id: str, request_key: str) -> StoryReceipt:
        """撤销一次提交：只反向恢复当时改动过的对象，后续无关编辑保留。"""
        fingerprint = _fingerprint({"kind": "undo", "change_id": change_id})
        with locked(self.lock_file):
            self._recover_locked()
            head_digest = self._head_digest()
            document = self._read_head()
            prior = self._receipt(document, request_key, fingerprint)
            if prior is not None:
                return prior
            if document is None:
                raise _change_missing(change_id)
            record = document.changes.get(change_id)
            if record is None:
                raise _change_missing(change_id)
            if record.undone_by:
                raise WorkspaceError(
                    code="STORY_CHANGE_CONFLICT",
                    where="story.undo",
                    why="这次变更已经撤销过",
                    fix="刷新改动记录后重新选择",
                    details={"change_id": change_id, "undone_by": record.undone_by},
                )
            variant = document.variants.get(record.variant_id)
            if variant is None:
                raise _variant_missing(record.variant_id)
            current = variant_digests(variant)
            stale = [
                key
                for key, digest in record.touched.items()
                if current.get(key, "") != digest
            ]
            if stale:
                raise WorkspaceError(
                    code="STORY_CHANGE_CONFLICT",
                    where="story.undo",
                    why="被改动的内容在提交之后又发生了变化",
                    fix="先处理这些目标的后续修改，再撤销这一次变更",
                    details={"change_id": change_id, "targets": sorted(stale)},
                )
            if not record.inverse_operations and not record.inverse_prose_patches:
                raise WorkspaceError(
                    code="STORY_CHANGE_CONFLICT",
                    where="story.undo",
                    why="这次变更没有可撤销的结构操作",
                    fix="改用正文或对话方式调整",
                    details={"change_id": change_id},
                )
            change = StoryChangeSet(
                id=new_id("change"),
                base_revision=variant.revision,
                operations=record.inverse_operations,
                prose_patches=record.inverse_prose_patches,
            )
            document = document.model_copy(deep=True)
            receipt = self._commit_locked(
                document,
                variant_id=variant.id,
                change=change,
                request_key=request_key,
                kind="undo",
                fingerprint=fingerprint,
                undone_by=change_id,
                expected_head_digest=head_digest,
                prose_shift=True,
            )
            return receipt

    # -- 内部 ---------------------------------------------------------------

    def _commit_locked(
        self,
        document: StoryDocument,
        *,
        variant_id: str,
        change: StoryChangeSet,
        request_key: str,
        kind: str,
        fingerprint: str,
        expected_head_digest: str | None,
        undone_by: str | None = None,
        result_extra: dict[str, JsonValue] | None = None,
        require_eligible: Callable[[str, int], None] | None = None,
        decision_guard: Callable[[], AbstractContextManager[None]] | None = None,
        prose_shift: bool = False,
    ) -> StoryReceipt:
        variant = document.variants[variant_id]
        self._validate_dependencies(document, change, variant_id)
        prose, inverse = self._prepare_prose(
            change.prose_patches, variant_id=variant_id, allow_shifted=prose_shift
        )
        try:
            updated = apply_change_set(variant, change)
        except StoryInvalid as exc:
            raise WorkspaceError(
                code="STORY_GRAPH_INVALID",
                where="story.change",
                why=str(exc),
                fix="按提示补齐目标或关系后重新提交",
                details={"variant_id": variant_id, "base_revision": change.base_revision},
            ) from exc
        updated.revision = new_id("rev")
        record = StoryChangeRecord(
            change_id=change.id,
            variant_id=variant_id,
            base_revision=change.base_revision,
            revision=updated.revision,
            request_key=request_key,
            created_at=_now(),
            operations=change.operations,
            inverse_operations=diff_variant(updated, variant),
            inverse_prose_patches=inverse,
            touched=touched_objects(variant, updated),
        )
        document.variants[variant_id] = updated
        document.revision = new_id("rev")
        document.changes[change.id] = record
        if undone_by is not None:
            document.changes[undone_by].undone_by = change.id
        receipt = StoryReceipt(
            fingerprint=fingerprint,
            kind=kind,
            variant_id=variant_id,
            base_revision=change.base_revision,
            revision=updated.revision,
            request_key=request_key,
            change_id=change.id,
            result={
                "change_id": change.id,
                "revision": updated.revision,
                "operations": len(change.operations),
                "prose_paths": [row["path"] for row in prose],
                **(result_extra or {}),
            },
        )
        document.requests[request_key] = receipt
        self._persist_locked(
            document, record, expected_head_digest=expected_head_digest,
            prose=prose,
            pre_decision=lambda: self._validate_commit(change, require_eligible, variant_id),
            decision_guard=decision_guard,
        )
        return receipt

    def _persist_locked(
        self, document: StoryDocument, record: StoryChangeRecord | None,
        *, expected_head_digest: str | None,
        prose: list[dict[str, str]] | None = None,
        pre_decision: Callable[[], None] | None = None,
        decision_guard: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> None:
        payload = document.model_dump(mode="json")
        prose = prose or []
        try:
            if self._head_digest() != expected_head_digest:
                raise self._external_change()
            decision_id = record.change_id if record else "variant"
            encoded = (
                json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            ).encode()
            atomic_write_json(
                self.pending,
                {"change_id": decision_id, "document": payload,
                 "previous_digest": expected_head_digest,
                 "target_digest": hashlib.sha256(encoded).hexdigest(), "prose": prose},
                prefix=".story-pending-",
            )
            with decision_guard() if decision_guard else nullcontext():
                if self._head_digest() != expected_head_digest:
                    raise self._external_change()
                self._check_prose(prose, allow_applied=False)
                if pre_decision is not None:
                    pre_decision()
                atomic_write_bytes(
                    self.decision,
                    json.dumps({"change_id": decision_id,
                                "target_digest": hashlib.sha256(encoded).hexdigest(),
                                "prose_digest": _fingerprint(prose)}).encode(),
                    prefix=".story-decision-",
                )
            if self._head_digest() != expected_head_digest:
                raise self._external_change()
            self._apply_prose(prose)
            atomic_write_json(self.file, payload, prefix=".story-")
            self._write_snapshot(document.revision, payload)
            if record is not None:
                self._append_log(record)
        except OSError as exc:
            # 保留 pending 与决定：下次读取会按「有决定就补完」的规则继续，
            # 现在删掉就等于丢掉已经做出的提交决定。
            raise WorkspaceError(
                code="STORY_WRITE_FAILED",
                where="story.commit",
                why="构思结构无法写入",
                fix="检查状态目录权限与可用空间；重启后会自动恢复到已决定的状态",
            ) from exc
        self._clear_pending()

    def _recover_locked(self) -> None:
        """只做确定性恢复：没有提交决定就回到原状态，有决定就补完剩余步骤。"""
        if not self.decision.exists():
            if self.pending.exists():
                self.pending.unlink(missing_ok=True)
            return
        try:
            decision = json.loads(self.decision.read_text(encoding="utf-8"))
            if not isinstance(decision, dict) or not isinstance(decision.get("change_id"), str):
                raise ValueError("提交决定格式不完整")
            decided = decision["change_id"]
            if not self.pending.exists():
                self.decision.unlink(missing_ok=True)
                return
            raw = json.loads(self.pending.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or str(raw.get("change_id", "")) != decided:
                raise ValueError("待提交记录与提交决定不一致")
            if "previous_digest" not in raw or not isinstance(raw.get("target_digest"), str):
                raise ValueError("待提交记录缺少文件摘要")
            prose = raw.get("prose", [])
            if (prose or "prose_digest" in decision) and (
                decision.get("prose_digest") != _fingerprint(prose)
            ):
                raise ValueError("正文提交内容与决定不一致")
            document = StoryDocument.model_validate(raw["document"])
            encoded = (
                json.dumps(document.model_dump(mode="json"), ensure_ascii=False,
                           indent=2, allow_nan=False) + "\n"
            ).encode()
            digest = hashlib.sha256(encoded).hexdigest()
            if raw["target_digest"] != digest or decision.get("target_digest") != digest:
                raise ValueError("提交决定与待提交内容摘要不一致")
        except (OSError, UnicodeError, ValueError, ValidationError) as exc:
            raise _state_error(self.pending, "无法恢复上次提交") from exc
        try:
            current_digest = self._head_digest()
            if current_digest not in (raw["previous_digest"], raw["target_digest"]):
                raise self._external_change()
            self._apply_prose(prose)
            if current_digest != raw["target_digest"]:
                atomic_write_json(self.file, document.model_dump(mode="json"), prefix=".story-")
            self._write_snapshot(document.revision, document.model_dump(mode="json"))
            record = document.changes.get(decided)
            if record is not None:
                self._append_log(record)
        except OSError as exc:
            raise _state_error(self.pending, "恢复提交时无法写入") from exc
        self._clear_pending()

    def _validate_commit(
        self,
        change: StoryChangeSet,
        require_eligible: Callable[[str, int], None] | None,
        variant_id: str = MAIN_VARIANT_ID,
    ) -> None:
        self._validate_dependencies(self._read_head(), change, variant_id)
        if require_eligible:
            require_eligible(change.operation_id, change.generation)

    def _validate_dependencies(
        self,
        document: StoryDocument | None,
        change: StoryChangeSet,
        variant_id: str = MAIN_VARIANT_ID,
    ) -> None:
        for dep in change.read_dependencies:
            actual: str | None = None
            supported = True
            if dep.kind == "prose" and self.workspace:
                actual = self._prose_state(dep.id, variant_id)[0]
            elif dep.kind == "variant" and document:
                variant = document.variants.get(dep.id)
                actual = variant.revision if variant else None
            elif dep.kind == "constraint" and document:
                variant = next((v for v in document.variants.values()
                                if v.revision == change.base_revision), None)
                constraint = variant.constraints.get(dep.id) if variant else None
                actual = constraint.revision if constraint else None
            elif dep.kind == "module":
                if self.library is None:
                    supported = False
                else:
                    exists = self.library.storage.revision_exists(dep.id, dep.revision)
                    actual = dep.revision if exists else None
            elif dep.kind == "conversation":
                if self.conversation_seq is None:
                    supported = False
                else:
                    actual = self.conversation_seq(dep.id)
            if supported and actual == dep.revision:
                continue
            raise WorkspaceError(
                code="STORY_DEPENDENCY_CONFLICT", where="story.commit",
                why=(f"读取依赖已变化或缺失：{dep.kind}/{dep.id}" if supported
                     else f"这类读取依赖无法核对：{dep.kind}/{dep.id}"),
                fix=("重新读取依赖并生成变更" if supported
                     else "提供对应的模块库或会话入口后再提交"),
            )

    def read_prose(self, path: str, variant_id: str = MAIN_VARIANT_ID) -> tuple[str, str]:
        """读取方案实际可见的正文：主方案是正式文件，备选是它的受管副本。"""
        digest, text, _ = self._prose_snapshot(path, variant_id)
        return digest, text

    def prose_state(
        self, path: str, variant_id: str = MAIN_VARIANT_ID,
    ) -> tuple[str, str, bool]:
        """摘要、文本与是否沿用主方案正文：备选还没写入自己的副本时为 ``True``。"""
        digest, text, copy = self._prose_snapshot(path, variant_id)
        return digest, text, copy is None and variant_id != MAIN_VARIANT_ID

    def _prose_copy(self, variant_id: str, path: str) -> Path:
        if managed_kind(path) != "chapter":
            raise WorkspaceError(
                code="STORY_PROSE_CONFLICT", where="story.commit",
                why=f"正文路径不受支持：{path}",
                fix="只提交 正文/ 下的 Markdown 文件",
            )
        if (
            not variant_id
            or variant_id in (".", "..")
            or "/" in variant_id
            or "\\" in variant_id
        ):
            raise WorkspaceError(
                code="STORY_VARIANT_INVALID", where="story.commit",
                why="方案标识不能用作目录名",
                fix="使用服务端返回的方案标识",
            )
        return self.dir / "variants" / variant_id / "prose" / Path(*PurePosixPath(path).parts)

    @staticmethod
    def _prose_marker(copy: Path) -> Path:
        return copy.with_name(copy.name + ".absent")

    def _variant_prose_paths(self, variant_id: str) -> list[str]:
        """来源方案此刻可能可见的章节：主方案文件加上备选自己的副本与删除标记。"""
        paths: set[str] = set()
        if self.workspace is not None:
            paths.update(
                entry.path for entry in self.workspace.catalog().chapters if entry.exists
            )
        if variant_id != MAIN_VARIANT_ID:
            root = self.dir / "variants" / variant_id / "prose"
            if root.exists():
                for item in root.rglob("*"):
                    if not item.is_file():
                        continue
                    relative = item.relative_to(root).as_posix()
                    if relative.endswith(".absent"):
                        relative = relative[: -len(".absent")]
                    paths.add(relative)
        return sorted(path for path in paths if managed_kind(path) == "chapter")

    def _copy_prose_baseline(self, source_variant_id: str, target_variant_id: str) -> None:
        """把来源实际可见的正文完整复制给新备选，包括「这一章已删除」的状态。

        首版按作者要求选择完整复制：来源清楚、恢复简单，代价是额外的存储开销。
        没有复制到的路径只可能是创建之后才出现的新章节，不在这次基线范围内。
        """
        if self.workspace is None:
            return
        for path in self._variant_prose_paths(source_variant_id):
            digest, text, _ = self._prose_snapshot(path, source_variant_id)
            copy = self._prose_copy(target_variant_id, path)
            marker = self._prose_marker(copy)
            if digest == "absent":
                atomic_write_bytes(marker, b"")
                with suppress(FileNotFoundError):
                    copy.unlink()
                continue
            atomic_write_bytes(copy, text.encode("utf-8"))
            with suppress(FileNotFoundError):
                marker.unlink()

    def _prose_state(
        self, path: str, variant_id: str = MAIN_VARIANT_ID,
    ) -> tuple[str, str]:
        digest, text, _ = self._prose_snapshot(path, variant_id)
        return digest, text

    def _prose_snapshot(
        self, path: str, variant_id: str,
    ) -> tuple[str, str, Path | None]:
        """返回正文摘要、文本与实际承载文件；备选副本不存在时沿用主方案正文。"""
        if variant_id == MAIN_VARIANT_ID:
            return self._main_prose_state(path)
        copy = self._prose_copy(variant_id, path)
        if self._prose_marker(copy).exists():
            return "absent", "", copy
        if copy.exists():
            try:
                raw = copy.read_bytes()
            except OSError as exc:
                raise self._external_change() from exc
            return digest_bytes(raw), raw.decode("utf-8"), copy
        return self._main_prose_state(path)

    def _main_prose_state(self, path: str) -> tuple[str, str, None]:
        assert self.workspace is not None
        if managed_kind(path) != "chapter":
            raise self._external_change()
        try:
            view = self.workspace.read(path)
            return view.digest, view.text, None
        except WorkspaceError as exc:
            if exc.code != "FILE_NOT_FOUND":
                raise
            self.workspace.check_write_intent(path, expected_digest=None)
            return "absent", "", None

    def _relocate(
        self, group: list[ProsePatch], original: str, path: str, digest: str,
    ) -> tuple[list[ProsePatch], str]:
        """撤销时按唯一原文重新定位；其他段落的后续编辑不影响本次恢复。"""
        relocated: list[ProsePatch] = []
        for patch in group:
            if patch.delete:
                if patch.start != 0 or patch.end != len(original) or patch.expected != original:
                    raise self._prose_conflict(path, "被删除的正文已经变化，无法恢复")
                relocated.append(patch.model_copy(update={"base_digest": digest}))
                continue
            if not patch.expected or original.count(patch.expected) != 1:
                raise self._prose_conflict(path, "要撤销的原文已被改动或不再唯一")
            start = original.find(patch.expected)
            relocated.append(patch.model_copy(update={
                "base_digest": digest, "start": start, "end": start + len(patch.expected),
            }))
        return relocated, digest

    def _prose_conflict(self, path: str, why: str) -> WorkspaceError:
        return WorkspaceError(
            code="STORY_PROSE_CONFLICT", where="story.commit",
            why=f"{why}：{path}",
            fix="检查当前正文后再决定是否撤销",
        )

    def _prepare_prose(
        self,
        patches: list[ProsePatch],
        *,
        variant_id: str = MAIN_VARIANT_ID,
        allow_shifted: bool = False,
    ) -> tuple[list[dict[str, Any]], list[ProsePatch]]:
        rows: list[dict[str, Any]] = []
        inverse: list[ProsePatch] = []
        if not patches:
            return rows, inverse
        if self.workspace is None:
            raise self._external_change()
        for path in sorted({patch.path for patch in patches}):
            copy = self._prose_copy(variant_id, path) if variant_id != MAIN_VARIANT_ID else None
            digest, original, _ = self._prose_snapshot(path, variant_id)
            group = sorted((p for p in patches if p.path == path), key=lambda p: p.start)
            if allow_shifted and digest != group[0].base_digest:
                group, digest = self._relocate(group, original, path, digest)
            deleting = any(p.delete for p in group)
            for index, patch in enumerate(group):
                if (patch.base_digest != digest or patch.end > len(original)
                    or original[patch.start:patch.end] != patch.expected
                    or (index and group[index - 1].end >= patch.start)
                    or (deleting and (len(group) != 1 or patch.start != 0
                        or patch.end != len(original) or patch.replacement or digest == "absent"))):
                    raise self._prose_conflict(path, "正文版本、区间或原文不一致")
            text = original
            for patch in reversed(group):
                text = text[:patch.start] + patch.replacement + text[patch.end:]
            text = normalize_newlines(text)
            if not deleting:
                self.workspace._reject_invalid_metadata(path, text)
            target = "absent" if deleting else digest_bytes(text.encode("utf-8"))
            rows.append({
                "path": path,
                "target": str(copy) if copy is not None else "",
                "variant_id": variant_id,
                "before": digest,
                "after": target,
                "text": text,
            })
            inverse.extend(self._inverse_patches(path, group, original, digest, target, text))
        return rows, inverse

    def _inverse_patches(
        self,
        path: str,
        group: list[ProsePatch],
        original: str,
        digest: str,
        target: str,
        text: str,
    ) -> list[ProsePatch]:
        """反向补丁尽量只覆盖本次改动的段落，其他后续编辑得以保留。"""
        if target == "absent":
            return [ProsePatch(path=path, base_digest="absent", start=0, end=0,
                               expected="", replacement=original)]
        if digest == "absent":
            return [ProsePatch(path=path, base_digest=target, start=0, end=len(text),
                               expected=text, replacement="", delete=True)]
        rows: list[ProsePatch] = []
        offset = 0
        for patch in group:
            start = patch.start + offset
            end = start + len(patch.replacement)
            rows.append(ProsePatch(path=path, base_digest=target, start=start, end=end,
                                   expected=patch.replacement, replacement=patch.expected))
            offset += len(patch.replacement) - len(patch.expected)
        return rows

    def _check_prose(
        self, rows: list[dict[str, Any]], *, allow_applied: bool = True,
    ) -> None:
        if rows and self.workspace is None:
            raise self._external_change()
        for row in rows:
            assert self.workspace is not None
            variant_id = str(row.get("variant_id") or MAIN_VARIANT_ID)
            accepted = (row["before"], row["after"]) if allow_applied else (row["before"],)
            target = ("absent" if row["after"] == "absent"
                      else digest_bytes(row["text"].encode("utf-8")))
            if (target != row["after"] or (target == "absent" and row["text"])
                or self._prose_state(row["path"], variant_id)[0] not in accepted):
                raise self._external_change()
            if row["after"] != "absent":
                self.workspace._reject_invalid_metadata(row["path"], row["text"])

    def _apply_prose(self, rows: list[dict[str, Any]]) -> None:
        # 先检查全部文件，再替换；恢复时只接受原状态或已决定的目标状态。
        self._check_prose(rows)
        for row in rows:
            assert self.workspace is not None
            variant_id = str(row.get("variant_id") or MAIN_VARIANT_ID)
            digest, _ = self._prose_state(row["path"], variant_id)
            if digest == row["after"]:
                continue
            if variant_id == MAIN_VARIANT_ID:
                if row["after"] == "absent":
                    self.workspace.delete(row["path"], expected_digest=row["before"])
                else:
                    self.workspace.write(row["path"], row["text"],
                        expected_digest=None if row["before"] == "absent" else row["before"])
                continue
            copy = self._prose_copy(variant_id, row["path"])
            marker = self._prose_marker(copy)
            if row["after"] == "absent":
                atomic_write_bytes(marker, b"")
                with suppress(FileNotFoundError):
                    copy.unlink()
            else:
                atomic_write_bytes(copy, row["text"].encode("utf-8"))
                with suppress(FileNotFoundError):
                    marker.unlink()

    def _head_digest(self) -> str | None:
        try:
            return hashlib.sha256(self.file.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None

    def _external_change(self) -> WorkspaceError:
        return WorkspaceError(
            code="STORY_WRITE_CONFLICT",
            where="story.commit",
            why="构思文件在提交期间发生了外部变化",
            fix="保留当前文件和待提交记录，检查差异后再处理",
            details={"path": str(self.file), "pending": str(self.pending)},
        )

    def _clear_pending(self) -> None:
        for path in (self.pending, self.decision):
            # 清理失败不影响已提交状态；下次读取会按同样规则再清理一遍。
            with suppress(OSError):
                path.unlink(missing_ok=True)

    def _read_head(self) -> StoryDocument | None:
        try:
            text = self.file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            raise _state_error(self.file, "无法读取构思结构") from exc
        try:
            raw = json.loads(text)
            if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int:
                raise ValueError("格式头不完整")
            if raw["schema_version"] != 1:
                raise ValueError("格式版本不受支持")
            return StoryDocument.model_validate(raw)
        except (ValueError, ValidationError) as exc:
            raise _state_error(self.file, "构思结构损坏或格式不受支持") from exc

    def _write_snapshot(self, revision: str, payload: dict[str, JsonValue]) -> None:
        self.revisions.mkdir(parents=True, exist_ok=True)
        target = self.revisions / f"{revision}.json"
        if target.exists():
            return
        atomic_write_json(target, payload, prefix=".story-rev-")

    def _append_log(self, record: StoryChangeRecord) -> None:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        if self.log.exists() and self._log_has(record.change_id):
            return
        line = json.dumps(
            {
                "change_id": record.change_id,
                "variant_id": record.variant_id,
                "base_revision": record.base_revision,
                "revision": record.revision,
                "request_key": record.request_key,
                "at": record.created_at,
            },
            ensure_ascii=False,
            allow_nan=False,
        )
        with self.log.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")
            stream.flush()

    def _log_has(self, change_id: str) -> bool:
        try:
            if self.log.stat().st_size > _MAX_LOG_TAIL:
                return False
            text = self.log.read_text(encoding="utf-8")
        except OSError:
            return False
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                if json.loads(line).get("change_id") == change_id:
                    return True
            except ValueError:
                # 日志尾部有半行（上次崩溃）时按「没有记录」处理，补记一次即可。
                continue
        return False

    def _receipt(
        self, document: StoryDocument | None, request_key: str, fingerprint: str
    ) -> StoryReceipt | None:
        if not request_key.strip():
            raise WorkspaceError(
                code="STORY_REQUEST_CONFLICT",
                where="story.request",
                why="请求键为空",
                fix="为这次操作提供独立请求键",
            )
        if document is None:
            return None
        prior = document.requests.get(request_key)
        if prior is None:
            return None
        if prior.fingerprint != fingerprint:
            raise WorkspaceError(
                code="STORY_REQUEST_CONFLICT",
                where="story.request",
                why="同一请求键的内容发生了变化",
                fix="使用新的请求键提交新操作",
                details={"request_key": request_key},
            )
        return prior


def _new_document() -> StoryDocument:
    variant = StoryVariant(
        id=MAIN_VARIANT_ID,
        name="主方案",
        revision=new_id("rev"),
    )
    return StoryDocument(
        workspace_lineage=new_id("ws"),
        revision=new_id("rev"),
        primary_variant_id=MAIN_VARIANT_ID,
        variants={MAIN_VARIANT_ID: variant},
    )


def _state_error(path: Path, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_STATE_FAILED",
        where=f"story.read:{path.name}",
        why=why,
        fix="检查状态目录里的构思文件；需要恢复时从提交快照取回当前格式数据",
    )


def _variant_missing(variant_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_VARIANT_NOT_FOUND",
        where="story.variant",
        why=f"找不到方案：{variant_id}",
        fix="刷新构思列表后重新选择方案",
        details={"variant_id": variant_id},
    )


def _change_missing(change_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_CHANGE_NOT_FOUND",
        where="story.undo",
        why=f"找不到这次变更：{change_id}",
        fix="刷新改动记录后重新选择",
        details={"change_id": change_id},
    )


def _revision_conflict(expected: str, current: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_REVISION_CONFLICT",
        where="story.commit",
        why="结构版本已经变化",
        fix="刷新当前结构后重新发起这次修改",
        details={"expected_revision": expected, "current_revision": current},
    )


def iter_change_ids(store: StoryStore) -> Iterator[str]:
    """按提交顺序列出变更标识；查看改动与影响分析共用。"""
    snapshot = store.read()
    if snapshot.document is None:
        return
    yield from snapshot.document.changes
