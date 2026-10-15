"""HTTP 出口：可读状态、受约束人工 Patch、审阅合并与重启。"""

from fastapi.testclient import TestClient

from hastings.domain.repository.ids import Actor, ArtifactId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange
from hastings.server.v2 import V2Runtime, build_v2_app


def test_new_project_has_legal_empty_change_list() -> None:
    runtime = V2Runtime.memory()
    project = runtime.create_project("空项目")
    response = TestClient(build_v2_app(runtime)).get(f"/api/v2/projects/{project.value}/changes")
    assert response.status_code == 200
    assert response.json() == {"head_commit_id": None, "changes": []}


def test_change_center_http_flow_and_restart(tmp_path):
    db = str(tmp_path / "changes.sqlite")
    runtime = V2Runtime.sqlite(db)
    project = runtime.create_project("冬馆")
    branch = runtime.service.get_branch(project, "main")
    seed = runtime.service.commit(
        CommitRequest(
            project,
            branch.id,
            branch.head_commit_id,
            Actor("human", "author"),
            "seed",
            "故事基线",
            changes=(
                ArtifactChange(ArtifactId("char"), {"name": "林岚"}, kind="story.character"),
                ArtifactChange(ArtifactId("beat"), {"name": "beat"}, kind="story.plot_beat"),
            ),
            relations=(RelationChange("add", ArtifactId("char"), "motivates", ArtifactId("beat")),),
        )
    )
    client = TestClient(build_v2_app(runtime))
    prefix = f"/api/v2/projects/{project.value}/changes"
    opened = client.post(
        prefix,
        json={
            "idempotency_key": "open",
            "intent": "改变动机",
            "base_commit_id": seed.commit_id.value,
            "seed_ids": ["char"],
        },
    )
    assert opened.status_code == 200, opened.text
    cs = opened.json()["change_set_id"]
    url = f"{prefix}/{cs}"
    for action in ("analyze", "plan", "approve"):
        response = client.post(
            f"{url}/{action}",
            json={
                "idempotency_key": action,
                "expected_head": client.get(url).json()["head_commit_id"],
            },
        )
        assert response.status_code == 200, response.text
    response = client.post(
        f"{url}/execute",
        json={"idempotency_key": "exec", "patches": {"beat": {"name": "beat", "motive": "操纵"}}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "executed"
    prepared = client.post(f"{url}/review", json={"idempotency_key": "review"}).json()
    detail = client.get(url).json()
    assert detail["change_set"]["status"] == "review"
    merged = client.post(
        f"{url}/merge",
        json={
            "idempotency_key": "merge",
            "expected_head": detail["head_commit_id"],
            "selected": [h["hunk_id"] for h in prepared["hunks"]],
        },
    )
    assert merged.status_code == 200, merged.text
    restarted = TestClient(build_v2_app(V2Runtime.sqlite(db)))
    assert restarted.get(url).json()["change_set"]["status"] == "committed"
    assert (
        restarted.post(
            f"{url}/merge",
            json={
                "idempotency_key": "merge",
                "expected_head": detail["head_commit_id"],
                "selected": [h["hunk_id"] for h in prepared["hunks"]],
            },
        ).json()
        == merged.json()
    )
