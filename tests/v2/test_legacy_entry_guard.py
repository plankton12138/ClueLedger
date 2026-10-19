"""V03-8.6 旧入口不可绕过证明。

三条不变量：

1. **被排除功能不能从旧 API 启动**：变更集逐章审批流、独立一致性审计、
   重写工作流、多 Agent 协调在 HTTP 层统一 410 ``FEATURE_RETIRED_IN_03``，
   带三要素且不留副作用（无新 Commit、无新 Job、无操作记录）。
2. **历史只读回看仍可用**：``GET /changes`` 与 ``GET /changes/{id}``。
3. **旧写入只能走统一管线**：旧单章导入、正式保存、历史恢复都返回
   ``operation_id``，且该操作记录可回读（正文变化可归因）。

底层服务保留（按用例复用），本文件只证明「入口不可绕过」。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app

RETIRED = "FEATURE_RETIRED_IN_03"

#: (方法, 相对路径, 请求体) —— 全部是被排除功能的启动面。
RETIRED_CALLS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("post", "/changes/cs_retired/analyze", {"idempotency_key": "analyze"}),
    ("post", "/changes/cs_retired/plan", {"idempotency_key": "plan"}),
    (
        "post",
        "/changes/cs_retired/approve",
        {"idempotency_key": "approve", "expected_head": "cmt_retired"},
    ),
    ("post", "/changes/cs_retired/execute", {"idempotency_key": "execute"}),
    ("post", "/changes/cs_retired/execute-agent", {"idempotency_key": "execute-agent"}),
    ("post", "/changes/cs_retired/review", {"idempotency_key": "review"}),
    (
        "post",
        "/changes/cs_retired/merge",
        {"idempotency_key": "merge", "expected_head": "cmt_retired"},
    ),
    ("post", "/audit", {"idempotency_key": "audit"}),
    (
        "post",
        "/rewrites",
        {"idempotency_key": "rewrite", "base_commit_id": "cmt_retired"},
    ),
    ("get", "/rewrites/run_retired/compare", None),
    ("post", "/rewrites/run_retired/execute", {"idempotency_key": "rewrite-execute"}),
    ("post", "/rewrites/run_retired/abort", {"idempotency_key": "rewrite-abort"}),
    ("post", "/coordinate", {"instruction": "把凶手改成协助者"}),
]


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(build_v2_app(V2Runtime.memory())) as http:
        yield http


def _project(client: TestClient) -> str:
    return client.post("/api/v2/projects", json={"name": "旧入口证明"}).json()["project_id"]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    RETIRED_CALLS,
    ids=[f"{m.upper()} {p}" for m, p, _ in RETIRED_CALLS],
)
def test_retired_surfaces_return_gone_with_three_part_error(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    """旧 UI/API 启动被排除功能：410 + 稳定错误码 + 三要素（哪里/为什么/怎样算对）。"""
    project = _project(client)
    response = getattr(client, method)(
        f"/api/v2/projects/{project}{path}",
        **({"json": body} if body is not None else {}),
    )
    assert response.status_code == 410, response.text
    error = response.json()["error"]
    assert error["code"] == RETIRED
    assert error["where"] and error["why"] and error["fix"]


def test_retired_surfaces_leave_no_state_behind(client: TestClient) -> None:
    """拒绝必须发生在副作用之前：版本头不变、不产生 Job、不产生操作记录。"""
    project = _project(client)
    prefix = f"/api/v2/projects/{project}"
    before_head = client.get(f"{prefix}/story").json()["commit_id"]
    before_jobs = len(client.get(f"{prefix}/agent-jobs").json())

    for method, path, body in RETIRED_CALLS:
        getattr(client, method)(f"{prefix}{path}", **({"json": body} if body else {}))

    after = client.get(f"{prefix}/story").json()
    assert after["commit_id"] == before_head
    assert len(client.get(f"{prefix}/agent-jobs").json()) == before_jobs


def test_change_history_stays_read_only_available(client: TestClient) -> None:
    """历史可回看：只读列表与详情保留（退役的是启动，不是历史）。"""
    project = _project(client)
    listing = client.get(f"/api/v2/projects/{project}/changes")
    assert listing.status_code == 200
    assert listing.json()["changes"] == []


def test_legacy_import_publishes_through_unified_pipeline(client: TestClient) -> None:
    """旧单章导入返回 operation_id，操作记录可回读（V03-8.6 前它绕过发布器）。"""
    project = _project(client)
    prefix = f"/api/v2/projects/{project}"
    imported = client.post(
        f"{prefix}/chapters/import",
        json={
            "idempotency_key": "legacy-import",
            "title": "第一章 旧入口",
            "text": "旧入口导入的正文，必须与预览导入同一发布管线。",
        },
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["operation_id"].startswith("op_")
    operation = client.get(f"{prefix}/operations/{body['operation_id']}")
    assert operation.status_code == 200
    assert body["chapter_id"] in operation.json()["changed_chapter_ids"]
    manuscript = client.get(f"{prefix}/manuscript").json()
    assert [c["chapter_id"] for c in manuscript["chapters"]] == [body["chapter_id"]]


def test_save_and_restore_publish_through_unified_pipeline(client: TestClient) -> None:
    """正式保存与历史恢复同样返回 operation_id，且操作记录可回读。"""
    project = _project(client)
    prefix = f"/api/v2/projects/{project}"
    imported = client.post(
        f"{prefix}/chapters/import",
        json={
            "idempotency_key": "import-for-save",
            "title": "第一章 归因",
            "text": "第一版正文。",
        },
    ).json()
    chapter_id = imported["chapter_id"]
    chapter = client.get(f"{prefix}/manuscript").json()["chapters"][0]

    saved = client.post(
        f"{prefix}/chapters/{chapter_id}/save",
        json={
            "idempotency_key": "save-v2",
            "full_text": "第二版正文，经过统一发布。",
            "expected_chapter_base": chapter["chapter_base"],
        },
    )
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()
    assert saved_body["operation_id"].startswith("op_")
    save_operation = client.get(f"{prefix}/operations/{saved_body['operation_id']}")
    assert save_operation.status_code == 200
    assert chapter_id in save_operation.json()["changed_chapter_ids"]

    restored = client.post(
        f"{prefix}/chapters/{chapter_id}/restore",
        json={
            "idempotency_key": "restore-v1",
            "source_commit_id": imported["commit_id"],
            "expected_chapter_base": saved_body["chapter_base"],
        },
    )
    assert restored.status_code == 200, restored.text
    restored_body = restored.json()
    assert restored_body["operation_id"].startswith("op_")
    restore_operation = client.get(f"{prefix}/operations/{restored_body['operation_id']}")
    assert restore_operation.status_code == 200
    assert chapter_id in restore_operation.json()["changed_chapter_ids"]
    # 恢复是一次新 Commit，正文回到第一版。
    text = client.get(f"{prefix}/chapters/{chapter_id}").json()["text"]
    assert "第一版正文" in text


def test_excluded_agent_role_cannot_start(client: TestClient) -> None:
    """作者模型自动链的角色（style_observer）不签发凭据：不能绕过 UI 启动。"""
    project = _project(client)
    prefix = f"/api/v2/projects/{project}"
    before = len(client.get(f"{prefix}/agent-jobs").json())
    refused = client.post(
        f"{prefix}/agent-jobs",
        json={
            "role": "style_observer",
            "instruction": "怀表谜案",
            "idempotency_key": "style-observer",
        },
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == "EXECUTION_NOT_AUTHORIZED"
    assert len(client.get(f"{prefix}/agent-jobs").json()) == before


def test_author_model_surfaces_are_gone(client: TestClient) -> None:
    """作者模型资料面已从 0.3 移除（V03-4 收口）：路由不存在，不是只隐藏入口。"""
    project = _project(client)
    paths = client.get("/openapi.json").json()["paths"]
    assert not [path for path in paths if "author-model" in path]
    response = client.get(f"/api/v2/projects/{project}/author-model/preferences")
    assert response.status_code == 404


def test_03_code_does_not_consume_corpus() -> None:
    """材料库/语料隔离（计划 §9.1）：除 V1 出口与 rag 模块本身，0.3 代码不引用语料。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "hastings"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        posix = path.as_posix()
        if "/rag/" in posix or path.name == "rest.py":
            continue
        text = path.read_text(encoding="utf-8")
        if any(
            marker in text
            for marker in ("CorpusStore", "corpus_store", "style_profile", "load_all(")
        ):
            offenders.append(posix.rsplit("hastings/", 1)[-1])
    assert offenders == [], f"0.3 代码不得消费语料/风格语料：{offenders}"


def test_legacy_import_appends_order_key_when_omitted(client: TestClient) -> None:
    """不传顺序键时接在现有章之后：两章不会都落到 0001 而按 ID 乱序。"""
    project = _project(client)
    prefix = f"/api/v2/projects/{project}"
    for index, title in enumerate(("第一章 起", "第二章 承"), 1):
        response = client.post(
            f"{prefix}/chapters/import",
            json={"idempotency_key": f"seq-{index}", "title": title, "text": f"第{index}段。"},
        )
        assert response.status_code == 200, response.text
    chapters = client.get(f"{prefix}/manuscript").json()["chapters"]
    assert [row["title"] for row in chapters] == ["第一章 起", "第二章 承"]

    # 显式顺序键仍然被尊重（旧客户端可以指定插入位置）
    explicit = client.post(
        f"{prefix}/chapters/import",
        json={
            "idempotency_key": "seq-prologue",
            "title": "序章",
            "text": "序。",
            "order_key": "0000",
        },
    )
    assert explicit.status_code == 200, explicit.text
    chapters = client.get(f"{prefix}/manuscript").json()["chapters"]
    assert [row["title"] for row in chapters][0] == "序章"
