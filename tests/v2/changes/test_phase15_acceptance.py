"""三章悬疑故事验收：作用变更→分支修订→接受两处/拒绝一处→审计。"""

from types import SimpleNamespace
from typing import Any

import pytest

from hastings.application.handlers.change_commands import ChangeSetCommandHandler
from hastings.application.workflows.audit import AuditWorkflow
from hastings.application.workflows.change_analysis import AnalyzeChangeImpact, AnalyzeIntent
from hastings.application.workflows.review import ReviewRevision
from hastings.application.workflows.revision import ExecuteIntent
from hastings.domain.changes.execution import PatchUnit
from hastings.domain.changes.models import ChangeScope, SeedChange
from hastings.domain.manuscript.models import Chapter, TextUnit
from hastings.domain.repository.ids import ArtifactId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange
from hastings.domain.story.canon.models import Character, Relationship
from hastings.domain.story.mystery.models import KnowledgeState, TruthFact
from hastings.domain.story.narrative.models import Beat
from hastings.infrastructure.repository.sqlite import build_sqlite_repository
from hastings.infrastructure.runtime import UuidIdGenerator

from .conftest import AUTHOR, FakeClock, head_of, make_open_command, run_command
from .test_branch_executor import FakeWorker, _executor, _plan_and_approve


