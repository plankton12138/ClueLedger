"""统一正文发布（V03-2.2，计划 §3.3 六步、M05；0.3 最核心的写路径）。

导入、候选采用、修改完成、历史恢复、整体调整及关联撤销**全部复用**本用例。
六步对应关系：

1. 按 ``operation_id`` 找到已有结果（OperationService 回执重放）；同键不同
   内容显式冲突；
2. 读取同一基础快照，核对目标正文与依赖（三方比较的输入）；
3. 领域命令纯函数产出正文变化集合（**不提交**）；
4. 依据实际差异确定性核对证据、失效摘要与概览（不调用模型）；
5. 全部变化 + 操作记录 + 回执 + 事件在**一个 CommitRequest** 里原子生效
   （经 OperationService.submit，V03-0.5 事务端口；任一写失败整体回滚）；
6. 返回持久操作回执与实际正式版本；投影未完成仍可按操作查询找回。

准备过程不调用模型、领域命令不自行提交——``ManuscriptCommand`` 是
``(catalog, new_id) -> ManuscriptChange`` 的纯函数，本用例只在内存里跑它。

无差异（§3.3 第 6 步）：``no_change`` 返回操作回执，不创建正文空版本，
也不创建建议/摘要任务。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.handlers.manuscript_commands import (
    ManuscriptCommand,
    manuscript_command,
)
from hastings.application.ports.ids import IdGenerator
from hastings.application.services.commit_service import CommitService
from hastings.application.services.operation_service import OperationService
from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.evidence import (
    KIND_BOOK_OVERVIEW,
    KIND_CHAPTER_SUMMARY,
    KIND_EVIDENCE_LINK,
    ChapterSummaryState,
    EvidenceLink,
    evidence_after_change,
    summary_after_change,
)
from hastings.domain.manuscript.identities import OperationEnvelope
from hastings.domain.manuscript.kinds import KIND_CHAPTER, KIND_TEXT_UNIT
from hastings.domain.manuscript.models import ManuscriptChange
from hastings.domain.repository.ids import ArtifactId, CommitId, ProjectId
from hastings.domain.repository.requests import ArtifactChange
from hastings.domain.repository.serialization import content_hash

__all__ = ["PublishManuscriptChange", "PublishResult"]


@dataclass(frozen=True, slots=True)
class PublishResult:
    """发布结果：操作信封 + 供旧路由兼容的字段。

    ``change`` 是本次实际生效的正文变化（no_change 时为空变化）。
    """

    envelope: OperationEnvelope
    warnings: tuple[str, ...]
    summary: str
    change: ManuscriptChange

    @property
    def status(self) -> str:
        return self.envelope.status

    @property
    def commit_id(self) -> str | None:
        return self.envelope.commit_id

    @property
    def new_head(self) -> str | None:
        return self.envelope.commit_id

    @property
    def operation_id(self) -> str:
        return self.envelope.operation_id


class PublishManuscriptChange:
    """统一发布用例：准备（纯函数）→ 核对（确定性）→ 原子提交。"""

    def __init__(
        self,
        repo: CommitService,
        operations: OperationService,
        ids: IdGenerator,
    ) -> None:
        self._repo = repo
        self._operations = operations
        self._ids = ids

    # -- 保存入口（首个接入的管线；导入/恢复随后接入） -------------------------

    async def publish_save(
        self,
        *,
        project_id: ProjectId,
        chapter_id: str,
        operations: tuple[Any, ...] | None,
        full_text: str | None,
        expected_chapter_base: str | None,
        operation_id: str,
        summary: str = "保存正文",
    ) -> PublishResult:
        """把一次人工保存意图走统一发布管线。"""
        if (operations is None) == (full_text is None):
            raise _violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"publish.save({project_id.value}.{chapter_id})",
                "operations 与 full_text 必须且只能提供其一"
                "（为什么错：两者都给无从确定意图，都不给没有内容可提交）",
                "优先给 operations（规范化编辑操作），兼容模式给 full_text",
            )
        from hastings.domain.manuscript import commands as manuscript_commands

        if operations is not None:
            command = manuscript_command(
                manuscript_commands.apply_edits,
                chapter_id=chapter_id,
                operations=tuple(operations),
                expected_chapter_base=expected_chapter_base,
            )
        else:
            assert full_text is not None
            command = manuscript_command(
                manuscript_commands.save_full_text,
                chapter_id=chapter_id,
                text=full_text,
                expected_chapter_base=expected_chapter_base,
            )
        return self.publish_command(
            project_id=project_id,
            command=command,
            operation_id=operation_id,
            kind="chapter.save",
            summary=summary,
            refs={"chapter_id": chapter_id},
        )

    # -- 通用管线 -------------------------------------------------------------

    def publish_command(
        self,
        *,
        project_id: ProjectId,
        command: ManuscriptCommand,
        operation_id: str,
        kind: str,
        summary: str,
        refs: dict[str, Any] | None = None,
        origin_conversation_id: str | None = None,
        base_commit_id: CommitId | None = None,
    ) -> PublishResult:
        """§3.3 六步：领域命令纯函数 → 证据/摘要核对 → 原子提交。

        单命令是 ``publish_commands`` 的特例：保留同一入口，重放指纹与旧版本一致。
        """
        return self.publish_commands(
            project_id=project_id,
            commands=(command,),
            operation_id=operation_id,
            kind=kind,
            summary=summary,
            refs=refs,
            origin_conversation_id=origin_conversation_id,
            base_commit_id=base_commit_id,
        )

    def publish_commands(
        self,
        *,
        project_id: ProjectId,
        commands: Sequence[ManuscriptCommand],
        operation_id: str,
        kind: str,
        summary: str,
        refs: dict[str, Any] | None = None,
        origin_conversation_id: str | None = None,
        base_commit_id: CommitId | None = None,
        extra_changes: Sequence[ArtifactChange] = (),
    ) -> PublishResult:
        """一次提交多条稿件命令（V03-4.6 撤销用）：多章恢复只产生一个版本。

        每条命令都对**同一个基础快照**求值，因此「恢复第 1、2 章」不会出现
        前一条已经改了 head、后一条仍按旧基础计算的情况；真正的一致性由提交时的
        ``expected_head`` 与各条命令自带的 ``expected_chapter_base`` 保证。
        同一章不要在同一次调用里出现两次——两条命令都基于同一基础，后者会覆盖前者。

        ``base_commit_id`` 是**准备这次修改时**的基础版本（试写候选的 base_head、
        手工副本打开时的 head、撤销预览时的 after 版本）。给了它就按那个快照求值：
        命令自带的 ``expected_chapter_base`` 与准备时一致，产出的
        ``old_revision_id`` 也是准备时版本——提交层据此做逐对象 CAS，准备之后
        被改过的对象显式冲突，**没动过的对象不受影响**（一次无关保存不阻断本次
        发布）。不给则按当前 head 求值（无准备阶段的路径，如恢复与导入）。

        ``extra_changes`` 是正文命令之外的关联对象恢复（V03-5.4 撤销账本）：与
        正文变化进同一个 Commit，任一对象 CAS 失败则整体不发布。它们不参与稿件
        证据/摘要推导，但计入幂等指纹与实际变化集合。
        """
        if not commands and not extra_changes:
            raise _violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"publish({operation_id})",
                "没有要发布的命令",
                "至少给一条稿件命令或一个关联对象变化",
            )
        pid = project_id
        branch = self._repo.get_branch(pid, "main")
        if branch is None:
            raise _violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"publish({operation_id})",
                f"作品 {pid.value} 没有 main 分支",
                "先创建作品再发布",
            )
        head = branch.head_commit_id
        # 求值基础：准备时版本优先；没有准备阶段的路径按当前 head。
        base = base_commit_id if base_commit_id is not None else head
        if len(commands) == 1 and not extra_changes:
            # 单命令沿用旧指纹形状：升级后重放既有操作仍能认出同一次请求。
            fingerprint = content_hash(
                {
                    "command": commands[0].name,
                    "intent": repr(sorted(commands[0].intent.items())),
                    "refs": refs,
                }
            )
        else:
            fingerprint = content_hash(
                {
                    "commands": [
                        [command.name, repr(sorted(command.intent.items()))] for command in commands
                    ],
                    "extra": [
                        [change.artifact_id.value, change.kind, change.content]
                        for change in extra_changes
                    ],
                    "refs": refs,
                }
            )
        replayed = self._operations.find_replay(pid.value, operation_id, fingerprint)
        if replayed is not None:
            return PublishResult(
                envelope=replayed,
                warnings=(),
                summary=summary,
                change=ManuscriptChange(summary=summary),
            )
        catalog = (
            ManuscriptCatalog.from_snapshot(self._repo.get_snapshot(pid, base))
            if base is not None
            else ManuscriptCatalog(commit_id=None, entries=())
        )
        artifact_changes: list[ArtifactChange] = []
        events: list[tuple[str, dict[str, Any]]] = []
        warnings: list[str] = []
        for command in commands:
            change = self._run(command, catalog)
            artifact_changes.extend(change.artifact_changes)
            events.extend(change.events)
            warnings.extend(change.warnings)
        merged = ManuscriptChange(
            artifact_changes=tuple(artifact_changes),
            events=tuple(events),
            warnings=tuple(warnings),
            summary=summary,
        )
        if merged.is_empty() and not extra_changes:
            envelope = self._operations.submit(
                project_id=pid.value,
                operation_id=operation_id,
                kind=kind,
                changes=(),
                summary=summary,
                refs=refs,
                request_fingerprint=fingerprint,
                origin_conversation_id=origin_conversation_id,
            )
            return PublishResult(envelope=envelope, warnings=(), summary=summary, change=merged)
        changed_ids = frozenset(
            [c.artifact_id.value for c in merged.artifact_changes]
            + [c.artifact_id.value for c in extra_changes]
        )
        chapter_ids = self._changed_chapters(catalog, merged)
        derived = self._derive_state_changes(
            pid, head, catalog, merged, changed_ids, chapter_ids, operation_id
        )
        envelope = self._operations.submit(
            project_id=pid.value,
            operation_id=operation_id,
            kind=kind,
            changes=(*merged.artifact_changes, *derived, *extra_changes),
            summary=summary,
            refs=refs,
            events=merged.events,
            changed_chapter_ids=tuple(chapter_ids),
            request_fingerprint=fingerprint,
            origin_conversation_id=origin_conversation_id,
        )
        return PublishResult(
            envelope=envelope, warnings=tuple(warnings), summary=summary, change=merged
        )

    # -- 内部 -----------------------------------------------------------------

    def _run(self, command: ManuscriptCommand, catalog: ManuscriptCatalog) -> ManuscriptChange:
        """领域命令边界：ManuscriptViolation → ViolationError（四透镜①）。"""
        from hastings.domain.manuscript.errors import ManuscriptViolation

        try:
            # Artifact ID 统一 art 前缀（handler 同款适配：kind 仅作语义提示）
            return command(catalog, lambda _kind: self._ids.new_id("art"))
        except ManuscriptViolation as exc:
            raise ViolationError(
                code=exc.code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
            ) from exc

    def _changed_chapters(self, catalog: ManuscriptCatalog, change: ManuscriptChange) -> list[str]:
        """本次实际影响的章节：变化对象按 catalog kind / 新对象 kind 判定。

        章对象自身的 ``chapter_id`` 字段是章身份（== Artifact ID）；段落对象
        （text_unit）的内容同样携带 chapter_id——两种变化都归到章。
        """
        kinds = {e.artifact_id: e.kind for e in catalog.entries}
        chapters: list[str] = []
        for c in change.artifact_changes:
            aid = c.artifact_id.value
            kind = c.kind or kinds.get(aid, "")
            chapter_id = c.content.get("chapter_id") if isinstance(c.content, dict) else None
            if kind == KIND_CHAPTER:
                chapters.append(aid)
            elif chapter_id and (kind == KIND_TEXT_UNIT or kinds.get(aid) == KIND_TEXT_UNIT):
                chapters.append(str(chapter_id))
        # 去重保序
        return list(dict.fromkeys(chapters))

    def _derive_state_changes(
        self,
        pid: ProjectId,
        head: Any,
        catalog: ManuscriptCatalog,
        change: ManuscriptChange,
        changed_artifact_ids: frozenset[str],
        chapter_ids: list[str],
        operation_id: str,
    ) -> tuple[ArtifactChange, ...]:
        """证据核对 + 摘要失效：只读快照 → 新 content（同事务提交，I03）。"""
        if head is None:
            return ()  # 空仓库首提交：还没有证据/摘要可核对
        if not chapter_ids:
            # 只恢复账本或关联对象：没有正文变化，不该把全书概览判成过期。
            return ()
        snapshot = self._repo.get_snapshot(pid, head)
        chapter_text_after = self._chapter_text_after(catalog, change, chapter_ids)
        # 证据核对的「变化集合」= 全部变化对象 ∪ 受影响章节（证据按章引用来源，
        # 段落级变化要归到章才算数）。
        evidence_changed = frozenset(set(changed_artifact_ids) | set(chapter_ids))
        derived: list[ArtifactChange] = []
        # 证据核对：来源章或依赖在变化集合里才动（无关变化不全量失效）。
        for view in snapshot.artifacts.values():
            if view.artifact.kind != KIND_EVIDENCE_LINK or view.revision is None:
                continue
            link = EvidenceLink.from_wire(view.revision.content)
            updated = evidence_after_change(
                link,
                changed_artifact_ids=evidence_changed,
                chapter_text_after=chapter_text_after,
            )
            if updated.to_wire() != link.to_wire():
                derived.append(
                    ArtifactChange(
                        artifact_id=ArtifactId(view.artifact.id.value),
                        content=updated.to_wire(),
                        old_revision_id=view.revision.id,
                    )
                )
        # 摘要失效：来源章变化 ⇒ fresh → stale；无关章保持。
        for view in snapshot.artifacts.values():
            if view.artifact.kind != KIND_CHAPTER_SUMMARY or view.revision is None:
                continue
            summary_state = ChapterSummaryState.from_wire(view.revision.content)
            updated_summary = summary_after_change(
                summary_state,
                changed_chapter_ids=frozenset(chapter_ids),
                operation_id=operation_id,
            )
            if updated_summary.to_wire() != summary_state.to_wire():
                derived.append(
                    ArtifactChange(
                        artifact_id=ArtifactId(view.artifact.id.value),
                        content=updated_summary.to_wire(),
                        old_revision_id=view.revision.id,
                    )
                )
        # 全书概览：任一章变化即失效（计划 §4.1 BookOverview），已过期不重复记因。
        for view in snapshot.artifacts.values():
            if view.artifact.kind != KIND_BOOK_OVERVIEW or view.revision is None:
                continue
            content = dict(view.revision.content)
            if content.get("status") == "stale":
                continue
            content["status"] = "stale"
            content["reason"] = f"章节内容已变化（操作 {operation_id}）"
            derived.append(
                ArtifactChange(
                    artifact_id=ArtifactId(view.artifact.id.value),
                    content=content,
                    old_revision_id=view.revision.id,
                )
            )
        return tuple(derived)

    def _chapter_text_after(
        self,
        catalog: ManuscriptCatalog,
        change: ManuscriptChange,
        chapter_ids: list[str],
    ) -> dict[str, str]:
        """拼装变化后的章全文：章的单元顺序 + 单元新内容。

        只对本次变化的章计算。整章删除（章内容消失且无新内容）不出现在结果里，
        ``evidence_after_change`` 据此判 needs_check。
        """
        if not chapter_ids:
            return {}
        kinds = {e.artifact_id: e.kind for e in catalog.entries}
        new_unit_text: dict[str, str] = {}
        deleted_units: set[str] = set()
        new_order: dict[str, list[str]] = {}
        for c in change.artifact_changes:
            content = c.content if isinstance(c.content, dict) else {}
            kind = c.kind or kinds.get(c.artifact_id.value, "")
            if kind == KIND_TEXT_UNIT and content.get("unit_id"):
                unit_id = str(content["unit_id"])
                if content.get("state") == "deleted" or content.get("text", "") == "" and c.kind:
                    deleted_units.add(unit_id)
                new_unit_text[unit_id] = str(content.get("text", ""))
            if content.get("chapter_id") and content.get("text_unit_ids"):
                new_order[str(content["chapter_id"])] = [str(x) for x in content["text_unit_ids"]]
        catalog_text: dict[str, str] = {}
        catalog_order: dict[str, list[str]] = {}
        for entry in catalog.entries:
            if entry.kind == KIND_TEXT_UNIT:
                unit = entry.model()
                catalog_text[unit.unit_id] = unit.text
            elif entry.kind == KIND_CHAPTER:
                chapter = entry.model()
                catalog_order[entry.artifact_id] = list(chapter.text_unit_ids)
        result: dict[str, str] = {}
        for chapter_id in chapter_ids:
            order = new_order.get(chapter_id) or catalog_order.get(chapter_id, [])
            parts = [
                new_unit_text.get(unit_id, catalog_text.get(unit_id, ""))
                for unit_id in order
                if unit_id not in deleted_units
            ]
            result[chapter_id] = "\n".join(parts)
        return result


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)
