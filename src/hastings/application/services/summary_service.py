"""章摘要任务（V03-4.3）：显式启动、冻结来源、迟到结果不覆盖。

三条硬约束：

1. **只有显式调用才运行模型**。保存正文、导入、试写采用都不会调用这里；
   入口只有一个 ``start()``，由作者的动作触发。
2. **来源清单在生成前冻结，落地前再核对**。期间正文变了就返回 ``superseded``，
   既不写版本，也不改已有摘要——「不覆盖新稿」不是靠时序运气。
3. **失败保留旧内容**。模型报错或空结果时不提交任何东西，上一次摘要（哪怕是
   stale）仍可读；状态由 V03-2.2 的失效裁决维护，不在这里混着改。

模型调用经 ``summarizer`` 注入：这条路径上没有其它模型依赖，测试可以断言调用次数。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.clock import Clock
from hastings.application.ports.ids import IdGenerator
from hastings.application.services.commit_service import CommitService
from hastings.domain.manuscript import summary as domain
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.evidence import KIND_CHAPTER_SUMMARY, ChapterSummaryState
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId, RevisionId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.domain.repository.results import NO_CHANGE

__all__ = ["SummaryDraft", "SummaryService", "SummaryWriterFn"]

_AUTHOR = Actor(kind="human", id="author")

#: 状态文案：前端不自己拼词，避免「已过期」和「待更新」两种说法并存。
STATUS_LABELS: dict[str, str] = {
    "missing": "尚未生成",
    "fresh": "可用",
    "stale": "来源已变，待更新",
    "superseded": "结果已过期，未写入",
}


@dataclass(frozen=True, slots=True)
class SummaryDraft:
    """一次模型产出：正文 + 实际使用的模型标识（用于来源追溯）。"""

    text: str
    model: str = ""


@dataclass(frozen=True, slots=True)
class _StoredSummary:
    """快照里的一条摘要 Artifact；摘要不属于 manuscript.*，只能从原始快照读。"""

    artifact_id: str
    revision_id: str
    content: dict[str, Any]


SummaryWriterFn = Callable[..., Awaitable[SummaryDraft]]


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


class SummaryService:
    def __init__(
        self,
        repo: CommitService,
        ids: IdGenerator,
        clock: Clock,
        summarizer: SummaryWriterFn | None = None,
    ) -> None:
        self._repo = repo
        self._ids = ids
        self._clock = clock
        self._summarizer = summarizer

    # -- 读 -----------------------------------------------------------------
    def get(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        stored = self._stored(pid, self._head(pid), chapter_id)
        if stored is None:
            return self._missing_view(chapter_id)
        return self._view(stored)

    def list_summaries(self, project_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        head = self._head(pid)
        rows = [self._view(item) for item in self._all_stored(pid, head)]
        rows.sort(key=lambda row: str(row["chapter_id"]))
        return {"summaries": rows, "commit_id": head.value if head else None}

    # -- 写（显式启动） ------------------------------------------------------
    async def start(
        self,
        project_id: str,
        chapter_id: str,
        *,
        request_id: str,
        guidance: str = "",
    ) -> dict[str, Any]:
        pid = ProjectId(project_id)
        key = f"summary-start:{pid.value}:{request_id}"
        prior = self._repo.find_commit_result(pid, key)
        if prior is not None and prior.commit_id is not None and prior.changed_artifact_ids:
            stored = self._artifact_at(pid, prior.commit_id, prior.changed_artifact_ids[0].value)
            if stored is not None:
                return {
                    "status": prior.status,
                    "committed": True,
                    "commit_id": prior.commit_id.value,
                    "summary": self._view(stored),
                    "replayed": True,
                    "reason": "",
                }

        head = self._head(pid)
        if head is None:
            raise _violation(
                "SUMMARY_CHAPTER_NOT_FOUND",
                f"summary.start({pid.value})",
                "作品还没有正文，无法生成摘要",
                "先导入或试写一章，再更新摘要",
                project_id=pid.value,
                chapter_id=chapter_id,
            )
        catalog = self._catalog(pid, head)
        chapter = catalog.chapter_entry(chapter_id)
        if chapter is None:
            raise _violation(
                "SUMMARY_CHAPTER_NOT_FOUND",
                f"summary.start({pid.value})",
                f"作品 {pid.value} 没有章节 {chapter_id!r}",
                "刷新章节目录后重新选择；摘要只针对现有章节",
                project_id=pid.value,
                chapter_id=chapter_id,
            )
        if self._summarizer is None:
            raise _violation(
                "SUMMARY_NOT_AVAILABLE",
                f"summary.start({pid.value})",
                "当前部署没有接通摘要生成服务",
                "在设置→模型接入里连接模型后重试；没有模型时正文与账本仍可正常使用",
            )

        # 冻结来源：清单与正文取自同一版本，提示词因此可复现。
        manifest = domain.manifest_from(catalog, chapter_id)
        text = domain.chapter_text(catalog, chapter_id)
        title = str(chapter.model().title)
        draft = await self._summarizer(
            system=domain.SUMMARY_SYSTEM_PROMPT,
            prompt=domain.render_summary_prompt(title=title, text=text, guidance=guidance),
        )
        produced = draft.text.strip()
        if not produced:
            raise _violation(
                "SUMMARY_EMPTY_RESULT",
                f"summary.start({pid.value})",
                "模型没有返回摘要内容（空结果不等于摘要已完成）",
                "重试一次；持续为空时换模型，或缩短本章范围后再试",
                chapter_id=chapter_id,
            )

        # 落地前重取版本：期间作者可能保存了正文（保存只会把旧摘要标 stale）。
        current_head = self._head(pid)
        if current_head is None:
            raise _violation(
                "SUMMARY_CHAPTER_NOT_FOUND",
                f"summary.start({pid.value})",
                "生成期间作品目录消失",
                "重新打开作品后再试",
            )
        current = self._catalog(pid, current_head)
        current_chapter = current.chapter_entry(chapter_id)
        accepted, reason = (
            domain.decide(manifest, domain.manifest_from(current, chapter_id))
            if current_chapter is not None
            else (False, "生成期间该章已被删除或替换；本次结果不写入")
        )
        if not accepted:
            return {
                "status": domain.SUMMARY_STATUS_SUPERSEDED,
                "committed": False,
                "commit_id": None,
                "summary": self.get(project_id, chapter_id),
                "replayed": False,
                "reason": reason,
            }

        existing = self._stored(pid, current_head, chapter_id)
        now = self._clock.now().isoformat()
        if existing is None:
            content = domain.build_content(
                summary_id=self._ids.new_id("sum"),
                manifest=manifest,
                text=produced,
                model=draft.model,
                task_id=key,
                generated_at=now,
            )
            change = ArtifactChange(
                artifact_id=ArtifactId(self._ids.new_id("art")),
                content=content,
                old_revision_id=None,
                kind=KIND_CHAPTER_SUMMARY,
            )
        else:
            state = ChapterSummaryState.from_wire(existing.content)
            content = domain.build_content(
                summary_id=state.summary_id,
                manifest=manifest,
                text=produced,
                model=draft.model,
                task_id=key,
                generated_at=now,
            )
            change = ArtifactChange(
                artifact_id=ArtifactId(existing.artifact_id),
                content=content,
                old_revision_id=RevisionId(existing.revision_id),
                kind=None,
            )
        result = self._repo.commit(
            CommitRequest(
                project_id=pid,
                branch_id=self._branch(pid).id,
                expected_head=current_head,
                actor=_AUTHOR,
                idempotency_key=key,
                summary=f"更新章摘要：{title or chapter_id}",
                changes=(change,),
                events=(("summary.updated", {"chapter_id": chapter_id, "model": draft.model}),),
            )
        )
        if result.commit_id is None:
            return {
                "status": NO_CHANGE,
                "committed": False,
                "commit_id": None,
                "summary": self.get(project_id, chapter_id),
                "replayed": False,
                "reason": "内容与当前摘要一致，未产生新版本",
            }
        stored = self._artifact_at(pid, result.commit_id, change.artifact_id.value)
        return {
            "status": result.status,
            "committed": True,
            "commit_id": result.commit_id.value,
            "summary": self._view(stored) if stored is not None else {},
            "replayed": False,
            "reason": "",
        }

    # -- 内部 ---------------------------------------------------------------
    def _head(self, pid: ProjectId) -> CommitId | None:
        branch = self._repo.get_branch(pid, "main")
        return branch.head_commit_id if branch is not None else None

    def _branch(self, pid: ProjectId) -> Any:
        branch = self._repo.get_branch(pid, "main")
        if branch is None:
            raise _violation(
                "REPO_BRANCH_NOT_FOUND",
                f"summary.start({pid.value})",
                f"作品 {pid.value} 没有 main 分支",
                "先创建作品并写入正文",
            )
        return branch

    def _catalog(self, pid: ProjectId, head: CommitId | None) -> ManuscriptCatalog:
        if head is None:
            return ManuscriptCatalog(commit_id=None, entries=())
        return ManuscriptCatalog.from_snapshot(self._repo.get_snapshot(pid, head))

    def _all_stored(self, pid: ProjectId, head: CommitId | None) -> list[_StoredSummary]:
        if head is None:
            return []
        snapshot = self._repo.get_snapshot(pid, head)
        rows: list[_StoredSummary] = []
        for view in snapshot.artifacts.values():
            if view.artifact.kind != KIND_CHAPTER_SUMMARY or view.revision is None:
                continue
            rows.append(
                _StoredSummary(
                    artifact_id=view.artifact.id.value,
                    revision_id=view.revision.id.value,
                    content=dict(view.revision.content),
                )
            )
        rows.sort(key=lambda item: str(item.content.get("chapter_id", "")))
        return rows

    def _stored(
        self, pid: ProjectId, head: CommitId | None, chapter_id: str
    ) -> _StoredSummary | None:
        for item in self._all_stored(pid, head):
            if item.content.get("chapter_id") == chapter_id:
                return item
        return None

    def _artifact_at(
        self, pid: ProjectId, commit_id: CommitId, artifact_id: str
    ) -> _StoredSummary | None:
        snapshot = self._repo.get_snapshot(pid, commit_id)
        view = snapshot.artifacts.get(ArtifactId(artifact_id))
        if view is None or view.revision is None:
            return None
        return _StoredSummary(
            artifact_id=view.artifact.id.value,
            revision_id=view.revision.id.value,
            content=dict(view.revision.content),
        )

    def _missing_view(self, chapter_id: str) -> dict[str, Any]:
        return {
            "chapter_id": chapter_id,
            "summary_id": "",
            "revision_id": "",
            "status": "missing",
            "status_label": STATUS_LABELS["missing"],
            "reason": "",
            "text": "",
            "sources": None,
            "model": "",
            "generated_at": "",
            "source_hash": "",
        }

    def _view(self, stored: _StoredSummary) -> dict[str, Any]:
        state = ChapterSummaryState.from_wire(stored.content)
        return {
            "chapter_id": state.chapter_id,
            "summary_id": state.summary_id,
            "revision_id": stored.revision_id,
            "status": state.status,
            "status_label": STATUS_LABELS.get(state.status, state.status),
            "reason": state.reason,
            "text": state.text,
            "sources": state.sources,
            "model": state.model,
            "generated_at": state.generated_at,
            "source_hash": state.source_hash,
        }