def test_three_chapter_repair_survives_restarts(tmp_path: Any) -> None:
    path = str(tmp_path / "acceptance.sqlite")
    clock, ids = FakeClock(), UuidIdGenerator()

    def connect() -> Any:
        repo = build_sqlite_repository(path, clock, ids)
        return SimpleNamespace(
            repo=repo,
            service=repo.service,
            clock=clock,
            ids=ids,
            handler=ChangeSetCommandHandler(repo.service, repo.service, ids, AUTHOR),
        )

    env = connect()
    project = env.handler.create_project("怀表谜案")
    branch = env.service.get_branch(project.id, "main")
    bodies = {
        "helper": ("story.character", Character("林岚", role="helper").to_content()),
        "hero": ("story.character", Character("侦探").to_content()),
        "fact": ("story.truth_fact", TruthFact("林岚预先调慢怀表").to_content()),
        "relation": (
            "story.relationship",
            Relationship("hero", "helper", true_state="信任").to_content(),
        ),
        "knowledge": (
            "story.knowledge",
            KnowledgeState("hero", "fact", "knows", "beat1").to_content(),
        ),
        "beat1": (
            "narrative.beat",
            Beat("beat1", parent_id="ch1", purpose="得到协助", order_key="1").to_content(),
        ),
        "beat2": (
            "narrative.beat",
            Beat("beat2", parent_id="ch2", purpose="相信证词", order_key="2").to_content(),
        ),
        "outside": ("narrative.beat", Beat("outside", parent_id="ch4", order_key="4").to_content()),
    }
    for n in range(1, 5):
        bodies[f"ch{n}"] = (
            "manuscript.chapter",
            Chapter(f"ch{n}", f"第{n}章", str(n), (f"tu{n}",)).to_content(),
        )
        bodies[f"tu{n}"] = (
            "manuscript.text_unit",
            TextUnit(f"tu{n}", f"ch{n}", f"旧稿{n}：他相信协助者。").to_content(),
        )
    edges = [
        ("helper", "relation"),
        ("relation", "knowledge"),
        ("knowledge", "beat1"),
        ("beat1", "beat2"),
        ("helper", "outside"),
    ]
    env.service.commit(
        CommitRequest(
            project.id,
            branch.id,
            branch.head_commit_id,
            AUTHOR,
            "seed",
            "三章故事基线",
            changes=tuple(
                ArtifactChange(ArtifactId(aid), body, kind=kind)
                for aid, (kind, body) in bodies.items()
            ),
            relations=tuple(
                RelationChange("add", ArtifactId(a), "motivates", ArtifactId(b)) for a, b in edges
            )
            + tuple(
                RelationChange("add", ArtifactId(a), "renders", ArtifactId(b))
                for a, b in (
                    ("beat1", "tu1"),
                    ("beat2", "tu2"),
                    ("beat2", "tu3"),
                    ("outside", "tu4"),
                )
            ),
        )
    )
    base = head_of(env, project.id)
    opened = run_command(
        env,
        project.id,
        make_open_command(
            base_commit_id=base,
            seed_changes=(
                SeedChange(
                    "helper",
                    "role",
                    "协助者改为幕后操纵者",
                    {**bodies["helper"][1], "role": "manipulator"},
                ),
            ),
            scope=ChangeScope(roots=("ch1", "ch2", "ch3")),
        ),
        key="open",
    )
    cs_id = opened.change.artifact_changes[0].artifact_id.value
    env = connect()
    AnalyzeChangeImpact(env.handler, env.service, clock, AUTHOR).analyze(
        AnalyzeIntent(project.id, cs_id, "analyze")
    )
    _plan_and_approve(env, project, cs_id)
    env = connect()
    patches = {
        "step_canon": (
            PatchUnit("helper", {**bodies["helper"][1], "role": "manipulator"}),
            PatchUnit("relation", {**bodies["relation"][1], "true_state": "被利用"}),
        ),
        "step_plan": (
            PatchUnit("knowledge", {**bodies["knowledge"][1], "note": "须检查知情顺序"}),
            PatchUnit("beat1", {**bodies["beat1"][1], "purpose": "被诱导"}),
            PatchUnit("beat2", {**bodies["beat2"][1], "purpose": "怀疑证词"}),
        ),
        "step_manuscript": tuple(
            PatchUnit(f"tu{n}", {**bodies[f"tu{n}"][1], "text": f"修订{n}：他开始怀疑协助者。"})
            for n in range(1, 4)
        ),
    }

    def crash(context: Any) -> None:
        if context.step_id == "step_plan":
            raise KeyboardInterrupt("模拟 Canon 提交后进程退出")

    with pytest.raises(KeyboardInterrupt):
        _executor(env).execute(
            ExecuteIntent(project.id, cs_id, "execute"), FakeWorker(patches, on_propose=crash)
        )
    env = connect()
    clock.advance(1000)
    worker = FakeWorker(patches)
    result = _executor(env).execute(
        ExecuteIntent(project.id, cs_id, "resume-after-restart"), worker
    )
    assert result.status == "executed"
    assert "step_canon" not in [context.step_id for context in worker.seen]
    env = connect()
    review = ReviewRevision(env.service, env.service, clock, AUTHOR)
    patch = review.prepare(project.id, cs_id, "prepare")
    assert {h["artifact_id"] for h in patch["hunks"]} >= {
        "relation",
        "knowledge",
        "beat1",
        "beat2",
        "tu1",
        "tu2",
        "tu3",
    }
    chosen = tuple(h["hunk_id"] for h in patch["hunks"] if h["artifact_id"] != "tu3")
    merged = review.merge(project.id, cs_id, chosen, {}, "merge", head_of(env, project.id))
    assert [i["target_artifact_id"] for i in merged["items"] if i["status"] == "open"] == ["tu3"]
    env = connect()
    audit = AuditWorkflow(env.service, env.service, clock, AUTHOR).run(project.id, "audit")
    assert any(
        f["code"] == "STORY_INVALID_KNOWLEDGE_ORDER" and f["status"] == "open"
        for f in audit["findings"]
    )
    snapshot = env.service.get_snapshot(
        project.id, env.service.get_branch(project.id, "main").head_commit_id
    )
    assert snapshot.artifacts[ArtifactId("helper")].revision.content["role"] == "manipulator"
    assert snapshot.artifacts[ArtifactId("tu3")].revision.content["text"].startswith("旧稿")
    assert snapshot.artifacts[ArtifactId("tu4")].revision.content["text"].startswith("旧稿")
