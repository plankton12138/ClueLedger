"""生产启动闭环冒烟（V03-8.3）：不传特殊开关，走真实装配链跑完整用户回路。

验收口径（计划 §8 / 执行指导 §12.2）：**不传特殊测试开关**也能创建作品、
生成/采用、跨章修改与撤销。本测试用 ``assemble_server``（``python -m
hastings.server`` 的同一段装配：同样的 config 解析、ProviderBook、registry、
SQLite 工作台）起应用，再按真实 HTTP 路径走完：

创建作品 → 导入正文 → 建账本条目 → 开修改副本 → 跨章自动保存 → 完成发布 →
撤销恢复。

模型相关步骤（建议生成/试写）不在此列：无凭据时它们按设计 fail fast，
属 V03-8.7 真模型实测。这里断言的是**不依赖模型的整条产品回路**能在生产
装配下跑通——「生产能起」不是假设，是被测出来的。
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from hastings.server.__main__ import assemble_server

MD = """# 雪山旅馆

## 第一章 雪夜

侦探在雪夜抵达旅馆，风雪封住了山路。

## 第二章 空房

走廊尽头有一扇门，门后没有声音。
"""

pytestmark = pytest.mark.usefixtures("isolated_production_env")


@pytest.fixture()
def isolated_production_env(tmp_path, monkeypatch):
    """隔离环境变量与工作目录：不使用真实 .env / config.yaml / data 目录。"""
    monkeypatch.delenv("HASTINGS_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _production_client(tmp_path) -> TestClient:
    """按生产默认值装配（load_env=False：不读真实 .env，其余链路完全一致）。"""
    assembled = assemble_server(
        stories_root=tmp_path / "stories",
        corpus_root=tmp_path / "corpus",
        v2_db=tmp_path / "workbench" / "v2.db",
        static_dir=None,
        load_env=False,
    )
    # 未配置模式也应照起：服务可建、可写，模型调用在 prompt 时才 fail fast
    assert assembled.unconfigured is True
    return TestClient(assembled.app)


def test_production_loop_without_test_switches(tmp_path) -> None:
    client = _production_client(tmp_path)

    # 1) 能力公布：空启动下仍是可用的工作台（未配置模式有明确提示）
    capabilities = client.get("/api/v2/capabilities").json()
    assert capabilities["features"], "能力公布不应为空"

    # 2) 创建作品
    project = client.post("/api/v2/projects", json={"name": "冒烟作品"}).json()
    pid = project["project_id"]
    assert pid.startswith("prj_")

    # 3) 导入正文（预览 → 按哈希确认）
    preview = client.post(
        f"/api/v2/projects/{pid}/imports/previews",
        json={
            "filename": "雪山旅馆.md",
            "content_base64": base64.b64encode(MD.encode("utf-8")).decode("ascii"),
        },
    ).json()
    confirmed = client.post(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}/confirm",
        json={"preview_hash": preview["preview_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    chapters = client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]
    assert [row["title"] for row in chapters] == ["第一章 雪夜", "第二章 空房"]

    # 4) 账本直接保存（无模型路径）
    character = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={"kind": "story.character", "fields": {"name": "姐姐"}, "request_id": "smoke-1"},
    )
    assert character.status_code == 201, character.text
    clue = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={
            "kind": "story.clue",
            "fields": {"statement": "床头有两只药瓶", "points_to": []},
            "request_id": "smoke-2",
        },
    )
    assert clue.status_code == 201, clue.text

    # 5) 跨章修改：开副本 → 逐章自动保存 → 完成发布
    session = client.post(
        f"/api/v2/projects/{pid}/edit-sessions",
        json={"chapter_ids": [row["chapter_id"] for row in chapters]},
    ).json()
    session_id = session["session"]["session_id"]
    saved: dict[str, int] = {}
    for index, row in enumerate(chapters):
        response = client.put(
            f"/api/v2/projects/{pid}/edit-sessions/{session_id}/chapters/{row['chapter_id']}",
            json={
                "expected_save_seq": saved.get(row["chapter_id"], 0),
                "request_id": f"smoke-save-{index}",
                "text": f"改写后的第 {index + 1} 章正文。",
            },
        )
        assert response.status_code == 200, response.text
        saved[row["chapter_id"]] = response.json()["save_seq"]

    finished = client.post(
        f"/api/v2/projects/{pid}/edit-sessions/{session_id}/finish",
        json={"operation_id": "op_smoke_edit", "expected_save_sequences": saved},
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "committed"
    first = client.get(f"/api/v2/projects/{pid}/chapters/{chapters[0]['chapter_id']}").json()
    assert first["text"] == "改写后的第 1 章正文。"

    # 6) 撤销那次修改：正文回到导入版本（原历史保留为新提交）
    undone = client.post(
        f"/api/v2/projects/{pid}/operations/op_smoke_edit/undo",
        json={},
    )
    assert undone.status_code == 200, undone.text
    restored = client.get(f"/api/v2/projects/{pid}/chapters/{chapters[0]['chapter_id']}").json()
    assert "侦探在雪夜抵达旅馆" in restored["text"]

    # 6b) 设定候选：暂存 → 采用（无模型路径；带因果链接指回刚才那次修改）
    staged = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/items",
        json={"kind": "story.place", "fields": {"name": "阁楼"}, "request_id": "smoke-cand"},
    ).json()
    accepted = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [staged["items"][0]["item_id"]],
            "expected_revision": staged["revision"],
            "request_id": "smoke-cand-accept",
            "source_operation_id": "op_smoke_edit",
        },
    )
    assert accepted.status_code == 200, accepted.text
    ledger_titles = {
        entry["title"] for entry in client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]
    }
    assert "阁楼" in ledger_titles
    impact = client.get(f"/api/v2/projects/{pid}/operations/op_smoke_edit/impact").json()
    assert [item["request_id"] for item in impact["related_acceptances"]] == ["smoke-cand-accept"]

    # 7) 备份与恢复闭环：预览 → 导出 → 恢复（差异未确认→确认）
    backup = client.get(f"/api/v2/projects/{pid}/backup").json()
    assert backup["counts"]["chapters"] == 2
    archive = client.get(f"/api/v2/projects/{pid}/export").content
    second = client.get(f"/api/v2/projects/{pid}/chapters/{chapters[1]['chapter_id']}").json()
    changed = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapters[1]['chapter_id']}/save",
        json={
            "idempotency_key": "smoke-after-backup",
            "full_text": "备份之后又改了一版。",
            "expected_chapter_base": second["chapter_base"],
        },
    )
    assert changed.status_code == 200, changed.text
    blocked = client.post(
        f"/api/v2/projects/{pid}/restore",
        json={"content_base64": base64.b64encode(archive).decode("ascii")},
    )
    assert blocked.status_code == 409, blocked.text
    restored_pkg = client.post(
        f"/api/v2/projects/{pid}/restore",
        json={
            "content_base64": base64.b64encode(archive).decode("ascii"),
            "confirm_overwrite": True,
        },
    )
    assert restored_pkg.status_code == 200, restored_pkg.text
    after_restore = client.get(
        f"/api/v2/projects/{pid}/chapters/{chapters[1]['chapter_id']}"
    ).json()
    assert after_restore["text"] != "备份之后又改了一版。"

    # 8) 恢复后工作台仍可写：再开一轮副本并发布
    again = client.post(
        f"/api/v2/projects/{pid}/edit-sessions",
        json={"chapter_ids": [chapters[0]["chapter_id"]]},
    ).json()
    draft = client.put(
        f"/api/v2/projects/{pid}/edit-sessions/{again['session']['session_id']}"
        f"/chapters/{chapters[0]['chapter_id']}",
        json={
            "expected_save_seq": 0,
            "request_id": "smoke-save-again",
            "text": "恢复之后的又一轮修改。",
        },
    )
    assert draft.status_code == 200, draft.text
    seq = draft.json()["save_seq"]
    finish_again = client.post(
        f"/api/v2/projects/{pid}/edit-sessions/{again['session']['session_id']}/finish",
        json={
            "operation_id": "op_smoke_second",
            "expected_save_sequences": {chapters[0]["chapter_id"]: seq},
        },
    )
    assert finish_again.status_code == 200, finish_again.text
    assert (
        client.get(f"/api/v2/projects/{pid}/chapters/{chapters[0]['chapter_id']}").json()["text"]
        == "恢复之后的又一轮修改。"
    )


def test_production_assembly_writes_to_configured_db(tmp_path) -> None:
    """装配把工作台库建在指定路径：生产默认值不会被测试目录意外改写。"""
    assembled = assemble_server(
        stories_root=tmp_path / "stories",
        corpus_root=tmp_path / "corpus",
        v2_db=tmp_path / "workbench" / "v2.db",
        static_dir=None,
        load_env=False,
    )
    assert assembled.v2_db == tmp_path / "workbench" / "v2.db"
    assert assembled.v2_db.exists(), "装配应真正创建 SQLite 工作台库"
    # 静态目录按生产规则探测：仓库内有 frontend/dist 就托管，否则纯 API 模式
    assert assembled.static_dir is None or assembled.static_dir.is_dir()
    client = TestClient(assembled.app)
    assert client.get("/api/v2/capabilities").status_code == 200
