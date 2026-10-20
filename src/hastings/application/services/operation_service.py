"""操作记录服务（V03-2.1，计划 §3.3 / §4.2）。

把一次正文提交包装成可定位的操作：

- **提交**：OperationRecord（版本化 Artifact）+ ``operation_receipts`` 回执
  （辅助表）+ ``manuscript.operation.committed`` Outbox 事件——与正文变化在
  **同一个 CommitRequest** 里原子生效（V03-0.5 的事务端口），任一写失败
  整体回滚。
- **幂等**：同 operation_id 同指纹返回原结果（不重复提交）；同键不同指纹
  显式冲突（OPERATION_KEY_CONFLICT → 409）。
- **no_change**：全部变化与基础有效版本一致时，不创建正文空版本、也不创建
  记录 Artifact——回执经引擎的 no-op 分支同事务落库（计划 §3.3 第 6 步）。
- **查询**：``get_operation`` 只依赖仓库（回执 + 记录 Artifact），不依赖
  对话投影——投影失败或未完成时结果仍可找回（§4.2）。

查询侧的状态裁决：回执存在而记录 Artifact 不存在 ⇒ no_change；记录 Artifact
存在 ⇒ committed，其引入 Commit 即操作的 after 版本。
"""

from __future__ import annotations

from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.services.commit_service import CommitService
from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.identities import OperationEnvelope
from hastings.domain.manuscript.operation_record import (
    OPERATION_EVENT_COMMITTED,
    OPERATION_RECEIPT_BUCKET,
    OPERATION_RECORD_KIND,
    OperationReceipt,
    OperationRecord,
    OperationStatus,
    creation_log_conversation_id,
)
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId, RevisionId
from hastings.domain.repository.requests import ArtifactChange, AuxWrite, CommitRequest
from hastings.domain.repository.serialization import content_hash, normalize_json

__all__ = ["OperationService"]

_AUTHOR = Actor(kind="human", id="author")


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


def _record_artifact_id(operation_id: str) -> str:
    """OperationRecord 的版本化 Artifact ID：``art_op_<后缀>``（art 前缀身份）。"""
    if not operation_id.startswith("op_") or operation_id.strip() != operation_id:
        raise _violation(
            ec.MANUSCRIPT_INVALID_OPERATION,
            "operations.record_artifact_id",
            f"操作 ID {operation_id!r} 必须是 op_ 前缀的稳定 ID",
            "使用 op_<后缀> 形式的操作 ID",
        )
    return f"art_{operation_id}"


