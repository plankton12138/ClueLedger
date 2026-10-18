"""正式稿前进时的三方合并（V03-5.2）：干净叠加、重叠报冲突、副本不丢。

场景：副本打开期间，另一条路径（另一页、导入、恢复）推进了正式稿。「修改
完成」不再以旧基础覆盖——能自动叠加的合并后发布，重叠的报冲突并给出
base/ours/theirs 三段事实，作者逐段选择；任何情况下副本与草稿都不丢。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.domain.manuscript.evidence import KIND_CHAPTER_SUMMARY
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.server.v2 import V2Runtime, build_v2_app

P_A = "侦探在雨夜抵达旅馆。"
P_B = "前台声称只有一位客人。"
FIRST = f"{P_A}\n\n{P_B}"

#: 作者只改第一段。
OURS = "侦探在雪夜抵达旅馆。\n\n前台声称只有一位客人。"
#: 正式稿只改第二段。
THEIRS = "侦探在雨夜抵达旅馆。\n\n前台空无一人，登记簿摊开着。"
#: 干净合并的期望结果：作者的第一段 + 正式稿的第二段。
MERGED = "侦探在雪夜抵达旅馆。\n\n前台空无一人，登记簿摊开着。"

#: 双方都改第二段且结果不同（第一段保持原样，冲突区就只有这一段）。
OURS_OVERLAP = "侦探在雨夜抵达旅馆。\n\n前台站着一个女人。"
THEIRS_OVERLAP = "侦探在雨夜抵达旅馆。\n\n前台只有一部电话。"


def _client() -> tuple[TestClient, V2Runtime, str, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "三方合并"}).json()["project_id"]
    chapter_id = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={
            "title": "第一章",
            "text": FIRST,
            "order_key": "0001",
            "idempotency_key": "imp-1",
        },
    ).json()["chapter_id"]
    return client, runtime, pid, chapter_id


def _open(client: TestClient, pid: str) -> str:
    response = client.post(f"/api/v2/projects/{pid}/edit-sessions", json={})
    assert response.status_code in (200, 201), response.text
    return response.json()["session"]["session_id"]


def _save(client: TestClient, pid: str, session_id: str, chapter_id: str, text: str, seq: int):
    return client.put(
        f"/api/v2/projects/{pid}/edit-sessions/{session_id}/chapters/{chapter_id}",
        json={"expected_save_seq": seq, "request_id": f"req-{seq}", "text": text},
    )


def _advance_formal(client: TestClient, pid: str, chapter_id: str, text: str, key: str) -> None:
    saved = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/save",
        json={"idempotency_key": key, "full_text": text},
    )
    assert saved.status_code == 200, saved.text


def _head(client: TestClient, pid: str) -> str:
    return client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]


def _text(client: TestClient, pid: str, chapter_id: str) -> str:
    return client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()["text"]


def _seed_fresh_summary(runtime: V2Runtime, pid: str, chapter_id: str) -> str:
    """给某一章预置 fresh 摘要，返回其修订号。"""
    repo = runtime.service
    branch = repo.get_branch(ProjectId(pid), "main")
    assert branch is not None and branch.head_commit_id is not None
    snapshot = repo.get_snapshot(ProjectId(pid), branch.head_commit_id)
    chapter = snapshot.artifacts[ArtifactId(chapter_id)]
    assert chapter.revision is not None
    summary_id = "sum_merge_1"
    result = repo.commit(
        CommitRequest(
            project_id=ProjectId(pid),
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=Actor(kind="human", id="author"),
            idempotency_key="seed-summary-merge",
            summary="预置摘要",
            changes=(
                ArtifactChange(
                    artifact_id=ArtifactId(summary_id),
                    content={
                        "summary_id": summary_id,
                        "chapter_id": chapter_id,
                        "source_hash": chapter.revision.content_hash.value,
                        "status": "fresh",
                    },
                    kind=KIND_CHAPTER_SUMMARY,
                ),
            ),
        )
    )
    revision = (
        repo.get_snapshot(ProjectId(pid), result.commit_id)
        .artifacts[ArtifactId(summary_id)]
        .revision
    )
    assert revision is not None
    return revision.id


def test_disjoint_changes_merge_cleanly_in_one_commit() -> None:
    """核心场景：作者改第一段、正式稿改第二段——两边都保留，一次提交。

    同时是 P1-2 的回归：合并的 base 必须是**作者读到的那一版**。若误绑成
    正式稿的新版本，合并会把正式稿的第二段改动冲掉——断言正式稿的新段落
    原样存在，即证明 base 没绑错。
    """
    client, _runtime, pid, chapter_id = _client()
    session = _open(client, pid)
    assert _save(client, pid, session, chapter_id, OURS, 0).status_code == 200

    head_before = _head(client, pid)
    _advance_formal(client, pid, chapter_id, THEIRS, "formal-1")
    assert _text(client, pid, chapter_id) == THEIRS

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["status"] == "committed"
    assert body["changed_chapter_ids"] == [chapter_id]
    assert body["merged_chapters"] == [chapter_id]
    # 作者的第一段 + 正式稿的第二段，两边都在。
    assert _text(client, pid, chapter_id) == MERGED
    # 一次提交：完成产生的 commit 就是当前 head。
    assert body["commit_id"] == _head(client, pid) != head_before
    assert client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()["session"] is None


def test_overlapping_changes_block_finish_with_three_sided_report() -> None:
    """双方改同一段且结果不同：409 + 逐段事实，正式稿与副本都原样保留。"""
    client, _runtime, pid, chapter_id = _client()
    session = _open(client, pid)
    assert _save(client, pid, session, chapter_id, OURS_OVERLAP, 0).status_code == 200
    _advance_formal(client, pid, chapter_id, THEIRS_OVERLAP, "formal-2")
    head_after_formal = _head(client, pid)

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 409, finished.text
    error = finished.json()["error"]
    assert error["code"] == "EDIT_SESSION_MERGE_CONFLICT"
    conflicts = error["details"]["conflicts"]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["chapter_id"] == chapter_id
    assert conflict["reason"] == "overlapping_changes"
    assert conflict["conflicts"] == [
        {
            "base": [P_B],
            "ours": ["前台站着一个女人。"],
            "theirs": ["前台只有一部电话。"],
        }
    ]
    # 正式稿没被覆盖，副本与草稿都还在，head 没动。
    assert _text(client, pid, chapter_id) == THEIRS_OVERLAP
    assert _head(client, pid) == head_after_formal
    still = client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()
    assert still["session"]["status"] == "open"
    assert still["drafts"][0]["text"] == OURS_OVERLAP


def test_author_can_resolve_a_conflict_and_finish() -> None:
    """冲突不是死路：作者按报告逐处选择，完成照常成功。

    「保留我的版本」是作者最常见的意图——只改草稿是到不了这一步的（草稿
    永远和正式稿的那一处冲突），所以选择必须是完成时的显式参数。
    """
    client, runtime, pid, chapter_id = _client()
    session = _open(client, pid)
    assert _save(client, pid, session, chapter_id, OURS_OVERLAP, 0).status_code == 200
    _advance_formal(client, pid, chapter_id, THEIRS_OVERLAP, "formal-3")

    blocked = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert blocked.status_code == 409

    finished = client.post(
        f"/api/v2/projects/{pid}/edit-sessions/{session}/finish",
        json={"merge_choices": [{"chapter_id": chapter_id, "conflict": 0, "choice": "ours"}]},
    )
    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["merged_chapters"] == [chapter_id]
    # 作者的第二段覆盖了正式稿的第二段——这是显式选择，不是静默覆盖。
    assert _text(client, pid, chapter_id) == OURS_OVERLAP
    # 选择写进操作记录的 refs（终态持久化）：版本历史里读得出这次合并在哪处
    # 取了哪边。回执信封不带 refs，记录才是权威存储。
    record = runtime.service.get_artifact(
        ArtifactId(f"art_{body['operation_id']}"), CommitId(body["commit_id"])
    )
    assert record is not None and record.revision is not None
    assert record.revision.content["refs"]["merge_choices"] == [
        {"chapter_id": chapter_id, "conflict": 0, "choice": "ours"}
    ]


def test_choosing_theirs_keeps_the_formal_version_on_that_conflict() -> None:
    client, _runtime, pid, chapter_id = _client()
    session = _open(client, pid)
    assert _save(client, pid, session, chapter_id, OURS_OVERLAP, 0).status_code == 200
    _advance_formal(client, pid, chapter_id, THEIRS_OVERLAP, "formal-3b")

    finished = client.post(
        f"/api/v2/projects/{pid}/edit-sessions/{session}/finish",
        json={"merge_choices": [{"chapter_id": chapter_id, "conflict": 0, "choice": "theirs"}]},
    )
    assert finished.status_code == 200, finished.text
    assert _text(client, pid, chapter_id) == THEIRS_OVERLAP


def test_partial_choices_are_rejected_with_a_fresh_report() -> None:
    """漏选冲突 = 没有做出选择：带着同样的报告再次 409，不擅自替作者决定。"""
    client, _runtime, pid, chapter_id = _client()
    session = _open(client, pid)
    assert _save(client, pid, session, chapter_id, OURS_OVERLAP, 0).status_code == 200
    _advance_formal(client, pid, chapter_id, THEIRS_OVERLAP, "formal-5")

    blocked = client.post(
        f"/api/v2/projects/{pid}/edit-sessions/{session}/finish",
        json={
            "merge_choices": [
                # 只选了一半：本章还有别的冲突没决定。
                {"chapter_id": chapter_id, "conflict": 99, "choice": "ours"}
            ]
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "EDIT_SESSION_MERGE_CONFLICT"
    assert blocked.json()["error"]["details"]["conflicts"][0]["conflicts"]
    # 副本与草稿原样保留。
    still = client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()
    assert still["session"]["status"] == "open"
    assert still["drafts"][0]["text"] == OURS_OVERLAP


def test_two_conflicts_in_one_chapter_can_be_chosen_separately() -> None:
    """一处章里两处冲突：可以一处取我的、一处取正式的。

    需要四段正文：两处 contested 区域之间隔着双方都没动的段落，diff 才会把
    它们分成两个区域（若各改相邻两段，会并成一个区域、一处冲突）。
    """
    client, _runtime, pid, _ = _client()
    chapter_id = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={
            "title": "第二章",
            "text": f"{P_A}\n\n{P_B}\n\n楼梯尽头有光。\n\n伞是旧的。",
            "order_key": "0002",
            "idempotency_key": "imp-2",
        },
    ).json()["chapter_id"]
    session = _open(client, pid)
    # 双方都改第一段和第三段，互不相让 → 两处冲突；第二、四段双方都没动。
    ours = "侦探在雪夜抵达旅馆。\n\n前台声称只有一位客人。\n\n楼梯通向地下室。\n\n伞是旧的。"
    theirs = "雨从黄昏下到清晨。\n\n前台声称只有一位客人。\n\n楼梯尽头是海。\n\n伞是旧的。"
    assert _save(client, pid, session, chapter_id, ours, 0).status_code == 200
    _advance_formal(client, pid, chapter_id, theirs, "formal-6")

    blocked = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert blocked.status_code == 409
    conflicts = blocked.json()["error"]["details"]["conflicts"][0]["conflicts"]
    assert len(conflicts) == 2
    assert conflicts[0]["ours"] == ["侦探在雪夜抵达旅馆。"]
    assert conflicts[1]["theirs"] == ["楼梯尽头是海。"]

    finished = client.post(
        f"/api/v2/projects/{pid}/edit-sessions/{session}/finish",
        json={
            "merge_choices": [
                {"chapter_id": chapter_id, "conflict": 0, "choice": "ours"},
                {"chapter_id": chapter_id, "conflict": 1, "choice": "theirs"},
            ]
        },
    )
    assert finished.status_code == 200, finished.text
    assert _text(client, pid, chapter_id) == (
        "侦探在雪夜抵达旅馆。\n\n前台声称只有一位客人。\n\n楼梯尽头是海。\n\n伞是旧的。"
    )


def test_untouched_draft_chapter_is_not_republished_after_formal_advance() -> None:
    """作者没真改的章（草稿等于 base）：正式稿的前进不被我们重复发布。"""
    client, runtime, pid, chapter_id = _client()
    seeded_rev = _seed_fresh_summary(runtime, pid, chapter_id)

    session = _open(client, pid)
    # 作者打开后又原样保存（自动保存触发，但没有任何实际修改）。
    assert _save(client, pid, session, chapter_id, FIRST, 0).status_code == 200
    _advance_formal(client, pid, chapter_id, THEIRS_OVERLAP, "formal-4")
    stale_rev = _summary_revision(runtime, pid)

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 200, finished.text
    body = finished.json()
    # 没有作者的改动可发布：本章不在本次变化里，也不是合并发布的章。
    assert body["changed_chapter_ids"] == []
    assert body["merged_chapters"] == []
    # 正式稿的推进自己失效过摘要一次；我们这轮没有再去动它。
    assert _summary_revision(runtime, pid) == stale_rev != seeded_rev
    # 正文就是正式稿那一版，没有被回退。
    assert _text(client, pid, chapter_id) == THEIRS_OVERLAP


def _summary_revision(runtime: V2Runtime, pid: str) -> str:
    repo = runtime.service
    branch = repo.get_branch(ProjectId(pid), "main")
    assert branch is not None and branch.head_commit_id is not None
    revision = (
        repo.get_snapshot(ProjectId(pid), branch.head_commit_id)
        .artifacts[ArtifactId("sum_merge_1")]
        .revision
    )
    assert revision is not None
    return revision.id
