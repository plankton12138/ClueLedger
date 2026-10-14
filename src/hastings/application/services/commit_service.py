"""Commit Service：固定顺序的原子提交引擎（Phase 12 T12.4）。

提交固定顺序（modules/repository-kernel.md §4，写入点依次编号）：

1. 规范化输入（normalize_json 规范值树 + 稳定哈希）；
2. 幂等重放检查（同键同指纹返回原结果，同键不同指纹抛
   REPO_IDEMPOTENCY_CONFLICT，不产生重复 Revision 或事件）；
3. 开事务；
4. head / branch version 检查（乐观并发，不变量 3）；
5. lease 验证（owner + version + 未过期）；
6. 基础快照有效 revision 检查（不变量 5）与 no-op 检测（合法空结果）；
7. 写不可变 Revision 与 RelationRevision（含 Commit 内 position 序号）；
8. 写 Commit 与 CommitEntries；
9. 移动 Branch head（存储层复查 version）；
10. 写 Outbox 事件（与 Commit 同事务，ADR-007）；
11. 记录幂等键（含请求指纹）；事务提交。

任何写点抛出异常都由存储事务整体回滚——内存实现靠快照还原、SQLite 靠
ROLLBACK，契约测试对两个后端用同一套故障注入证明无半提交。

no-op 检测放在事务内 head 检查之后：与「检测先于开事务」相比，把读也纳入
同一快照，避免检测与写入之间出现 TOCTOU 窗口（检测通过但 head 已被并发
提交移动）。语义不变，正确性更强。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.ids import IdGenerator
from hastings.application.ports.repository import (
    DEFAULT_SCOPE,
    RepositoryStorage,
    RepositoryTxn,
)
from hastings.domain.repository import error_codes as ec
from hastings.domain.repository.ids import (
    Actor,
    ArtifactId,
    BranchId,
    CommitId,
    ContentHash,
    ProjectId,
    RevisionId,
    Scope,
)
from hastings.domain.repository.models import (
    RELATION_ACTION_ADD,
    RELATION_ACTION_REMOVE,
    Artifact,
    ArtifactDiffEntry,
    ArtifactView,
    Branch,
    Commit,
    CommitEntry,
    OutboxEvent,
    Project,
    RelationRevision,
    RepositoryDiff,
    Revision,
    Snapshot,
)
from hastings.domain.repository.requests import (
    ArtifactChange,
    ArtifactQuery,
    CommitRequest,
    CreateBranchRequest,
    MoveBranchRequest,
    RelationChange,
)
from hastings.domain.repository.results import COMMITTED, NO_CHANGE, CommitResult
from hastings.domain.repository.serialization import content_hash, normalize_json
from hastings.domain.repository.snapshot import (
    fold_effective_relations,
    fold_effective_revisions,
)

__all__ = ["CommitService"]

EVENT_COMMIT_CREATED = "repository.commit.created"
EVENT_REVISION_CREATED = "artifact.revision.created"
EVENT_BRANCH_HEAD_MOVED = "branch.head.moved"


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


def _not_found(code: str, where: str, what: str, fix: str) -> ViolationError:
    return _violation(code, where, f"{what}不存在", fix)


def _id_str(value: Any) -> str | None:
    return value.value if value is not None else None


def _request_fingerprint(request: CommitRequest) -> ContentHash:
    """幂等请求指纹：「写什么」的稳定哈希，不含「凭据」字段。

    参与：project/branch/actor/summary/changes(artifact_id+content+kind)/
    relations/events。排除三类并发执行凭据——expected_head、old_revision_id、
    required_leases：它们是提交时刻的乐观并发条件，同一逻辑操作的重放天然
    可能携带前进后的凭据（如 restore 重放时基础版本已变成上次 restore 的
    结果），进指纹会把合法重放误判为冲突。
    """
    payload = {
        "project_id": request.project_id.value,
        "branch_id": request.branch_id.value,
        "actor": [request.actor.kind, request.actor.id],
        "summary": request.summary,
        "changes": [
            {"artifact_id": c.artifact_id.value, "content": c.content, "kind": c.kind}
            for c in request.changes
        ],
        "relations": [
            {
                "action": r.action,
                "source": r.source.value,
                "relation_type": r.relation_type,
                "target": r.target.value,
                "metadata": r.metadata,
            }
            for r in request.relations
        ],
        "events": [[t, p] for t, p in request.events],
    }
    return ContentHash(content_hash(payload))


class CommitService:
    """实现 RepositoryReader + CommitRepository；驱动任意 RepositoryStorage。"""

    def __init__(self, storage: RepositoryStorage, ids: IdGenerator) -> None:
        self._storage = storage
        self._ids = ids

    # ------------------------------------------------------------------
    # 写路径
    # ------------------------------------------------------------------

    def create_project(self, name: str) -> Project:
        if not name or not name.strip():
            raise _violation(
                ec.REPO_INVALID_CHANGE,
                "create_project",
                "项目名为空",
                "提供非空显示名；项目主键由仓库生成，显示名可变",
            )
        now = self._storage.now()
        with self._storage.transaction() as txn:
            project = Project(id=ProjectId(self._ids.new_id("prj")), name=name, created_at=now)
            branch = Branch(
                id=BranchId(self._ids.new_id("br")),
                project_id=project.id,
                name="main",
                head_commit_id=None,
                version=1,
            )
            txn.insert_project(project)
            txn.insert_branch(branch)
        return project

    def commit(self, request: CommitRequest) -> CommitResult:
        where = f"commit({request.branch_id.value})"
        request, prepared = self._normalize(request, where)
        now = self._storage.now()
        with self._storage.transaction() as txn:
            # (2) 幂等重放：同键必须同指纹——不同内容复用旧键显式冲突，
            #     不静默返回旧结果（否则调用者误以为新修改已生效）
            fingerprint = _request_fingerprint(request)
            record = txn.get_idempotent_result(request.project_id, request.idempotency_key)
            if record is not None:
                stored_fingerprint, replay = record
                if stored_fingerprint != fingerprint.value:
                    original = replay.commit_id.value if replay.commit_id else None
                    raise _violation(
                        ec.REPO_IDEMPOTENCY_CONFLICT,
                        where,
                        f"幂等键 {request.idempotency_key!r} 已被不同内容的请求使用"
                        f"（原 Commit {original}）",
                        "为本次新操作生成新的 idempotency_key；"
                        "同一个键只用于同一逻辑操作的全部重试",
                        key=request.idempotency_key,
                        original_commit_id=original,
                    )
                return replay
            # (3)(4) 项目 / 分支 / head 检查
            if txn.get_project(request.project_id) is None:
                raise _not_found(
                    ec.REPO_PROJECT_NOT_FOUND,
                    where,
                    f"项目 {request.project_id.value}",
                    "先 create_project，再向该项目提交",
                )
            branch = txn.get_branch(request.branch_id)
            if branch is None or branch.project_id != request.project_id:
                raise _not_found(
                    ec.REPO_BRANCH_NOT_FOUND,
                    where,
                    f"分支 {request.branch_id.value}",
                    "使用 get_branch 读取有效分支 ID 后重试",
                )
            if branch.head_commit_id != request.expected_head:
                raise _violation(
                    ec.REPO_HEAD_CONFLICT,
                    where,
                    f"expected_head {_id_str(request.expected_head)} 已过期，"
                    f"当前 head 是 {_id_str(branch.head_commit_id)}",
                    f"重新读取分支 head，基于 {_id_str(branch.head_commit_id)} 重新构造提交",
                    expected=_id_str(request.expected_head),
                    actual=_id_str(branch.head_commit_id),
                    branch_version=branch.version,
                )
            # (5) lease 验证
            for key, owner, version in request.required_leases:
                self._verify_lease(txn, key, owner, version, now, where)
            # (6) 基础快照检查 + no-op 检测
            head = branch.head_commit_id
            chain = txn.commit_chain(head) if head is not None else ()
            chain_ids = tuple(c.id for c in chain)
            entries_by_commit = txn.entries_for_commits(chain_ids)
            effective = fold_effective_revisions(chain, entries_by_commit)
            seen: set[ArtifactId] = set()
            writes: list[tuple[ArtifactChange, Revision, RevisionId | None]] = []
            changed_ids: list[ArtifactId] = []
            for change, digest in prepared:
                aid = change.artifact_id
                if aid in seen:
                    raise _violation(
                        ec.REPO_DUPLICATE_CHANGE,
                        where,
                        f"Artifact {aid.value} 在同一次提交中出现多次",
                        "合并为单条变更；一个对象在一次提交里只有一个新版本",
                    )
                seen.add(aid)
                existing = txn.get_artifact_record(aid)
                if existing is not None:
                    if existing.project_id != request.project_id:
                        raise _violation(
                            ec.REPO_INTEGRITY_ERROR,
                            where,
                            f"Artifact {aid.value} 属于项目 {existing.project_id.value}，"
                            f"不能提交到 {request.project_id.value}",
                            "跨项目对象必须各自提交；如需引用请用关系边",
                        )
                    eff_rev_id = effective.get(aid)
                    if change.old_revision_id != eff_rev_id:
                        raise _violation(
                            ec.REPO_REVISION_CONFLICT,
                            f"commit({aid.value})",
                            f"声明的 old_revision {_id_str(change.old_revision_id)} "
                            f"不是基础快照中的有效版本 {_id_str(eff_rev_id)}",
                            f"重新读取该对象在 {_id_str(head)} 的版本后再提交",
                            expected=_id_str(change.old_revision_id),
                            actual=_id_str(eff_rev_id),
                        )
                    eff_rev = txn.get_revision(eff_rev_id) if eff_rev_id else None
                    if eff_rev is not None and eff_rev.content_hash == digest:
                        continue  # 归一化后无内容差异 → no-op 候选
                else:
                    if change.kind is None:
                        raise _violation(
                            ec.REPO_INVALID_CHANGE,
                            f"commit({aid.value})",
                            "对象尚不存在却未声明 kind",
                            "首次引入对象时必须提供 kind（kind 创建后不可变）",
                        )
                    if change.old_revision_id is not None:
                        raise _violation(
                            ec.REPO_REVISION_CONFLICT,
                            f"commit({aid.value})",
                            "新对象不允许声明 old_revision",
                            "old_revision 只用于更新已存在对象",
                        )
                    txn.insert_artifact(
                        Artifact(
                            id=aid,
                            project_id=request.project_id,
                            kind=change.kind,
                            created_at=now,
                        )
                    )
                old_id = change.old_revision_id if existing is not None else None
                new_rev = Revision(
                    id=RevisionId(self._ids.new_id("rev")),
                    artifact_id=aid,
                    parent_id=old_id,
                    content=change.content,
                    content_hash=digest,
                    author=request.actor,
                    created_at=now,
                )
                writes.append((change, new_rev, old_id))
                changed_ids.append(aid)
            if not writes and not request.relations:
                return CommitResult(
                    status=NO_CHANGE,
                    no_change_reason=(
                        f"{ec.REPO_NO_CHANGE}: 全部变更与 {_id_str(head)} 的有效版本内容一致"
                    ),
                )
            # (7) 写不可变 Revision
            for _change, rev, _old in writes:
                txn.insert_revision(rev)
            # (8) 写 Commit 与 Entries
            commit = Commit(
                id=CommitId(self._ids.new_id("cmt")),
                project_id=request.project_id,
                parent_ids=(head,) if head is not None else (),
                message=request.summary,
                actor=request.actor,
                created_at=now,
                sequence=txn.next_sequence(request.project_id),
            )
            entries = tuple(
                CommitEntry(
                    commit_id=commit.id,
                    artifact_id=change.artifact_id,
                    old_revision_id=old_id,
                    new_revision_id=rev.id,
                )
                for change, rev, old_id in writes
            )
            for rel in request.relations:
                for endpoint in (rel.source, rel.target):
                    if endpoint not in seen and txn.get_artifact_record(endpoint) is None:
                        raise _violation(
                            ec.REPO_INVALID_CHANGE,
                            where,
                            f"关系端点 {endpoint.value} 既不在本次提交中，也不是已有对象",
                            "先创建对象（同一提交即可），或使用已存在的 artifact ID",
                        )
            txn.insert_commit(commit, entries)
            # 关系行引用 commit_id，必须在 Commit 落库之后写入；
            # position 固化请求内顺序（SQL 不保证行返回顺序）
            for position, rel in enumerate(request.relations):
                txn.insert_relation(
                    RelationRevision(
                        id=self._ids.new_id("rel"),
                        commit_id=commit.id,
                        action=rel.action,
                        source=rel.source,
                        relation_type=rel.relation_type,
                        target=rel.target,
                        metadata=dict(rel.metadata),
                        position=position,
                    )
                )
            # (9) 移动 Branch head
            new_version = branch.version + 1
            txn.set_branch_head(branch.id, commit.id, branch.version)
            # (10) Outbox 事件（与 Commit 同事务）
            txn.append_outbox_events(
                self._build_events(
                    txn,
                    request=request,
                    commit=commit,
                    branch=branch,
                    new_version=new_version,
                    writes=writes,
                )
            )
            # (11) 幂等记录
            result = CommitResult(
                status=COMMITTED,
                commit_id=commit.id,
                new_head=commit.id,
                sequence=commit.sequence,
                changed_artifact_ids=tuple(changed_ids),
            )
            txn.put_idempotent_result(
                request.project_id, request.idempotency_key, fingerprint.value, result
            )
        return result

    def create_branch(self, request: CreateBranchRequest) -> Branch:
        where = f"create_branch({request.project_id.value}, {request.name!r})"
        with self._storage.transaction() as txn:
            if txn.get_project(request.project_id) is None:
                raise _not_found(
                    ec.REPO_PROJECT_NOT_FOUND, where, f"项目 {request.project_id.value}", ""
                )
            at = txn.get_commit(request.at_commit_id)
            if at is None or at.project_id != request.project_id:
                raise _not_found(
                    ec.REPO_COMMIT_NOT_FOUND,
                    where,
                    f"Commit {request.at_commit_id.value}",
                    "使用该项目内存在的 Commit 作为分叉点",
                )
            if txn.branch_by_name(request.project_id, request.name) is not None:
                raise _violation(
                    ec.REPO_BRANCH_EXISTS,
                    where,
                    f"分支名 {request.name!r} 已被占用",
                    "换一个分支名；分支名是项目内唯一标识",
                )
            branch = Branch(
                id=BranchId(self._ids.new_id("br")),
                project_id=request.project_id,
                name=request.name,
                head_commit_id=request.at_commit_id,
                version=1,
            )
            txn.insert_branch(branch)
        return branch

    def move_branch(self, request: MoveBranchRequest) -> Branch:
        where = f"move_branch({request.branch_id.value})"
        with self._storage.transaction() as txn:
            branch = txn.get_branch(request.branch_id)
            if branch is None:
                raise _not_found(
                    ec.REPO_BRANCH_NOT_FOUND, where, f"分支 {request.branch_id.value}", ""
                )
            new_head = txn.get_commit(request.new_head_commit_id)
            if new_head is None or new_head.project_id != branch.project_id:
                raise _not_found(
                    ec.REPO_COMMIT_NOT_FOUND,
                    where,
                    f"Commit {request.new_head_commit_id.value}",
                    "目标 head 必须是同一项目内存在的 Commit",
                )
            if branch.head_commit_id is not None:
                chain_ids = {c.id for c in txn.commit_chain(request.new_head_commit_id)}
                if branch.head_commit_id not in chain_ids:
                    raise _violation(
                        ec.REPO_BRANCH_MOVE_BACKWARD,
                        where,
                        f"目标 {request.new_head_commit_id.value} 不在当前 head "
                        f"{branch.head_commit_id.value} 之后的历史上",
                        "回退历史必须构造新 Revision 和新 Commit（不变量 10），"
                        "不允许把分支指针移回旧提交伪装撤销",
                    )
            txn.set_branch_head(branch.id, request.new_head_commit_id, request.expected_version)
            moved = Branch(
                id=branch.id,
                project_id=branch.project_id,
                name=branch.name,
                head_commit_id=request.new_head_commit_id,
                version=request.expected_version + 1,
            )
            txn.append_outbox_events(
                (
                    OutboxEvent(
                        id=self._ids.new_id("evt"),
                        project_id=branch.project_id,
                        commit_id=None,
                        sequence=txn.next_outbox_sequence(branch.project_id),
                        type=EVENT_BRANCH_HEAD_MOVED,
                        payload={
                            "branch_id": branch.id.value,
                            "project_id": branch.project_id.value,
                            "old_head": _id_str(branch.head_commit_id),
                            "new_head": request.new_head_commit_id.value,
                            "branch_version": moved.version,
                        },
                    ),
                )
            )
        return moved

    # ------------------------------------------------------------------
    # 读路径
    # ------------------------------------------------------------------

    def get_project(self, project_id: ProjectId) -> Project | None:
        with self._storage.transaction() as txn:
            return txn.get_project(project_id)

    def get_branch(self, project_id: ProjectId, name: str) -> Branch | None:
        with self._storage.transaction() as txn:
            return txn.branch_by_name(project_id, name)

    def get_snapshot(self, project_id: ProjectId, commit_id: CommitId) -> Snapshot:
        with self._storage.transaction() as txn:
            commit = self._require_commit(txn, commit_id)
            if commit.project_id != project_id:
                raise _violation(
                    ec.REPO_INTEGRITY_ERROR,
                    f"get_snapshot({commit_id.value})",
                    f"Commit {commit_id.value} 属于项目 {commit.project_id.value}",
                    "使用该 Commit 所属项目的 ID 读取快照",
                )
            chain = txn.commit_chain(commit_id)
            chain_ids = tuple(c.id for c in chain)
            effective = fold_effective_revisions(chain, txn.entries_for_commits(chain_ids))
            views: dict[ArtifactId, ArtifactView] = {}
            for record in txn.artifacts_of_project(project_id):
                rev_id = effective.get(record.id)
                revision = txn.get_revision(rev_id) if rev_id else None
                views[record.id] = ArtifactView(artifact=record, revision=revision)
            relations = fold_effective_relations(txn.relation_rows_for_commits(chain_ids))
        return Snapshot(
            project_id=project_id, commit_id=commit_id, artifacts=views, relations=relations
        )

    def get_artifact(self, artifact_id: ArtifactId, at_commit_id: CommitId) -> ArtifactView | None:
        with self._storage.transaction() as txn:
            commit = txn.get_commit(at_commit_id)
            if commit is None:
                raise _not_found(
                    ec.REPO_COMMIT_NOT_FOUND,
                    f"get_artifact({artifact_id.value})",
                    f"Commit {at_commit_id.value}",
                    "",
                )
            record = txn.get_artifact_record(artifact_id)
            if record is None or record.project_id != commit.project_id:
                return None
            chain = txn.commit_chain(at_commit_id)
            chain_ids = tuple(c.id for c in chain)
            rev_id = fold_effective_revisions(chain, txn.entries_for_commits(chain_ids)).get(
                artifact_id
            )
            revision = txn.get_revision(rev_id) if rev_id else None
        return ArtifactView(artifact=record, revision=revision)

    def list_artifacts(self, query: ArtifactQuery, at_commit_id: CommitId) -> list[ArtifactView]:
        with self._storage.transaction() as txn:
            commit = txn.get_commit(at_commit_id)
            if commit is None:
                raise _not_found(
                    ec.REPO_COMMIT_NOT_FOUND, "list_artifacts", f"Commit {at_commit_id.value}", ""
                )
            chain = txn.commit_chain(at_commit_id)
            chain_ids = tuple(c.id for c in chain)
            effective = fold_effective_revisions(chain, txn.entries_for_commits(chain_ids))
            views: list[ArtifactView] = []
            for record in txn.artifacts_of_project(commit.project_id):
                if query.kind is not None and record.kind != query.kind:
                    continue
                rev_id = effective.get(record.id)
                revision = txn.get_revision(rev_id) if rev_id else None
                views.append(ArtifactView(artifact=record, revision=revision))
        views.sort(key=lambda v: v.artifact.id.value)
        return views

    def diff(
        self,
        before_commit_id: CommitId,
        after_commit_id: CommitId,
        scope: Scope = DEFAULT_SCOPE,
    ) -> RepositoryDiff:
        with self._storage.transaction() as txn:
            before = txn.get_commit(before_commit_id)
            after = txn.get_commit(after_commit_id)
            if before is None or after is None:
                raise _not_found(
                    ec.REPO_COMMIT_NOT_FOUND,
                    "diff",
                    f"Commit {before_commit_id.value} 或 {after_commit_id.value}",
                    "",
                )
            if before.project_id != after.project_id:
                raise _violation(
                    ec.REPO_INTEGRITY_ERROR,
                    "diff",
                    "两个 Commit 属于不同项目，无法比较",
                    "diff 只在同一项目的提交之间进行",
                )

            def fold_at(cid: CommitId) -> dict[ArtifactId, RevisionId]:
                chain = txn.commit_chain(cid)
                return fold_effective_revisions(
                    chain, txn.entries_for_commits(tuple(c.id for c in chain))
                )

            def relations_at(cid: CommitId) -> tuple[RelationRevision, ...]:
                chain = txn.commit_chain(cid)
                return fold_effective_relations(
                    txn.relation_rows_for_commits(tuple(c.id for c in chain))
                )

            before_rev = fold_at(before_commit_id)
            after_rev = fold_at(after_commit_id)
            artifact_changes: list[ArtifactDiffEntry] = []
            for aid in sorted(set(before_rev) | set(after_rev), key=lambda a: a.value):
                old_id = before_rev.get(aid)
                new_id = after_rev.get(aid)
                if old_id == new_id:
                    continue
                record = txn.get_artifact_record(aid)
                kind = record.kind if record else ""
                if scope.artifact_kinds and kind not in scope.artifact_kinds:
                    continue
                artifact_changes.append(
                    ArtifactDiffEntry(
                        artifact_id=aid, kind=kind, old_revision_id=old_id, new_revision_id=new_id
                    )
                )

            def edge_key(row: RelationRevision) -> tuple[str, str, str]:
                return (row.source.value, row.relation_type, row.target.value)

            before_edges = {edge_key(r): r for r in relations_at(before_commit_id)}
            after_edges = {edge_key(r): r for r in relations_at(after_commit_id)}
            relations_added = tuple(
                after_edges[k] for k in sorted(set(after_edges) - set(before_edges))
            )
            relations_removed = tuple(
                before_edges[k] for k in sorted(set(before_edges) - set(after_edges))
            )
        return RepositoryDiff(
            before_commit_id=before_commit_id,
            after_commit_id=after_commit_id,
            artifact_changes=tuple(artifact_changes),
            relations_added=relations_added,
            relations_removed=relations_removed,
        )

    # ------------------------------------------------------------------
    # 应用层便利操作：restore-as-new-commit（T12.5）
    # ------------------------------------------------------------------

    def restore(
        self,
        *,
        project_id: ProjectId,
        branch_id: BranchId,
        expected_head: CommitId | None,
        artifact_id: ArtifactId,
        at_commit_id: CommitId,
        actor: Actor,
        idempotency_key: str,
        summary: str,
    ) -> CommitResult:
        """把某历史版本恢复为当前 head 上的新 Revision 与新 Commit（不变量 10）。"""
        view = self.get_artifact(artifact_id, at_commit_id)
        if view is None or view.revision is None:
            raise _not_found(
                ec.REPO_ARTIFACT_NOT_FOUND,
                f"restore({artifact_id.value})",
                f"Commit {at_commit_id.value} 中的对象版本",
                "确认该提交确实包含此对象（get_artifact 返回 None 表示尚未引入）",
            )
        with self._storage.transaction() as txn:
            branch = txn.get_branch(branch_id)
            head = branch.head_commit_id if branch else None
            chain = txn.commit_chain(head) if head is not None else ()
            eff_rev_id = fold_effective_revisions(
                chain, txn.entries_for_commits(tuple(c.id for c in chain))
            ).get(artifact_id)
        request = CommitRequest(
            project_id=project_id,
            branch_id=branch_id,
            expected_head=expected_head,
            actor=actor,
            idempotency_key=idempotency_key,
            summary=summary,
            changes=(
                ArtifactChange(
                    artifact_id=artifact_id,
                    content=view.revision.content,
                    old_revision_id=eff_rev_id,
                ),
            ),
        )
        return self.commit(request)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _normalize(
        self, request: CommitRequest, where: str
    ) -> tuple[CommitRequest, list[tuple[ArtifactChange, ContentHash]]]:
        """规范化输入：内容/关系/事件全部换成规范值树（与调用者零共享），
        哈希在此固定；返回规范化请求与逐对象摘要，供提交全程使用。
        """
        norm_changes: list[ArtifactChange] = []
        prepared: list[tuple[ArtifactChange, ContentHash]] = []
        for change in request.changes:
            try:
                digest = ContentHash(content_hash(change.content))
                content = normalize_json(change.content)
            except (TypeError, ValueError) as exc:
                raise _violation(
                    ec.REPO_INVALID_CHANGE,
                    f"commit({change.artifact_id.value})",
                    f"内容无法规范序列化：{exc}",
                    "content 必须是 JSON 兼容结构：dict 键为 str、集合先排序、"
                    "datetime 带时区、不允许 NaN/Inf",
                    cause=str(exc),
                ) from exc
            norm = replace(change, content=content)
            norm_changes.append(norm)
            prepared.append((norm, digest))
        norm_relations: list[RelationChange] = []
        for rel in request.relations:
            if rel.action not in (RELATION_ACTION_ADD, RELATION_ACTION_REMOVE):
                raise _violation(
                    ec.REPO_INVALID_CHANGE,
                    where,
                    f"关系 action 非法：{rel.action!r}",
                    "新增边用 add，撤销边用 remove",
                )
            try:
                metadata = normalize_json(rel.metadata)
                content_hash(rel.metadata)
            except (TypeError, ValueError) as exc:
                raise _violation(
                    ec.REPO_INVALID_CHANGE,
                    where,
                    f"关系 metadata 无法规范序列化：{exc}",
                    "metadata 必须是 JSON 兼容结构",
                ) from exc
            norm_relations.append(replace(rel, metadata=metadata))
        norm_events: list[tuple[str, dict[str, Any]]] = []
        try:
            for event_type, payload in request.events:
                content_hash({"type": event_type, "payload": payload})
                norm_events.append((event_type, normalize_json(payload)))
        except (TypeError, ValueError) as exc:
            raise _violation(
                ec.REPO_INVALID_CHANGE,
                where,
                f"待发布事件无法规范序列化：{exc}",
                "事件 payload 必须是 JSON 兼容结构",
            ) from exc
        normalized = replace(
            request,
            changes=tuple(norm_changes),
            relations=tuple(norm_relations),
            events=tuple(norm_events),
        )
        return normalized, prepared

    def _verify_lease(
        self,
        txn: RepositoryTxn,
        resource_key: str,
        owner: str,
        version: int,
        now: Any,
        where: str,
    ) -> None:
        lease = txn.get_lease(resource_key)
        expired = lease is not None and lease.is_expired(now)
        if (
            lease is None
            or lease.owner_job_id.value != owner
            or lease.version != version
            or expired
        ):
            state = (
                "不存在"
                if lease is None
                else "已过期"
                if expired
                else f"由 {lease.owner_job_id.value} 持有（version={lease.version}）"
            )
            raise _violation(
                ec.REPO_LEASE_CONFLICT,
                where,
                f"资源 {resource_key} 的租约不可用：{state}",
                "重新 acquire 租约后携带新的 lease version/owner 提交",
            )

    def _require_commit(self, txn: RepositoryTxn, commit_id: CommitId) -> Commit:
        commit = txn.get_commit(commit_id)
        if commit is None:
            raise _not_found(
                ec.REPO_COMMIT_NOT_FOUND,
                f"get_snapshot({commit_id.value})",
                f"Commit {commit_id.value}",
                "",
            )
        return commit

    def _build_events(
        self,
        txn: RepositoryTxn,
        *,
        request: CommitRequest,
        commit: Commit,
        branch: Branch,
        new_version: int,
        writes: list[tuple[ArtifactChange, Revision, RevisionId | None]],
    ) -> tuple[OutboxEvent, ...]:
        # sequence 由仓库在提交时分配（发布器绝不分配）：先取基数，批内递增
        seq_base = txn.next_outbox_sequence(request.project_id)
        counter = [seq_base]

        def out(event_type: str, payload: dict[str, Any]) -> OutboxEvent:
            event = OutboxEvent(
                id=self._ids.new_id("evt"),
                project_id=request.project_id,
                commit_id=commit.id,
                sequence=counter[0],
                type=event_type,
                payload=payload,
            )
            counter[0] += 1
            return event

        events: list[OutboxEvent] = [
            out(
                EVENT_COMMIT_CREATED,
                {
                    "commit_id": commit.id.value,
                    "project_id": request.project_id.value,
                    "branch_id": branch.id.value,
                    "sequence": commit.sequence,
                    "summary": request.summary,
                    "artifact_ids": [c.artifact_id.value for c, _r, _o in writes],
                    "actor": f"{request.actor.kind}:{request.actor.id}",
                },
            )
        ]
        # 事件 payload 只含稳定 ID、kind、hash 与摘要，不包含完整正文（计划 §7）
        for change, rev, _old in writes:
            record = txn.get_artifact_record(change.artifact_id)
            kind = record.kind if record is not None else (change.kind or "")
            events.append(
                out(
                    EVENT_REVISION_CREATED,
                    {
                        "artifact_id": change.artifact_id.value,
                        "revision_id": rev.id.value,
                        "kind": kind,
                        "content_hash": rev.content_hash.value,
                        "commit_id": commit.id.value,
                    },
                )
            )
        events.append(
            out(
                EVENT_BRANCH_HEAD_MOVED,
                {
                    "branch_id": branch.id.value,
                    "project_id": request.project_id.value,
                    "old_head": _id_str(branch.head_commit_id),
                    "new_head": commit.id.value,
                    "branch_version": new_version,
                },
            )
        )
        for event_type, payload in request.events:
            events.append(out(event_type, dict(payload)))
        return tuple(events)