class OperationService:
    """提交与查询操作记录；讨论投影是另一个消费者的职责（不在此类）。"""

    def __init__(self, repo: CommitService) -> None:
        self._repo = repo

    # -- 提交 --------------------------------------------------------------

    def submit(
        self,
        *,
        project_id: str,
        operation_id: str,
        kind: str,
        changes: tuple[ArtifactChange, ...] = (),
        summary: str,
        source: str = "author",
        refs: dict[str, Any] | None = None,
        events: tuple[tuple[str, dict[str, Any]], ...] = (),
        changed_chapter_ids: tuple[str, ...] = (),
        request_fingerprint: str | None = None,
        origin_conversation_id: str | None = None,
    ) -> OperationEnvelope:
        """提交一次操作：正文变化 + 记录 + 回执 + 事件，同一事务。

        ``changes`` / ``events`` 由调用方构造（V03-2.2 起由统一发布准备器产出，
        领域命令的纯函数结果原样汇入）；``changed_chapter_ids`` 是本次操作
        实际影响的章节清单（由调用方按 catalog kind 判定，不按 ID 前缀猜测）。
        本服务负责补全 old_revision_id（读基础快照）、构造记录与回执、no_change
        检测。重试语义：同 operation_id 同内容返回原结果，不同内容显式冲突。

        ``origin_conversation_id`` 是**发起这次操作的讨论**（V03-4.5）：给了就
        固定归属到它，没给就落本作品「创作记录」。归属在提交时确定，之后切换
        讨论或换作品都不改变，投影也不会投进作者当时正打开的会话。
        """
        if not operation_id.startswith("op_") or operation_id.strip() != operation_id:
            raise _violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"operations.submit({operation_id})",
                f"操作 ID {operation_id!r} 必须是 op_ 前缀的稳定 ID",
                "使用 op_<后缀> 形式的操作 ID",
            )
        pid = ProjectId(project_id)
        branch = self._repo.get_branch(pid, "main")
        if branch is None:
            raise _violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"operations.submit({operation_id})",
                f"作品 {project_id} 没有 main 分支",
                "先创建作品再提交操作",
            )
        head = branch.head_commit_id
        fingerprint = request_fingerprint or self._fingerprint(kind, changes, summary, refs, events)
        # 1) 幂等重放：回执（同键同指纹）→ 原结果；同键不同指纹 → 冲突。
        replayed = self._replay(pid, operation_id, fingerprint)
        if replayed is not None:
            return replayed
        conversation_id = origin_conversation_id or creation_log_conversation_id(project_id)
        # 2) 基础快照：补全 old_revision_id 并预检 no_change（与引擎同一口径）。
        #    乐观预检：全部变化与有效版本内容一致（或本就无变化）⇒ no_change。
        snapshot = self._repo.get_snapshot(pid, head) if head is not None else None
        prepared: list[ArtifactChange] = []
        no_change = True
        for change in changes:
            view = snapshot.artifacts.get(change.artifact_id) if snapshot else None
            current_id: RevisionId | None = view.revision.id if view and view.revision else None
            declared = change.old_revision_id
            old_id: RevisionId | None
            if declared is not None:
                # 调用方自带的并发条件（准备这次修改时的基础版本）**原样贯穿到提交**。
                # 早先这里用当前有效版本替换 declared，于是「准备之后别人保存的新正文」
                # 会被静默覆盖——CAS 形同不存在。现在 declared 与当前版本不一致即显式
                # 冲突：没动过的对象不受影响（一次无关保存不阻断本次发布），动过的
                # 对象必须重新准备。
                if current_id is None:
                    raise _violation(
                        ec.MANUSCRIPT_INVALID_OPERATION,
                        f"operations.submit({operation_id})",
                        f"对象 {change.artifact_id.value} 已不存在却声明了 old_revision",
                        "新对象不携带 old_revision；对象不会被删除，请核对 ID",
                    )
                if declared != current_id:
                    raise _violation(
                        ec.MANUSCRIPT_BASE_CONFLICT,
                        f"operations.submit({operation_id})",
                        f"对象 {change.artifact_id.value} 的准备版本已过期："
                        f"声明 {declared}，当前 {current_id}（准备之后已被修改）",
                        "基于最新版本重新准备这次修改；系统不会用旧准备覆盖新内容",
                        artifact_id=change.artifact_id.value,
                        expected=str(declared),
                        current=str(current_id),
                    )
                old_id = declared
            else:
                old_id = current_id
            same = (
                view is not None
                and view.revision is not None
                and view.revision.content == normalize_json(change.content)
            )
            if not same:
                no_change = False
            prepared.append(
                ArtifactChange(
                    artifact_id=change.artifact_id,
                    content=change.content,
                    kind=change.kind if old_id is None else None,
                    old_revision_id=old_id,
                )
            )
        receipt_write = AuxWrite(
            OPERATION_RECEIPT_BUCKET,
            operation_id,
            OperationReceipt(
                operation_id=operation_id,
                request_fingerprint=fingerprint,
                kind=kind,
                project_id=project_id,
                conversation_id=conversation_id,
                source=source,
                before_commit_id=head.value if head is not None else None,
            ).to_wire(),
        )

        def build_request(
            changes: tuple[ArtifactChange, ...],
            request_events: tuple[tuple[str, dict[str, Any]], ...],
        ) -> CommitRequest:
            return CommitRequest(
                project_id=pid,
                branch_id=branch.id,
                expected_head=head,
                actor=_AUTHOR,
                idempotency_key=f"operation:{operation_id}",
                summary=summary,
                changes=changes,
                events=request_events,
                aux_writes=(receipt_write,),
            )

        if no_change:
            # 3a) 无差异：只提交回执——引擎 no-op 分支同事务落库，不创建正文
            #     空版本、不创建记录 Artifact、事件不发出（引擎 no-op 分支）。
            result = self._repo.commit(build_request((), ()))
            return OperationEnvelope(
                operation_id=operation_id,
                status=result.status,
                project_id=project_id,
                conversation_id=conversation_id,
                commit_id=None,
                changed_chapter_ids=(),
            )
        # 3b) 正文变化 + 记录 Artifact + 事件同事务提交。
        record = OperationRecord(
            operation_id=operation_id,
            request_fingerprint=fingerprint,
            kind=kind,
            project_id=project_id,
            conversation_id=conversation_id,
            source=source,
            before_commit_id=head.value if head is not None else None,
            changed_chapter_ids=changed_chapter_ids,
            status=OperationStatus.COMMITTED,
            refs=refs or {},
        )
        operation_events: tuple[tuple[str, dict[str, Any]], ...] = (
            *events,
            (
                OPERATION_EVENT_COMMITTED,
                {
                    "operation_id": operation_id,
                    "project_id": project_id,
                    "conversation_id": conversation_id,
                    "kind": kind,
                    "changed_chapter_ids": list(changed_chapter_ids),
                },
            ),
        )
        result = self._repo.commit(
            build_request(
                (
                    *prepared,
                    ArtifactChange(
                        artifact_id=ArtifactId(_record_artifact_id(operation_id)),
                        content=record.to_wire(),
                        kind=OPERATION_RECORD_KIND,
                    ),
                ),
                operation_events,
            )
        )
        return OperationEnvelope(
            operation_id=operation_id,
            status=result.status,
            project_id=project_id,
            conversation_id=conversation_id,
            commit_id=result.commit_id.value if result.commit_id else None,
            changed_chapter_ids=changed_chapter_ids,
        )

    # -- 查询 --------------------------------------------------------------

    def find_replay(
        self, project_id: str, operation_id: str, request_fingerprint: str
    ) -> OperationEnvelope | None:
        """回执重放（§3.3 第 1 步）：必须在执行领域命令**之前**调用。

        导入确认使用确定性章 ID：先执行命令的话，重试会撞上已存在的章而
        永远走不到回执重放。保存类命令恰好幂等，此前未暴露这个顺序问题。
        """
        return self._replay(ProjectId(project_id), operation_id, request_fingerprint)

    def _replay(
        self, pid: ProjectId, operation_id: str, fingerprint: str
    ) -> OperationEnvelope | None:
        existing = self._repo.aux_read(pid, OPERATION_RECEIPT_BUCKET, operation_id)
        if existing is None:
            return None
        receipt = OperationReceipt.from_wire(existing)
        if receipt.request_fingerprint != fingerprint:
            raise _violation(
                ec.OPERATION_KEY_CONFLICT,
                f"operations.submit({operation_id})",
                f"操作 {operation_id!r} 已被不同内容的请求使用"
                f"（原指纹 {receipt.request_fingerprint[:12]}…）",
                "同一个 operation_id 只用于同一逻辑操作的全部重试；新操作生成新的 operation_id",
                operation_id=operation_id,
            )
        return self.get_operation(pid, operation_id)

    def get_operation(self, project_id: ProjectId | str, operation_id: str) -> OperationEnvelope:
        """按 operation_id 找回结果：只依赖仓库，不依赖对话投影（§4.2）。

        投影失败或尚未完成时本查询仍然可用——正文已原子生效，展示层显示
        「同步中」后由投影重试补齐，不再次运行模型、不创建新版本。
        """
        pid = project_id if isinstance(project_id, ProjectId) else ProjectId(project_id)
        receipt_wire = self._repo.aux_read(pid, OPERATION_RECEIPT_BUCKET, operation_id)
        if receipt_wire is None:
            raise _violation(
                ec.OPERATION_NOT_FOUND,
                f"operations.get({operation_id})",
                f"操作 {operation_id!r} 不存在",
                "核对 operation_id；结果定位用提交方返回的 operation_id",
                operation_id=operation_id,
            )
        receipt = OperationReceipt.from_wire(receipt_wire)
        artifact_id = ArtifactId(_record_artifact_id(operation_id))
        head = self._repo.get_branch(pid, "main")
        head_id = head.head_commit_id if head else None
        if head_id is None:
            # 没有任何提交：只可能是 no_change 回执（记录随提交产生）。
            return self._no_change_envelope(operation_id, receipt)
        record_view = self._repo.get_artifact(artifact_id, head_id)
        if record_view is None or record_view.revision is None:
            # 回执在、记录不在 ⇒ no_change（不伪造 commit，计划 §3.3）。
            return self._no_change_envelope(operation_id, receipt)
        record = OperationRecord.from_wire(record_view.revision.content)
        commit_id = self._find_introducing_commit(pid, artifact_id, head_id)
        return OperationEnvelope(
            operation_id=operation_id,
            status=OperationStatus.COMMITTED.value,
            project_id=pid.value,
            conversation_id=receipt.conversation_id,
            commit_id=commit_id.value if commit_id else None,
            changed_chapter_ids=record.changed_chapter_ids,
        )

    def record_refs(self, project_id: ProjectId | str, operation_id: str) -> dict[str, Any]:
        """读取操作记录的 refs（来源、选择、段落来源）；no_change 返回空。"""
        pid = project_id if isinstance(project_id, ProjectId) else ProjectId(project_id)
        head = self._repo.get_branch(pid, "main")
        head_id = head.head_commit_id if head else None
        if head_id is None:
            return {}
        artifact_id = ArtifactId(_record_artifact_id(operation_id))
        record_view = self._repo.get_artifact(artifact_id, head_id)
        if record_view is None or record_view.revision is None:
            return {}
        record = OperationRecord.from_wire(record_view.revision.content)
        return dict(record.refs)

    # -- 内部 ---------------------------------------------------------------

    @staticmethod
    def _no_change_envelope(operation_id: str, receipt: OperationReceipt) -> OperationEnvelope:
        return OperationEnvelope(
            operation_id=operation_id,
            status=OperationStatus.NO_CHANGE.value,
            project_id=receipt.project_id,
            conversation_id=receipt.conversation_id,
            commit_id=None,
            changed_chapter_ids=(),
        )

    def _find_introducing_commit(
        self, project_id: ProjectId, artifact_id: ArtifactId, head: CommitId
    ) -> CommitId | None:
        """从 head 回溯首次引入该 Artifact 的 Commit（即操作的 after 版本）。"""
        for commit in self._repo.commit_history(project_id, head):
            view = self._repo.get_artifact(artifact_id, commit.id)
            if view is not None and view.revision is not None:
                return commit.id
        return None

    @staticmethod
    def _fingerprint(
        kind: str,
        changes: tuple[ArtifactChange, ...],
        summary: str,
        refs: dict[str, Any] | None,
        events: tuple[tuple[str, dict[str, Any]], ...] = (),
    ) -> str:
        """操作请求指纹：「写什么」的稳定哈希（排除并发凭据，同键重放比较用）。"""
        return content_hash(
            {
                "kind": kind,
                "summary": summary,
                "changes": [
                    {"artifact_id": c.artifact_id.value, "content": c.content, "kind": c.kind}
                    for c in changes
                ],
                "events": [[t, p] for t, p in events],
                "refs": refs or {},
            }
        )
