"""正文证据与摘要状态（V03-2.2，计划 §3.3 / M05）。

设计目的：新正文生效的瞬间，依赖旧文字的「已出现、已披露、已知」不能继续
被当作当前证据。核对是**确定性**的：原文和依赖可证明不变则保留有效；
无法证明时保守标记 ``needs_check``，不调用模型猜测。

存储（计划 §4.2）：证据关联与摘要状态按 Artifact/Revision 建模，与正文
变化在**同一 CommitRequest** 原子生效。kind 采用 ``evidence.``/``summary.``
前缀——刻意**不**放进 ``manuscript.`` 命名空间：``ManuscriptCatalog`` 按
``manuscript.`` 前缀收集章与段落对象，证据/摘要是派生资料，混进目录会
污染领域命令的遍历范围。

本模块只有纯函数与内容形状：读快照、算新状态、产出 ArtifactChange 由
发布器（publish_manuscript_change）负责，这里不做任何 I/O。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.errors import violation
from hastings.domain.repository.serialization import content_hash

__all__ = [
    "EVIDENCE_STATUS_NEEDS_CHECK",
    "EVIDENCE_STATUS_VALID",
    "KIND_BOOK_OVERVIEW",
    "KIND_CHAPTER_SUMMARY",
    "KIND_EVIDENCE_LINK",
    "SUMMARY_STATUS_FRESH",
    "SUMMARY_STATUS_STALE",
    "EvidenceLink",
    "ChapterSummaryState",
    "evidence_after_change",
]

#: Artifact kind：正文证据关联（派生资料，非正文/账本事实）。
KIND_EVIDENCE_LINK = "evidence.link"
#: Artifact kind：单章摘要（来源集合有变则失效）。
KIND_CHAPTER_SUMMARY = "summary.chapter"
#: Artifact kind：全书概览（任一章变化即失效）。
KIND_BOOK_OVERVIEW = "summary.overview"

EVIDENCE_STATUS_VALID = "valid"
EVIDENCE_STATUS_NEEDS_CHECK = "needs_check"

SUMMARY_STATUS_FRESH = "fresh"
SUMMARY_STATUS_STALE = "stale"

_ANCHOR_KINDS = frozenset(("setting", "disclosure", "cognition"))


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    """一条正文证据（Artifact content 的强类型视图）。

    - ``anchor_kind``：证据锚定的对象类型——设定 / 读者披露 / 角色认知（I09）；
    - ``target_id``：被锚定的账本 Artifact ID；
    - ``chapter_id``：来源章（Artifact ID，即章对象身份）；
    - ``quote``：来源原文片段——重锚定的确定性依据（仍在原文中即可重锚）；
    - ``source_hash``：来源章全文内容哈希（生效版本的指纹）；
    - ``dependencies``：关系依赖（账本 Artifact ID）；依赖对象变化同样触发核对；
    - ``previous``：前次依据（needs_check 时保留，人工重新关联可恢复）。
    """

    evidence_id: str
    anchor_kind: str
    target_id: str
    chapter_id: str
    quote: str
    source_hash: str
    dependencies: tuple[str, ...] = ()
    status: str = EVIDENCE_STATUS_VALID
    reason: str = ""
    previous: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        where = f"evidence_link({self.evidence_id})"
        if not self.evidence_id.startswith("evd_") or self.evidence_id.strip() != self.evidence_id:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                where,
                f"证据 ID {self.evidence_id!r} 必须是 evd_ 前缀的稳定 ID",
                "使用 evd_<后缀> 形式；证据身份创建后不可变",
            )
        if self.anchor_kind not in _ANCHOR_KINDS:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"{where}.anchor_kind",
                f"锚定类型 {self.anchor_kind!r} 非法（应为设定/披露/认知之一）",
                "使用 setting/disclosure/cognition；真相、披露与认知分别保存（I09）",
            )
        if self.status not in (EVIDENCE_STATUS_VALID, EVIDENCE_STATUS_NEEDS_CHECK):
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"{where}.status",
                f"证据状态 {self.status!r} 非法",
                "状态只有 valid/needs_check；失效不是独立状态，needs_check 即失效待核对",
            )

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "evidence_id": self.evidence_id,
            "anchor_kind": self.anchor_kind,
            "target_id": self.target_id,
            "chapter_id": self.chapter_id,
            "quote": self.quote,
            "source_hash": self.source_hash,
            "dependencies": list(self.dependencies),
            "status": self.status,
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.previous is not None:
            payload["previous"] = self.previous
        return payload

    @classmethod
    def from_wire(cls, payload: Any) -> EvidenceLink:
        if not isinstance(payload, dict):
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                "evidence_link.from_wire",
                f"证据负载不是对象：{type(payload).__name__}",
                "传入 to_wire 产出的对象",
            )
        missing = [
            f
            for f in ("evidence_id", "anchor_kind", "target_id", "chapter_id", "source_hash")
            if f not in payload
        ]
        if missing:
            raise violation(
                ec.MANUSCRIPT_MISSING_REQUIRED_FIELD,
                "evidence_link.from_wire",
                f"证据缺少必填字段 {missing}",
                "证据由发布器核对产出，不接受缺字段的手工负载",
            )
        return cls(
            evidence_id=str(payload["evidence_id"]),
            anchor_kind=str(payload["anchor_kind"]),
            target_id=str(payload["target_id"]),
            chapter_id=str(payload["chapter_id"]),
            quote=str(payload.get("quote", "")),
            source_hash=str(payload["source_hash"]),
            dependencies=tuple(str(d) for d in payload.get("dependencies", ())),
            status=str(payload.get("status", EVIDENCE_STATUS_VALID)),
            reason=str(payload.get("reason", "")),
            previous=payload.get("previous"),
        )


@dataclass(frozen=True, slots=True)
class ChapterSummaryState:
    """单章摘要的状态视图（Artifact content 的强类型视图）。

    V03-2.2 只负责**状态裁决**：来源正文变化 ⇒ fresh → stale（保留内容与来源清单
    供回看）。V03-4.3 起这段内容里还有摘要正文与来源清单——失效改写必须把它们
    原样带过去，否则一次保存就会把模型写好的摘要擦掉。

    ``extra`` 保留未识别字段（向前兼容）：旧摘要只有前五个字段，新摘要多出
    ``text``/``sources`` 等；两者都要能读，且改写后不丢。
    """

    summary_id: str
    chapter_id: str
    source_hash: str
    status: str
    reason: str = ""
    text: str = ""
    sources: dict[str, Any] | None = None
    model: str = ""
    generated_at: str = ""
    task_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra", dict(self.extra))
        if self.sources is not None:
            object.__setattr__(self, "sources", dict(self.sources))

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            **self.extra,
            "summary_id": self.summary_id,
            "chapter_id": self.chapter_id,
            "source_hash": self.source_hash,
            "status": self.status,
            "reason": self.reason,
        }
        if self.text:
            payload["text"] = self.text
        if self.sources:
            payload["sources"] = dict(self.sources)
        if self.model:
            payload["model"] = self.model
        if self.generated_at:
            payload["generated_at"] = self.generated_at
        if self.task_id:
            payload["task_id"] = self.task_id
        return payload

    @classmethod
    def from_wire(cls, payload: Any) -> ChapterSummaryState:
        if not isinstance(payload, dict):
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                "chapter_summary.from_wire",
                f"摘要负载不是对象：{type(payload).__name__}",
                "传入 to_wire 产出的对象",
            )
        missing = [
            f for f in ("summary_id", "chapter_id", "source_hash", "status") if f not in payload
        ]
        if missing:
            raise violation(
                ec.MANUSCRIPT_MISSING_REQUIRED_FIELD,
                "chapter_summary.from_wire",
                f"摘要缺少必填字段 {missing}",
                "摘要由摘要任务/发布器产出，不接受缺字段的手工负载",
            )
        known = {
            "summary_id",
            "chapter_id",
            "source_hash",
            "status",
            "reason",
            "text",
            "sources",
            "model",
            "generated_at",
            "task_id",
        }
        sources = payload.get("sources")
        return cls(
            summary_id=str(payload["summary_id"]),
            chapter_id=str(payload["chapter_id"]),
            source_hash=str(payload["source_hash"]),
            status=str(payload["status"]),
            reason=str(payload.get("reason", "")),
            text=str(payload.get("text", "")),
            sources=dict(sources) if isinstance(sources, dict) else None,
            model=str(payload.get("model", "")),
            generated_at=str(payload.get("generated_at", "")),
            task_id=str(payload.get("task_id", "")),
            extra={k: v for k, v in payload.items() if k not in known},
        )


def _require_prefixed(value: str, prefix: str) -> str:
    if not value.startswith(f"{prefix}_"):
        raise violation(
            ec.MANUSCRIPT_INVALID_OPERATION,
            "evidence_after_change",
            f"ID {value!r} 缺少 {prefix}_ 前缀",
            "使用对应生成器产出的 ID",
        )
    return value


def evidence_after_change(
    link: EvidenceLink,
    *,
    changed_artifact_ids: frozenset[str],
    chapter_text_after: dict[str, str],
) -> EvidenceLink:
    """确定性核对（计划 M05 示例的实现）。

    - 来源章与依赖都不在本次变化集合里 → 原样保留（无关变化不全量失效）；
    - 依赖对象变化但来源原文可证明未变（quote 仍在新章文本中）→ 重锚有效
      （更新 source_hash 到新章文本指纹）；
    - 来源原文已变且无法定位原片段 → ``needs_check``（保留 previous 供人工
      重新关联），不猜测新位置。

    ``chapter_text_after``：chapter_id → 变化后的章全文（由发布器用 catalog
    与变化集合拼装；未变化的章不要求出现在其中）。
    """
    _require_prefixed(link.evidence_id, "evd")
    chapter_changed = link.chapter_id in changed_artifact_ids
    dependency_changed = any(d in changed_artifact_ids for d in link.dependencies)
    if not chapter_changed and not dependency_changed:
        return link  # 无关变化：不失效
    if not chapter_changed:
        # 依赖变了但来源章没变：引用的原文仍有效，语义依赖由建议确认（模型
        # 提出事实变化仍走建议，M05）——标记待核对但不销毁来源。
        return _needs_check(link, "依赖对象已变化，需确认语义是否仍然成立")
    new_text = chapter_text_after.get(link.chapter_id)
    if new_text is None:
        return _needs_check(link, "来源章在本次变化中被删除")
    if link.quote and link.quote in new_text:
        # 确定性映射成立：原片段在新全文中原样可定位 → 重锚有效。
        return EvidenceLink(
            evidence_id=link.evidence_id,
            anchor_kind=link.anchor_kind,
            target_id=link.target_id,
            chapter_id=link.chapter_id,
            quote=link.quote,
            source_hash=content_hash(new_text),
            dependencies=link.dependencies,
            status=EVIDENCE_STATUS_VALID,
            reason="",
            previous=link.previous,
        )
    return _needs_check(link, "来源已改变或无法可靠定位")


def _needs_check(link: EvidenceLink, reason: str) -> EvidenceLink:
    previous = link.previous
    if link.status == EVIDENCE_STATUS_VALID:
        # 首次失效：保留前次依据（人工重新关联可恢复，M05）。
        previous = {
            "source_hash": link.source_hash,
            "status": link.status,
            "quote": link.quote,
        }
    return EvidenceLink(
        evidence_id=link.evidence_id,
        anchor_kind=link.anchor_kind,
        target_id=link.target_id,
        chapter_id=link.chapter_id,
        quote=link.quote,
        source_hash=link.source_hash,
        dependencies=link.dependencies,
        status=EVIDENCE_STATUS_NEEDS_CHECK,
        reason=reason,
        previous=previous,
    )


def summary_after_change(
    state: ChapterSummaryState,
    *,
    changed_chapter_ids: frozenset[str],
    operation_id: str,
) -> ChapterSummaryState:
    """摘要失效裁决：来源章变化 ⇒ fresh → stale；已 stale 保持（不重复记因）。"""
    _require_prefixed(state.summary_id, "sum")
    if state.chapter_id not in changed_chapter_ids:
        return state  # 无关章不失效
    if state.status != SUMMARY_STATUS_FRESH:
        return state  # 已过期：保持，不重复记因
    return ChapterSummaryState(
        summary_id=state.summary_id,
        chapter_id=state.chapter_id,
        source_hash=state.source_hash,
        status=SUMMARY_STATUS_STALE,
        reason=f"来源正文已变化（操作 {operation_id}）",
        # 失效只改状态与原因：正文、来源清单、模型与生成时间原样保留，
        # 作者仍能回看上次摘要写了什么（计划 §4.3 摘要状态表）。
        text=state.text,
        sources=state.sources,
        model=state.model,
        generated_at=state.generated_at,
        task_id=state.task_id,
        extra=state.extra,
    )
