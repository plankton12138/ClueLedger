"""REST 只读面测试（P3 T4，计划 D6）：state / bible / chapters 三组端点。

泄压阀三情形逐条钉住：
- 参数违规（未知账本类型、坏章节 id）→ 422 + 怎样算对；
- 合法但空（空账本、无章节）→ 200 空数组/空列表，不是错误；
- 环境与数据故障（损坏 YAML / 结构损坏 / 字段不合法）→ 422 + 路径指引。
零写入口：本文件只发 GET。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.bible.models import Character, Clue, TimelineEntry
from hastings.bible.store import StoryWorkspace, save_character, save_ledger
from hastings.server.app import create_app
from test_server_app import StubSession

BIBLE_TYPES = ("character", "clue", "foreshadow", "method", "timeline")


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path, slug="orient-express")


def _client(workspace: StoryWorkspace) -> TestClient:
    return TestClient(create_app(StubSession(), workspace))


# --- state ---


def test_rest_state_reports_session_and_bible_types(workspace: StoryWorkspace) -> None:
    body = _client(workspace).get("/api/state").json()
    assert body == {
        "slug": "orient-express",
        "model": "fake",
        "is_running": False,
        "tools": ["bible_upsert", "write_chapter"],
        "bible_types": ["character", "clue", "foreshadow", "method", "timeline"],
    }


# --- bible：结构化对象数组（前端表格要字段，不是摘要行文本） ---


def test_rest_bible_returns_structured_entities(workspace: StoryWorkspace) -> None:
    save_character(workspace, Character(name="波洛", identity="比利时侦探"))
    save_ledger(workspace.clues_path, [Clue(name="停摆的怀表", content="停在 21:47")])
    save_ledger(workspace.timeline_path, [TimelineEntry(name="死亡时刻", day=1)])

    client = _client(workspace)

    characters = client.get("/api/bible/character").json()
    assert characters["entity_type"] == "character"
    assert characters["count"] == 1
    assert characters["entities"][0]["name"] == "波洛"
    assert characters["entities"][0]["identity"] == "比利时侦探"
    assert characters["entities"][0]["arc"] == "flat"

    clues = client.get("/api/bible/clue").json()
    assert clues["entities"][0] == {
        "name": "停摆的怀表",
        "content": "停在 21:47",
        "chapter": "",
        "points_to": "",
        "is_red_herring": False,
        "is_genuine": False,
        "status": "planned",
    }

    timeline = client.get("/api/bible/timeline").json()
    assert timeline["entities"][0]["name"] == "死亡时刻"
    assert timeline["entities"][0]["day"] == 1


def test_rest_bible_empty_ledgers_return_empty_arrays(workspace: StoryWorkspace) -> None:
    """空账本是合法空结果（泄压阀）：200 + 空数组，绝不报错。"""
    client = _client(workspace)
    for entity_type in BIBLE_TYPES:
        body = client.get(f"/api/bible/{entity_type}").json()
        assert body == {"entity_type": entity_type, "count": 0, "entities": []}


def test_rest_bible_unknown_type_is_validation_error(workspace: StoryWorkspace) -> None:
    response = _client(workspace).get("/api/bible/novel")
    assert response.status_code == 422


def test_rest_bible_corrupt_yaml_returns_422_with_path_guidance(
    workspace: StoryWorkspace,
) -> None:
    workspace.ensure_dirs()
    workspace.clues_path.write_text(": [broken", encoding="utf-8")

    response = _client(workspace).get("/api/bible/clue")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "clues.yaml" in detail  # 路径指引：坏的是哪个文件
    assert "怎样算对" in detail


def test_rest_bible_non_list_root_returns_422(workspace: StoryWorkspace) -> None:
    workspace.ensure_dirs()
    workspace.foreshadows_path.write_text("只是一段文字", encoding="utf-8")

    response = _client(workspace).get("/api/bible/foreshadow")

    assert response.status_code == 422
    assert "foreshadows.yaml" in response.json()["detail"]


def test_rest_bible_invalid_entity_fields_return_422(
    workspace: StoryWorkspace,
) -> None:
    workspace.ensure_dirs()
    (workspace.characters_dir / "坏卡.yaml").write_text("123\n", encoding="utf-8")

    response = _client(workspace).get("/api/bible/character")

    assert response.status_code == 422
    assert "characters" in response.json()["detail"]


# --- chapters：列表 + 原文（curl 可查，P6 阅读器数据源） ---


def test_rest_chapters_empty_returns_empty_list(workspace: StoryWorkspace) -> None:
    assert _client(workspace).get("/api/chapters").json() == {"chapters": []}


def test_rest_chapters_list_and_detail(workspace: StoryWorkspace) -> None:
    workspace.ensure_dirs()
    body = "# 第1章 雪封之夜\n\n雪从午夜开始落。"
    (workspace.chapters_dir / "ch01.md").write_text(body, encoding="utf-8")

    client = _client(workspace)

    assert client.get("/api/chapters").json() == {"chapters": ["ch01"]}
    assert client.get("/api/chapters/ch01").json() == {"id": "ch01", "content": body}


def test_rest_chapter_not_found_returns_404_with_guidance(
    workspace: StoryWorkspace,
) -> None:
    workspace.ensure_dirs()
    (workspace.chapters_dir / "ch01.md").write_text("# 第1章\n", encoding="utf-8")

    response = _client(workspace).get("/api/chapters/ch99")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "ch99" in detail
    assert "/api/chapters" in detail  # 怎样算对：先列出可用章节


def test_rest_chapter_bad_id_returns_422(workspace: StoryWorkspace) -> None:
    response = _client(workspace).get("/api/chapters/坏 名")

    assert response.status_code == 422
    assert "章节 id" in response.json()["detail"]
