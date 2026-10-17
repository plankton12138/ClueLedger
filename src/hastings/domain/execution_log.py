"""执行日志领域模型（M13/V03-1.6）：公共运行事件的冻结字段与白名单序列化。

三条内容分流（guide §3.5）：模型公开过程、应用执行事件、正式回复。本模块只
承载「应用执行事件」的公共投影形状——任务/尝试/调用标识 + 摘要 + 版本引用，
不含模型原文、Key、认证头或配置原文（summary 是摘要不是内容回放）。

``sequence`` 由 ExecutionLog 在追加时分配（作品内单调）；构造事件时填 0 占位，
``to_payload`` 供存储与 API 序列化，字段白名单即本方法列出的全集。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ExecutionEvent", "VersionedRef"]


@dataclass(frozen=True, slots=True)
class VersionedRef:
    """事件关联的对象版本引用（如某章某 revision）；空元组表示无版本关联。"""

    kind: str
    id: str
    revision: int

    def to_payload(self) -> dict[str, object]:
        return {"kind": self.kind, "id": self.id, "revision": self.revision}

    @staticmethod
    def from_payload(raw: object) -> VersionedRef:
        data = dict(raw) if isinstance(raw, dict) else {}
        return VersionedRef(
            kind=str(data.get("kind", "")),
            id=str(data.get("id", "")),
            revision=int(data.get("revision", 0)),
        )


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """公共执行日志事件（M13 冻结字段）。

    标识三层的语义：``task_id`` 是任务通道（如一次对话任务的全部尝试），
    ``attempt_id`` 是其中一次尝试（重试产生新 attempt），``call_id`` 是
    尝试内的一次模型/工具调用（``parent_call_id`` 表达调用因果链）。
    """

    event_id: str
    project_id: str
    sequence: int
    task_id: str
    attempt_id: str
    agent_id: str | None
    call_id: str | None
    parent_call_id: str | None
    event_type: str
    occurred_at: str
    summary: str
    source_event_id: str
    version_refs: tuple[VersionedRef, ...]

    def to_payload(self) -> dict[str, object]:
        """白名单序列化：字段全集固定，新增敏感字段必须显式进白名单并脱敏。"""
        return {
            "event_id": self.event_id,
            "project_id": self.project_id,
            "sequence": self.sequence,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "agent_id": self.agent_id,
            "call_id": self.call_id,
            "parent_call_id": self.parent_call_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "summary": self.summary,
            "source_event_id": self.source_event_id,
            "version_refs": [ref.to_payload() for ref in self.version_refs],
        }

    @staticmethod
    def from_payload(raw: object) -> ExecutionEvent:
        data = dict(raw) if isinstance(raw, dict) else {}
        refs_raw = data.get("version_refs") or []
        return ExecutionEvent(
            event_id=str(data.get("event_id", "")),
            project_id=str(data.get("project_id", "")),
            sequence=int(data.get("sequence", 0)),
            task_id=str(data.get("task_id", "")),
            attempt_id=str(data.get("attempt_id", "")),
            agent_id=data.get("agent_id") if isinstance(data.get("agent_id"), str) else None,
            call_id=data.get("call_id") if isinstance(data.get("call_id"), str) else None,
            parent_call_id=(
                data.get("parent_call_id") if isinstance(data.get("parent_call_id"), str) else None
            ),
            event_type=str(data.get("event_type", "")),
            occurred_at=str(data.get("occurred_at", "")),
            summary=str(data.get("summary", "")),
            source_event_id=str(data.get("source_event_id", "")),
            version_refs=tuple(VersionedRef.from_payload(x) for x in refs_raw),
        )
