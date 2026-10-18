"""手工修改副本用例（V03-2.4 / 计划 M04）。

一个作品同时只有一个未结束副本；副本按访问章节惰性建章，不复制全书。
自动保存用保存序号做 CAS；「修改完成」把副本里的章一次性走统一发布，
只产生一个正式版本；「放弃」只结束副本，不删历史。

存储用仓库辅助表（V03-0.5 端口）：

- ``edit_session_current`` / ``current``：唯一未结束副本（单键即唯一约束）；
- ``edit_session`` / ``<session_id>``：已结束副本的归档（完成/放弃后保留）；
- ``edit_draft`` / ``<session_id>:<chapter_id>``：章草稿与保存序号。

写入走 ``CommitService.aux_mutate``（单事务读-改-写），保存序号的比较与
递增在同一事务里完成——两个并发保存不可能都通过检查。
"""

from __future__ import annotations

from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.ports.ids import IdGenerator
from hastings.application.services.commit_service import CommitService
from hastings.application.services.operation_service import OperationService
from hastings.application.services.publish_manuscript_change import PublishManuscriptChange
from hastings.domain.manuscript import commands as manuscript_commands
from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.diff import split_paragraphs
from hastings.domain.manuscript.edit_session import (
    SESSION_ABANDONED,
    SESSION_CLOSED,
    SESSION_OPEN,
    DraftChapter,
    EditSession,
)
from hastings.domain.repository.ids import ProjectId
from hastings.domain.repository.serialization import content_hash

__all__ = ["CURRENT_BUCKET", "DRAFT_BUCKET", "EditSessionService", "SESSION_BUCKET"]

#: 唯一未结束副本（key 固定为 ``current``）。
CURRENT_BUCKET = "edit_session_current"
#: 已结束副本归档（key = session_id）。
SESSION_BUCKET = "edit_session"
#: 章草稿（key = ``<session_id>:<chapter_id>``）。
DRAFT_BUCKET = "edit_draft"

_CURRENT_KEY = "current"


