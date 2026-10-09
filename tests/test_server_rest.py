"""REST 只读面测试（P3 T4，P4 T4 slug 化）：workspaces / state / bible / chapters。

泄压阀三情形逐条钉住：
- 参数违规（坏 slug、未知账本类型、坏章节 id）→ 422 + 怎样算对；
- 合法但空（空账本、无章节、无工作区）→ 200 空数组，不是错误；
- 撞名新建 → 409；数据故障（损坏 YAML）→ 422 + 路径指引。
零写入口：唯一 POST 是 /api/workspaces（资源创建，不碰业务数据）。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.bible.models import Character, Clue, TimelineEntry
from hastings.bible.store import StoryWorkspace, save_character, save_ledger
from hastings.server.app import create_app
from test_server_app import stub_registry

SLUG = "orient-express"
BIBLE_TYPES = ("character", "clue", "foreshadow", "method", "timeline")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def workspace(root: Path) -> StoryWorkspace:
    return StoryWorkspace(root=root, slug=SLUG)


def _client(root: Path) -> TestClient:
    return TestClient(create_app(stub_registry(root)))


# --- workspaces：列表 + 新建（唯一写 REST，资源创建） ---


def test_rest_workspaces_lists_existing_dirs(root: Path) -> None:
    (root / SLUG).mkdir()
    (root / "ghost-train").mkdir()
    (root / "notes.txt").write_text("不是目录", encoding="utf-8")

    body = _client(root).get("/api/workspaces").json()

    assert body == {"workspaces": ["ghost-train", SLUG]}


def test_rest_workspaces_empty_root_returns_empty_list(root: Path) -> None:
    assert _client(root).get("/api/workspaces").json() == {"workspaces": []}


def test_rest_create_workspace_makes_dirs(root: Path) -> None:
    response = _client(root).post("/api/workspaces", json={"slug": "fresh-story"})

    assert response.status_code == 201
    assert response.json() == {"slug": "fresh-story"}
    assert (root / "fresh-story" / "bible" / "characters").is_dir()
    assert (root / "fresh-story" / "chapters").is_dir()


def test_rest_create_workspace_duplicate_returns_409(root: Path) -> None:
    (root / SLUG).mkdir()

    response = _client(root).post("/api/workspaces", json={"slug": SLUG})

    assert response.status_code == 409
    assert "已存在" in response.json()["detail"]


@pytest.mark.parametrize("bad_slug", ["../escape", "CON", "a/b"])
def test_rest_create_workspace_invalid_slug_returns_422(root: Path, bad_slug: str) -> None:
    response = _client(root).post("/api/workspaces", json={"slug": bad_slug})

    assert response.status_code == 422
    assert "slug 不合法" in response.json()["detail"]
    assert not (root / "escape").exists()


# --- state：按 slug 解析 ---


def test_rest_state_reports_session_and_bible_types(root: Path) -> None:
    body = _client(root).get(f"/api/{SLUG}/state").json()
    assert body == {
        "slug": SLUG,
        "model": "fake",
        "is_running": False,
        "tools": ["bible_upsert", "write_chapter"],
        "bible_types": ["character", "clue", "foreshadow", "method", "timeline"],
    }


def test_rest_invalid_slug_in_path_returns_422(root: Path) -> None:
    # 裸 ../ 与 %2E%2E 都被 httpx 客户端归一化（归一化后 404，穿越到不了
    # 应用层——这也是一层防护）；应用侧闸门用 Windows 保留名钉住，
    # 穿越拒绝本身在 test_server_registry 钉住
    response = _client(root).get("/api/CON/state")
    assert response.status_code == 422
    assert "slug 不合法" in response.json()["detail"]
    assert not (root / "escape").exists()


# --- bible：结构化对象数组（前端表格要字段，不是摘要行文本） ---


def test_rest_bible_returns_structured_entities(workspace: StoryWorkspace) -> None:
    save_character(workspace, Character(name="波洛", identity="比利时侦探"))
    save_ledger(workspace.clues_path, [Clue(name="停摆的怀表", content="停在 21:47")])
    save_ledger(workspace.timeline_path, [TimelineEntry(name="死亡时刻", day=1)])

    client = _client(workspace.root)

    characters = client.get(f"/api/{SLUG}/bible/character").json()
    assert characters["entity_type"] == "character"
    assert characters["count"] == 1
    assert characters["entities"][0]["name"] == "波洛"
    assert characters["entities"][0]["identity"] == "比利时侦探"
    assert characters["entities"][0]["arc"] == "flat"

    clues = client.get(f"/api/{SLUG}/bible/clue").json()
    assert clues["entities"][0] == {
        "name": "停摆的怀表",
        "content": "停在 21:47",
        "chapter": "",
        "points_to": "",
        "is_red_herring": False,
        "is_genuine": False,
        "status": "planned",
    }

    timeline = client.get(f"/api/{SLUG}/bible/timeline").json()
    assert timeline["entities"][0]["name"] == "死亡时刻"
    assert timeline["entities"][0]["day"] == 1


def test_rest_bible_empty_ledgers_return_empty_arrays(workspace: StoryWorkspace) -> None:
    """空账本是合法空结果（泄压阀）：200 + 空数组，绝不报错。"""
    client = _client(workspace.root)
    for entity_type in BIBLE_TYPES:
        body = client.get(f"/api/{SLUG}/bible/{entity_type}").json()
        assert body == {"entity_type": entity_type, "count": 0, "entities": []}


def test_rest_bible_unknown_type_is_validation_error(workspace: StoryWorkspace) -> None:
    response = _client(workspace.root).get(f"/api/{SLUG}/bible/novel")
    assert response.status_code == 422


def test_rest_bible_corrupt_yaml_returns_422_with_path_guidance(
    workspace: StoryWorkspace,
) -> None:
    workspace.ensure_dirs()
    workspace.clues_path.write_text(": [broken", encoding="utf-8")

    response = _client(workspace.root).get(f"/api/{SLUG}/bible/clue")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "clues.yaml" in detail  # 路径指引：坏的是哪个文件
    assert "怎样算对" in detail


def test_rest_bible_non_list_root_returns_422(workspace: StoryWorkspace) -> None:
    workspace.ensure_dirs()
    workspace.foreshadows_path.write_text("只是一段文字", encoding="utf-8")

    response = _client(workspace.root).get(f"/api/{SLUG}/bible/foreshadow")

    assert response.status_code == 422
    assert "foreshadows.yaml" in response.json()["detail"]


def test_rest_bible_invalid_entity_fields_return_422(
    workspace: StoryWorkspace,
) -> None:
    workspace.ensure_dirs()
    (workspace.characters_dir / "坏卡.yaml").write_text("123\n", encoding="utf-8")

    response = _client(workspace.root).get(f"/api/{SLUG}/bible/character")

    assert response.status_code == 422
    assert "characters" in response.json()["detail"]


# --- chapters：列表 + 原文（阅读器数据源） ---


def test_rest_chapters_empty_returns_empty_list(workspace: StoryWorkspace) -> None:
    assert _client(workspace.root).get(f"/api/{SLUG}/chapters").json() == {"chapters": []}


def test_rest_chapters_list_and_detail(workspace: StoryWorkspace) -> None:
    workspace.ensure_dirs()
    body = "# 第1章 雪封之夜\n\n雪从午夜开始落。"
    (workspace.chapters_dir / "ch01.md").write_text(body, encoding="utf-8")

    client = _client(workspace.root)

    assert client.get(f"/api/{SLUG}/chapters").json() == {"chapters": ["ch01"]}
    assert client.get(f"/api/{SLUG}/chapters/ch01").json() == {
        "id": "ch01",
        "content": body,
    }


def test_rest_chapter_not_found_returns_404_with_guidance(
    workspace: StoryWorkspace,
) -> None:
    workspace.ensure_dirs()
    (workspace.chapters_dir / "ch01.md").write_text("# 第1章\n", encoding="utf-8")

    response = _client(workspace.root).get(f"/api/{SLUG}/chapters/ch99")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "ch99" in detail
    assert "/api/orient-express/chapters" in detail  # 怎样算对：先列出可用章节


def test_rest_chapter_bad_id_returns_422(workspace: StoryWorkspace) -> None:
    response = _client(workspace.root).get(f"/api/{SLUG}/chapters/坏 名")

    assert response.status_code == 422
    assert "章节 id" in response.json()["detail"]
