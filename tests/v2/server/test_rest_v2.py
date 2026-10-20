"""Phase 14 T14.8：V2 最小 REST 面（仅后端）。

覆盖：建项目 → 导入章 → 查询正文 → 保存（成功 / no-op / 冲突 409 / 幂等重放）
→ 404 信封 → 作者模型查询（preferences/observations/evidence）→ 保存事件经
``dispatch_pending`` 调度分析任务且不重复。

TestClient 用 ``with client:``（长驻 portal），HTTP 请求复用同一事件循环——
与 uvicorn 生产拓扑一致（见 Lessons Learned：请求级事件循环会连带取消任务）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
from hastings.server.v2 import V2Runtime, build_v2_app


@pytest.fixture
def runtime() -> V2Runtime:
    return V2Runtime.memory()


@pytest.fixture
def client(runtime: V2Runtime) -> Iterator[TestClient]:
    app: FastAPI = build_v2_app(runtime)
    with TestClient(app) as test_client:
        yield test_client


def _create_project(client: TestClient) -> str:
    response = client.post("/api/v2/projects", json={"name": "冬季庄园"})
    assert response.status_code == 200, response.text
    return str(response.json()["project_id"])


def _import_chapter(client: TestClient, project_id: str) -> str:
    response = client.post(
        f"/api/v2/projects/{project_id}/chapters/import",
        json={
            "idempotency_key": "import-1",
            "title": "第一章",
            "text": "第一段。\n\n第二段。\n\n第三段。",
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["commit_id"])


# ---------------------------------------------------------------------------
# 项目与正文查询
# ---------------------------------------------------------------------------
class TestProjectAndManuscript:
    def test_create_project_and_empty_manuscript(self, client: TestClient) -> None:
        project_id = _create_project(client)
        assert project_id

        response = client.get(f"/api/v2/projects/{project_id}/manuscript")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commit_id"] is None
        assert body["chapters"] == []

    def test_import_chapter_and_query_manuscript(self, client: TestClient) -> None:
        project_id = _create_project(client)
        commit_id = _import_chapter(client, project_id)
        assert commit_id

        response = client.get(f"/api/v2/projects/{project_id}/manuscript")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commit_id"] == commit_id
        assert len(body["chapters"]) == 1

        chapter = body["chapters"][0]
        assert chapter["title"] == "第一章"
        assert [unit["text"] for unit in chapter["units"]] == ["第一段。", "第二段。", "第三段。"]
        assert chapter["chapter_base"]

    def test_query_single_chapter_returns_text_and_base(self, client: TestClient) -> None:
        project_id = _create_project(client)
        _import_chapter(client, project_id)

        manuscript = client.get(f"/api/v2/projects/{project_id}/manuscript").json()
        chapter_id = manuscript["chapters"][0]["chapter_id"]

        response = client.get(f"/api/v2/projects/{project_id}/chapters/{chapter_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["text"] == "第一段。\n\n第二段。\n\n第三段。"
        assert body["chapter_base"] == manuscript["chapters"][0]["chapter_base"]

    def test_unknown_project_returns_404_envelope(self, client: TestClient) -> None:
        response = client.get("/api/v2/projects/prj_missing/manuscript")
        assert response.status_code == 404, response.text
        error = response.json()["error"]
        assert error["code"] == "REPO_BRANCH_NOT_FOUND"
        assert error["where"] and error["why"] and error["fix"]


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------
class TestSave:
    def test_save_full_text_commits_and_returns_new_base(self, client: TestClient) -> None:
        project_id = _create_project(client)
        _import_chapter(client, project_id)

        manuscript = client.get(f"/api/v2/projects/{project_id}/manuscript").json()
        chapter = manuscript["chapters"][0]
        chapter_id = chapter["chapter_id"]

        response = client.post(
            f"/api/v2/projects/{project_id}/chapters/{chapter_id}/save",
            json={
                "idempotency_key": "save-1",
                "full_text": "第一段。\n\n第二段已改。\n\n第三段。",
                "expected_chapter_base": chapter["chapter_base"],
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "committed"
        assert body["commit_id"]
        assert body["chapter_base"] != chapter["chapter_base"]

        # 正文确实更新，且新基础与查询一致
        after = client.get(f"/api/v2/projects/{project_id}/chapters/{chapter_id}").json()
        assert "第二段已改。" in after["text"]
        assert after["chapter_base"] == body["chapter_base"]

    def test_noop_save_returns_no_change(self, client: TestClient) -> None:
        project_id = _create_project(client)
        _import_chapter(client, project_id)

        chapter = client.get(f"/api/v2/projects/{project_id}/manuscript").json()["chapters"][0]
        chapter_id = chapter["chapter_id"]
        text = "\n\n".join(unit["text"] for unit in chapter["units"])

        response = client.post(
            f"/api/v2/projects/{project_id}/chapters/{chapter_id}/save",
            json={"idempotency_key": "save-noop", "full_text": text},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "no_change"

    def test_stale_base_conflict_returns_409(self, client: TestClient) -> None:
        project_id = _create_project(client)
        _import_chapter(client, project_id)

        chapter = client.get(f"/api/v2/projects/{project_id}/manuscript").json()["chapters"][0]
        chapter_id = chapter["chapter_id"]
        stale_base = chapter["chapter_base"]

        # 先提交一次，使 stale_base 过期
        first = client.post(
            f"/api/v2/projects/{project_id}/chapters/{chapter_id}/save",
            json={"idempotency_key": "save-1", "full_text": "改。\n\n第二段。\n\n第三段。"},
        )
        assert first.status_code == 200

        conflict = client.post(
            f"/api/v2/projects/{project_id}/chapters/{chapter_id}/save",
            json={
                "idempotency_key": "save-2",
                "full_text": "再改。\n\n第二段。\n\n第三段。",
                "expected_chapter_base": stale_base,
            },
        )
        assert conflict.status_code == 409, conflict.text
        error = conflict.json()["error"]
        assert error["code"] == "MANUSCRIPT_BASE_CONFLICT"
        assert error["where"] and error["why"] and error["fix"]

    def test_idempotent_replay_returns_same_commit(self, client: TestClient) -> None:
        project_id = _create_project(client)
        _import_chapter(client, project_id)

        chapter_id = client.get(f"/api/v2/projects/{project_id}/manuscript").json()["chapters"][0][
            "chapter_id"
        ]
        payload = {
            "idempotency_key": "save-retry",
            "full_text": "第一段。\n\n第二段已改。\n\n第三段。",
        }
        first = client.post(
            f"/api/v2/projects/{project_id}/chapters/{chapter_id}/save", json=payload
        )
        replay = client.post(
            f"/api/v2/projects/{project_id}/chapters/{chapter_id}/save", json=payload
        )
        assert first.status_code == replay.status_code == 200
        assert first.json()["commit_id"] == replay.json()["commit_id"]

    def test_save_requires_exactly_one_content_source(self, client: TestClient) -> None:
        project_id = _create_project(client)
        _import_chapter(client, project_id)
        chapter_id = client.get(f"/api/v2/projects/{project_id}/manuscript").json()["chapters"][0][
            "chapter_id"
        ]

        both = client.post(
            f"/api/v2/projects/{project_id}/chapters/{chapter_id}/save",
            json={"idempotency_key": "s1", "full_text": "文本", "operations": []},
        )
        neither = client.post(
            f"/api/v2/projects/{project_id}/chapters/{chapter_id}/save",
            json={"idempotency_key": "s2"},
        )
        assert both.status_code == neither.status_code == 400
        assert both.json()["error"]["fix"] and neither.json()["error"]["fix"]

    def test_unknown_chapter_save_returns_404(self, client: TestClient) -> None:
        project_id = _create_project(client)
        response = client.post(
            f"/api/v2/projects/{project_id}/chapters/art_nochapter/save",
            json={"idempotency_key": "s1", "full_text": "文本"},
        )
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "MANUSCRIPT_UNKNOWN_CHAPTER"


# ---------------------------------------------------------------------------
# 作者模型查询
# ---------------------------------------------------------------------------
def _seed_author_model(runtime: V2Runtime, project_id: str) -> None:
    """直接经 Commit Service 播种一条观察与一条 active 偏好（查询面的测试夹具）。"""
    branch = runtime.service.get_branch(ProjectId(project_id), "main")
    assert branch is not None and branch.head_commit_id is not None
    observation = AuthorObservation(
        observation_id="obs_1",
        commit_id=branch.head_commit_id.value,
        edit=EditSample(
            unit_id="art_u2",
            before="他解释了很久。",
            after="他说了。",
            chapter_id="art_ch1",
        ),
        classification=Classification(
            category="language_style", confidence=0.9, status="confirmed", signals=("concise",)
        ),
    )
    preference = PreferenceHypothesis(
        preference_id="pref_1",
        category="language_style",
        statement="作者偏好更精简的措辞与句式",
        status=PREFERENCE_ACTIVE,
        evidence=(branch.head_commit_id.value,),
    )
    result = runtime.service.commit(
        CommitRequest(
            project_id=ProjectId(project_id),
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=Actor(kind="human", id="author_test"),
            idempotency_key="seed-author-model",
            summary="播种作者模型观察与偏好",
            changes=(
                ArtifactChange(
                    artifact_id=ArtifactId("obs_1"),
                    kind=am_kinds.KIND_OBSERVATION,
                    content=observation.to_content(),
                ),
                ArtifactChange(
                    artifact_id=ArtifactId("pref_1"),
                    kind=am_kinds.KIND_PREFERENCE,
                    content=preference.to_content(),
                ),
            ),
        )
    )
    assert result.status == "committed"


class TestAuthorModelEntriesRemoved:
    """作者模型 0.3 不交付入口（计划 §9.1：移除 0.3 入口、注入与默认调度）。

    旧数据仍然留在仓库里、可备份回看，但 v2 不再暴露读写入口——
    这条用例盯的就是「入口不会悄悄长回来」。
    """

    def test_legacy_author_model_paths_are_gone(
        self, client: TestClient, runtime: V2Runtime
    ) -> None:
        project_id = _create_project(client)
        _import_chapter(client, project_id)
        _seed_author_model(runtime, project_id)

        for path in (
            "author-model/preferences",
            "author-model/observations",
            "author-model/preferences/pref_1/evidence",
        ):
            response = client.get(f"/api/v2/projects/{project_id}/{path}")
            assert response.status_code == 404, (path, response.text)

    def test_legacy_preference_write_is_gone(self, client: TestClient) -> None:
        project_id = _create_project(client)
        response = client.post(
            f"/api/v2/projects/{project_id}/author-model/preferences",
            json={
                "idempotency_key": "declare",
                "expected_head": None,
                "category": "language_style",
                "statement": "少用解释性独白",
                "scope_kind": "project",
                "scope_target": project_id,
            },
        )
        assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# 恢复为新 Commit（验收 9 的 HTTP 面）
# ---------------------------------------------------------------------------
class TestRestoreEndpoint:
    def test_restore_reverts_to_source_commit_as_new_commit(self) -> None:
        runtime = V2Runtime.memory()
        app = build_v2_app(runtime)
        with TestClient(app) as c:
            project_id = _create_project(c)
            first_commit = _import_chapter(c, project_id)
            chapter_id = c.get(f"/api/v2/projects/{project_id}/manuscript").json()["chapters"][0][
                "chapter_id"
            ]
            saved = c.post(
                f"/api/v2/projects/{project_id}/chapters/{chapter_id}/save",
                json={"idempotency_key": "save-1", "full_text": "第一段。\n\n大改。\n\n第三段。"},
            )
            assert saved.json()["status"] == "committed"

            restored = c.post(
                f"/api/v2/projects/{project_id}/chapters/{chapter_id}/restore",
                json={"idempotency_key": "restore-1", "source_commit_id": first_commit},
            )
            assert restored.status_code == 200, restored.text
            body = restored.json()
            assert body["status"] == "committed"
            assert body["commit_id"] != first_commit

            after = c.get(f"/api/v2/projects/{project_id}/chapters/{chapter_id}").json()
            assert "第二段。" in after["text"] and "大改" not in after["text"]

    def test_restore_with_chapter_missing_at_source_returns_404(self) -> None:
        runtime = V2Runtime.memory()
        app = build_v2_app(runtime)
        with TestClient(app) as c:
            project_id = _create_project(c)
            first_commit = _import_chapter(c, project_id)
            # 第二个 commit 引入新章；恢复「新章」到没有它的首 Commit 应 404
            second = c.post(
                f"/api/v2/projects/{project_id}/chapters/import",
                json={"idempotency_key": "import-2", "title": "第二章", "text": "新章内容。"},
            )
            new_chapter_id = str(second.json()["chapter_id"])
            response = c.post(
                f"/api/v2/projects/{project_id}/chapters/{new_chapter_id}/restore",
                json={"idempotency_key": "restore-x", "source_commit_id": first_commit},
            )
            assert response.status_code == 404, response.text
            assert response.json()["error"]["code"] == "MANUSCRIPT_UNKNOWN_CHAPTER"


# ---------------------------------------------------------------------------
# 保存 → Outbox → 分析任务调度（消费者接线）
# ---------------------------------------------------------------------------
class TestEventProjectionWiring:
    """0.3（V03-0.4）：保存事件只走确定性投影，不再派生自动分析 Job。"""

    def test_save_publishes_event_without_creating_jobs(self) -> None:
        runtime = V2Runtime.memory()
        app = build_v2_app(runtime)
        with TestClient(app) as c:
            project_id = _create_project(c)
            _import_chapter(c, project_id)
            chapter_id = c.get(f"/api/v2/projects/{project_id}/manuscript").json()["chapters"][0][
                "chapter_id"
            ]

            saved = c.post(
                f"/api/v2/projects/{project_id}/chapters/{chapter_id}/save",
                json={"idempotency_key": "save-1", "full_text": "第一段。\n\n改。\n\n第三段。"},
            )
            assert saved.status_code == 200
            assert saved.json()["status"] == "committed"

            # 事件发布并标记（不积压），但零自动 Job（切断模型调度，I06）。
            assert runtime.dispatch_pending(project_id) >= 1
            assert runtime.jobs.created == []
            assert runtime.event_projection.report().get("manuscript.chapter.committed", 0) >= 1

            # 重复发布不重复计数；无新业务事件时投影 stable
            runtime.dispatch_pending(project_id)
            assert runtime.jobs.created == []

    def test_noop_save_creates_no_event_no_job(self) -> None:
        runtime = V2Runtime.memory()
        app = build_v2_app(runtime)
        with TestClient(app) as c:
            project_id = _create_project(c)
            _import_chapter(c, project_id)
            chapter = c.get(f"/api/v2/projects/{project_id}/manuscript").json()["chapters"][0]
            text = "\n\n".join(unit["text"] for unit in chapter["units"])

            saved = c.post(
                f"/api/v2/projects/{project_id}/chapters/{chapter['chapter_id']}/save",
                json={"idempotency_key": "noop", "full_text": text},
            )
            assert saved.json()["status"] == "no_change"
            # no-op 不产生业务事件：Outbox 只可能有导入时的内核事件，无分析任务
            runtime.dispatch_pending(project_id)
            assert runtime.jobs.created == []


# ---------------------------------------------------------------------------
# SQLite 后端装配（重启恢复的集成验收在 test_recovery 扩展）
# ---------------------------------------------------------------------------
def test_runtime_builds_sqlite_backend(tmp_path: Path) -> None:
    runtime = V2Runtime.sqlite(str(tmp_path / "v2.db"))
    app = build_v2_app(runtime)
    with TestClient(app) as client:
        project_id = _create_project(client)
        _import_chapter(client, project_id)
        body = client.get(f"/api/v2/projects/{project_id}/manuscript").json()
        assert len(body["chapters"]) == 1


# ---------------------------------------------------------------------------
# 统一能力公布（V03-0.7）
# ---------------------------------------------------------------------------


def test_capabilities_endpoint_publishes_unified_config() -> None:
    """GET /api/v2/capabilities：格式/上限/功能状态统一公布，前端不另写常量。"""
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    payload = client.get("/api/v2/capabilities").json()
    assert payload["product_version"] == "0.3"
    assert payload["api_version"] == "v2"
    imp = payload["import"]
    assert imp["formats"] == [".txt", ".md"]
    assert imp["max_file_bytes"] == 10 * 1024 * 1024
    assert imp["max_batch_bytes"] == 20 * 1024 * 1024
    assert imp["max_chapters_per_batch"] == 1000
    assert imp["preview_ttl_hours"] == 24
    features = payload["features"]
    assert features["execution_grants"]["status"] == "implemented"
    assert features["edit_sessions"]["status"] == "implemented"
    assert features["operation_records"]["status"] == "implemented"
    assert features["chapter_drafts"]["status"] == "implemented"
    assert features["chapter_drafts"]["max_chars"] == 120_000
    # 排除项不公布为可用（计划 §1.4：禁止显示为可用占位入口）
    for excluded in ("author_model", "style_observation", "consistency_audit", "corpus_rag"):
        assert features[excluded]["status"] == "excluded"
