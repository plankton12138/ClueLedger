"""作品内模块变更集：一次准备、成组保存、一次提交装配（0.8）。

模型在一次创作任务里可能同时新建几份资料、修改已有模块。逐个调用现有装卸工具会让
每一份都提前结束本轮，也无法保证作者看到的是同一组关系一致的当前资料。这里把内容与
装配分开：

- **准备**只校验结构、范围与基础装配，把内容持久化为任务进度，不改作品；
- **应用**先保存每个操作的内容版本，再在同一作品锁内一次性替换本次涉及的绑定；
- **撤销**按变更集恢复本次涉及的绑定，保留库副本与无关装配。

库文件与作品装配没有跨目录原子事务。内容保存成功但装配提交失败时，变更集保留逐项
结果，重试只完成缺失步骤，不重新生成或重复建模块。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from hastings.library.bindings import AssemblyService
from hastings.library.ids import new_id, source_path
from hastings.library.modules import ModuleLibrary, RevisionInfo
from hastings.library.records import EntryDraft, parse_entry, validate_entries
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.materials import MaterialLedger
from hastings.workspace.module_assembly import Assembly, Binding, ModuleRef

__all__ = [
    "CHANGE_SET_SCHEMA",
    "ChangeEdit",
    "ChangeOperation",
    "ChangeSetStore",
    "ModuleChangeService",
]

CHANGE_SET_SCHEMA = 1
_CHANGE_ROOT = ".clueledger/module-change-sets"
_MAX_OPERATIONS = 20
_DEFAULT_MODULE_KIND = "reference"
_DEFAULT_ENTRY_KIND = "reference"

ChangeAction = Literal["create", "update"]


@dataclass(frozen=True, slots=True)
class ChangeEdit:
    """对已有条目的一次修改：唯一文本替换，或整份正文重写。

    ``base_digest`` 是读取时拿到的条目指纹。没有它不能修改，避免按记忆覆盖别的版本。
    """

    entry_id: str
    base_digest: str = ""
    find: str = ""
    replace: str = ""
    body: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeOperation:
    """变更集里的一项操作；新建用 ``operation_id`` 作为任务内稳定槽位。"""

    operation_id: str
    action: ChangeAction
    name: str = ""
    body: str = ""
    binding_id: str = ""
    module_id: str = ""
    base_revision: str = ""
    edits: tuple[ChangeEdit, ...] = field(default_factory=tuple)


class ChangeSetStore:
    """变更集的任务进度文件；只保存结构、内容与逐项结果，不复制库原文。"""

    def __init__(self, workspace_root: Path) -> None:
        self.root = Path(workspace_root) / _CHANGE_ROOT

    def path(self, change_set_id: str) -> Path:
        return self.root / f"{_checked_id(change_set_id)}.json"

    def find_by_request_key(self, request_key: str) -> dict[str, Any] | None:
        """同一请求键只准备一份；重放返回原变更集，不重复写进度文件。"""
        if not request_key or not self.root.is_dir():
            return None
        for path in sorted(self.root.glob("chs_*.json")):
            try:
                payload = json.loads(path.read_bytes().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("request_key") == request_key:
                return payload
        return None

    def read(self, change_set_id: str, *, where: str = "changes.load") -> dict[str, Any]:
        path = self.path(change_set_id)
        if not path.is_file():
            raise WorkspaceError(
                code="MODULE_CHANGES_NOT_FOUND",
                where=where,
                why=f"找不到这次变更集：{change_set_id}",
                fix="重新读取作品的任务进度；需要时重新准备内容",
                details={"change_set_id": change_set_id},
            )
        try:
            payload = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError(
                code="MODULE_CHANGES_INVALID",
                where=where,
                why=f"变更集无法解析：{exc}",
                fix="保留原文件并从备份恢复；不要把它当成空变更集",
                details={"change_set_id": change_set_id},
            ) from exc
        if not isinstance(payload, dict):
            raise WorkspaceError(
                code="MODULE_CHANGES_INVALID",
                where=where,
                why="变更集不是一个对象",
                fix="保留原文件并从备份恢复",
                details={"change_set_id": change_set_id},
            )
        return payload

    def write(self, payload: Mapping[str, object]) -> None:
        change_set_id = str(payload.get("change_set_id") or "")
        path = self.path(change_set_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".changes.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        except OSError as exc:
            raise WorkspaceError(
                code="MODULE_CHANGES_WRITE_FAILED",
                where="changes.write",
                why=f"无法写入变更集：{exc.strerror or exc}",
                fix="检查作品目录权限与磁盘空间后重试",
                details={"change_set_id": change_set_id},
            ) from exc


class ModuleChangeService:
    """一个作品的变更集：准备、应用与撤销；模型工具与 HTTP 接口共用。"""

    def __init__(self, library: ModuleLibrary, workspace: Workspace) -> None:
        self.library = library
        self.workspace = workspace
        self.assembly = AssemblyService(library, workspace)
        self.store = ChangeSetStore(Path(workspace.root))

    # -- 准备 ---------------------------------------------------------------

    def prepare(
        self,
        *,
        turn_id: str,
        operations: Sequence[ChangeOperation],
        base_digest: str | None,
        request_key: str = "",
    ) -> dict[str, object]:
        """校验结构与目标，把内容写成任务进度；不改库、不改装配。"""
        checked = _checked_operations(operations)
        request_digest = _request_digest(checked, base_digest)
        if request_key:
            existing = self.store.find_by_request_key(request_key)
            if existing is not None:
                if existing.get("request_digest") != request_digest:
                    raise WorkspaceError(
                        code="REQUEST_KEY_CONFLICT",
                        where="changes.prepare",
                        why=f"请求键 {request_key} 之前用于另一份内容",
                        fix="为这次准备生成新的请求键；不要悄悄替换请求体",
                        details={"request_key": request_key},
                    )
                return _prepared_view(existing)
        assembly = self.assembly.store.read()
        current_digest = self.assembly.store.digest()
        if base_digest != current_digest:
            raise WorkspaceError(
                code="MODULE_CHANGES_STALE_CONFLICT",
                where="changes.prepare",
                why="作品装配在读取之后被改过",
                fix="刷新作品模块后重新准备；不要用旧装配快照覆盖新的绑定",
                details={"expected": base_digest, "current": current_digest},
            )
        for operation in checked:
            if operation.action != "update":
                continue
            binding = assembly.binding(operation.binding_id)
            if binding is None or binding.module.module_id != operation.module_id:
                raise WorkspaceError(
                    code="MODULE_CHANGE_TARGET_INVALID",
                    where=f"changes.prepare:{operation.operation_id}",
                    why="要修改的模块已不在当前作品装配里",
                    fix="重新读取作品模块后再登记；未保存内容仍可保留",
                    details={
                        "operation_id": operation.operation_id,
                        "binding_id": operation.binding_id,
                    },
                )
            if binding.module.revision_id != operation.base_revision:
                raise WorkspaceError(
                    code="MODULE_CHANGES_STALE_CONFLICT",
                    where=f"changes.prepare:{operation.operation_id}",
                    why="作品正在使用的模块版本已经变化",
                    fix="重新读取当前版本后再修改；保留已有生成内容",
                    details={
                        "operation_id": operation.operation_id,
                        "expected": operation.base_revision,
                        "current": binding.module.revision_id,
                    },
                )
        payload: dict[str, object] = {
            "schema_version": CHANGE_SET_SCHEMA,
            "change_set_id": f"chs_{secrets.token_hex(8)}",
            "workspace": str(Path(self.workspace.root).resolve()),
            "turn_id": turn_id,
            "request_key": request_key,
            "request_digest": request_digest,
            "status": "prepared",
            "base_assembly_digest": current_digest,
            "base_assembly": assembly.view(),
            "operations": [_operation_payload(item) for item in checked],
            "result": None,
        }
        self.store.write(payload)
        return _prepared_view(payload)

    # -- 应用 ---------------------------------------------------------------

    def apply(
        self,
        *,
        change_set_id: str,
        turn_id: str,
        ledger: MaterialLedger | None = None,
        expected_digest: str | None = None,
    ) -> dict[str, object]:
        """保存所有内容版本，再一次性提交本作品装配。"""
        payload = self._owned(change_set_id, turn_id)
        if payload.get("status") == "applied" and isinstance(payload.get("result"), dict):
            return dict(payload["result"])
        if payload.get("status") == "reverted":
            raise WorkspaceError(
                code="MODULE_CHANGES_STALE_CONFLICT",
                where="changes.apply",
                why="这次变更已经撤销，不能再次应用同一份结果",
                fix="需要时重新准备一次新的变更集",
                details={"change_set_id": change_set_id},
            )
        current_digest = self.assembly.store.digest()
        if expected_digest is not None and expected_digest != current_digest:
            return self._failed(
                payload,
                stage="precondition",
                error=_error_view(
                    WorkspaceError(
                        code="MODULE_CHANGES_STALE_CONFLICT",
                        where="changes.apply",
                        why="提交前作品装配已经被改过",
                        fix="刷新作品模块后重试；已保存内容会保留",
                        details={"expected": expected_digest, "current": current_digest},
                    )
                ),
            )
        if payload.get("base_assembly_digest") != current_digest:
            return self._failed(
                payload,
                stage="precondition",
                error=_error_view(
                    WorkspaceError(
                        code="MODULE_CHANGES_STALE_CONFLICT",
                        where="changes.apply",
                        why="准备之后作品装配被其他操作改过",
                        fix="刷新作品模块后重试；已保存内容会保留",
                        details={
                            "expected": payload.get("base_assembly_digest"),
                            "current": current_digest,
                        },
                    )
                ),
            )

        assembly = self.assembly.store.read()
        operations = _payload_operations(payload)
        saved_events: list[dict[str, object]] = []
        for operation in operations:
            try:
                saved = self._apply_operation(operation, change_set_id=change_set_id, ledger=ledger)
            except WorkspaceError as exc:
                payload["status"] = "failed"
                payload["result"] = {
                    "applied": False,
                    "change_set_id": change_set_id,
                    "stage": "content",
                    "error": _error_view(exc),
                    "operations": [_operation_view(item) for item in operations],
                    "saved": [_saved_view(item) for item in operations if item.get("saved")],
                }
                self.store.write(payload)
                return dict(payload["result"])
            if saved is not None:
                saved_events.append(saved)
            self.store.write(payload)

        try:
            view = self._commit_assembly(
                change_set_id,
                assembly,
                operations,
                expected_digest=str(payload.get("base_assembly_digest") or "") or None,
            )
        except WorkspaceError as exc:
            payload["status"] = "failed"
            payload["result"] = {
                "applied": False,
                "change_set_id": change_set_id,
                "stage": "assembly",
                "error": _error_view(exc),
                "operations": [_operation_view(item) for item in operations],
                "saved": [_saved_view(item) for item in operations if item.get("saved")],
                "saved_events": saved_events,
            }
            self.store.write(payload)
            return dict(payload["result"])

        discarded, discard_failed = self._discard_superseded(change_set_id, turn_id)
        result = {
            "applied": True,
            "change_set_id": change_set_id,
            "stage": "applied",
            "assembly": view,
            "operations": [_operation_view(item) for item in operations],
            "saved": [_saved_view(item) for item in operations if item.get("saved")],
            "saved_events": saved_events,
            "undo": {"available": True, "change_set_id": change_set_id},
            "discarded": discarded,
            "discard_failed": discard_failed,
        }
        payload["status"] = "applied"
        payload["result"] = result
        self.store.write(payload)
        return result

    # -- 撤销 ---------------------------------------------------------------

    def undo(self, *, change_set_id: str, expected_digest: str | None = None) -> dict[str, object]:
        """恢复本次涉及的绑定；存在后续编辑时冲突，不整份覆盖。"""
        payload = self._load(change_set_id)
        if payload.get("status") != "applied":
            raise WorkspaceError(
                code="MODULE_CHANGES_UNDO_CONFLICT",
                where="changes.undo",
                why="这次变更还没有应用到当前作品",
                fix="只在已经显示「已用于本作品」的结果旁撤销",
                details={"change_set_id": change_set_id, "status": payload.get("status")},
            )
        current = self.assembly.store.read()
        current_digest = self.assembly.store.digest()
        if expected_digest is not None and expected_digest != current_digest:
            raise WorkspaceError(
                code="MODULE_CHANGES_STALE_CONFLICT",
                where="changes.undo",
                why="撤销前作品装配已经被改过",
                fix="刷新作品模块后再撤销；不要覆盖别人的绑定",
                details={"expected": expected_digest, "current": current_digest},
            )
        base = _assembly_from(payload.get("base_assembly"))
        base_bindings = {binding.id: binding for binding in base.bindings}
        operations = _payload_operations(payload)
        restored = list(current.bindings)
        for operation in operations:
            saved = operation.get("saved")
            if not isinstance(saved, dict):
                raise WorkspaceError(
                    code="MODULE_CHANGES_UNDO_CONFLICT",
                    where=f"changes.undo:{operation.get('operation_id')}",
                    why="这次操作没有可归档的保存结果",
                    fix="保留当前作品；需要时手工处理这一项",
                    details={"operation_id": operation.get("operation_id")},
                )
            binding_id = str(saved.get("binding_id") or "")
            index = next(
                (position for position, item in enumerate(restored) if item.id == binding_id),
                -1,
            )
            if operation.get("action") == "create":
                if index < 0:
                    raise WorkspaceError(
                        code="MODULE_CHANGES_UNDO_CONFLICT",
                        where=f"changes.undo:{operation.get('operation_id')}",
                        why="新建模块的绑定已经不在当前作品里",
                        fix="保留当前成果；需要时重新装入或手工整理",
                        details={"operation_id": operation.get("operation_id")},
                    )
                created_binding = restored[index]
                if created_binding.module.module_id != saved.get(
                    "module_id"
                ) or created_binding.module.revision_id != saved.get("revision_id"):
                    raise WorkspaceError(
                        code="MODULE_CHANGES_UNDO_CONFLICT",
                        where=f"changes.undo:{operation.get('operation_id')}",
                        why="这个新建模块在本次变更之后又被编辑或升级过",
                        fix="保留后续成果；先确认是否仍需移除本次新建",
                        details={
                            "operation_id": operation.get("operation_id"),
                            "expected": {
                                "module_id": saved.get("module_id"),
                                "revision_id": saved.get("revision_id"),
                            },
                            "current": created_binding.module.view(),
                        },
                    )
                restored.pop(index)
                continue
            if index < 0:
                raise WorkspaceError(
                    code="MODULE_CHANGES_UNDO_CONFLICT",
                    where=f"changes.undo:{operation.get('operation_id')}",
                    why="要恢复的模块绑定已经被移除",
                    fix="保留当前成果；需要时重新装入原版本",
                    details={"operation_id": operation.get("operation_id")},
                )
            current_binding = restored[index]
            if current_binding.module.module_id != saved.get(
                "module_id"
            ) or current_binding.module.revision_id != saved.get("revision_id"):
                raise WorkspaceError(
                    code="MODULE_CHANGES_UNDO_CONFLICT",
                    where=f"changes.undo:{operation.get('operation_id')}",
                    why="这个模块在本次变更之后又被修改或升级过",
                    fix="保留后续成果；先确认是否仍需恢复本次修改",
                    details={
                        "operation_id": operation.get("operation_id"),
                        "expected": {
                            "module_id": saved.get("module_id"),
                            "revision_id": saved.get("revision_id"),
                        },
                        "current": current_binding.module.view(),
                    },
                )
            original = base_bindings.get(binding_id)
            if original is None:
                raise WorkspaceError(
                    code="MODULE_CHANGES_UNDO_CONFLICT",
                    where=f"changes.undo:{operation.get('operation_id')}",
                    why="变更集里没有这次修改之前的绑定快照",
                    fix="保留当前成果；需要时手工恢复原版本",
                    details={"operation_id": operation.get("operation_id")},
                )
            restored[index] = original

        view = self.assembly.replace(
            bindings=restored,
            links=current.links,
            expected_digest=current_digest,
            request_key=_request_key("undo", change_set_id),
        )
        result = {
            "reverted": True,
            "change_set_id": change_set_id,
            "assembly": view,
            "operations": [_operation_view(item) for item in operations],
            "note": "库内新增副本仍保留；其他作品不受影响。",
        }
        payload["status"] = "reverted"
        payload["result"] = result
        self.store.write(payload)
        return result

    # -- 读取 ---------------------------------------------------------------

    def view(self, *, change_set_id: str) -> dict[str, object]:
        return _full_view(self._load(change_set_id))

    # -- 内部 ---------------------------------------------------------------

    def _load(self, change_set_id: str) -> dict[str, Any]:
        payload = self.store.read(change_set_id)
        if payload.get("schema_version") != CHANGE_SET_SCHEMA:
            raise WorkspaceError(
                code="MODULE_CHANGES_INVALID",
                where="changes.load",
                why=f"不支持的变更集格式版本：{payload.get('schema_version')}",
                fix="用当前版本重新准备内容；旧格式不会被猜测解析",
                details={"change_set_id": change_set_id},
            )
        stored = str(payload.get("workspace") or "")
        if stored and Path(stored) != Path(self.workspace.root).resolve():
            raise WorkspaceError(
                code="MODULE_CHANGES_NOT_FOUND",
                where="changes.load",
                why="这个变更集不属于当前作品",
                fix="在准备这次内容的作品里继续；不要跨作品复用",
                details={"change_set_id": change_set_id},
            )
        return payload

    def _owned(self, change_set_id: str, turn_id: str) -> dict[str, Any]:
        payload = self._load(change_set_id)
        stored = str(payload.get("turn_id") or "")
        if stored != turn_id:
            raise WorkspaceError(
                code="MODULE_CHANGES_NOT_FOUND",
                where="changes.apply",
                why="这个变更集不是本轮任务准备的",
                fix="使用本轮 prepare_module_changes 返回的变更集 ID",
                details={"change_set_id": change_set_id, "turn_id": turn_id},
            )
        return payload

    def _discard_superseded(self, keep_id: str, turn_id: str) -> tuple[list[str], list[str]]:
        """删除同一轮里仍为 prepared 的其它变更集：本次应用已取代它们。

        只删除从未应用过的进度文件；失败与已应用的记录保留。清理失败不影响已经完成的
        应用，失败项写入结果并在 stderr 留一条可观测记录。
        """
        removed: list[str] = []
        failed: list[str] = []
        if not turn_id or not self.store.root.is_dir():
            return removed, failed
        for path in sorted(self.store.root.glob("chs_*.json")):
            if path.stem == keep_id:
                continue
            try:
                item = json.loads(path.read_bytes().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict):
                continue
            if item.get("status") != "prepared" or str(item.get("turn_id") or "") != turn_id:
                continue
            change_set_id = str(item.get("change_set_id") or path.stem)
            try:
                path.unlink()
            except OSError as exc:
                failed.append(change_set_id)
                print(
                    f"[MODULE_CHANGES_DISCARD_FAILED] changes.discard："
                    f"无法删除已放弃的变更集 {change_set_id}：{exc.strerror or exc}。"
                    f"建议：检查作品目录权限；本次应用不受影响。",
                    file=sys.stderr,
                )
                continue
            removed.append(change_set_id)
        return removed, failed

    def _apply_operation(
        self,
        operation: dict[str, Any],
        *,
        change_set_id: str,
        ledger: MaterialLedger | None,
    ) -> dict[str, object] | None:
        """保存一项内容；已有保存结果时复用，不重复建模块或重复发布版本。"""
        saved = operation.get("saved")
        if isinstance(saved, dict):
            return None
        if operation.get("action") == "create":
            name = _checked_name(str(operation.get("name") or ""))
            body = _checked_body(str(operation.get("body") or ""))
            entry = EntryDraft(
                entry_id=_checked_entry_id(str(operation.get("operation_id") or "")),
                kind=_DEFAULT_ENTRY_KIND,
                title=name,
                body=body,
            )
            validate_entries([entry], module_kind=_DEFAULT_MODULE_KIND)
            created = self.library.create_module(
                kind=_DEFAULT_MODULE_KIND,
                name=name,
                entries=[entry],
                request_key=_request_key(
                    "content",
                    change_set_id,
                    str(operation.get("operation_id") or ""),
                    "create",
                ),
            )
            revision = created.get("revision")
            if not isinstance(revision, dict):
                raise WorkspaceError(
                    code="MODULE_CHANGES_INVALID",
                    where=f"changes.apply:{operation.get('operation_id')}",
                    why="新建模块没有返回版本",
                    fix="保留生成内容；重新读取模块库后重试",
                    details={"operation_id": operation.get("operation_id")},
                )
            operation["saved"] = {
                "module_id": str(revision.get("module_id") or ""),
                "revision_id": str(revision.get("revision_id") or ""),
                "name": name,
                "created": True,
            }
            return {"operation_id": operation.get("operation_id"), **operation["saved"]}

        revision, name = self._save_update(operation, change_set_id=change_set_id, ledger=ledger)
        operation["saved"] = {
            "module_id": str(operation.get("module_id") or ""),
            "revision_id": revision.revision_id,
            "name": name,
            "created": False,
        }
        return {"operation_id": operation.get("operation_id"), **operation["saved"]}

    def _save_update(
        self,
        operation: dict[str, Any],
        *,
        change_set_id: str,
        ledger: MaterialLedger | None,
    ) -> tuple[RevisionInfo, str]:
        module_id = str(operation.get("module_id") or "")
        base_revision = str(operation.get("base_revision") or "")
        module = self.library.module(module_id)
        catalog = self.library.revision(module_id=module_id, revision_id=base_revision)
        edits = {
            str(item.get("entry_id") or ""): item
            for item in operation.get("edits") or []
            if isinstance(item, dict)
        }
        if not edits:
            raise WorkspaceError(
                code="MODULE_CHANGE_TARGET_INVALID",
                where=f"changes.apply:{operation.get('operation_id')}",
                why="修改操作没有给出任何条目变化",
                fix="用 edits 指定条目与唯一文本替换或整份正文",
                details={"operation_id": operation.get("operation_id")},
            )
        drafts: list[EntryDraft] = []
        for entry in catalog.entries:
            raw, digest = self.library.storage.read_entry(module_id, base_revision, entry.entry_id)
            draft = parse_entry(raw, module_kind=module.kind)
            change = edits.pop(entry.entry_id, None)
            drafts.append(
                _edited_draft(
                    draft,
                    change,
                    current_digest=digest,
                    ledger=ledger,
                    library_id=self.library.library_id or "",
                    module_id=module_id,
                    revision_id=base_revision,
                    operation_id=str(operation.get("operation_id") or ""),
                )
                if change is not None
                else draft
            )
        if edits:
            raise WorkspaceError(
                code="MODULE_CHANGE_TARGET_INVALID",
                where=f"changes.apply:{operation.get('operation_id')}",
                why="要修改的条目不在这个版本里：" + "、".join(sorted(edits)),
                fix="先读取版本目录；条目 ID 必须来自实际版本",
                details={"entry_ids": sorted(edits)},
            )
        # 名称可以一起改；省略时保留库里的名称。
        name = str(operation.get("name") or module.name)
        revision = self.library.save_workspace_revision(
            module_id=module_id,
            base_revision=base_revision,
            entries=validate_entries(drafts, module_kind=module.kind),
            request_key=_request_key(
                "content",
                change_set_id,
                str(operation.get("operation_id") or ""),
                "update",
            ),
        )
        return revision, name

    def _commit_assembly(
        self,
        change_set_id: str,
        assembly: Assembly,
        operations: list[dict[str, Any]],
        *,
        expected_digest: str | None,
    ) -> dict[str, object]:
        bindings = list(assembly.bindings)
        for operation in operations:
            saved = operation.get("saved")
            if not isinstance(saved, dict):
                raise WorkspaceError(
                    code="MODULE_CHANGES_INVALID",
                    where=f"changes.commit:{operation.get('operation_id')}",
                    why="保存结果缺少模块引用",
                    fix="重新读取变更集；不要跳过内容保存",
                    details={"operation_id": operation.get("operation_id")},
                )
            ref = ModuleRef(
                library_id=self.library.library_id or "",
                module_id=str(saved.get("module_id") or ""),
                revision_id=str(saved.get("revision_id") or ""),
            )
            name = str(saved.get("name") or "")
            if operation.get("action") == "create":
                binding_id = new_id("binding")
                bindings.append(Binding(id=binding_id, module=ref, label=name))
                saved["binding_id"] = binding_id
                continue
            binding_id = str(saved.get("binding_id") or operation.get("binding_id") or "")
            index = next(
                (position for position, item in enumerate(bindings) if item.id == binding_id),
                -1,
            )
            if index < 0:
                raise WorkspaceError(
                    code="MODULE_CHANGE_TARGET_INVALID",
                    where=f"changes.commit:{operation.get('operation_id')}",
                    why="要更新的绑定已经不在当前作品里",
                    fix="刷新作品模块后重试；已保存内容会保留",
                    details={"binding_id": binding_id},
                )
            bindings[index] = Binding(
                id=binding_id,
                module=ref,
                label=name or bindings[index].label,
            )
            saved["binding_id"] = binding_id
        return self.assembly.replace(
            bindings=bindings,
            links=assembly.links,
            expected_digest=expected_digest,
            request_key=_request_key("assembly", change_set_id),
        )

    def _failed(
        self, payload: dict[str, Any], *, stage: str, error: Mapping[str, object]
    ) -> dict[str, object]:
        result = {
            "applied": False,
            "change_set_id": payload.get("change_set_id"),
            "stage": stage,
            "error": dict(error),
            "operations": [_operation_view(item) for item in _payload_operations(payload)],
            "saved": [
                _saved_view(item) for item in _payload_operations(payload) if item.get("saved")
            ],
        }
        payload["status"] = "failed"
        payload["result"] = result
        self.store.write(payload)
        return result


# -- 结构校验 ---------------------------------------------------------------


def _checked_operations(operations: Sequence[ChangeOperation]) -> tuple[ChangeOperation, ...]:
    if not operations:
        raise WorkspaceError(
            code="MODULE_CHANGES_INVALID",
            where="changes.prepare",
            why="变更集至少需要一个操作",
            fix="给出要新建或修改的内容；纯讨论不需要准备变更集",
        )
    if len(operations) > _MAX_OPERATIONS:
        raise WorkspaceError(
            code="MODULE_CHANGES_INVALID",
            where="changes.prepare",
            why=f"一次最多准备 {_MAX_OPERATIONS} 个操作，当前 {len(operations)} 个",
            fix="分几次提交；不要把整个作品一次塞进一份变更集",
        )
    seen: set[str] = set()
    checked: list[ChangeOperation] = []
    for operation in operations:
        operation_id = _checked_entry_id(operation.operation_id)
        if operation_id in seen:
            raise WorkspaceError(
                code="MODULE_CHANGE_OPERATION_CONFLICT",
                where="changes.prepare",
                why=f"同一变更集里操作标识重复：{operation_id}",
                fix="给每份内容一个独立且稳定的 operation_id",
                details={"operation_id": operation_id},
            )
        seen.add(operation_id)
        if operation.action == "create":
            _checked_name(operation.name)
            _checked_body(operation.body)
            checked.append(
                ChangeOperation(
                    operation_id=operation_id,
                    action="create",
                    name=operation.name.strip(),
                    body=operation.body,
                )
            )
            continue
        if not operation.binding_id or not operation.module_id or not operation.base_revision:
            raise WorkspaceError(
                code="MODULE_CHANGE_TARGET_INVALID",
                where=f"changes.prepare:{operation_id}",
                why="修改操作缺少绑定、模块或基础版本",
                fix="先读取作品模块，再按实际绑定与版本登记",
                details={"operation_id": operation_id},
            )
        for edit in operation.edits:
            _checked_edit(edit, operation_id=operation_id)
        checked.append(
            ChangeOperation(
                operation_id=operation_id,
                action="update",
                name=operation.name.strip(),
                binding_id=operation.binding_id,
                module_id=operation.module_id,
                base_revision=operation.base_revision,
                edits=operation.edits,
            )
        )
    return tuple(checked)


def _checked_edit(edit: ChangeEdit, *, operation_id: str) -> None:
    _checked_entry_id(edit.entry_id)
    if not edit.base_digest.strip():
        raise WorkspaceError(
            code="MODULE_CHANGE_TARGET_INVALID",
            where=f"changes.prepare:{operation_id}",
            why="修改条目缺少读取时拿到的指纹",
            fix="先用 read_record 读取这一版条目，再带上返回的 digest",
            details={"entry_id": edit.entry_id},
        )
    if edit.body is not None:
        if edit.find or edit.replace:
            raise WorkspaceError(
                code="MODULE_CHANGE_TARGET_INVALID",
                where=f"changes.prepare:{operation_id}",
                why="整份重写不能再带 find/replace",
                fix="整份正文与局部替换二选一",
                details={"entry_id": edit.entry_id},
            )
        _checked_body(edit.body)
        return
    if not edit.find:
        raise WorkspaceError(
            code="MODULE_CHANGE_TARGET_INVALID",
            where=f"changes.prepare:{operation_id}",
            why="局部修改缺少要替换的原文",
            fix="给出唯一匹配的 find 与 replace，或整份重写 body",
            details={"entry_id": edit.entry_id},
        )


def _checked_entry_id(value: str) -> str:
    text = value.strip()
    if not text or len(text) > 64 or not all(char.isalnum() or char in "-_" for char in text):
        raise WorkspaceError(
            code="MODULE_CHANGES_INVALID",
            where="changes.operation",
            why=f"操作标识不合法：{text or '（空）'}",
            fix="用字母或数字开头，只含字母、数字、连字符与下划线，最长 64 字符",
            details={"operation_id": text},
        )
    return text


def _checked_name(value: str) -> str:
    text = value.strip()
    if not text or len(text) > 80:
        raise WorkspaceError(
            code="MODULE_NAME_INVALID",
            where="changes.content",
            why="模块名称需要为 1–80 个字符",
            fix="缩短名称；详细内容写在正文里",
        )
    return text


def _checked_body(value: str) -> str:
    text = value.strip()
    if not text:
        raise WorkspaceError(
            code="MODULE_CHANGES_INVALID",
            where="changes.content",
            why="内容正文不能为空",
            fix="写完正文再提交；空模块不会保存",
        )
    return text


def _edited_draft(
    draft: EntryDraft,
    change: Mapping[str, Any],
    *,
    current_digest: str,
    ledger: MaterialLedger | None,
    library_id: str,
    module_id: str,
    revision_id: str,
    operation_id: str,
) -> EntryDraft:
    """按实际版本修改一条；指纹不符或局部匹配不唯一时拒绝。"""
    expected = str(change.get("base_digest") or "")
    if expected != current_digest:
        raise WorkspaceError(
            code="MODULE_CHANGES_STALE_CONFLICT",
            where=f"changes.edit:{operation_id}/{draft.entry_id}",
            why="条目内容在读取之后已经变化",
            fix="重新读取当前条目后再修改；保留未完成内容",
            details={
                "entry_id": draft.entry_id,
                "expected": expected,
                "current": current_digest,
            },
        )
    body = change.get("body")
    if isinstance(body, str):
        text = _checked_body(body)
        if ledger is not None:
            path = source_path(library_id, module_id, revision_id, draft.entry_id)
            coverage = ledger.coverage(path)
            if coverage is None or coverage.ref.digest != current_digest or not coverage.complete:
                raise WorkspaceError(
                    code="REFERENCE_NOT_FULLY_READ_CONFLICT",
                    where=f"changes.edit:{operation_id}/{draft.entry_id}",
                    why="整份重写前需要读到这个条目当前版本的全文",
                    fix="用 read_record 读完剩余部分，再提交整份正文",
                    details={"entry_id": draft.entry_id, "path": path},
                )
        return EntryDraft(
            entry_id=draft.entry_id,
            kind=draft.kind,
            title=draft.title,
            body=text,
            aliases=draft.aliases,
            fields=draft.fields,
        )
    find = str(change.get("find") or "")
    replace = str(change.get("replace") or "")
    count = draft.body.count(find)
    if count != 1:
        raise WorkspaceError(
            code="MODULE_CHANGE_MATCH_CONFLICT",
            where=f"changes.edit:{operation_id}/{draft.entry_id}",
            why=(
                "要替换的原文没有出现"
                if count == 0
                else f"要替换的原文出现 {count} 次，无法唯一确定"
            ),
            fix="引用条目里唯一的一段原文，或改用整份重写",
            details={"entry_id": draft.entry_id, "occurrences": count},
        )
    return EntryDraft(
        entry_id=draft.entry_id,
        kind=draft.kind,
        title=draft.title,
        body=draft.body.replace(find, replace, 1),
        aliases=draft.aliases,
        fields=draft.fields,
    )


# -- 视图 -------------------------------------------------------------------


def _operation_payload(operation: ChangeOperation) -> dict[str, object]:
    payload: dict[str, object] = {
        "operation_id": operation.operation_id,
        "action": operation.action,
    }
    if operation.name:
        payload["name"] = operation.name
    if operation.action == "create":
        payload["body"] = operation.body
        payload["slot"] = operation.operation_id
    else:
        payload["binding_id"] = operation.binding_id
        payload["module_id"] = operation.module_id
        payload["base_revision"] = operation.base_revision
        payload["edits"] = [
            {
                "entry_id": edit.entry_id,
                "base_digest": edit.base_digest,
                "find": edit.find,
                "replace": edit.replace,
                **({"body": edit.body} if edit.body is not None else {}),
            }
            for edit in operation.edits
        ]
    return payload


def _payload_operations(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("operations")
    if not isinstance(rows, list):
        raise WorkspaceError(
            code="MODULE_CHANGES_INVALID",
            where="changes.load",
            why="变更集缺少操作清单",
            fix="保留原文件并从备份恢复",
            details={"change_set_id": payload.get("change_set_id")},
        )
    # 返回原对象而不是副本：应用阶段要把逐项保存结果写回同一份任务进度。
    return [item for item in rows if isinstance(item, dict)]


def _prepared_view(payload: Mapping[str, Any]) -> dict[str, object]:
    operations = _payload_operations(payload)
    return {
        "change_set_id": payload.get("change_set_id"),
        "status": payload.get("status"),
        "base_assembly_digest": payload.get("base_assembly_digest"),
        "operations": [_operation_view(item) for item in operations],
        "content_ref": {
            "change_set_id": payload.get("change_set_id"),
            "operations": [str(item.get("operation_id") or "") for item in operations],
        },
        "note": "内容已保存为任务进度，尚未用于当前作品；应用后才出现在侧栏。",
    }


def _full_view(payload: Mapping[str, Any]) -> dict[str, object]:
    operations = _payload_operations(payload)
    return {
        "change_set_id": payload.get("change_set_id"),
        "status": payload.get("status"),
        "turn_id": payload.get("turn_id"),
        "base_assembly_digest": payload.get("base_assembly_digest"),
        "operations": [
            {
                **_operation_view(item),
                **({"body": item["body"]} if isinstance(item.get("body"), str) else {}),
                "edits": item.get("edits") or [],
            }
            for item in operations
        ],
        "saved": [_saved_view(item) for item in operations if item.get("saved")],
        "result": payload.get("result"),
    }


def _operation_view(operation: Mapping[str, Any]) -> dict[str, object]:
    view: dict[str, object] = {
        "operation_id": operation.get("operation_id"),
        "action": operation.get("action"),
        "status": "saved" if operation.get("saved") else "pending",
    }
    if operation.get("name"):
        view["name"] = operation["name"]
    if operation.get("action") == "create":
        view["chars"] = len(str(operation.get("body") or ""))
    else:
        view["binding_id"] = operation.get("binding_id")
        view["module_id"] = operation.get("module_id")
        view["base_revision"] = operation.get("base_revision")
        view["edits"] = len(operation.get("edits") or [])
    saved = operation.get("saved")
    if isinstance(saved, dict):
        view["saved"] = _saved_view(operation)
    return view


def _saved_view(operation: Mapping[str, Any]) -> dict[str, object]:
    saved = operation.get("saved")
    row = dict(saved) if isinstance(saved, dict) else {}
    return {
        "operation_id": operation.get("operation_id"),
        "module_id": row.get("module_id"),
        "revision_id": row.get("revision_id"),
        "binding_id": row.get("binding_id"),
        "name": row.get("name"),
        "created": bool(row.get("created")),
    }


def _assembly_from(raw: object) -> Assembly:
    if not isinstance(raw, dict):
        return Assembly()
    return Assembly(
        library_id=str(raw.get("library_id") or "") or None,
        bindings=tuple(
            Binding(
                id=str(row.get("id") or ""),
                module=ModuleRef(
                    library_id=str((row.get("module") or {}).get("library_id") or ""),
                    module_id=str((row.get("module") or {}).get("module_id") or ""),
                    revision_id=str((row.get("module") or {}).get("revision_id") or ""),
                ),
                label=str(row.get("label") or ""),
            )
            for row in raw.get("bindings") or []
            if isinstance(row, dict)
        ),
        links=tuple(
            (str(item[0]), str(item[1]))
            for item in raw.get("links") or []
            if isinstance(item, (list, tuple)) and len(item) == 2
        ),
    )


def _checked_id(change_set_id: str) -> str:
    text = str(change_set_id or "").strip()
    if (
        not text.startswith("chs_")
        or len(text) != 20
        or not all(char in "0123456789abcdef" for char in text[4:])
    ):
        raise WorkspaceError(
            code="MODULE_CHANGES_INVALID",
            where="changes.id",
            why=f"变更集标识不合法：{text or '（空）'}",
            fix="使用 prepare 返回的变更集 ID",
            details={"change_set_id": text},
        )
    return text


def _request_digest(operations: Sequence[ChangeOperation], base_digest: str | None) -> str:
    raw = json.dumps(
        {
            "operations": [_operation_payload(operation) for operation in operations],
            "base": base_digest,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _request_key(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"modchg-{digest[:48]}"


def _error_view(exc: WorkspaceError) -> dict[str, object]:
    return {
        "code": exc.code,
        "where": exc.where,
        "why": exc.why,
        "fix": exc.fix,
        "details": dict(exc.details),
    }
