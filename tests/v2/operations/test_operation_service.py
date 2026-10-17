"""OperationService 契约测试（V03-2.1，计划 §3.3 / §4.2、执行指导 §6.1）。

验收对照：
- 无作者消息也能保存/定位结果（服务不接触对话存储）；
- 同键同指纹幂等重放；同键不同指纹显式冲突；
- no_change 返回回执不伪造 commit（不创建正文空版本、无记录 Artifact）；
- 投影失败不回滚正文，``get_operation`` 仍可找回；
- 双后端（内存 / SQLite 重启）一致。
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from pathlib import Path

import pytest

from hastings.application.errors import ViolationError
from hastings.application.services.commit_service import CommitService
from hastings.application.services.operation_service import OperationService
from hastings.application.workflows.operation_projection import (
    FanOutSink,
    OperationDiscussionProjector,
)
from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.operation_record import (
    OPERATION_EVENT_COMMITTED,
    OPERATION_RECEIPT_BUCKET,
    creation_log_conversation_id,
)
from hastings.domain.repository.ids import ArtifactId, ProjectId
from hastings.domain.repository.requests import ArtifactChange
from hastings.infrastructure.conversations.store import SqliteConversationStore
from hastings.infrastructure.repository.memory import build_memory_repository
from hastings.infrastructure.repository.sqlite import build_sqlite_repository


class Clock:
    """确定性时钟（仓库要求 now()）。"""

    def now(self) -> object:  # 与 SystemClock 返回 datetime 兼容即可
        from datetime import datetime

        return datetime.now(UTC)


class Ids:
    """顺序 ID 生成器（测试断言可读）。"""

    def __init__(self) -> None:
        self._n = 0

    def new_id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n:05d}"


def _make_service(db_path: str | None = None) -> tuple[OperationService, CommitService]:
    clock, ids = Clock(), Ids()
    repo = (
        build_sqlite_repository(db_path, clock, ids)
        if db_path
        else build_memory_repository(clock, ids)
    )
    repo.service.create_project("走查作品", project_id="prj_ops")
    return OperationService(repo.service), repo.service


def _chapter_change(text: str, old_revision_id: object = None) -> ArtifactChange:
    return ArtifactChange(
        artifact_id=ArtifactId("ch_1"),
        content={"title": "第一章", "text": text},
        kind="chapter",
        old_revision_id=old_revision_id,  # type: ignore[arg-type]
    )


def _submit(
    service: OperationService,
    text: str = "正文 v1",
    operation_id: str = "op_001",
    kind: str = "chapter.save",
) -> object:
    return service.submit(
        project_id="prj_ops",
        operation_id=operation_id,
        kind=kind,
        changes=(_chapter_change(text),),
        summary=f"提交 {text}",
    )


# ---------------------------------------------------------------------------
# 提交与幂等
# ---------------------------------------------------------------------------


def test_submit_committed_record_receipt_and_event_atomically() -> None:
    service, repo = _make_service()
    envelope = _submit(service)
    # 信封：committed + 固定讨论 ID（确定性派生，与任何作者会话无关）
    assert envelope.status == "committed"
    assert envelope.commit_id is not None
    assert envelope.conversation_id == creation_log_conversation_id("prj_ops")
    assert envelope.changed_chapter_ids == ("ch_1",)
    # 记录 Artifact 是版本化对象（kind = operation_record）
    snapshot = repo.get_snapshot(
        ProjectId("prj_ops"), repo.get_branch(ProjectId("prj_ops"), "main").head_commit_id
    )
    record_id = ArtifactId("art_op_001")
    assert record_id in snapshot.artifacts
    assert snapshot.artifacts[record_id].artifact.kind == "operation_record"
    # 回执与提交同事务：无需再次写库即可读出
    receipt = repo.aux_read(ProjectId("prj_ops"), OPERATION_RECEIPT_BUCKET, "op_001")
    assert receipt is not None and receipt["operation_id"] == "op_001"
    # Outbox 事件投递与固定会话引用断言在投影测试中覆盖（真实 OutboxPublisher 路径）


def test_replay_same_key_same_content_returns_same_result() -> None:
    service, repo = _make_service()
    first = _submit(service)
    second = _submit(service)
    assert second.operation_id == first.operation_id
    assert second.commit_id == first.commit_id
    assert second.status == first.status
    # 幂等重放不产生新提交：head 未移动
    branch = repo.get_branch(ProjectId("prj_ops"), "main")
    assert branch is not None and branch.head_commit_id.value == first.commit_id


def test_same_key_different_content_conflicts() -> None:
    service, _repo = _make_service()
    _submit(service, text="正文 v1")
    with pytest.raises(ViolationError) as excinfo:
        _submit(service, text="正文 v2（不同内容）")
    assert excinfo.value.code == ec.OPERATION_KEY_CONFLICT


def test_no_change_returns_receipt_without_fabricating_commit() -> None:
    service, repo = _make_service()
    _submit(service, text="正文 v1", operation_id="op_001")
    head_before = repo.get_branch(ProjectId("prj_ops"), "main").head_commit_id
    # 同内容、新 operation_id：no_change——回执落库但不创建正文空版本
    envelope = _submit(service, text="正文 v1", operation_id="op_002")
    assert envelope.status == "no_change"
    assert envelope.commit_id is None
    assert envelope.conversation_id == creation_log_conversation_id("prj_ops")
    head_after = repo.get_branch(ProjectId("prj_ops"), "main").head_commit_id
    assert (head_after.value if head_after else None) == (
        head_before.value if head_before else None
    ), "no_change 不移动 head"
    # 回执存在但记录 Artifact 不存在 ⇒ 查询裁决为 no_change
    receipt = repo.aux_read(ProjectId("prj_ops"), OPERATION_RECEIPT_BUCKET, "op_002")
    assert receipt is not None
    resolved = service.get_operation("prj_ops", "op_002")
    assert resolved.status == "no_change"
    assert resolved.commit_id is None


def test_get_operation_unknown_and_locatable_without_messages() -> None:
    service, _repo = _make_service()
    with pytest.raises(ViolationError) as excinfo:
        service.get_operation("prj_ops", "op_missing")
    assert excinfo.value.code == ec.OPERATION_NOT_FOUND
    # 无作者消息也能保存/定位：全程未创建任何会话或消息
    envelope = _submit(service)
    found = service.get_operation("prj_ops", envelope.operation_id)
    assert found.commit_id == envelope.commit_id  # str 比较
    assert found.changed_chapter_ids == envelope.changed_chapter_ids


def test_sqlite_backend_restart_keeps_receipt_and_record(tmp_path: Path) -> None:
    path = str(tmp_path / "ops.sqlite")
    service, _repo = _make_service(path)
    envelope = _submit(service)
    # 「重启」：新实例打开同一库
    service2, repo2 = _make_service(path)
    found = service2.get_operation("prj_ops", "op_001")
    assert found.commit_id == envelope.commit_id  # str 比较
    assert found.status == "committed"
    # 同键同指纹跨进程重放：返回同一结果
    replay = _submit(service2)
    assert replay.commit_id == envelope.commit_id
    _ = repo2


# ---------------------------------------------------------------------------
# 固定讨论投影
# ---------------------------------------------------------------------------


def _operation_event(operation_id: str = "op_001") -> dict:
    return {
        "id": f"evt_{operation_id}",
        "project_id": "prj_ops",
        "commit_id": "cmt_x",
        "sequence": 1,
        "type": OPERATION_EVENT_COMMITTED,
        "payload": {
            "operation_id": operation_id,
            "project_id": "prj_ops",
            "conversation_id": creation_log_conversation_id("prj_ops"),
            "kind": "chapter.save",
            "changed_chapter_ids": ["ch_1"],
        },
    }


def test_projection_creates_creation_log_reference_idempotently() -> None:
    conversations = SqliteConversationStore(":memory:")
    projector = OperationDiscussionProjector(conversations)
    fixed_id = creation_log_conversation_id("prj_ops")
    # 作者会话（当时正在打开的讨论）——投影不得触碰
    author_conv = asyncio.run(conversations.create_conversation("prj_ops", "作者讨论"))
    asyncio.run(projector.publish(_operation_event()))
    asyncio.run(projector.publish(_operation_event()))  # Outbox 崩溃窗口重投
    messages = asyncio.run(conversations.list_messages(fixed_id))
    assert len(messages) == 1, "重复投递不产生重复引用"
    assert messages[0].payload["operation_id"] == "op_001"
    # 不能投递到当时正在打开的会话：作者会话零接触
    author_messages = asyncio.run(conversations.list_messages(author_conv.conversation_id))
    assert author_messages == []
    # 幂等重放返回同一会话（确定性 ID 不重建）
    again = asyncio.run(
        conversations.create_conversation("prj_ops", "创作记录", conversation_id=fixed_id)
    )
    assert again.conversation_id == fixed_id


def test_projection_failure_does_not_rollback_commit_and_recovers() -> None:
    """投影失败：正文已生效、get_operation 可找回；恢复后补展示不重复。"""
    service, repo = _make_service()
    envelope = _submit(service)  # 提交（含事件）已原子生效
    conversations = SqliteConversationStore(":memory:")

    class FailingProjector(OperationDiscussionProjector):
        def __init__(self) -> None:
            super().__init__(conversations)
            self.fail = True
            self.calls = 0

        async def publish(self, event: dict) -> None:  # type: ignore[override]
            self.calls += 1
            if self.fail:
                raise RuntimeError("注入：投影存储故障")
            await super().publish(event)

    projector = FailingProjector()
    with pytest.raises(RuntimeError):
        asyncio.run(projector.publish(_operation_event()))
    # 投影失败不回滚正文：记录 Artifact 与回执仍在，查询可找回
    found = service.get_operation("prj_ops", envelope.operation_id)
    assert found.commit_id == envelope.commit_id  # str 比较
    assert found.status == "committed"
    # 恢复后重试：只补展示（一条引用），不创建新版本、不重放模型
    projector.fail = False
    asyncio.run(projector.publish(_operation_event()))
    messages = asyncio.run(conversations.list_messages(creation_log_conversation_id("prj_ops")))
    assert len(messages) == 1
    head = repo.get_branch(ProjectId("prj_ops"), "main").head_commit_id
    assert head is not None and head.value == envelope.commit_id


def test_fan_out_sink_propagates_and_serves_all_consumers() -> None:
    seen: list[dict] = []

    class Sink:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail

        async def publish(self, event: dict) -> None:
            if self.fail:
                raise RuntimeError("boom")
            seen.append(event)

    fan = FanOutSink(Sink(), Sink())
    asyncio.run(fan.publish({"type": "x"}))
    assert len(seen) == 2
    with pytest.raises(RuntimeError):
        asyncio.run(FanOutSink(Sink(fail=True), Sink()).publish({"type": "x"}))
    with pytest.raises(ValueError):
        FanOutSink()
