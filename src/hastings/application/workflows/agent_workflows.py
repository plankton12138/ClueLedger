"""规划、草稿、修复、审计、风格观察的纵向接线；模型结果不直接生效。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from hastings.application.contexts import (
    AuditContextBuilder,
    ContextBuilder,
    DraftContextBuilder,
    RepairContextBuilder,
    StyleContextBuilder,
)
from hastings.application.guards_worker_output import validate_worker_output
from hastings.application.ports.clock import Clock
from hastings.application.ports.ids import IdGenerator
from hastings.application.ports.repository import CommitRepository, RepositoryReader
from hastings.application.story_proposals import preview_story
from hastings.application.workflows.author_learning import edit_samples_from_commits
from hastings.application.workflows.change_support import (
    branch_snapshot,
    content,
    reject,
    require_author,
)
from hastings.application.workflows.revision import ExecuteIntent, ExecuteRevisionPlan
from hastings.application.workflows.worker_job import WorkerJobRunner
from hastings.domain.audit.rules import audit_snapshot
from hastings.domain.author_model import commands as author_commands
from hastings.domain.author_model import rules as author_rules
from hastings.domain.author_model.catalog import AuthorModelCatalog, AuthorModelEntry
from hastings.domain.author_model.models import Classification, ObservationDraft
from hastings.domain.changes.execution import PatchProposal, PatchUnit, StepContext
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import import_chapter, save_full_text
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange
from hastings.domain.worker import ContextPackage

AUTHOR = Actor("human", "author")

PLANNABLE = (
    "story.character",
    "story.mystery_truth",
    "story.clue",
    "story.red_herring",
    "narrative.series",
    "narrative.book",
    "narrative.arc",
    "narrative.chapter",
    "narrative.scene",
    "narrative.beat",
)


class AgentWorkflows:
    def __init__(
        self,
        reader: RepositoryReader,
        committer: CommitRepository,
        jobs: WorkerJobRunner,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self.reader, self.committer, self.jobs, self.ids, self.clock = (
            reader,
            committer,
            jobs,
            ids,
            clock,
        )
        self.contexts = ContextBuilder(reader)
        self.draft_context = DraftContextBuilder(reader)
        self.repair_context = RepairContextBuilder(reader)
        self.audit_context = AuditContextBuilder(reader)
        self.style_context = StyleContextBuilder(reader)

    async def draft(
        self,
        project: ProjectId,
        base: str,
        beats: tuple[str, ...],
        chapter_id: str,
        key: str,
        instruction: str = "",
        *,
        token_budget: int = 16000,
        model_binding: Mapping[str, object] | None = None,
        grant: Mapping[str, object] | None = None,
    ) -> str:
        snapshot = self.reader.get_snapshot(project, CommitId(base))
        invalid = [
            beat
            for beat in beats
            if ArtifactId(beat) not in snapshot.artifacts
            or snapshot.artifacts[ArtifactId(beat)].artifact.kind
            not in ("narrative.beat", "story.plot_beat")
        ]
        if invalid:
            reject(
                "DRAFT_TARGET_INVALID",
                "draft.targets",
                f"引用 {invalid} 不是当前版本中的节拍",
                "从当前版本重新选择目标；章节起草不再要求节拍，正文范围与账本引用即可",
            )
        # V03-3.1：起草不再以节拍为前置。传入节拍时仍装配其状态并建立渲染血缘；
        # 不传时按「账本 + 作者要求 + 章节」起草，空作品也是合法首章来源。
        targets = (*beats, chapter_id) if ArtifactId(chapter_id) in snapshot.artifacts else beats
        ctx = self.draft_context.for_task(
            project,
            base,
            targets,
            instruction=instruction,
            token_budget=token_budget,
            writable_ids=(chapter_id,),
            evidence={"chapter_id": chapter_id},
            constraints=(f"只为 {chapter_id} 返回整章 text；不修改 Canon，缺失事实应澄清",),
        )
        return await self.jobs.submit(ctx, key, model_binding=model_binding, grant=grant)

    async def plan(
        self,
        project: ProjectId,
        base: str,
        targets: tuple[str, ...],
        key: str,
        instruction: str,
        *,
        token_budget: int = 16000,
        model_binding: Mapping[str, object] | None = None,
        grant: Mapping[str, object] | None = None,
    ) -> str:
        ctx = self.contexts.build(
            "planner",
            project,
            base,
            targets,
            instruction=instruction,
            token_budget=token_budget,
            writable_ids=(),
            creation_kinds=PLANNABLE,
            evidence={
                "planning_forms": {
                    "story.character": {
                        "name": "必填姓名",
                        "role": "support|protagonist|antagonist|accomplice|witness",
                        "identity": "身份",
                        "goal": "目标",
                        "traits": [],
                    },
                    "narrative.series": {"title": "系列名"},
                    "narrative.book": {
                        "title": "书名",
                        "parent_id": "已有或本批次较前的 series 对象 ID",
                    },
                    "narrative.arc": {"title": "幕名", "parent_id": "book ID"},
                    "narrative.chapter": {"title": "章名", "parent_id": "arc ID"},
                    "narrative.scene": {"title": "场景名", "parent_id": "chapter ID"},
                    "narrative.beat": {
                        "parent_id": "scene ID",
                        "title": "节拍名",
                        "purpose": "目的",
                        "pov_character_id": "已有角色 ID 或空",
                        "participants": [],
                        "constraints": [],
                    },
                    "story.clue": {
                        "statement": "必填线索表述",
                        "source": "来源",
                        "points_to": [],
                        "necessity": False,
                        "disclosure_plan": [],
                    },
                    "story.red_herring": {
                        "false_explanation": "必填错误解释",
                        "basis": "必填误导依据",
                        "resolution": "揭穿方式",
                    },
                    "story.mystery_truth": {
                        "culprit_id": "必填且已存在的角色 ID",
                        "motive": "动机",
                        "method": "手法",
                        "cover_up": "掩饰",
                        "accomplice_ids": [],
                        "true_timeline": [],
                    },
                }
            },
            constraints=(
                f"新对象 ID 使用 {project.value}: 前缀。"
                "items 按父级和引用依赖顺序排列，所有事实保持 proposed",
            ),
        )
        return await self.jobs.submit(ctx, key, model_binding=model_binding, grant=grant)

    async def audit(
        self,
        project: ProjectId,
        base: str,
        targets: tuple[str, ...],
        key: str,
        *,
        token_budget: int = 16000,
        model_binding: Mapping[str, object] | None = None,
        grant: Mapping[str, object] | None = None,
    ) -> str:
        snapshot = self.reader.get_snapshot(project, CommitId(base))
        findings = audit_snapshot(snapshot)
        ctx = self.audit_context.for_task(
            project,
            base,
            targets,
            evidence={"deterministic_findings": findings},
            token_budget=token_budget,
            constraints=("只返回带原文和对象定位的 Finding；泛泛评价写 editorial_notes",),
        )
        return await self.jobs.submit(ctx, key, model_binding=model_binding, grant=grant)

    async def style(
        self,
        project: ProjectId,
        before: str,
        after: str,
        chapter_id: str,
        key: str,
        *,
        token_budget: int = 16000,
    ) -> str:
        history = self.reader.commit_history(project, CommitId(after))
        edited = next(c for c in history if c.id.value == after)
        snapshot = self.reader.get_snapshot(project, CommitId(after))
        is_candidate_acceptance = any(
            snapshot.artifacts[d.artifact_id].artifact.kind == "agents.acceptance"
            for d in self.reader.diff(CommitId(before), CommitId(after)).artifact_changes
        )
        if (
            edited.actor.kind != "human"
            or CommitId(before) not in edited.parent_ids
            or is_candidate_acceptance
        ):
            reject(
                "STYLE_HUMAN_DIFF_REQUIRED",
                "style.diff",
                "所选差异不是一次人工提交与其直接父版本",
                "选择人工保存的 Commit 与它的父版本；不学习 AI 或修订分支输出",
            )
        samples = edit_samples_from_commits(
            self.reader,
            project_id=project,
            base_commit_id=CommitId(before),
            commit_id=CommitId(after),
            chapter_id=chapter_id,
        )
        ctx = self.style_context.for_task(
            project,
            after,
            tuple(s.unit_id for s in samples),
            token_budget=token_budget,
            evidence={
                "before_commit_id": before,
                "chapter_id": chapter_id,
                "edits": [s.to_content() for s in samples],
            },
            constraints=("只能观察真实人工 diff；事实、格式、错字不构成文风，不能激活偏好",),
        )
        return await self.jobs.submit(ctx, key)

    async def repair(
        self,
        project: ProjectId,
        change_set_id: str,
        key: str,
        executor: ExecuteRevisionPlan,
        *,
        model_binding: Mapping[str, object] | None = None,
        grant: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        owner = self

        class Worker:
            def propose(self, step: StepContext) -> PatchProposal:
                async def run() -> PatchProposal:
                    _, branch = branch_snapshot(owner.reader, project, f"revision/{change_set_id}")
                    _, main = branch_snapshot(owner.reader, project)
                    model = content(main, change_set_id)
                    ctx = owner.repair_context.for_task(
                        project,
                        branch.commit_id.value,
                        tuple(t.artifact_id for t in step.targets),
                        evidence={
                            "change_set": model,
                            "step": step.to_content(),
                            "impact_items": [
                                item
                                for view in main.artifacts.values()
                                if view.revision
                                and view.artifact.kind == "changes.impact_report"
                                and view.revision.content.get("change_set_id") == change_set_id
                                for item in view.revision.content.get("items", [])
                                if item.get("target_artifact_id")
                                in {t.artifact_id for t in step.targets}
                            ],
                            "approval": next(
                                v.revision.content
                                for v in main.artifacts.values()
                                if v.revision
                                and v.artifact.kind == "changes.approval_receipt"
                                and v.revision.content["change_set_id"] == change_set_id
                            ),
                        },
                        constraints=(
                            "仅修改本步骤 targets，不扩张审批范围；patch 携带 base_revision_id",
                        ),
                    )
                    jid = await owner.jobs.submit(
                        ctx, f"{key}:{step.step_id}:{ctx.context_id}", model_binding=model_binding
                    )
                    job = await owner.jobs.execute(jid)
                    if job["state"] != "waiting" or job["output"]["kind"] != "manuscript_patch":
                        if job.get("output", {}).get("kind") == "no_result":
                            return PatchProposal()
                        reject(
                            "REPAIR_WORKER_UNAVAILABLE",
                            "repair.step",
                            f"Worker 未产生可执行补丁（{job['state']}）",
                            "查看 Job 错误或澄清后重新执行",
                        )
                    return PatchProposal(
                        tuple(
                            PatchUnit(p["artifact_id"], p["content"])
                            for p in job["output"]["patches"]
                        )
                    )

                return asyncio.run_coroutine_threadsafe(run(), loop).result()

        from dataclasses import asdict

        return asdict(
            await asyncio.to_thread(
                executor.execute, ExecuteIntent(project, change_set_id, key), Worker()
            )
        )

    async def accept(
        self, job_id: str, expected_head: str, *, actor: Actor = AUTHOR
    ) -> dict[str, Any]:
        require_author(actor)
        job = await self.jobs.jobs.get(job_id)
        if job is None:
            raise ValueError("Job 不存在")
        project = ProjectId(job["payload"]["project_id"])
        key, marker_id = f"agent-accept:{job_id}", f"agent-acceptance:{job_id}"
        previous = self.reader.find_commit_result(project, key)
        if previous and previous.commit_id:
            result = content(self.reader.get_snapshot(project, previous.commit_id), marker_id)
            restored = {**result, "commit_id": previous.commit_id.value}
            if job["state"] == "waiting":
                await self.jobs.jobs.resolve(job_id, job["generation"], restored)
            return restored
        if job["state"] != "waiting" or job["job_type"] not in ("writer", "planner"):
            reject(
                "AGENT_REVIEW_REQUIRED",
                "agent.accept",
                "只有作者可接受待审阅的规划/正文提案",
                "修订提案走已批准的修订分支；取消的 Job 不能接受",
            )
        ctx = ContextPackage.model_validate(job["payload"]["context"])
        output = validate_worker_output(ctx, self.reader, job["output"])
        branch, snapshot = branch_snapshot(self.reader, project)
        if (
            expected_head != snapshot.commit_id.value
            or output.get("base_commit_id") != ctx.repository_commit_id
        ):
            reject(
                "AGENT_BASE_CONFLICT",
                "agent.accept",
                "确认页或提案基础版本不一致",
                "重新读取当前版本并生成提案",
            )
        base = self.reader.get_snapshot(project, CommitId(ctx.repository_commit_id))
        relevant = set(ctx.capability.readable_ids) | set(ctx.capability.writable_ids)
        if any(
            d.artifact_id.value in relevant
            for d in self.reader.diff(base.commit_id, snapshot.commit_id).artifact_changes
        ):
            reject(
                "AGENT_BASE_CONFLICT",
                "agent.accept",
                "生成后相关事实或正文已修改",
                "重新构建上下文；不要覆盖人工编辑",
            )
        changes: tuple[ArtifactChange, ...]
        relations: tuple[RelationChange, ...] = ()
        if output["kind"] == "manuscript_patch" and job["job_type"] == "writer":
            cid = output.get("chapter_id")
            if cid not in ctx.capability.writable_ids or not isinstance(output.get("text"), str):
                reject(
                    "RUN_SCOPE_VIOLATION",
                    "draft.accept",
                    "候选不属于本章或未提供整章内容",
                    "仅接受当前 chapter_id 的正文候选",
                )
            assert isinstance(cid, str)
            manuscript = ManuscriptCatalog.from_snapshot(snapshot)
            if manuscript.get(cid):
                change = save_full_text(
                    manuscript,
                    lambda kind: self.ids.new_id(kind),
                    chapter_id=cid,
                    text=output["text"],
                    expected_chapter_base=manuscript.chapter_base(cid),
                )
            else:
                change = import_chapter(
                    manuscript,
                    lambda kind: self.ids.new_id(kind),
                    chapter_id=cid,
                    title=ctx.instruction or "新章节",
                    text=output["text"],
                )
            changes = change.artifact_changes
            units = (
                {e.artifact_id for e in manuscript.units_of(cid)} if manuscript.get(cid) else set()
            )
            for changed in changes:
                if changed.kind == "manuscript.text_unit" or changed.artifact_id.value in units:
                    if changed.content.get("state", "active") == "active":
                        units.add(changed.artifact_id.value)
                    else:
                        units.discard(changed.artifact_id.value)
            beat_ids = [
                aid
                for aid in ctx.target_ids
                if base.artifacts[ArtifactId(aid)].artifact.kind
                in ("narrative.beat", "story.plot_beat")
            ]
            relations = tuple(
                RelationChange("add", ArtifactId(beat), "renders", ArtifactId(unit))
                for beat in beat_ids
                for unit in sorted(units)
            )
        elif output["kind"] == "story_proposal" and job["job_type"] == "planner":
            changes, relations = preview_story(snapshot, ctx.capability, output)
        else:
            reject(
                "AGENT_REVIEW_REQUIRED",
                "agent.accept",
                "当前输出不是可提交的规划或正文",
                "处理澄清或读取空结果",
            )
            raise AssertionError("unreachable")
        result = {"job_id": job_id, "status": "accepted", "context_id": ctx.context_id}
        committed = self.committer.commit(
            CommitRequest(
                project,
                branch.id,
                branch.head_commit_id,
                actor,
                key,
                "作者接受 Agent 候选",
                changes=(
                    *changes,
                    ArtifactChange(ArtifactId(marker_id), result, kind="agents.acceptance"),
                ),
                relations=relations,
                events=(("agent.proposal.accepted", result),),
            )
        )
        resolved = {
            **result,
            "commit_id": committed.commit_id.value if committed.commit_id else None,
        }
        await self.jobs.jobs.resolve(job_id, job["generation"], resolved)
        return resolved

    async def apply_analysis(self, job_id: str) -> dict[str, Any]:
        job = await self.jobs.jobs.get(job_id)
        if job is None or job["state"] != "completed":
            raise ValueError("分析 Job 尚未完成；先运行或重试任务")
        output = job["output"]
        if output["kind"] == "no_result":
            return {"status": "no_result"}
        ctx = ContextPackage.model_validate(job["payload"]["context"])
        output = validate_worker_output(ctx, self.reader, output)
        project = ProjectId(ctx.project_id)
        key = f"agent-analysis:{job_id}"
        prior = self.reader.find_commit_result(project, key)
        if prior:
            return {
                "status": "recorded",
                "commit_id": prior.commit_id.value if prior.commit_id else None,
            }
        branch, snapshot = branch_snapshot(self.reader, project)
        changes = []
        if output["kind"] == "finding_set" and job["job_type"] == "auditor":
            changes.append(
                ArtifactChange(
                    ArtifactId(f"semantic-audit:{job_id}"),
                    {
                        "job_id": job_id,
                        "checked_commit_id": ctx.repository_commit_id,
                        "findings": output["findings"],
                        "editorial_notes": output["editorial_notes"],
                        "status": "open",
                    },
                    kind="audit.semantic_report",
                )
            )
        elif output["kind"] == "observation_set" and job["job_type"] == "style_observer":
            before = ctx.evidence["before_commit_id"]
            samples = edit_samples_from_commits(
                self.reader,
                project_id=project,
                base_commit_id=CommitId(before),
                commit_id=CommitId(ctx.repository_commit_id),
                chapter_id=ctx.evidence["chapter_id"],
            )
            model = {o["unit_id"]: o for o in output["observations"]}
            drafts = []
            for sample in samples:
                rule = author_rules.classify_edit(sample)
                proposed = model.get(sample.unit_id)
                if not rule.is_confirmed and proposed and proposed["category"] == "style":
                    rule = Classification(
                        category="language_style",
                        confidence=proposed["confidence"],
                        status="candidate",
                        signals=(proposed["statement"],),
                        source="model",
                    )
                drafts.append(
                    ObservationDraft(
                        edit=sample, classification=rule, commit_id=ctx.repository_commit_id
                    )
                )
            catalog = AuthorModelCatalog.from_snapshot(snapshot)
            recorded = author_commands.record_observations(
                catalog, lambda kind: self.ids.new_id(kind), drafts=tuple(drafts)
            )
            changes.extend(recorded.artifact_changes)
            augmented = replace(
                catalog,
                entries=(
                    *catalog.entries,
                    *(
                        AuthorModelEntry(c.artifact_id.value, c.kind or "", "pending", c.content)
                        for c in changes
                    ),
                ),
            )
            induced = author_commands.induce_preferences(
                augmented,
                lambda kind: self.ids.new_id(kind),
                scope_kind="project",
                policy=author_rules.InductionPolicy(evidence_threshold=10**9),
            )
            active_ids = {
                e.artifact_id
                for e in catalog.entries
                if e.content.get("status") in ("active", "revoked")
            }
            changes.extend(
                c for c in induced.artifact_changes if c.artifact_id.value not in active_ids
            )
        else:
            reject(
                "RUN_SCOPE_VIOLATION",
                "analysis.apply",
                "只允许审计和风格分析的元数据输出",
                "正文与设定必须走作者审阅",
            )
        if not changes:
            return {"status": "no_result"}
        commit = self.committer.commit(
            CommitRequest(
                project,
                branch.id,
                branch.head_commit_id,
                Actor("agent", "observer"),
                key,
                "保存 Agent 分析候选与证据",
                changes=tuple(changes),
                events=(("agent.analysis.recorded", {"job_id": job_id, "kind": output["kind"]}),),
            )
        )
        return {
            "status": "recorded",
            "commit_id": commit.commit_id.value if commit.commit_id else None,
        }
