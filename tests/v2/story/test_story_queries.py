"""T13.7 应用查询层测试：视图绑定 Commit，head 前移不会返回旧结果。

用一个只读假端口按 (project_id, commit_id) 返回预置 Snapshot，断言：
- 每个视图的 commit_id 等于所请求的 Commit（无缓存，不会张冠李戴）；
- 同一项目两个 Commit 的投影互不污染；
- StoryStateAtBeat 也经同一入口绑定 Commit。
"""

from __future__ import annotations

from typing import Any

from hastings.application.queries.story_queries import StoryQueries
from hastings.domain.repository.ids import (
    ArtifactId,
    CommitId,
    ProjectId,
)
from hastings.domain.repository.models import ArtifactView, RelationRevision, Snapshot
from hastings.domain.repository.requests import (
    ArtifactQuery,
    CommitRequest,
    CreateBranchRequest,
    MoveBranchRequest,
)
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Character
from hastings.domain.story.narrative.models import Beat, NarrativeNode

from .conftest import build_snapshot

PROJECT = ProjectId("prj_test")


class FakeReader:
    """按 Commit 返回预置快照的只读假端口，并记录被请求的 Commit。"""

    def __init__(self, snapshots: dict[str, Snapshot]) -> None:
        self._snapshots = snapshots
        self.requested: list[str] = []

    def get_snapshot(self, project_id: ProjectId, commit_id: CommitId) -> Snapshot:
        self.requested.append(commit_id.value)
        return self._snapshots[commit_id.value]

    # ---- 未用到的只读方法：显式失败，避免假装实现 ----
    def get_project(self, project_id: ProjectId) -> Any:
        raise NotImplementedError

    def get_branch(self, project_id: ProjectId, name: str) -> Any:
        raise NotImplementedError

    def get_artifact(self, artifact_id: ArtifactId, at_commit_id: CommitId) -> ArtifactView | None:
        raise NotImplementedError

    def list_artifacts(self, query: ArtifactQuery, at_commit_id: CommitId) -> list[ArtifactView]:
        raise NotImplementedError

    def diff(self, before: CommitId, after: CommitId) -> Any:
        raise NotImplementedError

    def commit(self, request: CommitRequest) -> Any:  # pragma: no cover - 只读端口
        raise NotImplementedError

    def create_project(self, name: str) -> Any:  # pragma: no cover
        raise NotImplementedError

    def create_branch(self, request: CreateBranchRequest) -> Any:  # pragma: no cover
        raise NotImplementedError

    def move_branch(self, request: MoveBranchRequest) -> Any:  # pragma: no cover
        raise NotImplementedError


def _character(artifact_id: str, name: str) -> tuple[str, str, dict[str, Any]]:
    return (artifact_id, kinds.KIND_CHARACTER, Character(name=name).to_content())


def _beat(
    artifact_id: str, parent_id: str | None, order_key: str
) -> tuple[str, str, dict[str, Any]]:
    return (
        artifact_id,
        kinds.KIND_BEAT,
        Beat(beat_id=artifact_id, parent_id=parent_id, order_key=order_key).to_content(),
    )


def _node(artifact_id: str, parent_id: str | None = None) -> tuple[str, str, dict[str, Any]]:
    return (
        artifact_id,
        kinds.KIND_CHAPTER,
        NarrativeNode(
            node_id=artifact_id, kind=kinds.KIND_CHAPTER, parent_id=parent_id
        ).to_content(),
    )


def _reader_for_two_commits() -> FakeReader:
    old = build_snapshot(
        [
            _character("art_c1", "美咲"),
            _node("art_ch1"),
            _beat("art_b1", "art_ch1", "010"),
        ],
        commit_id="cmt_1",
    )
    new = build_snapshot(
        [
            _character("art_c1", "美咲"),
            _character("art_c2", "管家"),
            _node("art_ch1"),
            _beat("art_b1", "art_ch1", "010"),
            _beat("art_b2", "art_ch1", "020"),
        ],
        commit_id="cmt_2",
    )
    return FakeReader({"cmt_1": old, "cmt_2": new})


def test_views_carry_requested_commit_id() -> None:
    reader = _reader_for_two_commits()
    queries = StoryQueries(reader)

    canon = queries.canon_snapshot(PROJECT, CommitId("cmt_1"))
    tree = queries.narrative_tree(PROJECT, CommitId("cmt_2"))
    timeline = queries.disclosure_timeline(PROJECT, CommitId("cmt_1"))
    fairness = queries.mystery_fairness(PROJECT, CommitId("cmt_2"), fairness_cutoff=0.5)
    setups = queries.open_setups(PROJECT, CommitId("cmt_1"), deadline=0.5)

    assert canon.commit_id == "cmt_1"
    assert tree.commit_id == "cmt_2"
    assert timeline.commit_id == "cmt_1"
    assert fairness.commit_id == "cmt_2"
    assert setups.commit_id == "cmt_1"
    assert reader.requested == ["cmt_1", "cmt_2", "cmt_1", "cmt_2", "cmt_1"]


def test_old_commit_view_is_stable_after_new_commit_exists() -> None:
    reader = _reader_for_two_commits()
    queries = StoryQueries(reader)

    # 旧 Commit 的视图不会因为新 Commit 存在而「顺手升级」到最新
    old_facts = queries.canon_snapshot(PROJECT, CommitId("cmt_1")).facts
    new_facts = queries.canon_snapshot(PROJECT, CommitId("cmt_2")).facts
    assert {f.artifact_id for f in old_facts} != {f.artifact_id for f in new_facts}
    assert {f.artifact_id for f in old_facts} == {"art_c1"}


def test_story_state_is_bound_to_commit() -> None:
    reader = _reader_for_two_commits()
    queries = StoryQueries(reader)

    final = queries.story_state(PROJECT, CommitId("cmt_2"))
    at_beat = queries.story_state(PROJECT, CommitId("cmt_2"), beat_id="art_b1", after=False)
    assert final.commit_id == "cmt_2"
    assert at_beat.commit_id == "cmt_2"
    assert final.at_beat_id is None
    assert at_beat.at_beat_id == "art_b1"


def test_relations_are_carried_into_catalog() -> None:
    relation = RelationRevision(
        id="rel_1",
        commit_id=CommitId("cmt_1"),
        action="add",
        source=ArtifactId("art_c2"),
        relation_type=kinds.REL_SUPERSEDES,
        target=ArtifactId("art_c1"),
    )
    snapshot = build_snapshot(
        [_character("art_c1", "美咲"), _character("art_c2", "美咲")],
        commit_id="cmt_1",
        relations=(relation,),
    )
    queries = StoryQueries(FakeReader({"cmt_1": snapshot}))
    canon = queries.canon_snapshot(PROJECT, CommitId("cmt_1"))
    assert [f.artifact_id for f in canon.facts] == ["art_c2"]
    assert canon.superseded[0].old_id == "art_c1"
