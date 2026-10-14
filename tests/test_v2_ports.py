"""V2 端口与错误信封测试（Phase 11 T11.5）。

验收口径：
1. 七个端口名称冻结且可从稳定导入路径 import；
2. 端口是 runtime_checkable Protocol，测试替身可实现并通过 isinstance 检查
   （Phase 12 的内存/SQLite 实现与契约测试依赖同一口径）；
3. 错误信封携带稳定错误码、结果类别与三要素消息；LEGAL_EMPTY 不是异常。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hastings.application.errors import (
    EnvironmentFailure,
    HastingsError,
    ResultCategory,
    ViolationError,
)
from hastings.application.ports import (
    AgentRunner,
    Clock,
    CommitRepository,
    EventOutbox,
    IdGenerator,
    JobStore,
    RepositoryReader,
)
from hastings.domain.repository.ids import ArtifactId, CommitId, ProjectId, Scope
from hastings.domain.repository.models import (
    ArtifactView,
    Branch,
    Project,
    RepositoryDiff,
    Snapshot,
)
from hastings.domain.repository.requests import (
    ArtifactQuery,
    CommitRequest,
    CreateBranchRequest,
    MoveBranchRequest,
)
from hastings.domain.repository.results import CommitResult

# B008：frozen dataclass 单例作缺省值，避免函数调用默认参数
_DEFAULT_SCOPE = Scope()

EXPECTED_PORTS = (
    Clock,
    IdGenerator,
    RepositoryReader,
    CommitRepository,
    AgentRunner,
    JobStore,
    EventOutbox,
)


@pytest.mark.parametrize("port", EXPECTED_PORTS)
def test_ports_are_runtime_checkable_protocols(port: type) -> None:
    assert hasattr(port, "__protocol_attrs__") or hasattr(port, "_is_protocol")
    # runtime_checkable：用仅含方法的 Protocol，isinstance 检查方法存在性
    assert isinstance(object(), port) is False


def _assert_implements(instance: object, port: type) -> None:
    assert isinstance(instance, port), f"{type(instance).__name__} 未实现端口 {port.__name__}"


def test_fakes_implement_all_ports() -> None:
    """最小测试替身（Phase 12 内存实现的雏形）必须满足全部端口。"""

    class FakeClock:
        def now(self) -> datetime:
            return datetime(2026, 10, 14, tzinfo=UTC)

    class FakeIdGenerator:
        def new_id(self, prefix: str) -> str:
            return f"{prefix}_1"

    class FakeReader:
        """Phase 12 收窄后的签名：领域值对象进、领域模型出。"""

        def get_project(self, project_id: ProjectId) -> Project | None:
            return None

        def get_branch(self, project_id: ProjectId, name: str) -> Branch | None:
            return None

        def get_snapshot(self, project_id: ProjectId, commit_id: CommitId) -> Snapshot:
            raise NotImplementedError

        def get_artifact(
            self, artifact_id: ArtifactId, at_commit_id: CommitId
        ) -> ArtifactView | None:
            return None

        def list_artifacts(
            self, query: ArtifactQuery, at_commit_id: CommitId
        ) -> list[ArtifactView]:
            return []

        def diff(
            self,
            before_commit_id: CommitId,
            after_commit_id: CommitId,
            scope: Scope = _DEFAULT_SCOPE,
        ) -> RepositoryDiff:
            raise NotImplementedError

    class FakeCommitRepository:
        def create_project(self, name: str) -> Project:
            raise NotImplementedError

        def commit(self, request: CommitRequest) -> CommitResult:
            raise NotImplementedError

        def create_branch(self, request: CreateBranchRequest) -> Branch:
            raise NotImplementedError

        def move_branch(self, request: MoveBranchRequest) -> Branch:
            raise NotImplementedError

    class FakeAgentRunner:
        async def run(
            self, task_kind: str, context_package: dict[str, object]
        ) -> dict[str, object]:
            return {"kind": "proposal"}

    class FakeJobStore:
        async def create(self, job_type: str, payload: dict[str, object]) -> str:
            return "job_1"

        async def mark_retryable(self, job_id: str, reason: str) -> None:
            return None

        async def get(self, job_id: str) -> dict[str, object] | None:
            return None

    class FakeOutbox:
        async def pending(self, project_id: str, limit: int) -> list[dict[str, object]]:
            return []

        async def mark_published(self, event_id: str) -> None:
            return None

    _assert_implements(FakeClock(), Clock)
    _assert_implements(FakeIdGenerator(), IdGenerator)
    _assert_implements(FakeReader(), RepositoryReader)
    _assert_implements(FakeCommitRepository(), CommitRepository)
    _assert_implements(FakeAgentRunner(), AgentRunner)
    _assert_implements(FakeJobStore(), JobStore)
    _assert_implements(FakeOutbox(), EventOutbox)


def test_event_outbox_has_no_append_entry() -> None:
    """边界固化（评审收口）：Outbox 端口只暴露消费侧方法。

    事件写入唯一入口是 CommitRepository.commit（ADR-007）；端口若提供
    通用 append，应用层就能绕过提交事务追加事件，重新制造双写窗口。
    """
    assert not hasattr(EventOutbox, "append")


class TestErrorEnvelope:
    def test_violation_error_carries_three_elements(self) -> None:
        err = ViolationError(
            code="REPO_HEAD_CONFLICT",
            where="commit(main)",
            why="expected_head c-01 已过期，当前 head 是 c-02",
            fix="重新读取 main head 后基于 c-02 重新提交",
            details={"expected": "c-01", "actual": "c-02"},
        )
        assert err.code == "REPO_HEAD_CONFLICT"
        assert err.category is ResultCategory.VIOLATION
        assert err.where in str(err)
        assert err.why in str(err)
        assert err.fix in str(err)
        assert err.details == {"expected": "c-01", "actual": "c-02"}

    def test_environment_failure_has_environment_category(self) -> None:
        err = EnvironmentFailure(
            code="REPO_UNAVAILABLE",
            where="sqlite commit",
            why="数据库文件不可写（磁盘只读）",
            fix="恢复磁盘写权限后重试；事务已回滚，无半提交",
        )
        assert err.category is ResultCategory.ENVIRONMENT
        assert isinstance(err, HastingsError)

    def test_legal_empty_is_not_an_exception(self) -> None:
        """合法空结果必须以正常返回值表达，不设异常子类。"""
        subclasses = HastingsError.__subclasses__()
        assert not any(
            getattr(s, "category", None) is ResultCategory.LEGAL_EMPTY for s in subclasses
        )
        assert not any("Empty" in s.__name__ for s in subclasses)
