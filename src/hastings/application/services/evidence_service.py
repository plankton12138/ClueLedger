"""正文证据查询与人工重新关联（V03-2.5 / M05、M11）。

核对本身是确定性的（V03-2.2 的纯函数）：原文可证明不变则重锚有效，否则
标为 ``needs_check`` 并保留旧依据。本服务只补两件事：

1. **查询**：按章列出证据及状态，供作者在正文旁查看「哪些依据需要核对」；
2. **人工重新关联**：作者给出仍在当前正文里的引用片段，更新关联并恢复
   ``valid``——只改关联，不改真相，也不调用模型。

重新关联仍走 ``OperationService``：证据是版本化 Artifact，修改产生一次
操作记录，可按 ``operation_id`` 回看；旧依据写进 ``previous``。
"""

from __future__ import annotations

from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.services.commit_service import CommitService
from hastings.application.services.operation_service import OperationService
from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.evidence import (
    EVIDENCE_STATUS_NEEDS_CHECK,
    EVIDENCE_STATUS_VALID,
    KIND_EVIDENCE_LINK,
    EvidenceLink,
)
from hastings.domain.repository.ids import ArtifactId, ProjectId
from hastings.domain.repository.requests import ArtifactChange
from hastings.domain.repository.serialization import content_hash

__all__ = ["EvidenceService"]


class EvidenceService:
    """证据只读列表与人工重锚（不生成证据，不判断语义）。"""

    def __init__(self, repo: CommitService, operations: OperationService) -> None:
        self._repo = repo
        self._operations = operations

    def list(
        self,
        project_id: str,
        *,
        chapter_id: str | None = None,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        """证据列表，可按章（``chapter_id``）或按被锚定的账本对象（``target_id``）过滤。

        ``target_id`` 收窄到某条账本对象（设定/披露/认知）在正文中的全部依据——供
        图谱节点「来源定位」回读，不改变证据语义，也不新增可写事实。
        """
        pid = ProjectId(project_id)
        branch = self._repo.get_branch(pid, "main")
        head = branch.head_commit_id if branch else None
        if head is None:
            return {"chapter_id": chapter_id, "head_commit_id": None, "evidence": []}
        snapshot = self._repo.get_snapshot(pid, head)
        items: list[dict[str, Any]] = []
        for view in snapshot.artifacts.values():
            if view.artifact.kind != KIND_EVIDENCE_LINK or view.revision is None:
                continue
            link = EvidenceLink.from_wire(view.revision.content)
            if chapter_id is not None and link.chapter_id != chapter_id:
                continue
            if target_id is not None and link.target_id != target_id:
                continue
            items.append({"artifact_id": view.artifact.id.value, **link.to_wire()})
        # 需要核对的排在前面：这是作者真正要处理的部分。
        items.sort(
            key=lambda item: (
                item["status"] != EVIDENCE_STATUS_NEEDS_CHECK,
                item["evidence_id"],
            )
        )
        return {
            "chapter_id": chapter_id,
            "head_commit_id": head.value,
            "evidence": items,
        }

    def reanchor(
        self,
        project_id: str,
        evidence_id: str,
        *,
        quote: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """人工重新关联：引用片段必须在当前正文里可定位，否则拒绝。"""
        pid = ProjectId(project_id)
        branch = self._repo.get_branch(pid, "main")
        head = branch.head_commit_id if branch else None
        if head is None:
            raise _violation(
                ec.EVIDENCE_NOT_FOUND,
                f"evidence.reanchor({evidence_id})",
                "作品还没有任何正文版本",
                "先导入或保存正文，再核对证据",
            )
        snapshot = self._repo.get_snapshot(pid, head)
        artifact_id, view = _find(snapshot, evidence_id)
        assert view.revision is not None
        link = EvidenceLink.from_wire(view.revision.content)
        catalog = ManuscriptCatalog.from_snapshot(snapshot)
        if catalog.chapter_entry(link.chapter_id) is None:
            raise _violation(
                ec.EVIDENCE_QUOTE_MISSING,
                f"evidence.reanchor({evidence_id})",
                "来源章已不存在",
                "该证据无法重新关联；在账本中移除或重新建立来源",
            )
        text = "\n".join(unit.model().text for unit in catalog.units_of(link.chapter_id))
        new_quote = (quote if quote is not None else link.quote).strip()
        if not new_quote or new_quote not in text:
            raise _violation(
                ec.EVIDENCE_QUOTE_MISSING,
                f"evidence.reanchor({evidence_id})",
                "给出的引用片段在当前正文中找不到",
                "从当前正文中复制一段仍然存在的原文，或保留待核对状态",
            )
        updated = EvidenceLink(
            evidence_id=link.evidence_id,
            anchor_kind=link.anchor_kind,
            target_id=link.target_id,
            chapter_id=link.chapter_id,
            quote=new_quote,
            source_hash=content_hash(text),
            dependencies=link.dependencies,
            status=EVIDENCE_STATUS_VALID,
            reason="",
            previous=_previous(link),
        )
        op_id = operation_id or (
            "op_"
            + content_hash(["evidence.reanchor", project_id, evidence_id, new_quote])[:16]
        )
        envelope = self._operations.submit(
            project_id=project_id,
            operation_id=op_id,
            kind="evidence.reanchor",
            changes=(
                ArtifactChange(
                    artifact_id=ArtifactId(artifact_id),
                    content=updated.to_wire(),
                    old_revision_id=view.revision.id,
                ),
            ),
            summary=f"重新关联证据 {evidence_id}",
            refs={"evidence_id": evidence_id, "chapter_id": link.chapter_id},
            changed_chapter_ids=(),
        )
        return {
            "status": envelope.status,
            "operation_id": envelope.operation_id,
            "commit_id": envelope.commit_id,
            "evidence": updated.to_wire(),
        }


def _find(snapshot: Any, evidence_id: str) -> tuple[str, Any]:
    for view in snapshot.artifacts.values():
        if view.artifact.kind != KIND_EVIDENCE_LINK or view.revision is None:
            continue
        if str(view.revision.content.get("evidence_id")) == evidence_id:
            return view.artifact.id.value, view
    raise _violation(
        ec.EVIDENCE_NOT_FOUND,
        f"evidence({evidence_id})",
        "没有这条证据",
        "刷新账本后重试；证据 ID 来自账本条目，不是章 ID",
    )


def _previous(link: EvidenceLink) -> dict[str, Any]:
    """旧依据：保留最近一次关联（不含更早的 previous，避免无限嵌套）。"""
    wire = link.to_wire()
    wire.pop("previous", None)
    return wire


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)
