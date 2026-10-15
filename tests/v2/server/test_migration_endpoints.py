"""迁移 REST 面（T17.5）测试：状态 / dry-run / 确认落盘 / 幂等与失败语义。

覆盖：未迁移状态、dry-run 不写盘且带逐文件归类、apply 落成 V2 项目并写映射
（architecture_version 翻转 + v2_project_id）、重复迁移拒绝、确认 run_id 不符拒绝、
报告未通过拒绝且不写映射、未知 slug 404。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.bible.store import StoryWorkspace
from hastings.server.app import create_app
from hastings.server.registry import WorkspaceRegistry
from hastings.server.v2 import V2Runtime
from hastings.settings import load_settings, v2_project_id, workspace_architecture_version

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "v1_workspace" / "winter-manor-case"
SLUG = "winter-manor-case"


@pytest.fixture
def env(tmp_path: Path) -> tuple[TestClient, dict[str, Any], StoryWorkspace]:
    """真实装配：registry（stub 会话工厂）+ 内存 V2 运行时 + 迁移/REST 面。"""
    root = tmp_path / "stories"
    root.mkdir()
    shutil.copytree(FIXTURE, root / SLUG)
    workspace = StoryWorkspace(root=root, slug=SLUG)

    runtime = V2Runtime.memory()

    def factory(_workspace: StoryWorkspace) -> Any:
        # 迁移端点不触碰会话（只读扫描 + 仓库写入）；桩工厂永不装配
        return object()

    registry = WorkspaceRegistry(root=root, session_factory=factory)
    app = create_app(registry, v2_runtime=runtime)
    client = TestClient(app, raise_server_exceptions=False)
    return client, {"runtime": runtime, "registry": registry}, workspace


def _resolve_blockers(workspace: StoryWorkspace) -> None:
    """作者解决阻断项：排除缺依据的干扰项与缺真凶的手法（同迁移夹具口径）。"""
    from hastings.bible.models import Clue
    from hastings.bible.store import load_ledger, save_ledger

    clues = load_ledger(workspace.clues_path, Clue)
    save_ledger(workspace.clues_path, [c for c in clues if not c.is_red_herring])
    save_ledger(workspace.method_path, [])


def test_status_reports_unmigrated(env) -> None:
    client, _ctx, _workspace = env
    response = client.get(f"/api/{SLUG}/v2-migration/status")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "slug": SLUG,
        "architecture_version": "v1",
        "v2_project_id": None,
        "migrated": False,
    }


def test_status_unknown_workspace_is_404(env) -> None:
    client, _ctx, _workspace = env
    assert client.get("/api/no-such-ws/v2-migration/status").status_code == 404


def test_dry_run_classifies_without_writing(env) -> None:
    client, _ctx, workspace = env
    response = client.post(f"/api/{SLUG}/v2-migration/dry-run")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False, "基准 fixture 含阻断实体，报告应不通过"
    paths = {item["path"] for item in body["items"]}
    assert "bible/clues.yaml" in paths
    # dry-run 不写盘：工作区仍无会话索引变化、settings 仍无迁移映射
    assert v2_project_id(workspace) is None
    assert workspace_architecture_version(workspace) == "v1"


def test_apply_rejects_unconfirmed_run_id(env) -> None:
    client, _ctx, _workspace = env
    response = client.post(f"/api/{SLUG}/v2-migration/apply", json={"confirm_run_id": "0" * 64})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "REPO_INVALID_CHANGE"
    assert "重新运行 dry-run" in error["fix"]


def test_apply_rejects_failing_report_without_mapping(env) -> None:
    client, _ctx, workspace = env
    report = client.post(f"/api/{SLUG}/v2-migration/dry-run").json()
    response = client.post(
        f"/api/{SLUG}/v2-migration/apply", json={"confirm_run_id": report["run_id"]}
    )
    assert response.status_code == 400
    # 迁移失败不切换 active：工作区保持 V1 可用、无映射
    assert v2_project_id(workspace) is None
    assert workspace_architecture_version(workspace) == "v1"


def test_apply_commits_and_records_mapping(env) -> None:
    client, ctx, workspace = env
    _resolve_blockers(workspace)

    report = client.post(f"/api/{SLUG}/v2-migration/dry-run").json()
    assert report["ok"] is True

    response = client.post(
        f"/api/{SLUG}/v2-migration/apply", json={"confirm_run_id": report["run_id"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "committed"
    assert body["validation_ok"] is True
    project_id = body["project_id"]

    # 映射落盘：架构版本翻转（旧写路径门即刻生效）+ 项目 ID 记录
    assert v2_project_id(workspace) == project_id
    assert workspace_architecture_version(workspace) == "v2"
    assert load_settings(workspace).writing_language, "既有设置字段不得被迁移映射抹掉"

    # V2 项目可读：head 上有 3 章正文
    runtime: V2Runtime = ctx["runtime"]
    from hastings.domain.repository.ids import ProjectId

    head = runtime.head(ProjectId(project_id))
    assert head is not None
    catalog = runtime.queries.catalog(ProjectId(project_id), head)
    assert len(catalog.chapters()) == 3


def test_apply_is_replay_safe_but_repeat_migration_refused(env) -> None:
    client, ctx, workspace = env
    _resolve_blockers(workspace)
    report = client.post(f"/api/{SLUG}/v2-migration/dry-run").json()
    first = client.post(
        f"/api/{SLUG}/v2-migration/apply", json={"confirm_run_id": report["run_id"]}
    ).json()

    # 同 confirm_run_id 重试：幂等表命中（返回原 commit），映射不变
    retry = client.post(
        f"/api/{SLUG}/v2-migration/apply", json={"confirm_run_id": report["run_id"]}
    )
    assert retry.status_code == 400, "已迁移后端点直接拒绝，不再走幂等通道"
    assert retry.json()["error"]["code"] == "MIGRATION_ALREADY_DONE"
    assert v2_project_id(workspace) == first["project_id"]

    # 旧写路径门在映射落盘后即刻生效：V2 项目拒绝旧 REST 章节保存
    chapter = next(
        item
        for item in first["report"]["items"]
        if item["item_type"] == "chapter" and item["outcome"] == "mapped"
    )
    blocked = client.put(
        f"/api/{SLUG}/chapters/{Path(chapter['path']).stem}",
        json={"content": "绕过 V2 的直接写入", "baseRevision": 1},
    )
    assert blocked.status_code == 409, "旧写路径必须被拒（迁移后禁止再走 V1 写）"
