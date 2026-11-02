"""通用创作文本候选（1.0.9 P3）。

候选是「按目标生成、绑定基础修订、等待作者采用」的文本，不是正式作品内容。它保存在
独立目录，按标识读取；正式保存时服务端会再次核对目标与基础修订，客户端标记不能代替
服务端判断。

状态只表达候选自身：``streaming`` 正在生成，``ready`` 可比较或采用，``stale`` 基础
修订已过期，``failed`` 失败但可保留部分文本，``discarded`` 作者已放弃。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hastings.workspace.atomic_io import atomic_write_json, locked
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_store import new_id

__all__ = [
    "CandidateBase",
    "CandidateStatus",
    "CreativeCandidate",
    "CreativeCandidateStore",
    "CreativeTarget",
    "ProposedBinding",
]

CandidateStatus = Literal["streaming", "ready", "stale", "failed", "discarded"]
TargetKind = Literal[
    "new_workspace",
    "workspace_title",
    "book_intent",
    "chapter_title",
    "chapter_direction",
    "prose",
    "idea",
    "module",
    "conversation",
]


class CreativeTarget(BaseModel):
    """候选针对的对象；路径与归属由服务端按标识解析，客户端不能传任意路径。"""

    model_config = ConfigDict(extra="forbid")

    kind: TargetKind
    workspace_id: str | None = Field(default=None, max_length=64)
    chapter_id: str | None = Field(default=None, max_length=64)
    draft_id: str | None = Field(default=None, max_length=64)


class CandidateBase(BaseModel):
    """生成时的基础修订；采用前逐项核对。"""

    model_config = ConfigDict(extra="forbid")

    plan_revision: str | None = Field(default=None, max_length=64)
    context_revision: str | None = Field(default=None, max_length=64)
    prose_path: str | None = Field(default=None, max_length=512)
    prose_digest: str | None = Field(default=None, max_length=128)
    draft_revision: str | None = Field(default=None, max_length=128)


class ProposedBinding(BaseModel):
    """候选建议的正文出处；采用时服务端重新核对原句与当前修订。"""

    model_config = ConfigDict(extra="forbid")

    move_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(min_length=1, max_length=2000)


class CreativeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    workspace_id: str | None
    target: CreativeTarget
    kind: str
    text: str = ""
    alternatives: list[str] = Field(default_factory=list)
    base: CandidateBase = Field(default_factory=CandidateBase)
    proposed_bindings: list[ProposedBinding] = Field(default_factory=list)
    status: CandidateStatus = "streaming"
    error: str | None = None
    request_key: str = ""
    created_at: str
    updated_at: str


class CreativeCandidateStore:
    """按候选标识保存单个 JSON 文件；目录与作品状态目录同级。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dir = self.root / "writing" / "candidates"
        self.lock_file = self.dir / ".lock"

    def read(self, candidate_id: str) -> CreativeCandidate:
        path = self._path(candidate_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkspaceError(
                code="CANDIDATE_NOT_FOUND",
                where=f"candidate:{candidate_id}",
                why="候选不存在或已被清理",
                fix="重新请求模型候选",
                details={"candidate_id": candidate_id},
            ) from exc
        except (OSError, ValueError) as exc:
            raise WorkspaceError(
                code="CANDIDATE_STATE_FAILED",
                where=f"candidate:{candidate_id}",
                why="候选记录无法读取",
                fix="放弃这条候选后重新生成",
                details={"candidate_id": candidate_id},
            ) from exc
        try:
            return CreativeCandidate.model_validate(payload)
        except ValueError as exc:
            raise WorkspaceError(
                code="CANDIDATE_STATE_FAILED",
                where=f"candidate:{candidate_id}",
                why="候选记录格式不正确",
                fix="放弃这条候选后重新生成",
                details={"candidate_id": candidate_id},
            ) from exc

    def create(
        self,
        *,
        workspace_id: str | None,
        target: CreativeTarget,
        kind: str,
        base: CandidateBase,
        request_key: str,
    ) -> CreativeCandidate:
        with locked(self.lock_file):
            prior = self._find_request(request_key)
            if prior is not None:
                return prior
            now = _now()
            candidate = CreativeCandidate(
                candidate_id=new_id("cand"),
                workspace_id=workspace_id,
                target=target,
                kind=kind,
                base=base,
                status="streaming",
                request_key=request_key,
                created_at=now,
                updated_at=now,
            )
            self._write(candidate)
            return candidate

    def update(
        self,
        candidate_id: str,
        *,
        text: str | None = None,
        alternatives: list[str] | None = None,
        status: CandidateStatus | None = None,
        error: str | None = None,
        proposed_bindings: list[ProposedBinding] | None = None,
    ) -> CreativeCandidate:
        with locked(self.lock_file):
            current = self.read(candidate_id)
            updates: dict[str, object] = {"updated_at": _now()}
            if text is not None:
                updates["text"] = text
            if alternatives is not None:
                updates["alternatives"] = alternatives
            if status is not None:
                updates["status"] = status
            if error is not None:
                updates["error"] = error
            if proposed_bindings is not None:
                updates["proposed_bindings"] = proposed_bindings
            updated = current.model_copy(update=updates)
            self._write(updated)
            return updated

    def discard(self, candidate_id: str) -> CreativeCandidate:
        return self.update(candidate_id, status="discarded")

    def _path(self, candidate_id: str) -> Path:
        if not candidate_id or "/" in candidate_id or "\\" in candidate_id:
            raise WorkspaceError(
                code="CANDIDATE_NOT_FOUND",
                where="candidate",
                why="候选标识不合法",
                fix="从服务端返回的候选中重新选择",
            )
        return self.dir / f"{candidate_id}.json"

    def _write(self, candidate: CreativeCandidate) -> None:
        atomic_write_json(
            self._path(candidate.candidate_id),
            candidate.model_dump(mode="json"),
            prefix=".candidate-",
        )

    def _find_request(self, request_key: str) -> CreativeCandidate | None:
        if not request_key or not self.dir.exists():
            return None
        for path in self.dir.glob("cand_*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if payload.get("request_key") != request_key:
                continue
            try:
                return CreativeCandidate.model_validate(payload)
            except ValueError:
                continue
        return None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")
