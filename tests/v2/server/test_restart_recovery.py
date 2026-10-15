"""Phase 14 验收 8：应用重启后正文、偏好与任务状态保持一致（SQLite 集成）。

「重启」= 丢弃全部进程内对象，仅保留磁盘库，用同一 db 路径重建 ``V2Runtime``。
三类事实必须跨越重启存活：

- **正文**：Commit 是唯一事实——章文本、顺序、章基础（内容哈希）原样可查；
- **偏好**：观察与偏好是普通 Artifact，随 Commit 持久化，active 状态与证据引用
  原样可查（作者模型没有自己的存储，这就是它不丢状态的原因）；
- **任务**：保存路径只写 Outbox 不建任务（评审 #3）；重启后未发布事件被新消费者
  重新投递（补偿），已发布标记是持久的（去重）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hastings.application.workflows.manual_edit import ManualEditIntent
from hastings.domain.author_model import kinds as am_kinds
from hastings.domain.author_model.models import (
    PREFERENCE_ACTIVE,
    AuthorObservation,
    Classification,
    EditSample,
    PreferenceHypothesis,
)
from hastings.domain.repository.ids import Actor, ArtifactId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.server.v2 import V2Runtime

CHAPTER = "art_ch1"
FIRST_TEXT = "第一段。\n\n第二段。\n\n第三段。"
EDITED_TEXT = "第一段。\n\n第二段已被大改。\n\n第三段。"


def _build(path: Path) -> V2Runtime:
    return V2Runtime.sqlite(str(path))


def _intent(project_id: str, chapter_id: str, key: str, text: str) -> ManualEditIntent:
    return ManualEditIntent(
        project_id=ProjectId(project_id),
        chapter_id=chapter_id,
        idempotency_key=key,
        full_text=text,
    )


def _seed_preference(rt: V2Runtime, project_id: str) -> str:
    """在当前 head 后播种一条观察 + active 偏好（作者模型页数据形状）。"""
    branch = rt.service.get_branch(ProjectId(project_id), "main")
    assert branch is not None and branch.head_commit_id is not None
    commit_id = branch.head_commit_id.value
    observation = AuthorObservation(
        observation_id="obs_r1",
        commit_id=commit_id,
        edit=EditSample(
            unit_id="art_u2", before="第二段。", after="第二段已被大改。", chapter_id=CHAPTER
        ),
        classification=Classification(
            category="language_style", confidence=0.9, status="confirmed"
        ),
    )
    preference = PreferenceHypothesis(
        preference_id="pref_r1",
        category="language_style",
        statement="作者偏好更精简的措辞与句式",
        status=PREFERENCE_ACTIVE,
        evidence=(commit_id,),
    )
    result = rt.service.commit(
        CommitRequest(
            project_id=ProjectId(project_id),
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=Actor(kind="human", id="author"),
            idempotency_key="seed-pref",
            summary="播种偏好",
            changes=(
                ArtifactChange(
                    artifact_id=ArtifactId("obs_r1"),
                    kind=am_kinds.KIND_OBSERVATION,
                    content=observation.to_content(),
                ),
                ArtifactChange(
                    artifact_id=ArtifactId("pref_r1"),
                    kind=am_kinds.KIND_PREFERENCE,
                    content=preference.to_content(),
                ),
            ),
        )
    )
    assert result.status == "committed"
    return commit_id


def test_restart_preserves_manuscript(tmp_path: Path) -> None:
    path = tmp_path / "repository.db"
    rt1 = _build(path)
    project_id = rt1.create_project("冬馆").value
    imported = rt1.handler.execute(
        manuscript_import(),
        project_id=ProjectId(project_id),
        idempotency_key="import-1",
        summary="导入",
    )
    assert imported.is_committed
    chapter_id = _chapter_id(rt1, project_id)
    saved = asyncio.run(rt1.workflow.save(_intent(project_id, chapter_id, "save-1", EDITED_TEXT)))
    assert saved.is_committed
    before = _manuscript_view(rt1, project_id)

    rt2 = _build(path)  # 重启：仅磁盘库存活
    after = _manuscript_view(rt2, project_id)
    assert after == before
    # 且新提交仍能落在恢复出的 head 上（内容基础与重启前一致）
    assert imported.commit_id is not None
    restored_save = asyncio.run(
        rt2.workflow.save(_intent(project_id, chapter_id, "save-2", FIRST_TEXT))
    )
    assert restored_save.is_committed


def test_restart_preserves_author_model(tmp_path: Path) -> None:
    path = tmp_path / "repository.db"
    rt1 = _build(path)
    project_id = rt1.create_project("冬馆").value
    rt1.handler.execute(
        manuscript_import(),
        project_id=ProjectId(project_id),
        idempotency_key="import-1",
        summary="导入",
    )
    _seed_preference(rt1, project_id)
    before_prefs = _prefs_view(rt1, project_id)
    before_obs = _obs_view(rt1, project_id)

    rt2 = _build(path)  # 重启
    assert _prefs_view(rt2, project_id) == before_prefs
    assert _obs_view(rt2, project_id) == before_obs

    # 生成上下文口径同样恢复：active 偏好可解析、证据可反查
    head = rt2.head(ProjectId(project_id))
    assert head is not None
    effective = rt2.author_queries.effective_preferences(ProjectId(project_id), head)
    assert [p.preference_id for p in effective] == ["pref_r1"]
    evidence = rt2.author_queries.preference_evidence(ProjectId(project_id), head, "pref_r1")
    assert [o.observation_id for o in evidence] == ["obs_r1"]


def test_restart_recovers_pending_analysis_dispatch(tmp_path: Path) -> None:
    path = tmp_path / "repository.db"
    rt1 = _build(path)
    project_id = rt1.create_project("冬馆").value
    rt1.handler.execute(
        manuscript_import(),
        project_id=ProjectId(project_id),
        idempotency_key="import-1",
        summary="导入",
    )
    chapter_id = _chapter_id(rt1, project_id)
    saved = asyncio.run(rt1.workflow.save(_intent(project_id, chapter_id, "save-1", EDITED_TEXT)))
    assert saved.is_committed
    # 重启前不驱动发布：任务尚未创建，只存在于持久 Outbox

    rt2 = _build(path)  # 重启：全新 JobStore 与消费者，零进程内记忆
    assert rt2.jobs.created == []
    assert rt2.dispatch_pending(project_id) >= 1  # 待发布事件重投（补偿）
    assert [job_type for job_type, _ in rt2.jobs.created] == ["manuscript.analysis"]
    assert rt2.dispatch_pending(project_id) >= 0  # 已发布标记持久：不再重复建任务
    assert len(rt2.jobs.created) == 1


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def manuscript_import() -> object:
    from hastings.application.handlers.manuscript_commands import manuscript_command
    from hastings.domain.manuscript.commands import import_chapter

    return manuscript_command(import_chapter, chapter_id=CHAPTER, title="第一章", text=FIRST_TEXT)


def _chapter_id(rt: V2Runtime, project_id: str) -> str:
    head = rt.head(ProjectId(project_id))
    assert head is not None
    catalog = rt.queries.catalog(ProjectId(project_id), head)
    chapters = catalog.by_kind("manuscript.chapter")
    assert chapters
    return chapters[0].artifact_id


def _manuscript_view(rt: V2Runtime, project_id: str) -> dict[str, object]:
    head = rt.head(ProjectId(project_id))
    assert head is not None
    catalog = rt.queries.catalog(ProjectId(project_id), head)
    return {
        "commit_id": catalog.commit_id,
        "chapters": [
            {
                "chapter_id": entry.artifact_id,
                "content": entry.content,
                "revision_id": entry.revision_id,
            }
            for entry in catalog.entries
        ],
    }


def _prefs_view(rt: V2Runtime, project_id: str) -> list[dict[str, object]]:
    head = rt.head(ProjectId(project_id))
    assert head is not None
    return [
        {
            "preference_id": p.preference_id,
            "category": p.category,
            "statement": p.statement,
            "status": p.status,
            "evidence": p.evidence,
        }
        for p in rt.author_queries.preferences(ProjectId(project_id), head)
    ]


def _obs_view(rt: V2Runtime, project_id: str) -> list[dict[str, object]]:
    head = rt.head(ProjectId(project_id))
    assert head is not None
    return [
        {"observation_id": o.observation_id, "edit": o.edit, "classification": o.classification}
        for o in rt.author_queries.observations(ProjectId(project_id), head)
    ]
