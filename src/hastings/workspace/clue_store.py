"""当前格式线索账本：严格读取、跨进程锁与原子事务。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from pydantic import JsonValue, ValidationError

from hastings.workspace.atomic_io import atomic_write_json, locked
from hastings.workspace.clue_models import (
    ClueContent,
    ClueContribution,
    ClueCoverage,
    ClueEntry,
    ClueIdentity,
    ClueLedger,
    ClueReceipt,
)
from hastings.workspace.errors import WorkspaceError


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def state_error(path: Path, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="CLUE_STATE_FAILED",
        where=f"clues.read:{path.name}",
        why=why,
        fix="检查当前账本文件；需要恢复当前格式数据时从状态备份恢复",
    )


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ClueSnapshot:
    revision: str
    ledger: ClueLedger | None = None

    def entries(self) -> tuple[ClueEntry, ...]:
        if self.ledger is not None:
            return tuple(
                replace(
                    row.content.entry(row.clue_id),
                    source_path=row.source_path,
                    source_digest=row.source_digest,
                )
                for row in self.ledger.contributions.values()
            )
        return ()

    def coverage(self) -> tuple[ClueCoverage, ...]:
        if self.ledger is not None:
            return tuple(self.ledger.coverage.values())
        return ()


class ClueLedgerStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.file = self.root / "clue-ledger.json"
        self.lock_file = self.root / ".clue-ledger.lock"

    def read(self) -> ClueSnapshot:
        """无副作用读取；损坏的新账本绝不回退到旧记录。"""
        try:
            data = self.file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ClueSnapshot("empty")
        except (OSError, UnicodeError) as exc:
            raise state_error(self.file, "无法读取线索账本") from exc
        try:
            raw = json.loads(data)
            # Literal[int] 会接受 True；格式头需要明确排除它。
            if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int:
                raise ValueError("格式头不完整")
            # 明确弃用之前的格式，不读旧线索、覆盖、别名或迁移源。
            if raw["schema_version"] == 1:
                return ClueSnapshot("empty")
            if type(raw.get("recipe_version")) is not int:
                raise ValueError("生成规则版本不完整")
            if not set(ClueLedger.model_fields).issubset(raw):
                raise ValueError("账本字段缺失")
            ledger = ClueLedger.model_validate(raw)
        except (ValueError, ValidationError) as exc:
            raise state_error(self.file, "线索账本损坏或格式不受支持") from exc
        return ClueSnapshot(ledger.ledger_revision, ledger=ledger)

    def mutate(
        self,
        *,
        expected_revision: str,
        request_key: str,
        kind: str,
        payload: Mapping[str, object],
        apply: Callable[[ClueLedger], dict[str, JsonValue]],
        task_id: str = "",
        preflight: Callable[[], None] | None = None,
    ) -> dict[str, JsonValue]:
        if not request_key.strip():
            raise _conflict("请求键为空", "为这次操作提供独立请求键")
        fingerprint = _fingerprint(
            {
                "kind": kind,
                "expected_revision": expected_revision,
                "payload": payload,
                "task_id": task_id,
            }
        )
        try:
            with locked(self.lock_file):
                snapshot = self.read()
                current = snapshot.ledger
                if current is not None:
                    previous = current.requests.get(request_key)
                    if previous is not None:
                        if previous.fingerprint != fingerprint:
                            raise _conflict("同一请求键的内容发生变化", "使用新请求键提交新操作")
                        return dict(previous.result)
                self._require_revision(snapshot, expected_revision, task_id)
                if preflight is not None:
                    preflight()
                if current is None:
                    candidate = ClueLedger(
                        workspace_lineage=new_id("ws"), ledger_revision=new_id("rev")
                    )
                else:
                    candidate = current.model_copy(deep=True)
                result = apply(candidate)
                candidate.ledger_revision = new_id("rev")
                result = {**result, "ledger_revision": candidate.ledger_revision}
                candidate.requests[request_key] = ClueReceipt(
                    fingerprint=fingerprint,
                    kind=kind,
                    base_revision=expected_revision,
                    task_id=task_id,
                    result=result,
                )
                candidate = ClueLedger.model_validate(candidate.model_dump(mode="json"))
                if preflight is not None:
                    preflight()
                atomic_write_json(
                    self.file, candidate.model_dump(mode="json"), prefix=".clue-ledger-"
                )
                return dict(result)
        except OSError as exc:
            raise WorkspaceError(
                code="CLUE_WRITE_FAILED",
                where="clues.commit",
                why="线索账本无法写入",
                fix="检查状态目录权限与可用空间，再使用原请求键重试",
            ) from exc

    @staticmethod
    def _require_revision(snapshot: ClueSnapshot, expected: str, task_id: str) -> None:
        if snapshot.revision == expected:
            return
        # 同一次受理任务逐章提交可接续自己的事务；外部写入后必须冲突。
        if (
            task_id
            and snapshot.ledger is not None
            and any(
                receipt.task_id == task_id
                and receipt.base_revision == expected
                and receipt.result.get("ledger_revision") == snapshot.revision
                for receipt in snapshot.ledger.requests.values()
            )
        ):
            return
        raise _conflict("线索账本版本已变化", "刷新资料后重新发起操作")

    def publish(
        self,
        entries: Sequence[ClueEntry],
        *,
        source_chapter: int | None,
        expected_revision: str,
        request_key: str,
        coverage: ClueCoverage | None = None,
        task_id: str = "",
        allowed_ids: Sequence[str] | None = None,
        preflight: Callable[[], None] | None = None,
    ) -> dict[str, JsonValue]:
        try:
            contents = tuple(ClueContent.from_entry(item) for item in entries)
        except ValidationError as exc:
            raise WorkspaceError(
                code="STATE_EVIDENCE_INVALID",
                where="clues.publish",
                why="线索记录的字段、章号或依据区间不完整",
                fix="按当前原文重新提交完整记录",
            ) from exc
        if source_chapter is not None and any(
            item.source_chapter != source_chapter for item in contents
        ):
            raise _conflict("贡献章与提交目标不一致", "只提交本次目标章的贡献")
        if coverage is not None and (
            source_chapter != coverage.chapter or (coverage.outcome == "found") != bool(entries)
        ):
            raise _conflict("贡献与覆盖结果不一致", "有发现时提交记录，空结果只提交覆盖")
        payload: dict[str, object] = {
            "entries": [entry.to_dict() for entry in entries],
            "source_chapter": source_chapter,
            "coverage": coverage.model_dump(mode="json", exclude={"at"}) if coverage else None,
            "allowed_ids": sorted(allowed_ids) if allowed_ids is not None else None,
        }

        def apply(ledger: ClueLedger) -> dict[str, JsonValue]:
            assigned: list[tuple[str, ClueContent]] = []
            visible = set(allowed_ids) if allowed_ids is not None else set(ledger.clues)
            visible.update(
                str(value)
                for receipt in ledger.requests.values()
                if task_id and receipt.task_id == task_id
                for value in cast(list[JsonValue], receipt.result.get("clue_ids", []))
            )
            for entry, content in zip(entries, contents, strict=True):
                clue_id = entry.clue_id
                if clue_id:
                    if entry.new_identity or clue_id not in ledger.clues or clue_id not in visible:
                        raise WorkspaceError(
                            code="CLUE_IDENTITY_CONFLICT",
                            where="clues.publish",
                            why="提交的线索身份不在本次已知身份中，或与另建意图冲突",
                            fix="读取已有线索并复用其 ID；另建时不提交已有 ID",
                        )
                else:
                    if not entry.new_identity and any(
                        value.title.strip() == entry.key for value in ledger.clues.values()
                    ):
                        raise WorkspaceError(
                            code="CLUE_IDENTITY_CONFLICT",
                            where="clues.publish",
                            why=f"已有同名线索，尚未明确本次记录的身份：{entry.key}",
                            fix="更新时提供已有 clue_id；确为另一条线索时明确 new_identity=true",
                        )
                    clue_id = new_id("clue")
                if any(value == clue_id for value, _ in assigned):
                    raise _conflict("同一章重复提交同一线索身份", "每条身份在本章只提交一份贡献")
                assigned.append((clue_id, content))
            affected = {
                row.clue_id
                for row in ledger.contributions.values()
                if source_chapter is not None
                and row.content.source_chapter == source_chapter
                and (coverage is None or coverage.outcome != "insufficient")
            }
            if coverage is None or coverage.outcome != "insufficient":
                ledger.contributions = {
                    key: row
                    for key, row in ledger.contributions.items()
                    if not (
                        row.content.source_chapter == source_chapter
                        if source_chapter is not None
                        else (row.clue_id, row.content.source_chapter)
                        in {(value, content.source_chapter) for value, content in assigned}
                    )
                }
            for clue_id, content in assigned:
                previous = ledger.clues.get(clue_id)
                ledger.clues[clue_id] = ClueIdentity(
                    clue_id=clue_id,
                    title=content.clue.strip(),
                    clue_revision=new_id("rev"),
                    created_at=previous.created_at if previous else _now(),
                    created_by=previous.created_by if previous else task_id or "state_publish",
                    first_chapter=min(
                        previous.first_chapter or content.planted_chapter, content.planted_chapter
                    )
                    if previous
                    else content.planted_chapter,
                )
                affected.add(clue_id)
                evidence = content.evidence
                contribution_id = new_id("contrib")
                ledger.contributions[contribution_id] = ClueContribution(
                    contribution_id=contribution_id,
                    observation_id=new_id("obs"),
                    clue_id=clue_id,
                    source_path=coverage.path if coverage else evidence.path,
                    source_digest=coverage.digest if coverage else evidence.digest,
                    task_id=task_id,
                    mode=content.mode,
                    content=content,
                )
            for clue_id in affected:
                ledger.clues[clue_id].clue_revision = new_id("rev")
            if coverage is not None:
                ledger.coverage[coverage.path] = coverage
            return {"clue_ids": [value for value, _ in assigned], "published": len(assigned)}

        return self.mutate(
            expected_revision=expected_revision,
            request_key=request_key,
            kind="publish_chapter",
            payload=payload,
            task_id=task_id,
            apply=apply,
            preflight=preflight,
        )


def _conflict(why: str, fix: str) -> WorkspaceError:
    return WorkspaceError(code="CLUE_REVISION_CONFLICT", where="clues.commit", why=why, fix=fix)
