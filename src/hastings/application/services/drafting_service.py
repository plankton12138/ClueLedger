"""试写候选服务（V03-3.3/3.4/3.5）：从账本与作者要求出发，产出只读候选。

流程与边界：

1. **起草输入**：账本引用 + 作者要求 + 视角 + 目标 + 篇幅 + 叙事边界。
   空项目（``head is None``）是合法的首章来源；没有完整大纲、没有决定凶手
   都不阻断（计划 M05）。
2. **候选落辅助表**，不提交、不改正式正文；重复点「开始」由幂等键回放。
3. **继续调整**：生成新修订，旧修订变为不可采用（哈希与修订号同时失效）。
4. **采用**：携带预览哈希，服务端重算后经 ``PublishManuscriptChange`` 一次
   提交；新章追加在末尾，现有章按生成时基础做乐观并发校验。

生成器以 callable 注入（``writer``），测试用假实现；生产由 REST 层把已保存
连接与模型适配进来。本模块不直接依赖任何 HTTP 客户端。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from hastings.application.contexts.collector import BackgroundService
from hastings.application.errors import ViolationError
from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.ports.clock import Clock
from hastings.application.ports.ids import IdGenerator
from hastings.application.queries.ledger import entry_view
from hastings.application.queries.story_queries import StoryQueries
from hastings.application.services.commit_service import CommitService
from hastings.application.services.disclosure_service import DisclosureService
from hastings.application.services.publish_manuscript_change import PublishManuscriptChange
from hastings.domain.manuscript import commands as manuscript_commands
from hastings.domain.manuscript import drafting
from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.repository.serialization import content_hash

__all__ = ["DraftingService", "WrittenChapter"]

#: 辅助表桶名：候选与创建幂等键分开存，键值都是候选 ID。
CANDIDATE_BUCKET = "draft_candidates"
CANDIDATE_KEY_BUCKET = "draft_candidate_keys"


@dataclass(frozen=True, slots=True)
class WrittenChapter:
    """生成结果：标题可空（用 brief 的标题或默认章名），正文必须非空。"""

    text: str
    title: str = ""
    model: str = ""


ChapterWriterFn = Callable[..., Awaitable[WrittenChapter]]


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details or None)


class DraftingService:
    def __init__(
        self,
        repo: CommitService,
        publisher: PublishManuscriptChange,
        edit_sessions: Any,
        ids: IdGenerator,
        clock: Clock,
        writer: ChapterWriterFn | None = None,
        background: BackgroundService | None = None,
        disclosure: DisclosureService | None = None,
    ) -> None:
        self._repo = repo
        self._publisher = publisher
        self._edit_sessions = edit_sessions
        self._ids = ids
        self._clock = clock
        self._writer = writer
        # 常驻背景（V03-4.2）：按试写的基础版本取，不随当前 head 漂移。
        self._background = background
        # 披露边界（V03-4.7）：采用改写稿前检查有没有提前披露后文真相。
        self._disclosure = disclosure

    # -- 创建与调整 ----------------------------------------------------------

    async def create(
        self,
        project_id: str,
        *,
        target: str = drafting.TARGET_NEW_CHAPTER,
        chapter_title: str = "",
        instruction: str = "",
        viewpoint: str = "",
        length_target: str = "",
        narrative_boundary: str = "",
        story_chapter_id: str = "",
        refs: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """按 brief 生成第一版候选；同一幂等键重放同一候选，不重复调模型。"""
        pid = ProjectId(project_id)
        if idempotency_key:
            existing = self._repo.aux_read(pid, CANDIDATE_KEY_BUCKET, idempotency_key)
            if existing is not None:
                replay = self.get(project_id, str(existing["candidate_id"]))
                return replay
        if not instruction.strip():
            raise _violation(
                ec.DRAFT_EMPTY_RESULT,
                f"drafts.create({project_id})",
                "没有写下这一章要发生什么，模型没有可依据的要求",
                "填写作者要求（哪怕一句话），再开始试写",
            )
        catalog = self._catalog(pid)
        if target != drafting.TARGET_NEW_CHAPTER:
            catalog.require_chapter(target, where="drafts.create")
        brief = drafting.DraftBrief(
            brief_id=self._ids.new_id("drf"),
            project_id=project_id,
            target=target,
            chapter_title=chapter_title.strip(),
            instruction=instruction.strip(),
            viewpoint=viewpoint.strip(),
            length_target=length_target.strip(),
            narrative_boundary=narrative_boundary.strip(),
            story_chapter_id=story_chapter_id.strip(),
            refs=tuple(str(r) for r in (refs or [])),
            base_head=catalog.commit_id,
            base_chapter=catalog.chapter_base(target)
            if target != drafting.TARGET_NEW_CHAPTER
            else None,
            created_at=self._now(),
        )
        candidate_id = self._ids.new_id("cnd")
        written = await self._generate(brief, previous_text="", adjustment="")
        revision = drafting.CandidateRevision(
            revision=1,
            title=written.title or brief.chapter_title or "未命名章节",
            text=written.text,
            preview_hash=drafting.preview_hash(
                candidate_id=candidate_id,
                revision=1,
                title=written.title or brief.chapter_title or "未命名章节",
                text=written.text,
                base_head=brief.base_head,
            ),
            instruction=brief.instruction,
            created_at=self._now(),
            model=written.model,
        )
        candidate = drafting.ChapterCandidate(
            candidate_id=candidate_id,
            project_id=project_id,
            brief=brief,
            current_revision=1,
            status=drafting.CANDIDATE_READY,
            revisions=(revision,),
            created_at=self._now(),
            updated_at=self._now(),
        )
        self._write(pid, CANDIDATE_BUCKET, candidate_id, candidate.to_dict())
        if idempotency_key:
            self._write(pid, CANDIDATE_KEY_BUCKET, idempotency_key, {"candidate_id": candidate_id})
        return self._view(candidate)

    async def revise(
        self,
        project_id: str,
        candidate_id: str,
        *,
        instruction: str = "",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """继续调整：生成新修订，旧修订不再是可采用目标。"""
        pid = ProjectId(project_id)
        candidate = self._load(pid, candidate_id)
        if expected_revision is not None and candidate.current_revision != expected_revision:
            raise _violation(
                ec.DRAFT_REVISION_CONFLICT,
                f"drafts.revise({candidate_id})",
                f"候选已推进到修订 {candidate.current_revision}，本次调整基于 {expected_revision}",
                "重新读取候选后再调整；不要用过期的修订覆盖新内容",
                current_revision=candidate.current_revision,
                expected_revision=expected_revision,
            )
        if candidate.status != drafting.CANDIDATE_READY:
            raise _violation(
                ec.DRAFT_CANDIDATE_NOT_ADOPTABLE,
                f"drafts.revise({candidate_id})",
                f"候选当前状态是 {candidate.status}，不能再调整",
                "从新的一次试写开始",
                status=candidate.status,
            )
        latest = candidate.latest
        assert latest is not None  # 候选至少有一版；存储损坏才会为 None
        written = await self._generate(
            candidate.brief, previous_text=latest.text, adjustment=instruction.strip()
        )
        revision_no = candidate.current_revision + 1
        title = written.title or latest.title
        revision = drafting.CandidateRevision(
            revision=revision_no,
            title=title,
            text=written.text,
            preview_hash=drafting.preview_hash(
                candidate_id=candidate.candidate_id,
                revision=revision_no,
                title=title,
                text=written.text,
                base_head=candidate.brief.base_head,
            ),
            instruction=instruction.strip() or latest.instruction,
            created_at=self._now(),
            model=written.model,
        )
        updated = replace(
            candidate,
            current_revision=revision_no,
            revisions=(*candidate.revisions, revision),
            updated_at=self._now(),
        )
        self._write(pid, CANDIDATE_BUCKET, candidate_id, updated.to_dict())
        return self._view(updated)

    # -- 读取 ----------------------------------------------------------------

    def get(self, project_id: str, candidate_id: str) -> dict[str, Any]:
        return self._view(self._load(ProjectId(project_id), candidate_id))

    def list_candidates(self, project_id: str) -> list[dict[str, Any]]:
        """候选清单（只读）：按更新时间倒序，正文只给摘要，完整文本按 ID 取。"""
        rows = self._repo.aux_list(ProjectId(project_id), CANDIDATE_BUCKET)
        candidates = [drafting.ChapterCandidate.from_dict(row) for row in rows.values()]
        candidates.sort(key=lambda c: c.updated_at, reverse=True)
        return [self._summary(c) for c in candidates]

    # -- 采用与丢弃 ----------------------------------------------------------

    def adopt(
        self,
        project_id: str,
        candidate_id: str,
        *,
        preview_hash: str,
        revision: int | None = None,
        operation_id: str | None = None,
        allow_disclosure_change: bool = False,
    ) -> dict[str, Any]:
        """按预览哈希采用：一次发布，迟到候选不能覆盖新稿或未结束副本。"""
        pid = ProjectId(project_id)
        candidate = self._load(pid, candidate_id)
        if candidate.status == drafting.CANDIDATE_ADOPTED:
            # 幂等重放：已采用返回既有结果，不产生第二个版本。
            return {
                "status": "no_change",
                "candidate_id": candidate_id,
                "chapter_id": candidate.adopted_chapter_id,
                "operation_id": candidate.adopted_operation_id,
                "commit_id": None,
            }
        if candidate.status != drafting.CANDIDATE_READY:
            raise _violation(
                ec.DRAFT_CANDIDATE_NOT_ADOPTABLE,
                f"drafts.adopt({candidate_id})",
                f"候选当前状态是 {candidate.status}，不能采用",
                "从新的一次试写开始，或改用仍是 ready 的候选",
                status=candidate.status,
            )
        target_revision = candidate.current_revision if revision is None else revision
        if target_revision != candidate.current_revision:
            raise _violation(
                ec.DRAFT_REVISION_CONFLICT,
                f"drafts.adopt({candidate_id})",
                f"修订 {target_revision} 已被修订 {candidate.current_revision} 取代",
                "预览最新修订后再采用；迟到候选不能覆盖新稿",
                current_revision=candidate.current_revision,
                requested_revision=target_revision,
            )
        chosen = candidate.revision(target_revision)
        if chosen is None:
            raise _violation(
                ec.DRAFT_CANDIDATE_NOT_FOUND,
                f"drafts.adopt({candidate_id})",
                f"候选没有修订 {target_revision}",
                "重新读取候选后选择现有修订",
            )
        if chosen.preview_hash != preview_hash:
            raise _violation(
                ec.DRAFT_PREVIEW_CONFLICT,
                f"drafts.adopt({candidate_id})",
                "采用的正文与作者看到的预览不是同一份",
                "重新打开预览，按当前预览重新采用",
            )
        catalog = self._catalog(pid)
        if candidate.brief.target != drafting.TARGET_NEW_CHAPTER:
            self._reject_edit_session_overlap(pid, candidate.brief.target)
            current_base = catalog.chapter_base(candidate.brief.target)
            if candidate.brief.base_chapter != current_base:
                raise _violation(
                    ec.DRAFT_TARGET_CONFLICT,
                    f"drafts.adopt({candidate_id})",
                    f"章节 {candidate.brief.target} 在试写之后已被修改，候选基于旧版本",
                    "重新试写这一章，或在版本历史里比较后再决定",
                    chapter_id=candidate.brief.target,
                )
        # 默认润色不扩大披露：改写现有章时，新稿不能提前写出后文真相。
        # 这是采用门槛而不是生成门槛——生成时可能已经越界，落地前必须挡住。
        if candidate.brief.target != drafting.TARGET_NEW_CHAPTER:
            self._ensure_disclosure_scope(candidate, chosen.text, allow_disclosure_change)
        op_id = operation_id or (
            "op_"
            + content_hash(
                {
                    "kind": "draft.adopt",
                    "candidate": candidate_id,
                    "revision": target_revision,
                    "hash": preview_hash,
                }
            )[:24]
        )
        chapter_id = (
            f"ch_{content_hash({'candidate': candidate_id})[:16]}"
            if candidate.brief.target == drafting.TARGET_NEW_CHAPTER
            else candidate.brief.target
        )
        if candidate.brief.target == drafting.TARGET_NEW_CHAPTER:
            command = manuscript_command(
                manuscript_commands.import_chapter,
                chapter_id=chapter_id,
                title=chosen.title,
                text=chosen.text,
                order_key=self._next_order_key(catalog),
            )
            kind = "chapter.create"
        else:
            command = manuscript_command(
                manuscript_commands.save_full_text,
                chapter_id=chapter_id,
                text=chosen.text,
                expected_chapter_base=candidate.brief.base_chapter,
            )
            kind = "chapter.adopt_candidate"
        result = self._publisher.publish_command(
            project_id=pid,
            command=command,
            operation_id=op_id,
            kind=kind,
            summary=f"采用试写候选（{chosen.title}，修订 {target_revision}）",
            refs={"candidate_id": candidate_id, "preview_hash": preview_hash},
        )
        adopted = replace(
            candidate,
            status=drafting.CANDIDATE_ADOPTED,
            adopted_operation_id=op_id,
            adopted_chapter_id=chapter_id,
            adopted_revision=target_revision,
            updated_at=self._now(),
        )
        self._write(pid, CANDIDATE_BUCKET, candidate_id, adopted.to_dict())
        return {
            "status": result.status,
            "candidate_id": candidate_id,
            "chapter_id": chapter_id,
            "operation_id": op_id,
            "commit_id": result.commit_id,
            "changed_chapter_ids": list(result.envelope.changed_chapter_ids),
        }

    def _ensure_disclosure_scope(
        self,
        candidate: drafting.ChapterCandidate,
        text: str,
        authorized: bool,
    ) -> None:
        """改写现有章前核对披露范围；没有叙事位置时不猜也不拦。

        基线是**当前正文**：作者原来写过的事实名不算扩大披露，新出现且尚未揭示的
        事实名才算。检测是字符串级的，长陈述句不在范围内——它拦的是「怀表坏了」
        这类可命名事实被提前说破。
        """
        brief = candidate.brief
        if self._disclosure is None or not brief.story_chapter_id:
            return
        pid = ProjectId(brief.project_id)
        manuscript = self._catalog(pid).manuscript()
        original = manuscript.text_of(brief.target)
        report = self._disclosure.check(
            brief.project_id,
            original=original,
            candidate=text,
            chapter_id=brief.story_chapter_id,
            authorized=authorized,
        )
        if not report.get("blocked"):
            return
        names = "、".join(str(item["label"]) for item in report["findings"])
        raise _violation(
            "DISCLOSURE_NEEDS_AUTHORIZATION",
            f"drafts.adopt({candidate.candidate_id})",
            f"新稿提前写出了后文才揭示的事实：{names}",
            "去掉这些内容，或由作者明确授权改变揭示安排后重新采用",
            findings=report["findings"],
            position=report["position"],
        )

    def discard(self, project_id: str, candidate_id: str) -> dict[str, Any]:
        """丢弃候选：只改候选状态，不碰正文与历史。"""
        pid = ProjectId(project_id)
        candidate = self._load(pid, candidate_id)
        if candidate.status == drafting.CANDIDATE_ADOPTED:
            raise _violation(
                ec.DRAFT_CANDIDATE_NOT_ADOPTABLE,
                f"drafts.discard({candidate_id})",
                "候选已被采用，不能丢弃；正文已在版本历史中",
                "如需撤销，使用版本历史恢复之前的版本",
            )
        updated = replace(candidate, status=drafting.CANDIDATE_DISCARDED, updated_at=self._now())
        self._write(pid, CANDIDATE_BUCKET, candidate_id, updated.to_dict())
        return self._view(updated)

    # -- 内部 ----------------------------------------------------------------

    async def _generate(
        self, brief: drafting.DraftBrief, *, previous_text: str, adjustment: str
    ) -> WrittenChapter:
        if self._writer is None:
            raise _violation(
                ec.DRAFT_EMPTY_RESULT,
                f"drafts.generate({brief.project_id})",
                "当前部署没有接通正文生成服务",
                "在设置→模型接入里连接模型后重试",
            )
        ledger = self._ledger(brief)
        prompt = drafting.render_user_prompt(
            brief,
            background=self._background_text(brief),
            ledger=ledger,
            previous_text=previous_text,
            adjustment=adjustment,
        )
        written = await self._writer(
            system=drafting.render_system_prompt(),
            prompt=prompt,
        )
        text = drafting.clean_chapter_text(written.text)
        if not text.strip():
            raise _violation(
                ec.DRAFT_EMPTY_RESULT,
                f"drafts.generate({brief.project_id})",
                "模型没有返回正文（空结果不等于写好了）",
                "重试一次；持续为空时换模型或把要求写得更具体",
            )
        if len(text) > drafting.MAX_CANDIDATE_CHARS:
            raise _violation(
                ec.DRAFT_EMPTY_RESULT,
                f"drafts.generate({brief.project_id})",
                f"生成的正文 {len(text)} 字超过单章上限 {drafting.MAX_CANDIDATE_CHARS}",
                "缩短篇幅要求，或把这一章拆成两章分别试写",
            )
        return WrittenChapter(text=text, title=written.title.strip(), model=written.model)

    def _background_text(self, brief: drafting.DraftBrief) -> str:
        """按试写的基础版本取作品背景；没有接通装配器时退化为空。

        预算不足时直接失败而不是悄悄少给：一份缺了本书约定的背景，比没有背景
        更容易让模型写出「看起来对、其实违反作者要求」的正文。
        """
        if self._background is None:
            return ""
        try:
            return self._background.render(
                brief.project_id,
                scope="manuscript",
                commit_id=brief.base_head,
                # 有叙事位置才带披露边界；没有就不塞一份猜测出来的约束。
                story_chapter_id=brief.story_chapter_id or None,
            )
        except ValueError as exc:
            raise _violation(
                "DRAFT_BACKGROUND_TOO_SMALL",
                f"drafts.context({brief.project_id})",
                str(exc),
                "减少生效约定或故事核心设定，或把本次试写的范围缩小后重试",
            ) from exc

    def _ledger(self, brief: drafting.DraftBrief) -> list[dict[str, str]]:
        """按引用顺序读取账本条目；空项目或未引用任何条目时返回空列表。"""
        if not brief.refs or brief.base_head is None:
            return []
        catalog = StoryQueries(self._repo).catalog(
            ProjectId(brief.project_id), CommitId(brief.base_head)
        )
        rows: list[dict[str, str]] = []
        for ref in brief.refs:
            entry = catalog.get(ref)
            if entry is None:
                raise _violation(
                    ec.DRAFT_TARGET_CONFLICT,
                    f"drafts.context({brief.project_id})",
                    f"引用的设定 {ref!r} 在生成时的版本里不存在",
                    "从账本重新选择引用，或先创建该设定",
                    ref=ref,
                )
            rows.append(entry_view(entry))
        return rows

    def _catalog(self, pid: ProjectId) -> ManuscriptCatalog:
        branch = self._repo.get_branch(pid, "main")
        if branch is None:
            raise _violation(
                ec.DRAFT_CANDIDATE_NOT_FOUND,
                f"drafts.catalog({pid.value})",
                f"作品 {pid.value} 没有 main 分支",
                "先创建作品再试写",
            )
        head = branch.head_commit_id
        if head is None:
            return ManuscriptCatalog(commit_id=None, entries=())
        return ManuscriptCatalog.from_snapshot(self._repo.get_snapshot(pid, head))

    def _reject_edit_session_overlap(self, pid: ProjectId, chapter_id: str) -> None:
        if self._edit_sessions is None:
            return
        current = self._edit_sessions.current(pid.value)
        if current is None:
            return
        drafts = {str(draft["chapter_id"]) for draft in current.get("drafts", [])}
        if chapter_id not in drafts:
            return
        raise _violation(
            ec.EDIT_SESSION_CONFLICT,
            f"drafts.adopt({chapter_id})",
            f"章节 {chapter_id} 正在手工修改副本中，采用候选会覆盖未完成的修改",
            "先完成或放弃这一轮修改，再采用候选",
            chapter_id=chapter_id,
        )

    def _load(self, pid: ProjectId, candidate_id: str) -> drafting.ChapterCandidate:
        row = self._repo.aux_read(pid, CANDIDATE_BUCKET, candidate_id)
        if row is None:
            raise _violation(
                ec.DRAFT_CANDIDATE_NOT_FOUND,
                f"drafts.get({candidate_id})",
                f"作品 {pid.value} 没有候选 {candidate_id}",
                "刷新候选列表后重试",
            )
        return drafting.ChapterCandidate.from_dict(row)

    def _view(self, candidate: drafting.ChapterCandidate) -> dict[str, Any]:
        latest = candidate.latest
        assert latest is not None
        return {
            "candidate_id": candidate.candidate_id,
            "status": candidate.status,
            "current_revision": candidate.current_revision,
            "brief": candidate.brief.to_dict(),
            "title": latest.title,
            "text": latest.text,
            "preview_hash": latest.preview_hash,
            "model": latest.model,
            "created_at": candidate.created_at,
            "updated_at": candidate.updated_at,
            "adopted_operation_id": candidate.adopted_operation_id,
            "adopted_chapter_id": candidate.adopted_chapter_id,
            "revisions": [
                {
                    "revision": r.revision,
                    "title": r.title,
                    "chars": len(r.text),
                    "preview_hash": r.preview_hash,
                    "created_at": r.created_at,
                }
                for r in candidate.revisions
            ],
        }

    def _summary(self, candidate: drafting.ChapterCandidate) -> dict[str, Any]:
        latest = candidate.latest
        assert latest is not None
        return {
            "candidate_id": candidate.candidate_id,
            "status": candidate.status,
            "current_revision": candidate.current_revision,
            "title": latest.title,
            "excerpt": latest.text[:160],
            "chars": len(latest.text),
            "target": candidate.brief.target,
            "preview_hash": latest.preview_hash,
            "updated_at": candidate.updated_at,
            "adopted_chapter_id": candidate.adopted_chapter_id,
        }

    @staticmethod
    def _next_order_key(catalog: ManuscriptCatalog) -> str:
        orders = [str(entry.model().order_key) for entry in catalog.chapters()]
        numbers = [int(o) for o in orders if o.isdigit()]
        return f"{(max(numbers) if numbers else 0) + 1:04d}"

    def _write(self, pid: ProjectId, bucket: str, key: str, payload: dict[str, Any]) -> None:
        self._repo.aux_mutate(pid, bucket, key, lambda _current: payload)

    def _now(self) -> str:
        return self._clock.now().isoformat()