class EditSessionService:
    """开始/恢复副本、保存草稿、暂存、放弃与完成。"""

    def __init__(
        self,
        repo: CommitService,
        publisher: PublishManuscriptChange,
        operations: OperationService,
        ids: IdGenerator,
        clock: Any,
    ) -> None:
        self._repo = repo
        self._publisher = publisher
        self._operations = operations
        self._ids = ids
        self._clock: Any = clock

    # -- 查询与开始 -----------------------------------------------------------

    def current(self, project_id: str) -> dict[str, Any] | None:
        payload = self._repo.aux_read(ProjectId(project_id), CURRENT_BUCKET, _CURRENT_KEY)
        if payload is None:
            return None
        return self._view(EditSession.from_wire(payload))

    def start(self, project_id: str) -> dict[str, Any]:
        """开始或恢复唯一未结束副本；已有则原样返回（幂等）。"""
        pid = ProjectId(project_id)
        existing = self._repo.aux_read(pid, CURRENT_BUCKET, _CURRENT_KEY)
        if existing is not None:
            session = EditSession.from_wire(existing)
            if session.is_open:
                return self._view(session)
            # 崩溃窗口：副本已结束但没来得及归档，补完归档再开新的。
            self._archive(session)
        session = EditSession(
            session_id=self._ids.new_id("edt"),
            project_id=project_id,
            base_commit_id=self._head(pid),
            status=SESSION_OPEN,
            started_at=self._now(),
        )
        self._repo.aux_mutate(pid, CURRENT_BUCKET, _CURRENT_KEY, lambda _current: session.to_wire())
        return self._view(session)

    def get(self, project_id: str, session_id: str) -> dict[str, Any]:
        return self._view(self._load(ProjectId(project_id), session_id))

    # -- 自动保存 -------------------------------------------------------------

    def save_draft(
        self,
        project_id: str,
        session_id: str,
        chapter_id: str,
        *,
        expected_save_seq: int,
        request_id: str,
        text: str,
    ) -> dict[str, Any]:
        """按章保存草稿：序号 CAS + 同一请求重试不重复自增。"""
        pid = ProjectId(project_id)
        session = self._load(pid, session_id)
        if not session.is_open:
            raise _violation(
                ec.EDIT_SESSION_CONFLICT,
                f"edit_sessions.save({session_id})",
                "副本已结束，不能再保存",
                "重新开始一次手工修改",
            )
        catalog = self._catalog(pid)
        entry = catalog.get(chapter_id)
        if entry is None:
            raise _violation(
                ec.EDIT_SESSION_NOT_FOUND,
                f"edit_sessions.save({chapter_id})",
                f"作品里没有章节 {chapter_id}",
                "刷新目录后重新打开该章",
            )
        base = catalog.chapter_base(chapter_id)
        now = self._now()

        def mutate(current: dict[str, Any] | None) -> dict[str, Any]:
            if current is None:
                if expected_save_seq != 0:
                    raise _violation(
                        ec.EDIT_SAVE_SEQ_CONFLICT,
                        f"edit_sessions.save({chapter_id})",
                        f"该章还没有草稿，却要求基于序号 {expected_save_seq} 保存",
                        "首次保存使用 expected_save_seq=0",
                    )
                return DraftChapter(
                    session_id=session_id,
                    chapter_id=chapter_id,
                    base_chapter_base=base,
                    text=text,
                    content_hash=_text_hash(text),
                    save_seq=1,
                    updated_at=now,
                    request_id=request_id,
                ).to_wire()
            draft = DraftChapter.from_wire(current)
            if draft.request_id == request_id:
                return current  # 同一请求的重试：原样返回，不重复自增
            if draft.save_seq != expected_save_seq:
                raise _violation(
                    ec.EDIT_SAVE_SEQ_CONFLICT,
                    f"edit_sessions.save({chapter_id})",
                    f"草稿已前进到序号 {draft.save_seq}，请求基于 {expected_save_seq}",
                    "重新读取最新草稿后再保存；迟到的旧响应不能覆盖新文本",
                    current_save_seq=draft.save_seq,
                )
            return DraftChapter(
                session_id=session_id,
                chapter_id=chapter_id,
                base_chapter_base=draft.base_chapter_base,
                text=text,
                content_hash=_text_hash(text),
                save_seq=draft.save_seq + 1,
                updated_at=now,
                request_id=request_id,
            ).to_wire()

        saved = self._repo.aux_mutate(pid, DRAFT_BUCKET, f"{session_id}:{chapter_id}", mutate)
        assert saved is not None
        return DraftChapter.from_wire(saved).to_wire()

    # -- 暂存 / 放弃 / 完成 ---------------------------------------------------

    def suspend(self, project_id: str, session_id: str) -> dict[str, Any]:
        """暂存返回：不结束副本、不产生正式版本（M04 第 3 条）。"""
        return self._view(self._load(ProjectId(project_id), session_id))

    def abandon(self, project_id: str, session_id: str) -> dict[str, Any]:
        """放弃本轮：只结束副本，正式正文与历史不变。"""
        pid = ProjectId(project_id)
        session = self._load(pid, session_id)
        if not session.is_open:
            return self._view(session)
        ended = _ended(session, SESSION_ABANDONED, None, self._now())
        self._repo.aux_mutate(pid, CURRENT_BUCKET, _CURRENT_KEY, lambda _c: ended.to_wire())
        self._archive(ended)
        return self._view(ended)

    def finish(
        self,
        project_id: str,
        session_id: str,
        *,
        operation_id: str | None = None,
        expected_save_sequences: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """修改完成：整批发布一次，成功后关闭副本（可重试）。"""
        pid = ProjectId(project_id)
        session = self._load(pid, session_id)
        if not session.is_open:
            return self._closed_result(pid, session)

        drafts = self._drafts(pid, session_id)
        if not drafts:
            raise _violation(
                ec.EDIT_SESSION_EMPTY,
                f"edit_sessions.finish({session_id})",
                "本轮没有任何修改内容",
                "先修改至少一章，或放弃本轮",
            )
        if expected_save_sequences:
            for draft in drafts:
                expected = expected_save_sequences.get(draft.chapter_id)
                if expected is not None and expected != draft.save_seq:
                    raise _violation(
                        ec.EDIT_SAVE_SEQ_CONFLICT,
                        f"edit_sessions.finish({draft.chapter_id})",
                        f"完成时草稿序号是 {draft.save_seq}，请求冻结的是 {expected}",
                        "确认最新草稿后再完成；期间可能还有未确认的自动保存",
                        current_save_seq=draft.save_seq,
                    )

        op_id = operation_id or (
            "op_"
            + content_hash(
                [
                    "edit.finish",
                    project_id,
                    session_id,
                    [[d.chapter_id, d.save_seq] for d in drafts],
                ]
            )[:16]
        )
        command = manuscript_command(
            manuscript_commands.save_drafts,
            drafts=tuple(
                manuscript_commands.ChapterDraft(
                    chapter_id=draft.chapter_id,
                    text=draft.text,
                    expected_chapter_base=draft.base_chapter_base,
                )
                for draft in drafts
            ),
        )
        result = self._publisher.publish_command(
            project_id=pid,
            command=command,
            operation_id=op_id,
            kind="edit_session.finish",
            summary=f"完成手工修改：{len(drafts)} 章",
            refs={"session_id": session_id},
        )
        closed = _ended(session, SESSION_CLOSED, result.operation_id, self._now())
        self._repo.aux_mutate(pid, CURRENT_BUCKET, _CURRENT_KEY, lambda _c: closed.to_wire())
        self._archive(closed)
        return {
            "status": result.status,
            "session_id": session_id,
            "operation_id": result.operation_id,
            "commit_id": result.commit_id,
            "changed_chapter_ids": list(result.envelope.changed_chapter_ids),
        }

    # -- 内部 -----------------------------------------------------------------

    def _closed_result(self, pid: ProjectId, session: EditSession) -> dict[str, Any]:
        """已完成副本的重试：返回当时的操作结果，不再发布第二个版本。"""
        if session.closed_operation_id is None:
            raise _violation(
                ec.EDIT_SESSION_CONFLICT,
                f"edit_sessions.finish({session.session_id})",
                "副本已放弃，没有可发布的修改",
                "重新开始一次手工修改",
            )
        envelope = self._operations.get_operation(pid.value, session.closed_operation_id)
        return {
            "status": envelope.status,
            "session_id": session.session_id,
            "operation_id": envelope.operation_id,
            "commit_id": envelope.commit_id,
            "changed_chapter_ids": list(envelope.changed_chapter_ids),
        }

    def _view(self, session: EditSession) -> dict[str, Any]:
        pid = ProjectId(session.project_id)
        return {
            "session": session.to_wire(),
            "drafts": [draft.to_wire() for draft in self._drafts(pid, session.session_id)],
        }

    def _drafts(self, pid: ProjectId, session_id: str) -> list[DraftChapter]:
        prefix = f"{session_id}:"
        records = self._repo.aux_list(pid, DRAFT_BUCKET)
        drafts = [
            DraftChapter.from_wire(payload)
            for key, payload in records.items()
            if key.startswith(prefix)
        ]
        return sorted(drafts, key=lambda draft: draft.chapter_id)

    def _load(self, pid: ProjectId, session_id: str) -> EditSession:
        current = self._repo.aux_read(pid, CURRENT_BUCKET, _CURRENT_KEY)
        if current is not None and str(current.get("session_id")) == session_id:
            return EditSession.from_wire(current)
        archived = self._repo.aux_read(pid, SESSION_BUCKET, session_id)
        if archived is not None:
            return EditSession.from_wire(archived)
        raise _violation(
            ec.EDIT_SESSION_NOT_FOUND,
            f"edit_sessions({session_id})",
            "修改副本不存在或不属于这个作品",
            "刷新后重新开始手工修改",
        )

    def _archive(self, session: EditSession) -> None:
        """归档已结束副本：先写历史，再删当前指针（崩溃可重入）。"""
        pid = ProjectId(session.project_id)
        self._repo.aux_mutate(pid, SESSION_BUCKET, session.session_id, lambda _c: session.to_wire())
        self._repo.aux_mutate(pid, CURRENT_BUCKET, _CURRENT_KEY, lambda _c: None)

    def _head(self, pid: ProjectId) -> str | None:
        branch = self._repo.get_branch(pid, "main")
        return branch.head_commit_id.value if branch and branch.head_commit_id else None

    def _catalog(self, pid: ProjectId) -> ManuscriptCatalog:
        branch = self._repo.get_branch(pid, "main")
        if branch is None or branch.head_commit_id is None:
            return ManuscriptCatalog(commit_id=None, entries=())
        return ManuscriptCatalog.from_snapshot(self._repo.get_snapshot(pid, branch.head_commit_id))

    def _now(self) -> str:
        return str(self._clock.now().isoformat())


def _text_hash(text: str) -> str:
    return content_hash({"paragraphs": split_paragraphs(text)})


def _ended(
    session: EditSession,
    status: str,
    operation_id: str | None,
    now: str,
) -> EditSession:
    return EditSession(
        session_id=session.session_id,
        project_id=session.project_id,
        base_commit_id=session.base_commit_id,
        status=status,
        started_at=session.started_at,
        ended_at=now,
        closed_operation_id=operation_id,
    )


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)
