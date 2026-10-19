"""HTTP 出口：可读状态、变更集打开与只读回看（逐章审批流 V03-8.6 起已退役）。"""

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


def test_change_set_opens_reads_and_survives_restart(tmp_path):
    """变更集仍可打开与回看；启动类动作（分析/计划/批准/执行/审阅/合并）统一 410。"""
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
    # 打开变更集本身是一次 Commit；退役动作不得再移动版本头。
    head_after_open = client.get(url).json()["head_commit_id"]

    for action in ("analyze", "plan", "approve", "execute", "review", "merge"):
        response = client.post(
            f"{url}/{action}",
            json={
                "idempotency_key": action,
                "expected_head": head_after_open,
            },
        )
        assert response.status_code == 410, response.text
        assert response.json()["error"]["code"] == "FEATURE_RETIRED_IN_03"

    detail = client.get(url).json()
    assert detail["change_set"]["status"] == "opened"
    assert detail["head_commit_id"] == head_after_open

    restarted = TestClient(build_v2_app(V2Runtime.sqlite(db)))
    restored = restarted.get(url).json()
    assert restored["change_set"]["status"] == "opened"
    assert restored["head_commit_id"] == head_after_open
