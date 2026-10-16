"""作者的偏好声明与撤销必须留下版本，陈旧页面不能覆盖新决定。"""

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app


def test_declare_replay_revoke_and_stale_page() -> None:
    client = TestClient(build_v2_app(V2Runtime.memory()))
    pid = client.post("/api/v2/projects", json={"name": "作者偏好"}).json()["project_id"]
    prefix = f"/api/v2/projects/{pid}/author-model/preferences"
    body = {
        "idempotency_key": "declare",
        "expected_head": None,
        "category": "language_style",
        "statement": "少用解释性独白",
        "scope_kind": "project",
        "scope_target": pid,
    }
    first = client.post(prefix, json=body)
    assert first.status_code == 200, first.text
    assert client.post(prefix, json=body).json()["commit_id"] == first.json()["commit_id"]
    prefs = client.get(prefix).json()
    assert len(prefs["preferences"]) == 1
    pref = prefs["preferences"][0]
    assert pref["status"] == "active"
    stale = client.post(prefix, json={**body, "idempotency_key": "stale", "statement": "另一条"})
    assert stale.status_code == 409
    revoked = client.post(
        f"{prefix}/{pref['preference_id']}/revoke",
        json={
            "idempotency_key": "revoke",
            "expected_head": prefs["commit_id"],
            "reason": "不再适用",
        },
    )
    assert revoked.status_code == 200, revoked.text
    updated = client.get(prefix).json()["preferences"][0]
    assert updated["status"] == "revoked"
    assert updated["reason"] == "不再适用"
    assert revoked.json()["commit_id"] != first.json()["commit_id"]
