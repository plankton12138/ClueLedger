"""受限 TaskDraft（Phase 19.4）：可检查、可启动的任务建议。

边界（计划 §3.3、§19.4）：建议本身不创建 Job——它只是一份结构化的参数草案；
「开始」是显式动作，由应用服务在缺失项清空、基础版本未过期时才放行，并绑定
幂等键与 job_ref。draft 不能执行任何事，也没有任何写端口（四透镜②）。

``missing_items`` 是纯函数：给定任务参数与项目只读视图，返回逐条缺失项
（每条含 code/where/why/fix，文案里写的就是解决办法——四透镜①）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from hastings.domain.conversation import error_codes as ec

__all__ = [
    "DRAFT_ACTIVE",
    "DRAFT_ABANDONED",
    "DRAFT_STARTED",
    "ProjectView",
    "TASK_KINDS",
    "TaskDraft",
    "missing_items",
]

# 任务状态：建议 → 已启动（绑定 job_ref）/ 已放弃。
DRAFT_ACTIVE = "draft"
DRAFT_STARTED = "started"
DRAFT_ABANDONED = "abandoned"

# 四类创作入口（§4.3）：规划 / 章节起草 / 检查 / 定向修订。
TASK_KINDS = ("plan", "chapter_draft", "check", "targeted_revision")


@dataclass(frozen=True)
class ProjectView:
    """项目只读视图：缺失项计算的唯一事实来源；不含任何写能力。"""

    model_configured: bool
    has_accepted_beats: bool
    chapter_ids: frozenset[str] = frozenset()
    head: str | None = None


def _missing(code: str, where: str, why: str, fix: str) -> dict[str, str]:
    return {"code": code, "where": where, "why": why, "fix": fix}


def missing_items(
    task_kind: str,
    *,
    goal: str,
    target_ids: list[str],
    base_version: str | None,
    view: ProjectView,
) -> list[dict[str, str]]:
    """按任务类型计算缺失项；空列表 = 「开始」可用的充分条件。"""
    items: list[dict[str, str]] = []
    if not view.model_configured:
        items.append(
            _missing(
                ec.TASK_MODEL_NOT_CONFIGURED,
                "task.model",
                "当前没有可用模型，任务无法执行",
                "打开设置→模型接入，连接厂商或本地服务并选择当前模型",
            )
        )
    if task_kind == "plan":
        return items  # 规划不需要前置内容：空项目也能讨论构想。
    if view.head is None:
        items.append(
            _missing(
                "TASK_NO_BASE",
                "task.base_version",
                "项目还没有任何提交，没有可依据的内容版本",
                "先在创作台导入或保存一章正文，再准备这个任务",
            )
        )
    if task_kind == "chapter_draft":
        if not target_ids:
            items.append(
                _missing(
                    "TASK_NO_BEAT_TARGET",
                    "task.target_ids",
                    "尚未选择本章要写的节拍",
                    "在任务卡「调整」中选择已接受节拍后保存",
                )
            )
        if not view.has_accepted_beats:
            items.append(
                _missing(
                    "TASK_NO_PLAN",
                    "task.basis",
                    "没有已接受的节拍计划，直接起草容易脱离整体结构",
                    "先运行「规划故事与节拍」并在审阅页接受其候选",
                )
            )
        if not goal:
            items.append(
                _missing(
                    "TASK_NO_CHAPTER_TARGET",
                    "task.goal",
                    "没有指定要起草的章节",
                    "在创作台选择目标章节后，重新准备或调整这个任务",
                )
            )
    if task_kind == "check" and not target_ids:
        items.append(
            _missing(
                "TASK_NO_TARGETS",
                "task.target_ids",
                "没有选择要检查的对象（人物、线索或章节）",
                "在任务卡调整目标，把要检查的对象加进范围",
            )
        )
    if task_kind == "targeted_revision":
        if not base_version:
            items.append(
                _missing(
                    "TASK_NO_BASE_VERSION",
                    "task.base_version",
                    "定向修订需要可靠的基础版本，未保存摘录不能作为 Patch 依据（§3.4）",
                    "先保存正文，或从变更中心发起修订流程",
                )
            )
        if not target_ids:
            items.append(
                _missing(
                    "TASK_NO_TARGETS",
                    "task.target_ids",
                    "没有选择要修订的对象",
                    "在任务卡调整目标，把要修订的设定或章节加进范围",
                )
            )
    return items


@dataclass(frozen=True)
class TaskDraft:
    """受限任务建议：类型、指令、目标、基础版本、上下文、缺失项。

    存储体含项目/会话归属与状态；``to_payload`` 输出与前端契约
    （``frontend/src/api/chat.ts`` TaskDraftPayload）逐字对应的形状。
    """

    draft_id: str
    project_id: str
    conversation_id: str
    message_id: str
    revision: int
    task_kind: str
    instruction: str
    goal: str = ""
    target_ids: list[str] = field(default_factory=list)
    base_version: str | None = None
    refs: list[dict[str, Any]] = field(default_factory=list)
    basis: list[str] = field(default_factory=list)
    missing: list[dict[str, str]] = field(default_factory=list)
    source_message_id: str | None = None
    job_ref: str | None = None
    status: str = DRAFT_ACTIVE
    # 内部字段：启动幂等键、执行绑定快照、投影状态；不进前端 payload。
    start_key: str | None = None
    candidate_message_id: str | None = None
    job_settled: bool = False
    model_binding: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "revision": self.revision,
            "task_kind": self.task_kind,
            "instruction": self.instruction,
            "goal": self.goal,
            "target_ids": list(self.target_ids),
            "base_version": self.base_version,
            "refs": [dict(r) for r in self.refs],
            "basis": list(self.basis),
            "missing": [dict(x) for x in self.missing],
            "source_message_id": self.source_message_id,
            "job_ref": self.job_ref,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "project_id": self.project_id,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "revision": self.revision,
            "task_kind": self.task_kind,
            "instruction": self.instruction,
            "goal": self.goal,
            "target_ids": list(self.target_ids),
            "base_version": self.base_version,
            "refs": [dict(r) for r in self.refs],
            "basis": list(self.basis),
            "missing": [dict(x) for x in self.missing],
            "source_message_id": self.source_message_id,
            "job_ref": self.job_ref,
            "status": self.status,
            "start_key": self.start_key,
            "candidate_message_id": self.candidate_message_id,
            "job_settled": self.job_settled,
            "model_binding": dict(self.model_binding) if self.model_binding else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskDraft:
        kind = str(data["task_kind"])
        if kind not in TASK_KINDS:
            raise ValueError(f"未知任务类型 {kind!r}；使用 {TASK_KINDS} 之一")
        status = str(data.get("status", DRAFT_ACTIVE))
        if status not in (DRAFT_ACTIVE, DRAFT_STARTED, DRAFT_ABANDONED):
            raise ValueError(f"未知任务状态 {status!r}")
        return cls(
            draft_id=str(data["draft_id"]),
            project_id=str(data["project_id"]),
            conversation_id=str(data["conversation_id"]),
            message_id=str(data["message_id"]),
            revision=int(data["revision"]),
            task_kind=kind,
            instruction=str(data.get("instruction", "")),
            goal=str(data.get("goal", "")),
            target_ids=[str(t) for t in data.get("target_ids", [])],
            base_version=None if data.get("base_version") is None else str(data["base_version"]),
            refs=[dict(r) for r in data.get("refs", [])],
            basis=[str(b) for b in data.get("basis", [])],
            missing=[dict(x) for x in data.get("missing", [])],
            source_message_id=None
            if data.get("source_message_id") is None
            else str(data["source_message_id"]),
            job_ref=None if data.get("job_ref") is None else str(data["job_ref"]),
            status=status,
            start_key=None if data.get("start_key") is None else str(data["start_key"]),
            candidate_message_id=None
            if data.get("candidate_message_id") is None
            else str(data["candidate_message_id"]),
            job_settled=bool(data.get("job_settled", False)),
            model_binding=dict(data["model_binding"]) if data.get("model_binding") else None,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )
