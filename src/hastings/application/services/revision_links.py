"""整体操作与后续建议采用的因果关联（V03-7.7）。

一次整体调整发布后，作者可能基于这次结果继续采用账本建议。采用本身是**独立提交**
（有自己的 Commit 与幂等键），但它与发起它的整体操作有明确的因果关系。这里用一条
辅助记录把这条因果关系存下来：

- key 是原始 ``operation_id``；
- value 是该操作之后采用的批次（commit_id / request_id / item_ids / 时间）。

它只记录「谁引发了谁」，不复制账本内容、不建立第二份事实。影响查询用它列出关联
采用；撤销预览用它把关联账本纳入同一份检查（是否可恢复仍按各自当前版本判定）。
"""

from __future__ import annotations

from typing import Any

from hastings.application.services.commit_service import CommitService
from hastings.domain.repository.ids import ProjectId

__all__ = ["LINKS_BUCKET", "read_acceptances", "record_acceptance"]

LINKS_BUCKET = "revision_links"


def record_acceptance(
    repo: CommitService,
    project_id: str,
    source_operation_id: str,
    *,
    commit_id: str,
    request_id: str,
    item_ids: list[str],
    at: str,
) -> None:
    """记录一次「基于某整体操作采用建议」；同 request_id 重试不重复追加。"""
    pid = ProjectId(project_id)

    def mutate(current: dict[str, Any] | None) -> dict[str, Any]:
        data = dict(current) if current is not None else {}
        acceptances = [
            dict(item)
            for item in data.get("acceptances", [])
            if str(item.get("request_id", "")) != request_id
        ]
        acceptances.append(
            {
                "commit_id": commit_id,
                "request_id": request_id,
                "item_ids": list(item_ids),
                "at": at,
            }
        )
        return {"operation_id": source_operation_id, "acceptances": acceptances}

    repo.aux_mutate(pid, LINKS_BUCKET, source_operation_id, mutate)


def read_acceptances(
    repo: CommitService, project_id: str, operation_id: str
) -> list[dict[str, Any]]:
    payload = repo.aux_read(ProjectId(project_id), LINKS_BUCKET, operation_id)
    if payload is None:
        return []
    return [dict(item) for item in payload.get("acceptances", [])]
