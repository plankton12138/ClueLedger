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
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue, ValidationError

from hastings.workspace.atomic_io import atomic_write_bytes, atomic_write_json, locked
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_changes import (
    StoryInvalid,
    apply_change_set,
    diff_variant,
    touched_objects,
    variant_digests,
)
from hastings.workspace.story_models import (
    StoryChangeRecord,
    StoryChangeSet,
    StoryDocument,
    StoryReceipt,
    StoryVariant,
)

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
    def __init__(self, root: Path) -> None:
        """``root`` 是作品状态目录，与 ``workspace_state_root`` 一致。"""
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

    # -- 写入 ---------------------------------------------------------------

    def commit_change(
        self,
        *,
        variant_id: str,
        change: StoryChangeSet,
        request_key: str,
        kind: str = "change",
        require_eligible: Callable[[str, int], None] | None = None,
    ) -> StoryReceipt:
        """应用一次结构变更；同键同负载返回同一收据，同键异负载报冲突。

        ``require_eligible`` 由服务层给：带上操作与代次时，提交决定必须由仍在运行
        的那一代做出。放在查重之后——已经提交过的同一请求返回收据，不因暂停而消失。
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
            document = self._read_head()
            prior = self._receipt(document, request_key, fingerprint)
            if prior is not None:
                return prior
            if require_eligible is not None:
                require_eligible(change.operation_id, change.generation)
            created = False
            if document is None:
                if variant_id != MAIN_VARIANT_ID:
                    raise _variant_missing(variant_id)
                document = _new_document()
                created = True
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
            self._persist_locked(document, None)
            return receipt

    def undo(self, *, change_id: str, request_key: str) -> StoryReceipt:
        """撤销一次提交：只反向恢复当时改动过的对象，后续无关编辑保留。"""
        fingerprint = _fingerprint({"kind": "undo", "change_id": change_id})
        with locked(self.lock_file):
            self._recover_locked()
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
            if not record.inverse_operations:
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
        undone_by: str | None = None,
    ) -> StoryReceipt:
        variant = document.variants[variant_id]
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
            },
        )
        document.requests[request_key] = receipt
        self._persist_locked(document, record)
        return receipt

    def _persist_locked(
        self, document: StoryDocument, record: StoryChangeRecord | None
    ) -> None:
        payload = document.model_dump(mode="json")
        try:
            atomic_write_json(
                self.pending,
                {"change_id": record.change_id if record else "", "document": payload},
                prefix=".story-pending-",
            )
            atomic_write_bytes(
                self.decision,
                (record.change_id if record else "variant").encode(),
                prefix=".story-decision-",
            )
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
            decided = self.decision.read_text(encoding="utf-8").strip()
            if not self.pending.exists():
                self.decision.unlink(missing_ok=True)
                return
            raw = json.loads(self.pending.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or str(raw.get("change_id", "")) != decided:
                raise ValueError("待提交记录与提交决定不一致")
            document = StoryDocument.model_validate(raw["document"])
        except (OSError, UnicodeError, ValueError, ValidationError) as exc:
            raise _state_error(self.pending, "无法恢复上次提交") from exc
        try:
            head = self._read_head()
        except WorkspaceError:
            # 已决定的状态优先于损坏的 head：决定是提交时写下的，head 只是它的投影。
            head = None
        applied = (
            head is not None
            and (head.revision == document.revision or decided in head.changes)
        )
        try:
            if not applied:
                atomic_write_json(self.file, document.model_dump(mode="json"), prefix=".story-")
            self._write_snapshot(document.revision, document.model_dump(mode="json"))
            record = document.changes.get(decided)
            if record is not None:
                self._append_log(record)
        except OSError as exc:
            raise _state_error(self.pending, "恢复提交时无法写入") from exc
        self._clear_pending()

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
