"""操作查询 REST 契约（V03-2.1）：GET /operations/{id} 的可找回性。

提交侧经服务直接驱动（统一发布路径由 V03-2.2 接管并复用本服务）；
本文件钉死查询端点的信封形状与 404 信封。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.domain.repository.ids import ArtifactId, ProjectId
from hastings.domain.repository.requests import ArtifactChange
from hastings.server.v2 import V2Runtime, build_v2_app


def _client() -> tuple[TestClient, V2Runtime, str]:
    runtime = V2Runtime.memory()
    project = runtime.create_project("操作走查")
    return TestClient(build_v2_app(runtime)), runtime, project.value


def test_get_operation_returns_envelope() -> None:
    client, runtime, project_id = _client()
    envelope = runtime.operations.submit(
        project_id=project_id,
        operation_id="op_api_1",
        kind="chapter.save",
        changes=(
            ArtifactChange(
                artifact_id=ArtifactId("ch_1"),
                content={"title": "第一章", "text": "正文"},
                kind="chapter",
            ),
        ),
        summary="API 走查",
    )
    response = client.get(f"/api/v2/projects/{project_id}/operations/op_api_1")
    assert response.status_code == 200
    body = response.json()
    assert body["operation_id"] == "op_api_1"
    assert body["status"] == "committed"
    assert body["commit_id"] == envelope.commit_id
    assert body["conversation_id"].startswith("conv_")
    assert body["changed_chapter_ids"] == ["ch_1"]


def test_get_operation_unknown_is_404_envelope() -> None:
    client, _runtime, project_id = _client()
    response = client.get(f"/api/v2/projects/{project_id}/operations/op_missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "OPERATION_NOT_FOUND"


def test_get_operation_isolated_per_project() -> None:
    client, runtime, project_id = _client()
    other = runtime.create_project("另一个作品")
    runtime.operations.submit(
        project_id=project_id,
        operation_id="op_iso",
        kind="chapter.save",
        changes=(
            ArtifactChange(
                artifact_id=ArtifactId("ch_1"),
                content={"title": "第一章", "text": "正文"},
                kind="chapter",
            ),
        ),
        summary="隔离走查",
    )
    # 操作归属作品：其他作品查询同一 operation_id 是 404（辅助表按项目隔离）
    response = client.get(f"/api/v2/projects/{other.value}/operations/op_iso")
    assert response.status_code == 404
    _ = ProjectId
