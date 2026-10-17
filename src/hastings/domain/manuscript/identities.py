"""0.3 稿件身份冻结（V03-0.2）：四类身份的可辨识联合与 ID 前缀。

计划 §4.1：副本、任务建议、摘要、账本更新建议分别使用不同类型和 ID 前缀；
禁止一个 ``draft`` 字段同时指代四种内容（不变量 I01：正式稿、修改副本、
候选、任务快照身份不同；API 引用带类型，查询/写入拒绝混用）。

本模块只冻结**身份与引用规则**，不实现各记录的存储（副本表、回执、
操作记录分别在 V03-0.5 / V03-2.1 落地）：

- 正式稿（formal）沿用仓库 Artifact/Revision/Commit，前缀 ``art``/``rev``/``cmt``；
- 修改副本（edit_draft）是独立持久工作数据，会话前缀 ``eds``、副本章节 ``edc``；
- 候选（candidate）是模型提案，前缀 ``cnd``；
- 任务快照（task_snapshot）是不可变任务输入，前缀 ``tks``。

ID 是稳定主键不是显示名：创建后不可变，校验规则沿用
``domain/repository/ids.py``（非空、无首尾空白、无路径分隔符）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.errors import violation

__all__ = [
    "ID_PREFIXES",
    "ManuscriptKind",
    "ManuscriptRef",
    "OperationEnvelope",
    "kind_of_id",
    "reject_mixed_kinds",
]

# ---------------------------------------------------------------------------
# 四类身份与 ID 前缀
# ---------------------------------------------------------------------------


class ManuscriptKind(StrEnum):
    """稿件四类身份（计划 §4.1、不变量 I01）。"""

    FORMAL = "formal"  # 正式稿：仓库谱系中的当前正文
    EDIT_DRAFT = "edit_draft"  # 修改副本：作者手工修改的工作数据
    CANDIDATE = "candidate"  # 候选：模型提案，采用前不是正文
    TASK_SNAPSHOT = "task_snapshot"  # 任务快照：不可变任务输入


#: 每类身份允许的 ID 前缀。查询/写入按此前缀拒绝混用（I01）。
ID_PREFIXES: dict[ManuscriptKind, tuple[str, ...]] = {
    ManuscriptKind.FORMAL: ("art", "rev", "cmt"),
    ManuscriptKind.EDIT_DRAFT: ("eds", "edc"),
    ManuscriptKind.CANDIDATE: ("cnd",),
    ManuscriptKind.TASK_SNAPSHOT: ("tks",),
}

#: 0.3 新增 ID 前缀总表（既有前缀一并登记，便于盘点）。
V03_ID_PREFIXES: dict[str, str] = {
    "eds": "修改副本会话 EditSession",
    "edc": "副本章节 DraftChapter",
    "cnd": "候选 Candidate",
    "tks": "任务快照 ContextSnapshot",
    "op": "操作记录 OperationRecord",
    "prj": "作品 Project（既有）",
    "br": "分支 Branch（既有）",
    "art": "可版本化对象 Artifact（既有）",
    "rev": "不可变版本 Revision（既有）",
    "cmt": "原子提交 Commit（既有）",
    "rel": "关系 Relation（既有）",
    "evt": "事件 Event（既有）",
    "job": "任务 Job（既有）",
    "run": "运行 attempt（既有）",
    "conv": "讨论 Conversation（既有）",
    "msg": "消息 Message（既有）",
    "turn": "轮次 Turn（既有）",
    "draft": "任务草稿 TaskDraft（既有）",
}


def _prefix_of(value: str) -> str:
    head, sep, _ = value.partition("_")
    return head if sep else ""


def kind_of_id(value: str) -> ManuscriptKind | None:
    """按 ID 前缀推断身份；无法识别返回 None（调用方决定拒绝还是忽略）。"""
    prefix = _prefix_of(value)
    for kind, prefixes in ID_PREFIXES.items():
        if prefix in prefixes:
            return kind
    return None


# ---------------------------------------------------------------------------
# 可辨识联合：引用带类型，拒绝混用
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ManuscriptRef:
    """一次对稿件的引用：身份 + ID + 该类身份必需的版本锚点。

    - ``formal`` 必须带 ``commit_id``（读请求明确 at_commit_id，计划 §6.1）；
    - ``edit_draft`` 必须带 ``save_seq``（模型引用冻结具体序号，计划 M04）；
    - ``candidate`` 必须带 ``content_hash``（采用携带预览版本及哈希，计划 M03）；
    - ``task_snapshot`` 必须带 ``context_version``（与 Job 绑定的不可变输入）。

    锚点字段按身份互斥：正式稿引用不得携带副本序号，反之亦然——这是 I01
    在 wire 层的硬约束，不依赖调用方自觉。
    """

    kind: ManuscriptKind
    id: str
    commit_id: str | None = None
    save_seq: int | None = None
    content_hash: str | None = None
    context_version: int | None = None

    def __post_init__(self) -> None:
        where = f"manuscript_ref({self.id})"
        if not self.id or self.id.strip() != self.id:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                where,
                f"引用 ID {self.id!r} 为空或含首尾空白",
                "使用生成器产出的 <prefix>_<后缀> 形式",
            )
        prefixes = ID_PREFIXES[self.kind]
        if _prefix_of(self.id) not in prefixes:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                where,
                f"ID 前缀与身份不符：{self.kind.value} 只接受 {prefixes} 前缀",
                "为该身份使用正确前缀的 ID；四类身份禁止混用（I01）",
                kind=self.kind.value,
                id=self.id,
                allowed_prefixes=list(prefixes),
            )
        required = {
            ManuscriptKind.FORMAL: ("commit_id",),
            ManuscriptKind.EDIT_DRAFT: ("save_seq",),
            ManuscriptKind.CANDIDATE: ("content_hash",),
            ManuscriptKind.TASK_SNAPSHOT: ("context_version",),
        }[self.kind]
        forbidden = {"commit_id", "save_seq", "content_hash", "context_version"} - set(required)
        for field in required:
            if getattr(self, field) is None:
                raise violation(
                    ec.MANUSCRIPT_MISSING_REQUIRED_FIELD,
                    f"{where}.{field}",
                    f"{self.kind.value} 引用缺少锚点字段 {field}",
                    f"补充 {field}；{self.kind.value} 的版本锚点不可省略",
                    kind=self.kind.value,
                )
        for field in forbidden:
            if getattr(self, field) is not None:
                raise violation(
                    ec.MANUSCRIPT_INVALID_OPERATION,
                    f"{where}.{field}",
                    f"{self.kind.value} 引用不得携带 {field}（身份互斥，I01）",
                    f"只保留 {list(required)} 字段；跨身份引用请构造对应类型的 ManuscriptRef",
                    kind=self.kind.value,
                    field=field,
                )

    # -- 类型守卫（计划 V03-0.2：补类型守卫测试） --------------------------

    @property
    def is_formal(self) -> bool:
        return self.kind is ManuscriptKind.FORMAL

    @property
    def is_edit_draft(self) -> bool:
        return self.kind is ManuscriptKind.EDIT_DRAFT

    @property
    def is_candidate(self) -> bool:
        return self.kind is ManuscriptKind.CANDIDATE

    @property
    def is_task_snapshot(self) -> bool:
        return self.kind is ManuscriptKind.TASK_SNAPSHOT

    # -- wire 往返（前后端同一契约，snake_case 与 chat.ts 既有约定一致） ----

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind.value, "id": self.id}
        if self.commit_id is not None:
            payload["commit_id"] = self.commit_id
        if self.save_seq is not None:
            payload["save_seq"] = self.save_seq
        if self.content_hash is not None:
            payload["content_hash"] = self.content_hash
        if self.context_version is not None:
            payload["context_version"] = self.context_version
        return payload

    @classmethod
    def from_wire(cls, payload: Any) -> ManuscriptRef:
        if not isinstance(payload, dict):
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                "manuscript_ref.from_wire",
                f"引用负载不是对象：{type(payload).__name__}",
                "传入含 kind/id 与对应锚点字段的对象",
            )
        try:
            kind = ManuscriptKind(payload["kind"])
        except KeyError as exc:
            raise violation(
                ec.MANUSCRIPT_MISSING_REQUIRED_FIELD,
                "manuscript_ref.from_wire.kind",
                "引用负载缺少 kind",
                "补充 kind：formal/edit_draft/candidate/task_snapshot 之一",
            ) from exc
        except ValueError as exc:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                "manuscript_ref.from_wire.kind",
                f"未知身份 {payload['kind']!r}",
                "kind 只接受 formal/edit_draft/candidate/task_snapshot",
            ) from exc
        if "id" not in payload:
            raise violation(
                ec.MANUSCRIPT_MISSING_REQUIRED_FIELD,
                "manuscript_ref.from_wire.id",
                "引用负载缺少 id",
                "补充带正确前缀的 ID",
            )
        return cls(
            kind=kind,
            id=payload["id"],
            commit_id=payload.get("commit_id"),
            save_seq=payload.get("save_seq"),
            content_hash=payload.get("content_hash"),
            context_version=payload.get("context_version"),
        )


def reject_mixed_kinds(left: ManuscriptRef, right: ManuscriptRef, *, where: str) -> None:
    """两类身份不得互相充当（I01）：查询/写入入口统一调用。"""
    if left.kind is not right.kind:
        raise violation(
            ec.MANUSCRIPT_INVALID_OPERATION,
            where,
            f"身份混用：{left.kind.value}({left.id}) 与 {right.kind.value}({right.id})",
            "同一操作只引用一类身份；跨类请拆成独立操作",
            left_kind=left.kind.value,
            right_kind=right.kind.value,
        )


# ---------------------------------------------------------------------------
# 统一成功响应信封（计划 §6.1 样例冻结）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OperationEnvelope:
    """用例统一返回信封；``no_change`` 不伪造 commit（计划 §3.3、§6.1）。"""

    operation_id: str
    status: str  # committed / no_change / blocked / failed
    project_id: str
    conversation_id: str | None = None
    commit_id: str | None = None
    changed_chapter_ids: tuple[str, ...] = ()
    summary_state: str | None = None
    ledger_suggestions_state: str | None = None
    result_url: str | None = None

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation_id": self.operation_id,
            "status": self.status,
            "project_id": self.project_id,
            "changed_chapter_ids": list(self.changed_chapter_ids),
        }
        if self.conversation_id is not None:
            payload["conversation_id"] = self.conversation_id
        if self.commit_id is not None:
            payload["commit_id"] = self.commit_id
        if self.summary_state is not None:
            payload["summary_state"] = self.summary_state
        if self.ledger_suggestions_state is not None:
            payload["ledger_suggestions_state"] = self.ledger_suggestions_state
        if self.result_url is not None:
            payload["result_url"] = self.result_url
        return payload

    @classmethod
    def from_wire(cls, payload: Any) -> OperationEnvelope:
        if not isinstance(payload, dict):
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                "operation_envelope.from_wire",
                f"信封负载不是对象：{type(payload).__name__}",
                "传入用例返回的信封对象",
            )
        missing = [f for f in ("operation_id", "status", "project_id") if f not in payload]
        if missing:
            raise violation(
                ec.MANUSCRIPT_MISSING_REQUIRED_FIELD,
                "operation_envelope.from_wire",
                f"信封缺少必填字段 {missing}",
                "补齐 operation_id/status/project_id",
            )
        return cls(
            operation_id=payload["operation_id"],
            status=payload["status"],
            project_id=payload["project_id"],
            conversation_id=payload.get("conversation_id"),
            commit_id=payload.get("commit_id"),
            changed_chapter_ids=tuple(payload.get("changed_chapter_ids", ())),
            summary_state=payload.get("summary_state"),
            ledger_suggestions_state=payload.get("ledger_suggestions_state"),
            result_url=payload.get("result_url"),
        )
