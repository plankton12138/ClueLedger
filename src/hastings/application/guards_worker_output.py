"""Provider-independent proposal boundary: scope, pinned versions, and quoted evidence."""

from __future__ import annotations

import json
from typing import Any

from hastings.agents.schemas import parse_output
from hastings.application.errors import ViolationError
from hastings.application.ports.repository import RepositoryReader
from hastings.application.story_proposals import preview_story
from hastings.domain.changes.errors import ChangesViolation
from hastings.domain.changes.patch_guard import validate_content_patch
from hastings.domain.repository.ids import ArtifactId, CommitId, ProjectId
from hastings.domain.story.errors import StoryViolation
from hastings.domain.worker import ContextPackage


def validate_worker_output(
    ctx: ContextPackage, reader: RepositoryReader, value: Any
) -> dict[str, Any]:
    cap = ctx.capability
    if (cap.role, cap.project_id, cap.base_commit_id) != (
        ctx.task_kind,
        ctx.project_id,
        ctx.repository_commit_id,
    ):
        raise ValueError("Context 与 Capability 的角色、项目或版本不一致；重新构建上下文")
    result = parse_output(ctx.task_kind, value).model_dump(mode="json")
    snapshot = reader.get_snapshot(ProjectId(ctx.project_id), CommitId(ctx.repository_commit_id))
    if result.get("base_commit_id", ctx.repository_commit_id) != ctx.repository_commit_id:
        raise ValueError("output.base_commit_id 不是 Context 固定版本；使用 repository_commit_id")
    if result["kind"] == "manuscript_patch":
        for patch in result["patches"]:
            aid = patch["artifact_id"]
            if aid not in cap.writable_ids:
                raise ValueError(f"output.patches.{aid} 超出范围；只允许 {cap.writable_ids}")
            view = snapshot.artifacts.get(ArtifactId(aid))
            if (
                view is None
                or view.revision is None
                or patch["base_revision_id"] != view.revision.id.value
            ):
                raise ValueError(f"output.patches.{aid}.base_revision_id 过期；读取指定版本对象")
            try:
                validate_content_patch(view.artifact.kind, view.revision.content, patch["content"])
            except ChangesViolation as exc:
                raise ValueError(f"{exc.where}: {exc.why}；{exc.fix}") from exc
        if result["chapter_id"] is not None and result["chapter_id"] not in cap.writable_ids:
            raise ValueError(f"output.chapter_id 不在 {cap.writable_ids}；不得扩大写作范围")
    if result["kind"] == "story_proposal" and len(result["items"]) > cap.max_new_artifacts:
        raise ValueError("StoryProposal 超过候选数量上限；缩小任务")
    if result["kind"] == "story_proposal" and ctx.task_kind != "writer":
        for item in result["items"]:
            new = (
                item["kind"] in cap.creation_kinds
                and item["artifact_id"].startswith(cap.creation_prefix)
                and ArtifactId(item["artifact_id"]) not in snapshot.artifacts
            )
            if item["artifact_id"] not in cap.writable_ids and not new:
                raise ValueError("StoryProposal 对象超出能力范围；只提出当前任务的候选")
        if ctx.task_kind == "planner":
            try:
                preview_story(snapshot, cap, result)
            except (StoryViolation, ViolationError) as exc:
                raise ValueError(f"{exc.where}: {exc.why}；{exc.fix}") from exc
            except (TypeError, AttributeError, KeyError) as exc:
                raise ValueError(
                    f"StoryProposal.content 字段缺失或错误：{exc}；按 planning_forms 提供必填参数"
                ) from exc
    if result["kind"] == "finding_set":
        for finding in result["findings"]:
            if not set(finding["artifact_ids"]) <= set(cap.readable_ids):
                raise ValueError("Finding 引用了检查范围外的对象；提供当前可读对象与引用证据")
            texts = []
            for aid in finding["artifact_ids"]:
                view = snapshot.artifacts.get(ArtifactId(aid))
                if view is None or view.revision is None:
                    raise ValueError("Finding 引用对象在检查版本不存在；选择有原文证据的对象")
                texts.append(json.dumps(view.revision.content, ensure_ascii=False))
            if not any(finding["quote"] in text for text in texts):
                raise ValueError(
                    "Finding.quote 不在引用对象中；准确引用原文，否则放入 editorial_notes"
                )
    if result["kind"] == "observation_set":
        edited = {edit["unit_id"] for edit in ctx.evidence.get("edits", [])}
        if any(
            o["unit_id"] not in cap.readable_ids or o["unit_id"] not in edited
            for o in result["observations"]
        ):
            raise ValueError("Observation 引用了编辑范围外的段落；只观察本次真实 diff")
    if result["kind"] == "impact_supplement" and any(
        i["artifact_id"] not in cap.readable_ids for i in result["items"]
    ):
        raise ValueError("ImpactSupplement 超出可读范围；提供当前上下文内的证据")
    return result
