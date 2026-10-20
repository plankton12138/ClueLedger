"""手工修改副本（V03-2.4，计划 M04）。

契约：一个作品同时只有一个未结束副本；草稿按章惰性建立，不写正式正文；
自动保存用保存序号 CAS（迟到响应不覆盖新文本）；完成整批发布一次并关闭
副本；放弃只结束副本；重启/重试不丢字、不重复建版本。

覆盖：唯一副本与恢复、草稿隔离、序号冲突与请求重试、完成只改草稿章、
完成后重试幂等、基础冲突保副本、放弃不发布、暂存不结束、无变化不造版本、
空副本拒绝完成。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app

FIRST = "侦探在雨夜抵达旅馆。\n\n前台声称只有一位客人。"
SECOND = "雪停了。\n\n老板娘在擦杯子。"


def _client() -> tuple[TestClient, V2Runtime, str, list[str]]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "修改走查"}).json()["project_id"]
    ids: list[str] = []
    for index, text in enumerate((FIRST, SECOND), start=1):
        chapter_id = client.post(
            f"/api/v2/projects/{pid}/chapters/import",
            json={
                "title": f"第{index}章",
                "text": text,
                "order_key": f"{index:04d}",
                "idempotency_key": f"imp-{index}",
            },
        ).json()["chapter_id"]
        ids.append(chapter_id)
    return client, runtime, pid, ids


def _start(client: TestClient, pid: str) -> dict:
    response = client.post(f"/api/v2/projects/{pid}/edit-sessions")
    assert response.status_code == 200, response.text
    return response.json()


def _save(
    client: TestClient,
    pid: str,
    session_id: str,
    chapter_id: str,
    text: str,
    seq: int,
    request_id: str = "req-1",
):
    return client.put(
        f"/api/v2/projects/{pid}/edit-sessions/{session_id}/chapters/{chapter_id}",
        json={"expected_save_seq": seq, "request_id": request_id, "text": text},
    )


def _chapter_text(client: TestClient, pid: str, chapter_id: str) -> str:
    return client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()["text"]


def test_start_returns_single_open_session() -> None:
    client, _runtime, pid, _chapters = _client()
    first = _start(client, pid)
    second = _start(client, pid)
    assert first["session"]["session_id"] == second["session"]["session_id"]
    assert first["session"]["status"] == "open"
    assert (
        client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()["session"]["session_id"]
        == first["session"]["session_id"]
    )


def test_draft_does_not_touch_formal_text_and_cas_rejects_stale() -> None:
    client, _runtime, pid, chapters = _client()
    session = _start(client, pid)["session"]["session_id"]

    saved = _save(client, pid, session, chapters[0], "改后的第一段。", 0, "req-a")
    assert saved.status_code == 200, saved.text
    assert saved.json()["save_seq"] == 1
    assert _chapter_text(client, pid, chapters[0]) == FIRST

    stale = _save(client, pid, session, chapters[0], "迟到的旧文本。", 0, "req-b")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "EDIT_SAVE_SEQ_CONFLICT"

    replay = _save(client, pid, session, chapters[0], "改后的第一段。", 0, "req-a")
    assert replay.status_code == 200
    assert replay.json()["save_seq"] == 1

    advanced = _save(client, pid, session, chapters[0], "再改一次。", 1, "req-c")
    assert advanced.status_code == 200
    assert advanced.json()["save_seq"] == 2


def test_finish_publishes_drafts_and_closes_session() -> None:
    client, _runtime, pid, chapters = _client()
    session = _start(client, pid)["session"]["session_id"]
    _save(client, pid, session, chapters[0], "侦探在清晨抵达旅馆。", 0)

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["status"] == "committed"
    assert body["changed_chapter_ids"] == [chapters[0]]
    assert _chapter_text(client, pid, chapters[0]).strip() == "侦探在清晨抵达旅馆。"
    # 未修改的章不动
    assert _chapter_text(client, pid, chapters[1]) == SECOND
    assert client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()["session"] is None

    retry = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert retry.status_code == 200, retry.text
    assert retry.json()["operation_id"] == body["operation_id"]
    assert retry.json()["commit_id"] == body["commit_id"]


def test_finish_conflict_keeps_session_and_draft() -> None:
    """副本之外正式稿被另一条路径推进：双方重写全章，按合并冲突处理。

    V03-5.2 起同章前进不再是一律硬冲突——能自动叠加的会合并（见
    test_disjoint_changes_merge_cleanly）；这里双方各自重写全章，没有可
    自动叠加的部分，报 EDIT_SESSION_MERGE_CONFLICT 并保留副本与草稿。
    """
    client, _runtime, pid, chapters = _client()
    session = _start(client, pid)["session"]["session_id"]
    _save(client, pid, session, chapters[0], "副本里的新文本。", 0)

    # 副本之外，正式正文被另一条路径推进
    saved = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapters[0]}/save",
        json={"idempotency_key": "outside-edit", "full_text": "正式稿被外部修改。"},
    )
    assert saved.status_code == 200, saved.text

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 409
    assert finished.json()["error"]["code"] == "EDIT_SESSION_MERGE_CONFLICT"

    still = client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()
    assert still["session"]["session_id"] == session
    assert still["session"]["status"] == "open"
    assert still["drafts"][0]["text"] == "副本里的新文本。"


def test_abandon_ends_session_without_publishing() -> None:
    client, _runtime, pid, chapters = _client()
    session = _start(client, pid)["session"]["session_id"]
    _save(client, pid, session, chapters[0], "不会发布的文本。", 0)

    abandoned = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/abandon")
    assert abandoned.status_code == 200
    assert abandoned.json()["session"]["status"] == "abandoned"
    assert _chapter_text(client, pid, chapters[0]) == FIRST

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 409
    assert finished.json()["error"]["code"] == "EDIT_SESSION_CONFLICT"


def test_suspend_keeps_session_open() -> None:
    client, _runtime, pid, chapters = _client()
    session = _start(client, pid)["session"]["session_id"]
    _save(client, pid, session, chapters[0], "暂存前改的内容。", 0)

    suspended = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/suspend")
    assert suspended.status_code == 200
    assert suspended.json()["session"]["status"] == "open"
    assert suspended.json()["drafts"][0]["text"] == "暂存前改的内容。"


def test_finish_without_change_creates_no_version() -> None:
    client, _runtime, pid, chapters = _client()
    head_before = client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]
    session = _start(client, pid)["session"]["session_id"]
    _save(client, pid, session, chapters[0], FIRST, 0)

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "no_change"
    assert finished.json()["commit_id"] is None
    head_after = client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]
    assert head_after == head_before
    assert client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()["session"] is None


def test_finish_without_drafts_is_rejected() -> None:
    client, _runtime, pid, _chapters = _client()
    session = _start(client, pid)["session"]["session_id"]
    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 400
    assert finished.json()["error"]["code"] == "EDIT_SESSION_EMPTY"


def test_finish_rejects_stale_expected_sequences() -> None:
    """完成时冻结的序号集合与草稿不一致：说明还有未确认的自动保存。"""
    client, _runtime, pid, chapters = _client()
    session = _start(client, pid)["session"]["session_id"]
    _save(client, pid, session, chapters[0], "第一次改。", 0, "req-1")
    _save(client, pid, session, chapters[0], "第二次改。", 1, "req-2")

    finished = client.post(
        f"/api/v2/projects/{pid}/edit-sessions/{session}/finish",
        json={"expected_save_sequences": {chapters[0]: 1}},
    )
    assert finished.status_code == 409
    assert finished.json()["error"]["code"] == "EDIT_SAVE_SEQ_CONFLICT"
    assert (
        client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()["session"]["status"]
        == "open"
    )


def test_draft_and_session_survive_restart(tmp_path) -> None:
    """重启后副本与草稿仍在：未落盘内容不能只活在内存里（M04 第 3 条）。"""
    db = str(tmp_path / "edit.db")
    first = V2Runtime(db_path=db)
    client = TestClient(build_v2_app(first))
    pid = client.post("/api/v2/projects", json={"name": "重启走查"}).json()["project_id"]
    chapter_id = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={
            "title": "第一章",
            "text": FIRST,
            "order_key": "0001",
            "idempotency_key": "imp-1",
        },
    ).json()["chapter_id"]
    session = _start(client, pid)["session"]["session_id"]
    assert _save(client, pid, session, chapter_id, "重启前改的内容。", 0).status_code == 200

    second = V2Runtime(db_path=db)
    resumed = TestClient(build_v2_app(second))
    current = resumed.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()
    assert current["session"]["session_id"] == session
    assert current["drafts"][0]["text"] == "重启前改的内容。"
    assert current["drafts"][0]["save_seq"] == 1


def test_first_save_binds_the_version_the_author_read() -> None:
    """打开旧正文后别人保存新稿：首次保存仍绑定作者读到的那一版。

    复现路径：会话打开（H1）→ 别的页面保存同一章（H2）→ 首次自动保存。
    早先这里取「当前章基础」，把新基础绑给旧文本，随后「修改完成」拿着新基础
    放行，直接覆盖别人的保存。现在基础固定在会话打开时的版本，完成时显式冲突。
    """
    from hastings.domain.manuscript.catalog import ManuscriptCatalog
    from hastings.domain.repository.ids import CommitId, ProjectId

    client, runtime, pid, chapters = _client()
    session = _start(client, pid)["session"]["session_id"]
    head_at_open = client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]
    base_at_open = ManuscriptCatalog.from_snapshot(
        runtime.service.get_snapshot(ProjectId(pid), CommitId(head_at_open))
    ).chapter_base(chapters[0])

    # 副本之外，正式正文先被推进——作者此刻还在读旧文本。
    saved = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapters[0]}/save",
        json={"idempotency_key": "outside-first", "full_text": "别的页面先改了这一章。"},
    )
    assert saved.status_code == 200, saved.text

    stored = _save(client, pid, session, chapters[0], "副本里的新文本。", 0)
    assert stored.status_code == 200, stored.text
    drafts = client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()["drafts"]
    assert drafts[0]["base_chapter_base"] == base_at_open

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    # 双方各自重写全章：没有可自动叠加的部分，按合并冲突拒绝（V03-5.2），
    # 别人的保存没有被覆盖。
    assert finished.status_code == 409, finished.text
    assert finished.json()["error"]["code"] == "EDIT_SESSION_MERGE_CONFLICT"
    # 别人的保存没有被覆盖，副本与草稿都还在。
    assert _chapter_text(client, pid, chapters[0]) == "别的页面先改了这一章。"
    still = client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()
    assert still["session"]["status"] == "open"
    assert still["drafts"][0]["text"] == "副本里的新文本。"


def test_unrelated_save_between_open_and_finish_does_not_block() -> None:
    """一次无关章的保存不该阻断本次修改：冲突按对象判，不按 commit 判。"""
    client, _runtime, pid, chapters = _client()
    session = _start(client, pid)["session"]["session_id"]
    _save(client, pid, session, chapters[0], "副本里的新文本。", 0)

    saved = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapters[1]}/save",
        json={"idempotency_key": "outside-other", "full_text": "第二章也被改了。"},
    )
    assert saved.status_code == 200, saved.text

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 200, finished.text
    assert _chapter_text(client, pid, chapters[0]).strip() == "副本里的新文本。"
    assert _chapter_text(client, pid, chapters[1]) == "第二章也被改了。"


# ---------------------------------------------------------------------------
# 跨章修改（V03-5.1）：本轮章节清单、完成前 flush、无关章不变
# ---------------------------------------------------------------------------

THIRD = "门后是一条向下的楼梯，灯坏了。\n\n有人在楼梯口放了一把伞。"


def _start_with(client: TestClient, pid: str, chapter_ids: list[str] | None = None) -> dict:
    payload = {} if chapter_ids is None else {"chapter_ids": chapter_ids}
    response = client.post(f"/api/v2/projects/{pid}/edit-sessions", json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()


def _seed_summary(runtime: V2Runtime, pid: str, chapter_id: str) -> str:
    """给某一章预置一条 fresh 摘要，返回该摘要对象的当前修订号。"""
    from hastings.domain.manuscript.evidence import KIND_CHAPTER_SUMMARY
    from hastings.domain.repository.ids import Actor, ArtifactId, ProjectId
    from hastings.domain.repository.requests import ArtifactChange, CommitRequest

    repo = runtime.service
    branch = repo.get_branch(ProjectId(pid), "main")
    assert branch is not None and branch.head_commit_id is not None
    head = branch.head_commit_id
    snapshot = repo.get_snapshot(ProjectId(pid), head)
    chapter = snapshot.artifacts[ArtifactId(chapter_id)]
    assert chapter.revision is not None
    summary_id = f"sum_{chapter_id}"
    result = repo.commit(
        CommitRequest(
            project_id=ProjectId(pid),
            branch_id=branch.id,
            expected_head=head,
            actor=Actor(kind="human", id="author"),
            idempotency_key=f"seed-summary-{chapter_id}",
            summary=f"预置 {chapter_id} 摘要",
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
    after = repo.get_snapshot(ProjectId(pid), result.commit_id)
    revision = after.artifacts[ArtifactId(summary_id)].revision
    assert revision is not None
    return revision.id


def test_start_declares_round_chapters_in_order() -> None:
    """本轮清单按声明顺序去重：界面「本轮改了哪几章」与完成核对都用它。"""
    client, _runtime, pid, chapters = _client()
    body = _start_with(client, pid, [chapters[1], chapters[0], chapters[1]])
    assert body["session"]["chapters"] == [chapters[1], chapters[0]]
    # 恢复同一副本：清单不变，后到的声明不改写正在进行的一轮。
    again = _start_with(client, pid, [chapters[0]])
    assert again["session"]["chapters"] == [chapters[1], chapters[0]]


def test_declaring_unknown_chapter_is_rejected() -> None:
    """清单只收作品里已有的章：写错的章 ID 不能混进完成核对。"""
    client, _runtime, pid, _chapters = _client()
    response = client.post(
        f"/api/v2/projects/{pid}/edit-sessions", json={"chapter_ids": ["ch_nope"]}
    )
    # 章不存在与「会话不存在」同族：按 404 报，不按请求格式错误报。
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "MANUSCRIPT_UNKNOWN_CHAPTER"
    assert client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()["session"] is None


def test_first_save_joins_the_round() -> None:
    """有机流式修改不事先声明：首次保存的章自动进入清单。"""
    client, _runtime, pid, chapters = _client()
    session = _start_with(client, pid)["session"]["session_id"]
    assert session
    _save(client, pid, session, chapters[1], "先改第二章。", 0)
    current = client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()
    assert current["session"]["chapters"] == [chapters[1]]


def test_finish_requires_all_declared_chapters_flushed() -> None:
    """声明了却没保存的章挡住完成：不产生「带病」的半套版本。"""
    client, _runtime, pid, chapters = _client()
    session = _start_with(client, pid, list(chapters))["session"]["session_id"]
    _save(client, pid, session, chapters[0], "只改了第一章。", 0)

    blocked = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "EDIT_SESSION_UNFLUSHED"
    assert blocked.json()["error"]["details"]["missing"] == [chapters[1]]
    # 正文没动，副本还开着。
    assert _chapter_text(client, pid, chapters[0]) == FIRST
    still = client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()
    assert still["session"]["status"] == "open"

    _save(client, pid, session, chapters[1], "第二章也改完了。", 0)
    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 200, finished.text
    assert _chapter_text(client, pid, chapters[1]) == "第二章也改完了。"


def test_cross_chapter_finish_is_one_commit_and_leaves_the_rest_untouched() -> None:
    """V03-5.1 退出条件：改 1、2 章一次 commit，第 3 章正文与摘要不变。"""
    from hastings.domain.repository.ids import ArtifactId, ProjectId

    client, runtime, pid, chapters = _client()
    third_id = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={
            "title": "第三章",
            "text": THIRD,
            "order_key": "0003",
            "idempotency_key": "imp-3",
        },
    ).json()["chapter_id"]

    summary_revisions = {
        chapter_id: _seed_summary(runtime, pid, chapter_id)
        for chapter_id in (chapters[0], chapters[1], third_id)
    }

    session = _start_with(client, pid, [chapters[0], chapters[1]])["session"]["session_id"]
    _save(client, pid, session, chapters[0], "第一章的新文本。", 0)
    _save(client, pid, session, chapters[1], "第二章的新文本。", 0)

    finished = client.post(f"/api/v2/projects/{pid}/edit-sessions/{session}/finish", json={})
    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["status"] == "committed"
    assert body["changed_chapter_ids"] == sorted([chapters[0], chapters[1]])
    assert _chapter_text(client, pid, chapters[0]).strip() == "第一章的新文本。"
    assert _chapter_text(client, pid, chapters[1]).strip() == "第二章的新文本。"

    # 第 3 章：正文与摘要都不动（摘要连修订号都没变，说明没有多余提交碰它）。
    assert _chapter_text(client, pid, third_id) == THIRD
    branch = runtime.service.get_branch(ProjectId(pid), "main")
    assert branch is not None and branch.head_commit_id is not None
    snapshot = runtime.service.get_snapshot(ProjectId(pid), branch.head_commit_id)
    for chapter_id in (chapters[0], chapters[1]):
        view = snapshot.artifacts[ArtifactId(f"sum_{chapter_id}")]
        assert view.revision is not None
        assert view.revision.content["status"] == "stale"
        assert view.revision.id != summary_revisions[chapter_id]
    third_view = snapshot.artifacts[ArtifactId(f"sum_{third_id}")]
    assert third_view.revision is not None
    assert third_view.revision.id == summary_revisions[third_id]
    assert third_view.revision.content["status"] == "fresh"
    # 清单里的章都发了；没在清单里的第 3 章没有草稿可发。
    drafts_after = client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()
    assert drafts_after["session"] is None
