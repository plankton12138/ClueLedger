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

from dataclasses import replace
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
    SOURCE_AGENT,
    SOURCE_UNKNOWN,
    DraftChapter,
    EditSession,
    EditSnapshot,
    merge_saved_sources,
    normalize_sources,
    snapshot_id_for,
    snapshot_key_for,
    source_runs,
)
from hastings.domain.manuscript.merge import merge_texts
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.repository.serialization import content_hash

__all__ = [
    "CURRENT_BUCKET",
    "DRAFT_BUCKET",
    "EditSessionService",
    "SESSION_BUCKET",
    "SNAPSHOT_BUCKET",
    "SNAPSHOT_KEY_BUCKET",
]

#: 唯一未结束副本（key 固定为 ``current``）。
CURRENT_BUCKET = "edit_session_current"
#: 已结束副本归档（key = session_id）。
SESSION_BUCKET = "edit_session"
#: 章草稿（key = ``<session_id>:<chapter_id>``）。
DRAFT_BUCKET = "edit_draft"
#: 修改稿快照（key = snapshot_id，不可变）。
SNAPSHOT_BUCKET = "edit_snapshot"
#: 快照索引（key = 冻结点，不含文本哈希）：副本前进了也能回放同一引用。
SNAPSHOT_KEY_BUCKET = "edit_snapshot_key"

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

    def start(self, project_id: str, chapter_ids: list[str] | None = None) -> dict[str, Any]:
        """开始或恢复唯一未结束副本；已有则原样返回（幂等）。

        ``chapter_ids`` 是本轮章节清单（V03-5.1）：开始时就声明要改哪几章。
        只收作品里已存在的章；重复项按首次出现去重，顺序即声明顺序（界面
        据此展示）。恢复已有副本时忽略传入的声明——清单以副本里的为准，
        避免后到的请求悄悄改写正在进行的一轮。
        """
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
            chapters=self._validate_chapters(pid, chapter_ids or []),
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
        # 草稿基础绑定在**会话打开时**的版本——作者读到的那一版。早先这里取当前
        # 章基础，于是「打开旧正文 → 别人保存新稿 → 首次自动保存」会把新基础绑给
        # 旧文本，之后的「修改完成」拿着新基础放行，直接覆盖别人的保存。
        bound = self._base_at_open(pid, session.base_commit_id, chapter_id)
        if bound is None:
            # 章在会话打开时还不存在（之后才建的章）：作者的文本本来就基于建章后
            # 的版本，取当前基础；会话本身没有基础版本时同样如此。
            base = catalog.chapter_base(chapter_id)
            base_commit = self._head(pid) or ""
        else:
            base, base_commit = bound
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
                    base_commit_id=base_commit,
                    paragraph_sources=normalize_sources(text, None),
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
                base_commit_id=draft.base_commit_id,
                # 未改动的段落沿用原来源；改过/新插入的段落才是人工输入。
                paragraph_sources=merge_saved_sources(draft.text, draft.paragraph_sources, text),
            ).to_wire()

        saved = self._repo.aux_mutate(pid, DRAFT_BUCKET, f"{session_id}:{chapter_id}", mutate)
        assert saved is not None
        # 首次保存的章进入本轮清单（有机流式修改不事先声明，V03-5.1）。
        self._join_round(pid, session_id, chapter_id)
        return DraftChapter.from_wire(saved).to_wire()

    # -- 快照与生成文本（V03-5.3） ------------------------------------------

    def create_snapshot(
        self,
        project_id: str,
        session_id: str,
        *,
        chapter_id: str,
        save_seq: int,
        text_hash: str | None = None,
        start_paragraph: int | None = None,
        end_paragraph: int | None = None,
    ) -> dict[str, Any]:
        """冻结某个已保存的 ``save_seq``（可选段落选区），返回不可变引用。

        只接受**已经落盘的草稿**：请求里的序号或哈希与草稿不符时显式冲突，
        不能拿界面上的未保存缓冲冒充快照。快照 ID 由冻结点确定性派生，同一
        冻结点的重复请求回放同一个引用。
        """
        pid = ProjectId(project_id)
        session = self._load(pid, session_id)
        if not session.is_open:
            raise _violation(
                ec.EDIT_SESSION_CONFLICT,
                f"edit_sessions.snapshot({session_id})",
                "副本已结束，不能再创建快照",
                "重新开始一次手工修改",
            )
        # 同一冻结点（章 + 序号 + 选区）已有快照：即使副本已经前进，也回放
        # 原引用——不可变引用不能因为后来的保存而失效。
        key = snapshot_key_for(
            project_id=project_id,
            session_id=session_id,
            chapter_id=chapter_id,
            save_seq=save_seq,
            start=start_paragraph,
            end=end_paragraph,
        )
        indexed = self._repo.aux_read(pid, SNAPSHOT_KEY_BUCKET, key)
        if indexed is not None:
            return self.get_snapshot(project_id, session_id, str(indexed["snapshot_id"]))
        draft = next(
            (item for item in self._drafts(pid, session_id) if item.chapter_id == chapter_id),
            None,
        )
        if draft is None:
            raise _violation(
                ec.EDIT_SESSION_CONFLICT,
                f"edit_sessions.snapshot({chapter_id})",
                "该章还没有保存过草稿，无法冻结快照",
                "先保存这一章的修改，再冻结引用",
            )
        if draft.save_seq != save_seq:
            raise _violation(
                ec.EDIT_SNAPSHOT_CONFLICT,
                f"edit_sessions.snapshot({chapter_id})",
                f"草稿当前序号是 {draft.save_seq}，请求冻结的是 {save_seq}",
                "重新读取最新草稿后再冻结；未保存的缓冲不能当作快照",
                current_save_seq=draft.save_seq,
                requested_save_seq=save_seq,
            )
        paragraphs = split_paragraphs(draft.text)
        start, end = _selection_bounds(start_paragraph, end_paragraph, len(paragraphs))
        text = "\n\n".join(paragraphs[start:end])
        actual_hash = _text_hash(text)
        if text_hash is not None and text_hash != actual_hash:
            raise _violation(
                ec.EDIT_SNAPSHOT_CONFLICT,
                f"edit_sessions.snapshot({chapter_id})",
                "快照文本哈希与草稿当前内容不符",
                "重新读取草稿后按最新内容冻结；不要用旧界面的哈希",
                current_save_seq=draft.save_seq,
                current_text_hash=actual_hash,
            )
        sources = normalize_sources(draft.text, draft.paragraph_sources)[start:end]
        snapshot = EditSnapshot(
            snapshot_id=snapshot_id_for(
                project_id=project_id,
                session_id=session_id,
                chapter_id=chapter_id,
                save_seq=save_seq,
                start=start if (start_paragraph is not None) else None,
                end=end if (end_paragraph is not None) else None,
                text_hash=actual_hash,
            ),
            project_id=project_id,
            session_id=session_id,
            chapter_id=chapter_id,
            save_seq=save_seq,
            text=text,
            text_hash=actual_hash,
            paragraph_sources=sources,
            base_chapter_base=draft.base_chapter_base,
            base_commit_id=draft.base_commit_id,
            selection_start=start if start_paragraph is not None else None,
            selection_end=end if end_paragraph is not None else None,
            created_at=self._now(),
        )

        def mutate(current: dict[str, Any] | None) -> dict[str, Any]:
            # 快照不可变：确定性 ID 已存在时原样返回（同一冻结点的重试）。
            return current if current is not None else snapshot.to_wire()

        stored = self._repo.aux_mutate(pid, SNAPSHOT_BUCKET, snapshot.snapshot_id, mutate)
        assert stored is not None
        # 索引与快照分开写：先快照后索引，崩溃重试会命中确定性 ID，不会丢引用。
        self._repo.aux_mutate(
            pid,
            SNAPSHOT_KEY_BUCKET,
            key,
            lambda _current: {"snapshot_id": snapshot.snapshot_id},
        )
        return EditSnapshot.from_wire(stored).to_wire()

    def get_snapshot(self, project_id: str, session_id: str, snapshot_id: str) -> dict[str, Any]:
        """读取快照：只读，不随副本前进变化。"""
        pid = ProjectId(project_id)
        payload = self._repo.aux_read(pid, SNAPSHOT_BUCKET, snapshot_id)
        if payload is None:
            raise _violation(
                ec.EDIT_SNAPSHOT_NOT_FOUND,
                f"edit_sessions.snapshot({snapshot_id})",
                "快照不存在或已被清理",
                "核对 snapshot_id；快照属于创建它的作品与副本",
            )
        snapshot = EditSnapshot.from_wire(payload)
        if snapshot.session_id != session_id or snapshot.project_id != project_id:
            raise _violation(
                ec.EDIT_SNAPSHOT_NOT_FOUND,
                f"edit_sessions.snapshot({snapshot_id})",
                "快照不属于这个副本",
                "从创建快照的副本读取引用",
            )
        return snapshot.to_wire()

    def apply_generated_text(
        self,
        project_id: str,
        session_id: str,
        chapter_id: str,
        *,
        expected_save_seq: int,
        request_id: str,
        text: str,
        candidate_id: str,
        preview_hash: str,
        start_paragraph: int | None = None,
        end_paragraph: int | None = None,
    ) -> dict[str, Any]:
        """把模型候选写进副本，并把写入段落标为 ``agent``（V03-5.3）。

        与自动保存共用保存序号 CAS：迟到候选不能覆盖作者更新的草稿；同一
        ``request_id`` 的重试原样返回。选区为空表示整章替换，否则只替换
        ``[start, end)`` 段落，选区外的人工段落保持原来源——这就是「混合
        来源」。这个入口只写副本，不发布正式正文；采用仍由作者显式触发。
        """
        pid = ProjectId(project_id)
        session = self._load(pid, session_id)
        if not session.is_open:
            raise _violation(
                ec.EDIT_SESSION_CONFLICT,
                f"edit_sessions.apply({session_id})",
                "副本已结束，不能再写入候选",
                "重新开始一次手工修改",
            )
        catalog = self._catalog(pid)
        if catalog.get(chapter_id) is None:
            raise _violation(
                ec.EDIT_SESSION_NOT_FOUND,
                f"edit_sessions.apply({chapter_id})",
                f"作品里没有章节 {chapter_id}",
                "刷新目录后重新打开该章",
            )
        bound = self._base_at_open(pid, session.base_commit_id, chapter_id)
        if bound is None:
            base = catalog.chapter_base(chapter_id)
            base_commit = self._head(pid) or ""
        else:
            base, base_commit = bound
        now = self._now()
        generated = tuple(str(item) for item in split_paragraphs(text))
        if not generated:
            raise _violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"edit_sessions.apply({chapter_id})",
                "候选正文为空，没有可写入的段落",
                "重新生成候选，或改用直接输入",
            )

        def mutate(current: dict[str, Any] | None) -> dict[str, Any]:
            if current is None:
                if expected_save_seq != 0:
                    raise _violation(
                        ec.EDIT_SAVE_SEQ_CONFLICT,
                        f"edit_sessions.apply({chapter_id})",
                        f"该章还没有草稿，却要求基于序号 {expected_save_seq} 写入",
                        "首次写入使用 expected_save_seq=0",
                    )
                if start_paragraph is not None or end_paragraph is not None:
                    raise _violation(
                        ec.MANUSCRIPT_INVALID_OPERATION,
                        f"edit_sessions.apply({chapter_id})",
                        "该章还没有草稿，没有可替换的段落选区",
                        "首次写入不传选区（整章替换），或先保存一版草稿",
                    )
                paragraphs: list[str] = list(generated)
                sources: list[str] = [SOURCE_AGENT] * len(generated)
                save_seq = 1
                draft_base, draft_commit = base, base_commit
            else:
                draft = DraftChapter.from_wire(current)
                if draft.request_id == request_id:
                    return current  # 同一请求重试：不重复自增
                if draft.save_seq != expected_save_seq:
                    raise _violation(
                        ec.EDIT_SAVE_SEQ_CONFLICT,
                        f"edit_sessions.apply({chapter_id})",
                        f"草稿已前进到序号 {draft.save_seq}，候选基于 {expected_save_seq}",
                        "重新读取最新草稿后再写入；迟到的候选不能覆盖新文本",
                        current_save_seq=draft.save_seq,
                    )
                old_paragraphs = split_paragraphs(draft.text)
                old_sources = normalize_sources(draft.text, draft.paragraph_sources)
                start, end = _selection_bounds(start_paragraph, end_paragraph, len(old_paragraphs))
                if start_paragraph is None and end_paragraph is None:
                    start, end = 0, len(old_paragraphs)
                paragraphs = [
                    *old_paragraphs[:start],
                    *generated,
                    *old_paragraphs[end:],
                ]
                sources = [
                    *old_sources[:start],
                    *([SOURCE_AGENT] * len(generated)),
                    *old_sources[end:],
                ]
                save_seq = draft.save_seq + 1
                draft_base, draft_commit = draft.base_chapter_base, draft.base_commit_id
            combined = "\n\n".join(paragraphs)
            return DraftChapter(
                session_id=session_id,
                chapter_id=chapter_id,
                base_chapter_base=draft_base,
                text=combined,
                content_hash=_text_hash(combined),
                save_seq=save_seq,
                updated_at=now,
                request_id=request_id,
                base_commit_id=draft_commit,
                paragraph_sources=tuple(sources),
            ).to_wire()

        saved = self._repo.aux_mutate(pid, DRAFT_BUCKET, f"{session_id}:{chapter_id}", mutate)
        assert saved is not None
        self._join_round(pid, session_id, chapter_id)
        draft = DraftChapter.from_wire(saved)
        return {
            "session_id": session_id,
            "chapter_id": chapter_id,
            "candidate_id": candidate_id,
            "preview_hash": preview_hash,
            "draft": draft.to_wire(),
        }

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
        conversation_id: str | None = None,
        merge_choices: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """修改完成：整批发布一次，成功后关闭副本（可重试）。

        ``merge_choices`` 是重叠冲突的逐处选择（V03-5.2）：``[{"chapter_id":
        ..., "conflict": 序号, "choice": "ours"|"theirs"}]``，序号即上一次
        409 报告里该章冲突的顺序。选择必须**逐处覆盖**本次重算出的冲突集合——
        漏选、错号都会带着新报告再次 409（冲突列表可能因期间的新保存而变）。
        """
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
        # 完成前 flush 核对（V03-5.1）：本轮清单里的每一章都必须已落草稿。
        # 声明了却没保存的章不允许「带病完成」——否则作者以为改完了，实际
        # 有一章的修改从没进入这次提交，而完成又会关闭副本、无从补救。
        saved_ids = {draft.chapter_id for draft in drafts}
        missing = [cid for cid in session.chapters if cid not in saved_ids]
        if missing:
            raise _violation(
                ec.EDIT_SESSION_UNFLUSHED,
                f"edit_sessions.finish({session_id})",
                f"本轮还有 {len(missing)} 章没有保存过修改：{missing}",
                "先保存这些章的修改，或放弃本轮后重新声明；未保存的章不进入本次发布",
                missing=missing,
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

        # 正式稿前进时三方合并（V03-5.2）：base=作者读到的那一版、ours=草稿、
        # theirs=当前正式稿。干净则按**当前基础**发布合并稿——别人的保存不被
        # 覆盖，作者的修改也不丢失；重叠则整批拒绝并给出逐段事实，副本与草稿
        # 原样保留，作者改完再完成。
        current = self._catalog(pid)
        picked = _merge_choices(merge_choices or [])
        plans: list[tuple[str, str, str, tuple[str, ...]]] = []
        merged_chapters: list[str] = []
        conflicts: list[dict[str, Any]] = []
        for draft in drafts:
            entry = current.get(draft.chapter_id)
            theirs_base = current.chapter_base(draft.chapter_id) if entry is not None else None
            if theirs_base is None:
                # 章在正式稿里不存在了（本版无删除原语，正常不会发生）：按冲突
                # 处理，不猜作者想怎么办。
                conflicts.append(
                    {
                        "chapter_id": draft.chapter_id,
                        "reason": "chapter_missing",
                        "conflicts": [],
                    }
                )
                continue
            if theirs_base == draft.base_chapter_base:
                plans.append(
                    (
                        draft.chapter_id,
                        draft.text,
                        draft.base_chapter_base,
                        normalize_sources(draft.text, draft.paragraph_sources),
                    )
                )
                continue
            base_text = self._text_at(
                pid, draft.base_commit_id or session.base_commit_id, draft.chapter_id
            )
            if base_text is None:
                # 找不到作者读取时的版本（旧草稿没有 base_commit_id 且会话基础
                # 里没有这一章）：无法安全合并，按冲突处理并说明原因。
                conflicts.append(
                    {
                        "chapter_id": draft.chapter_id,
                        "reason": "base_unavailable",
                        "conflicts": [],
                    }
                )
                continue
            wanted = {
                index: choice
                for (chapter_id, index), choice in picked.items()
                if chapter_id == draft.chapter_id
            }
            merged = merge_texts(
                base=base_text,
                ours=draft.text,
                theirs=current.manuscript().text_of(draft.chapter_id),
                choices=wanted,
            )
            if merged.clean:
                # 合并过的章混入了正式稿（来源未必是作者这次输入），逐段来源
                # 无法再精确判断：保守标 unknown，绝不把生成文字算成人工。
                plans.append(
                    (
                        draft.chapter_id,
                        merged.text,
                        theirs_base,
                        (SOURCE_UNKNOWN,) * len(split_paragraphs(merged.text)),
                    )
                )
                merged_chapters.append(draft.chapter_id)
                continue
            # 有冲突：作者的选择必须逐处覆盖本次重算出来的冲突集合。漏选或序号
            # 对不上（期间又有新保存）都重新报告——不猜作者想选哪边。
            if set(wanted) != set(range(len(merged.conflicts))):
                conflicts.append(
                    {
                        "chapter_id": draft.chapter_id,
                        "reason": "overlapping_changes",
                        "conflicts": [item.to_wire() for item in merged.conflicts],
                    }
                )
                continue
            plans.append(
                (
                    draft.chapter_id,
                    merged.text,
                    theirs_base,
                    (SOURCE_UNKNOWN,) * len(split_paragraphs(merged.text)),
                )
            )
            merged_chapters.append(draft.chapter_id)
        if conflicts:
            titles = []
            for item in conflicts:
                entry = current.get(str(item["chapter_id"]))
                titles.append(
                    str(entry.model().title) if entry is not None else str(item["chapter_id"])
                )
            raise _violation(
                ec.EDIT_SESSION_MERGE_CONFLICT,
                f"edit_sessions.finish({session_id})",
                f"{len(conflicts)} 章在副本期间被正式稿修改，且与你的修改重叠：{titles}",
                "逐段选择保留我的版本、采用正式版本，或自行修改后保存；副本不会丢失",
                conflicts=conflicts,
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
                    chapter_id=chapter_id,
                    text=text,
                    expected_chapter_base=expected_base,
                )
                for chapter_id, text, expected_base, _sources in plans
            ),
        )
        # 选择进 refs：版本历史里能读出「这次合并在哪几处取了哪边」。
        refs: dict[str, Any] = {"session_id": session_id}
        if picked:
            refs["merge_choices"] = [
                {"chapter_id": chapter_id, "conflict": index, "choice": choice}
                for (chapter_id, index), choice in sorted(picked.items())
            ]
        # 段落来源随版本保留（V03-5.3）：生成段落不能事后被当作人工编辑证据。
        # 只记录这次真的会变内容的章；与正文同哈希的章发布时是 no_change。
        provenance: dict[str, list[dict[str, Any]]] = {}
        for chapter_id, text, _expected_base, sources in plans:
            if _text_hash(text) == _text_hash(current.manuscript().text_of(chapter_id)):
                continue
            provenance[chapter_id] = source_runs(sources)
        if provenance:
            refs["paragraph_sources"] = provenance
        result = self._publisher.publish_command(
            project_id=pid,
            command=command,
            operation_id=op_id,
            kind="edit_session.finish",
            summary=f"完成手工修改：{len(drafts)} 章",
            refs=refs,
            origin_conversation_id=conversation_id,
            # 按**当前 head** 求值：合并稿是叠在最新正式稿上的，各命令的
            # expected_chapter_base 就是当前基础；求值到提交之间若有新保存，
            # 领域检查与提交层逐对象 CAS 都会显式冲突，不会静默覆盖。
        )
        closed = _ended(session, SESSION_CLOSED, result.operation_id, self._now())
        self._repo.aux_mutate(pid, CURRENT_BUCKET, _CURRENT_KEY, lambda _c: closed.to_wire())
        self._archive(closed)
        published = set(result.envelope.changed_chapter_ids)
        return {
            "status": result.status,
            "session_id": session_id,
            "operation_id": result.operation_id,
            "commit_id": result.commit_id,
            "changed_chapter_ids": list(result.envelope.changed_chapter_ids),
            # 如实报告哪些章是**合并后真正发布**的（V03-5.2）：作者有权知道
            # 「我完成的不只是我写的」。走过合并但没有任何可发布内容的章
            # （作者没真改）不在其中——它什么也没改变。
            "merged_chapters": [cid for cid in merged_chapters if cid in published],
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

    def _validate_chapters(self, pid: ProjectId, chapter_ids: list[str]) -> tuple[str, ...]:
        """本轮清单校验：章必须存在；重复按首次出现去重，保序。

        空清单合法（有机流式修改不事先声明，首次保存即入列）。
        """
        ordered = tuple(dict.fromkeys(str(c).strip() for c in chapter_ids if str(c).strip()))
        if not ordered:
            return ()
        catalog = self._catalog(pid)
        missing = [cid for cid in ordered if catalog.get(cid) is None]
        if missing:
            raise _violation(
                ec.MANUSCRIPT_UNKNOWN_CHAPTER,
                f"edit_sessions.start({pid.value})",
                f"本轮清单里的章不存在：{missing}",
                "刷新章节目录后重新声明；清单只收作品里已有的章",
                missing=missing,
            )
        return ordered

    def _join_round(self, pid: ProjectId, session_id: str, chapter_id: str) -> None:
        """把首次保存的章加进本轮清单（V03-5.1）。

        与草稿写入是两个事务，顺序固定为**先草稿后清单**：中途失败最坏是
        「已保存但不在清单里」——草稿仍随完成发布，界面清单下次保存自愈；
        反过来（在清单里却没有草稿）才会挡住完成，那是要避免的方向。
        """

        def mutate(current: dict[str, Any] | None) -> dict[str, Any] | None:
            # 没有当前副本（已放弃/已完成）：无处可加，也不是错误。
            if current is None:
                return None
            session = EditSession.from_wire(current)
            if chapter_id in session.chapters:
                return current
            return replace(session, chapters=(*session.chapters, chapter_id)).to_wire()

        self._repo.aux_mutate(pid, CURRENT_BUCKET, _CURRENT_KEY, mutate)

    def _text_at(self, pid: ProjectId, commit_id: str | None, chapter_id: str) -> str | None:
        """任意提交上某章的整章文本；取不到返回 None（调用方按冲突降级）。"""
        if not commit_id:
            return None
        try:
            snapshot = self._repo.get_snapshot(pid, CommitId(commit_id))
        except ViolationError:
            return None
        catalog = ManuscriptCatalog.from_snapshot(snapshot)
        if catalog.get(chapter_id) is None:
            return None
        return catalog.manuscript().text_of(chapter_id)

    def _base_at_open(
        self, pid: ProjectId, base_commit_id: str | None, chapter_id: str
    ) -> tuple[str, str] | None:
        """会话打开时该章的基础版本与对应提交；取不到返回 None。

        读取失败（基础提交不存在）同样返回 None：副本仍可用，只是这一章按当前
        版本绑定——比让整次保存失败更合理，且后续保存会沿用这个基础。
        """
        if not base_commit_id:
            return None
        try:
            snapshot = self._repo.get_snapshot(pid, CommitId(base_commit_id))
        except ViolationError:
            return None
        catalog = ManuscriptCatalog.from_snapshot(snapshot)
        if catalog.get(chapter_id) is None:
            return None
        return catalog.chapter_base(chapter_id), base_commit_id

    def _now(self) -> str:
        return str(self._clock.now().isoformat())


def _merge_choices(raw: list[dict[str, Any]]) -> dict[tuple[str, int], str]:
    """归一化冲突选择：``{(chapter_id, 冲突序号): "ours"|"theirs"}``。

    只做形状归一（章节 ID 去空格、序号转 int、取值原样）；「取哪边」的语义
    在合并引擎里（``theirs`` 取正式稿，其余取副本）。重复的 (章, 序号) 以后
    出现的为准——同一处冲突给两个说法没有意义，按最后送达的处理。
    """
    picked: dict[tuple[str, int], str] = {}
    for item in raw:
        chapter_id = str(item.get("chapter_id", "")).strip()
        index = item.get("conflict")
        if not chapter_id or not isinstance(index, int):
            raise _violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                "edit_sessions.finish(merge_choices)",
                f"冲突选择的形状不对：{item!r}",
                "每项给 chapter_id（章 ID）与 conflict（报告里的冲突序号）",
            )
        picked[(chapter_id, index)] = str(item.get("choice", "ours"))
    return picked


def _text_hash(text: str) -> str:
    return content_hash({"paragraphs": split_paragraphs(text)})


def _selection_bounds(start: int | None, end: int | None, count: int) -> tuple[int, int]:
    """段落选区归一：两个都给才是选区，范围左闭右开且不能为空。"""
    if start is None and end is None:
        return 0, count
    if start is None or end is None:
        raise _violation(
            ec.MANUSCRIPT_INVALID_OPERATION,
            "edit_sessions.selection",
            "段落选区必须同时给出起止位置",
            "同时给 start_paragraph 与 end_paragraph，或都不给表示整章",
        )
    if start < 0 or end > count or start >= end:
        raise _violation(
            ec.MANUSCRIPT_INVALID_OPERATION,
            "edit_sessions.selection",
            f"选区 [{start}, {end}) 超出本章 {count} 个段落的范围",
            "按段落下标给出非空选区；只改整章时不要传选区",
            start_paragraph=start,
            end_paragraph=end,
            paragraph_count=count,
        )
    return start, end


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
        # 本轮清单是会话事实：结束后仍要能在归档里读出改过哪几章。
        chapters=session.chapters,
    )


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)
