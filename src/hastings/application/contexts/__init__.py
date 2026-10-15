"""确定性任务选择：先选目标和依赖，再按预算记录裁剪。"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from hastings.application.ports.repository import RepositoryReader
from hastings.application.queries.author_profile import AuthorProfileQueries
from hastings.application.queries.story_queries import StoryQueries
from hastings.domain.repository.ids import ArtifactId, CommitId, ProjectId
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story.narrative.order import ordered_beats
from hastings.domain.worker import (
    ROLE_TOOLS,
    ContextPackage,
    Omission,
    RunCapability,
    SelectedInput,
    WorkerRole,
)


def estimate_tokens(value: Any) -> int:
    # UTF-8 字节上界，偏保守；不依赖某个 Provider 的分词器或浮动 usage。
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class ContextBuilder:
    def __init__(self, reader: RepositoryReader) -> None:
        self.reader = reader

    def build(
        self,
        role: WorkerRole,
        project: ProjectId,
        commit_id: str,
        target_ids: tuple[str, ...],
        *,
        instruction: str = "",
        token_budget: int = 16000,
        writable_ids: tuple[str, ...] | None = None,
        evidence: dict[str, Any] | None = None,
        creation_kinds: tuple[str, ...] = (),
        constraints: tuple[str, ...] = (),
    ) -> ContextPackage:
        snapshot = self.reader.get_snapshot(project, CommitId(commit_id))
        targets = sorted(set(target_ids))
        for aid in targets:
            view = snapshot.artifacts.get(ArtifactId(aid))
            if view is None or view.revision is None:
                raise ValueError(f"Context.targets[{aid}] 不在指定版本；选择已有对象或先确认结构")
        selected = {aid: "任务目标，必须保留" for aid in targets}
        frontier = set(targets)
        for depth in range({"coordinator": 0, "style_observer": 1, "reviser": 1}.get(role, 2)):
            neighbours = set()
            for relation in snapshot.relations:
                a, b = relation.source.value, relation.target.value
                if a in frontier:
                    neighbours.add(b)
                if b in frontier:
                    neighbours.add(a)
            for aid in sorted(frontier):
                view = snapshot.artifacts.get(ArtifactId(aid))
                if view and view.revision and view.revision.content.get("parent_id"):
                    neighbours.add(view.revision.content["parent_id"])
            for aid in sorted(neighbours - selected.keys()):
                selected[aid] = f"目标的第 {depth + 1} 跳显式依赖"
            frontier = neighbours
        task_evidence = dict(evidence or {})
        if role == "writer":
            queries = StoryQueries(self.reader)
            sequence = ordered_beats(queries.catalog(project, CommitId(commit_id)))
            beat_states = []
            for aid in targets:
                if snapshot.artifacts[ArtifactId(aid)].artifact.kind != "narrative.beat":
                    continue
                for index, entry in enumerate(sequence):
                    if entry.artifact_id == aid and index:
                        previous = sequence[index - 1].artifact_id
                        selected.setdefault(previous, "前一节拍，限定叙事衔接")
                        linked = {
                            r.target.value
                            for r in snapshot.relations
                            if r.source.value == previous and r.relation_type == "renders"
                        }
                        ordered_units = [
                            unit
                            for view in sorted(
                                snapshot.artifacts.values(), key=lambda v: v.artifact.id.value
                            )
                            if view.revision and view.artifact.kind == "manuscript.chapter"
                            for unit in view.revision.content.get("text_unit_ids", [])
                            if unit in linked
                        ]
                        tails = (ordered_units or sorted(linked))[-2:]
                        for tail in tails:
                            selected.setdefault(tail, "前一节拍的局部正文尾部")
                states = {
                    label: asdict(
                        queries.story_state(project, CommitId(commit_id), beat_id=aid, after=after)
                    )
                    for label, after in (("enter", False), ("leave", True))
                }
                for state in states.values():
                    state["truths"] = [v for v in state["truths"] if v in selected]
                    state["knowledge"] = [
                        v
                        for v in state["knowledge"]
                        if v["character_id"] in selected or v["fact_id"] in selected
                    ]
                    state["disclosures"] = [
                        v for v in state["disclosures"] if v["fact_id"] in selected
                    ]
                    state["relationships"] = [
                        v
                        for v in state["relationships"]
                        if v["source_id"] in selected or v["target_id"] in selected
                    ]
                    state["custom"] = [
                        v
                        for v in state["custom"]
                        if v["beat_id"] in targets
                        or v["character_id"] in selected
                        or v["target_id"] in selected
                    ]
                beat_states.append({"beat_id": aid, **states})
            task_evidence["beat_states"] = beat_states
        target_chapters = set()
        for aid in targets:
            revision = snapshot.artifacts[ArtifactId(aid)].revision
            if revision:
                target_chapters.add(revision.content.get("chapter_id"))
            if snapshot.artifacts[ArtifactId(aid)].artifact.kind == "manuscript.chapter":
                target_chapters.add(aid)
        if evidence and evidence.get("chapter_id"):
            target_chapters.add(evidence["chapter_id"])
        for view in snapshot.artifacts.values():
            if not view.revision:
                continue
            body = view.revision.content
            if (
                view.artifact.kind == "manuscript.chapter"
                and view.artifact.id.value in target_chapters
            ):
                order = body.get("text_unit_ids", [])
                for target in targets:
                    if target in order:
                        index = order.index(target)
                        for aid in order[max(0, index - 1) : index + 2]:
                            selected.setdefault(aid, "相邻段落窗口")
        # 工作者读回权只覆盖这个确定性集合；被预算裁剪的对象仍可按引用读回。
        writable = list(writable_ids if writable_ids is not None else targets)
        if role in ("auditor", "style_observer", "coordinator"):
            writable = []
        capability = RunCapability(
            role=role,
            project_id=project.value,
            base_commit_id=commit_id,
            readable_ids=sorted(selected),
            writable_ids=sorted(set(writable)),
            allowed_tools=list(ROLE_TOOLS[role]),
            creation_kinds=list(creation_kinds),
            creation_prefix=f"{project.value}:",
        )
        package = ContextPackage(
            context_id="",
            task_kind=role,
            project_id=project.value,
            repository_commit_id=commit_id,
            target_ids=targets,
            instruction=instruction,
            constraints=["输出仅为候选；作者审批与 Commit 才表示项目变化", *constraints],
            evidence=task_evidence,
            token_budget=token_budget,
            capability=capability,
        )
        package.omissions = [
            Omission(artifact_id=aid, reason="预算截断，按固定版本读回")
            for aid in selected
            if aid not in targets
        ]
        if estimate_tokens(package.model_dump()) > token_budget:
            raise ValueError(
                "Context 预算不足以保留版本、授权、失败证据与作者指令；增大预算或缩小任务"
            )
        for aid, reason in sorted(
            selected.items(), key=lambda pair: (pair[0] not in targets, pair[0])
        ):
            view = snapshot.artifacts.get(ArtifactId(aid))
            if view is None or view.revision is None:
                continue
            item = SelectedInput(
                artifact_id=aid,
                revision_id=view.revision.id.value,
                kind=view.artifact.kind,
                reason=reason,
                content=view.revision.content,
            )
            old_omissions = package.omissions
            package.omissions = [o for o in old_omissions if o.artifact_id != aid]
            package.selected_inputs.append(item)
            if estimate_tokens(package.model_dump()) + 90 > token_budget:
                package.selected_inputs.pop()
                package.omissions = old_omissions
                if aid in targets:
                    raise ValueError(f"Context 目标 {aid} 无法完整放入预算；增大预算或拆小目标")
        chapter = next((str(c) for c in sorted(str(v) for v in target_chapters if v)), "")
        profiles = AuthorProfileQueries(self.reader)
        preferences = profiles.effective_preferences(
            project,
            CommitId(commit_id),
            scope_kind="chapter" if chapter else "project",
            scope_target=chapter,
        )
        for preference in preferences:
            package.capability.readable_ids = sorted(
                set(package.capability.readable_ids) | {preference.preference_id}
            )
            body = preference.to_content()
            package.author_preferences.append(body)
            if estimate_tokens(package.model_dump()) > token_budget - 200:
                package.author_preferences.pop()
                package.omissions.append(
                    Omission(
                        artifact_id=preference.preference_id,
                        reason="偏好预算不足",
                        refetch_tool="author_profile_read",
                    )
                )
        for example in profiles.effective_examples(project, CommitId(commit_id)):
            if example.unit_id not in selected:
                continue
            package.capability.readable_ids = sorted(
                set(package.capability.readable_ids) | {example.example_id}
            )
            package.author_examples.append(example.to_content())
            if estimate_tokens(package.model_dump()) > token_budget - 200:
                package.author_examples.pop()
                package.omissions.append(
                    Omission(
                        artifact_id=example.example_id,
                        reason="作者样例预算不足",
                        refetch_tool="author_profile_read",
                    )
                )
        package.context_id = content_hash(package.model_dump())
        package.estimated_tokens = estimate_tokens(package.model_dump()) + 16
        if package.estimated_tokens > token_budget:
            raise ValueError("Context 元数据与裁剪记录超预算；增大预算或缩小选择范围")
        return package


class TaskContextBuilder(ContextBuilder):
    role: WorkerRole

    def for_task(
        self, project: ProjectId, commit_id: str, targets: tuple[str, ...], **options: Any
    ) -> ContextPackage:
        return self.build(self.role, project, commit_id, targets, **options)


class DraftContextBuilder(TaskContextBuilder):
    role: WorkerRole = "writer"


class RepairContextBuilder(TaskContextBuilder):
    role: WorkerRole = "reviser"


class AuditContextBuilder(TaskContextBuilder):
    role: WorkerRole = "auditor"


class StyleContextBuilder(TaskContextBuilder):
    role: WorkerRole = "style_observer"
