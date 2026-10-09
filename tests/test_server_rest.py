"""REST 面测试（P3 T4，P4 T4 slug 化，P5 T2 受管写端点）。

泄压阀三情形逐条钉住：
- 参数违规（坏 slug、未知账本类型、坏章节 id、坏标题）→ 422 + 怎样算对；
- 合法但空（空账本、无章节、无工作区、空 diff no-op）→ 200，不是错误；
- 撞名新建 / 过期提交 / 写作流冲突 → 409；数据故障（损坏 YAML）→ 422；
  环境故障（IO）→ 500 带归因。
红线（D3 演进）：写路径必须经守门管道——业务写入口仅有
PUT chapters/{id}（归档 + 锁 + 乐观并发）；POST /api/workspaces 只是资源创建。
"""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.bible.models import Character, Clue, TimelineEntry
from hastings.bible.store import StoryWorkspace, save_character, save_ledger
from hastings.manuscript.diff import DiffHunk
from hastings.server.app import create_app
from hastings.server.provider_config import ProviderProfile, ServerConfig
from hastings.settings import StorySettings, load_settings, save_settings
from test_server_app import StubSession, stub_registry

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


# --- providers：档案目录（P6 D1，key 只进不出） ---


def test_rest_providers_lists_profiles_with_masked_keys(root: Path) -> None:
    server_config = ServerConfig(
        providers=[
            ProviderProfile(
                name="deepseek",
                base_url="https://api.deepseek.com/v1",
                api_key="sk-direct-12345678",
                default_model="deepseek-chat",
            ),
            ProviderProfile(name="nokey", base_url="https://api.example.com/v1"),
        ]
    )
    client = TestClient(create_app(stub_registry(root), server_config=server_config))

    body = client.get("/api/providers").json()

    assert body == {
        "providers": [
            {
                "name": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "default_model": "deepseek-chat",
                "key_hint": "…5678",
                "available": True,
            },
            {
                "name": "nokey",
                "base_url": "https://api.example.com/v1",
                "default_model": "",
                "key_hint": "",
                "available": False,
            },
        ]
    }
    # key 只进不出：完整 key 不得出现在任何响应里
    assert "sk-direct-12345678" not in client.get("/api/providers").text


def test_rest_providers_without_config_falls_back_to_env(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未注入 server_config → env 回落单档案（与启动装配同源）。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key-123456")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("HASTINGS_MODEL", "env-model")
    client = TestClient(create_app(stub_registry(root)))

    body = client.get("/api/providers").json()

    assert [p["name"] for p in body["providers"]] == ["default"]
    assert body["providers"][0]["available"] is True
    assert body["providers"][0]["key_hint"] == "…3456"  # 尾 4 位


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
        "revision": 1,
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


# --- PUT chapters/{id}：受管写端点（P5 T2，D3/D4） ---


class _RunningSession(StubSession):
    """is_running 恒真的会话：写作流冲突场景。"""

    def __init__(self) -> None:
        super().__init__()
        self._running = True


def _seed_chapter(workspace: StoryWorkspace, chapter_id: str = "ch03") -> None:
    workspace.ensure_dirs()
    (workspace.chapters_dir / f"{chapter_id}.md").write_text(
        "# 第3章\n\n火车在九点四十七分抵达。\n", encoding="utf-8"
    )


def test_put_chapter_saves_archives_and_returns_revision_diff(
    workspace: StoryWorkspace,
) -> None:
    _seed_chapter(workspace)
    new_content = "# 第3章\n\n火车在十点零七分抵达。\n"

    response = _client(workspace.root).put(
        f"/api/{SLUG}/chapters/ch03",
        json={"content": new_content, "baseRevision": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "ch03"
    assert body["revision"] == 2
    assert body["diff"] == [
        {
            "kind": "modified",
            "oldIndex": 1,
            "newIndex": 1,
            "oldText": "火车在九点四十七分抵达。",
            "newText": "火车在十点零七分抵达。",
        },
    ]
    # 主文件与归档均落盘
    assert (workspace.chapters_dir / "ch03.md").read_text(encoding="utf-8") == new_content
    archive_dir = workspace.chapters_dir / ".revisions" / "ch03"
    assert (archive_dir / "001.md").exists()
    metadata = json.loads((archive_dir / "001.json").read_text(encoding="utf-8"))
    assert metadata["hunks"][0]["old_index"] == 1


def test_put_chapter_identical_content_is_noop(workspace: StoryWorkspace) -> None:
    _seed_chapter(workspace)
    original = (workspace.chapters_dir / "ch03.md").read_text(encoding="utf-8")

    response = _client(workspace.root).put(
        f"/api/{SLUG}/chapters/ch03",
        json={"content": original, "baseRevision": 1},
    )

    assert response.status_code == 200
    assert response.json() == {"id": "ch03", "revision": 1, "diff": []}
    assert not (workspace.chapters_dir / ".revisions").exists()


def test_put_chapter_bad_id_returns_422(workspace: StoryWorkspace) -> None:
    response = _client(workspace.root).put(
        f"/api/{SLUG}/chapters/坏 名",
        json={"content": "# 第1章\n\n正文。", "baseRevision": 1},
    )

    assert response.status_code == 422
    assert "章节 id" in response.json()["detail"]


def test_put_chapter_bad_title_returns_422_with_three_elements(
    workspace: StoryWorkspace,
) -> None:
    _seed_chapter(workspace)

    response = _client(workspace.root).put(
        f"/api/{SLUG}/chapters/ch03",
        json={"content": "没有标题行的正文。", "baseRevision": 1},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "首行" in detail
    assert "为什么错" in detail
    assert "怎样算对" in detail


def test_put_chapter_missing_returns_404_with_guidance(workspace: StoryWorkspace) -> None:
    workspace.ensure_dirs()

    response = _client(workspace.root).put(
        f"/api/{SLUG}/chapters/ch09",
        json={"content": "# 第9章\n\n正文。", "baseRevision": 1},
    )

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "ch09" in detail
    assert f"/api/{SLUG}/chapters" in detail


def test_put_chapter_stale_revision_returns_409_with_guidance(
    workspace: StoryWorkspace,
) -> None:
    _seed_chapter(workspace)

    response = _client(workspace.root).put(
        f"/api/{SLUG}/chapters/ch03",
        json={"content": "# 第3章\n\n过期提交。\n", "baseRevision": 7},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "revision" in detail
    assert "为什么错" in detail
    assert "怎样算对" in detail
    # 冲突提交未触盘
    assert "十点零七分" not in (workspace.chapters_dir / "ch03.md").read_text(encoding="utf-8")


def test_put_chapter_while_agent_running_returns_409(
    workspace: StoryWorkspace,
) -> None:
    _seed_chapter(workspace)
    client = TestClient(create_app(stub_registry(workspace.root, session=_RunningSession())))

    response = client.put(
        f"/api/{SLUG}/chapters/ch03",
        json={"content": "# 第3章\n\n抢写。\n", "baseRevision": 1},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "写作流" in detail
    assert "为什么错" in detail
    assert "怎样算对" in detail
    # 锁未建、文件未动（运行态检查先于归档）
    assert "抢写" not in (workspace.chapters_dir / "ch03.md").read_text(encoding="utf-8")


def test_put_chapter_io_failure_returns_500_with_attribution(
    workspace: StoryWorkspace,
) -> None:
    _seed_chapter(workspace)
    # .revisions 被普通文件占位：归档必失败，主文件保持不动
    (workspace.chapters_dir / ".revisions").write_text("占位", encoding="utf-8")
    main_path = workspace.chapters_dir / "ch03.md"
    content_before = main_path.read_text(encoding="utf-8")

    response = _client(workspace.root).put(
        f"/api/{SLUG}/chapters/ch03",
        json={"content": "# 第3章\n\n新内容。\n", "baseRevision": 1},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "文件系统错误" in detail
    assert "主文件未受影响" in detail
    assert main_path.read_text(encoding="utf-8") == content_before


# --- edit-mark：作者草稿锁（P5 T3，D4） ---


def _client_with_session(root: Path, session: StubSession) -> TestClient:
    return TestClient(create_app(stub_registry(root, session=session)))


def test_acquire_edit_mark_locks_chapter_for_user(workspace: StoryWorkspace) -> None:
    _seed_chapter(workspace)
    session = StubSession()
    client = _client_with_session(workspace.root, session)

    response = client.put(f"/api/{SLUG}/chapters/ch03/edit-mark")

    assert response.status_code == 200
    body = response.json()
    assert body["chapterId"] == "ch03"
    assert body["holder"] == "user"
    assert isinstance(body["ts"], float)
    assert session.edit_marks.mark_of("ch03") is not None


def test_acquire_edit_mark_missing_chapter_returns_404(workspace: StoryWorkspace) -> None:
    response = _client(workspace.root).put(f"/api/{SLUG}/chapters/ch99/edit-mark")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "ch99" in detail
    assert "怎样算对" in detail


def test_acquire_edit_mark_bad_id_returns_422(workspace: StoryWorkspace) -> None:
    response = _client(workspace.root).put(f"/api/{SLUG}/chapters/坏 名/edit-mark")

    assert response.status_code == 422
    assert "章节 id" in response.json()["detail"]


def test_release_edit_mark_is_idempotent(workspace: StoryWorkspace) -> None:
    session = StubSession()
    client = _client_with_session(workspace.root, session)
    session.edit_marks.acquire("ch03")

    first = client.delete(f"/api/{SLUG}/chapters/ch03/edit-mark")
    second = client.delete(f"/api/{SLUG}/chapters/ch03/edit-mark")

    assert first.status_code == 200
    assert first.json() == {"chapterId": "ch03", "released": True}
    assert second.json() == {"chapterId": "ch03", "released": False}  # 幂等：未持锁无害
    assert session.edit_marks.mark_of("ch03") is None


def test_put_save_releases_edit_mark(workspace: StoryWorkspace) -> None:
    """锁生命周期：保存成功即释放（agent 随后可写该章）。"""
    _seed_chapter(workspace)
    session = StubSession()
    client = _client_with_session(workspace.root, session)
    session.edit_marks.acquire("ch03")

    response = client.put(
        f"/api/{SLUG}/chapters/ch03",
        json={"content": "# 第3章\n\n火车在十点零七分抵达。\n", "baseRevision": 1},
    )

    assert response.status_code == 200
    assert session.edit_marks.mark_of("ch03") is None


# --- PUT 自动入队对账（P5 T4，D5）：sync 开关 + 空 diff no-op ---


class _ReconcileRecordingSession(StubSession):
    """记录 request_reconcile 调用的桩：钉住 PUT → 对账队列的接线。"""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[tuple[str, list[DiffHunk]]] = []

    async def request_reconcile(self, chapter_id: str, hunks: Sequence[DiffHunk]) -> None:
        self.requests.append((chapter_id, list(hunks)))


def test_put_save_with_changes_enqueues_reconcile(workspace: StoryWorkspace) -> None:
    _seed_chapter(workspace)
    session = _ReconcileRecordingSession()
    client = _client_with_session(workspace.root, session)

    response = client.put(
        f"/api/{SLUG}/chapters/ch03",
        json={"content": "# 第3章\n\n火车在十点零七分抵达。\n", "baseRevision": 1},
    )

    assert response.status_code == 200
    assert len(session.requests) == 1
    chapter_id, hunks = session.requests[0]
    assert chapter_id == "ch03"
    assert [(h.kind, h.new_text) for h in hunks] == [("modified", "火车在十点零七分抵达。")]


def test_put_save_sync_false_skips_reconcile(workspace: StoryWorkspace) -> None:
    """sync=false 只存不同步：正文落盘、归档照常，对账队列不见请求。"""
    _seed_chapter(workspace)
    session = _ReconcileRecordingSession()
    client = _client_with_session(workspace.root, session)

    response = client.put(
        f"/api/{SLUG}/chapters/ch03",
        json={
            "content": "# 第3章\n\n火车在十点零七分抵达。\n",
            "baseRevision": 1,
            "sync": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert session.requests == []


def test_put_save_noop_diff_skips_reconcile(workspace: StoryWorkspace) -> None:
    """空 diff 是合法 no-op：无可对账，不入队不通知。"""
    _seed_chapter(workspace)
    original = (workspace.chapters_dir / "ch03.md").read_text(encoding="utf-8")
    session = _ReconcileRecordingSession()
    client = _client_with_session(workspace.root, session)

    response = client.put(
        f"/api/{SLUG}/chapters/ch03",
        json={"content": original, "baseRevision": 1},
    )

    assert response.status_code == 200
    assert session.requests == []


# --- GET/PUT settings：写作语言（P6，D1/D3/D4） ---


def test_get_settings_defaults_to_chinese_for_legacy_workspace(
    workspace: StoryWorkspace,
) -> None:
    """旧工作区无 settings.yaml → 默认中文（配置资源非正文写路径）。"""
    response = _client(workspace.root).get(f"/api/{SLUG}/settings")

    assert response.status_code == 200
    assert response.json() == {"writing_language": "中文"}
    assert not workspace.settings_path.exists()  # 读不落盘


def test_get_settings_reads_saved_value(workspace: StoryWorkspace) -> None:
    save_settings(workspace, StorySettings(writing_language="English"))

    response = _client(workspace.root).get(f"/api/{SLUG}/settings")

    assert response.json() == {"writing_language": "English"}


def test_get_settings_corrupt_yaml_returns_422_with_path(
    workspace: StoryWorkspace,
) -> None:
    workspace.ensure_dirs()
    workspace.settings_path.write_text("- 只是列表\n", encoding="utf-8")

    response = _client(workspace.root).get(f"/api/{SLUG}/settings")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "settings.yaml" in detail
    assert "怎样算对" in detail


def test_put_settings_persists_and_hot_applies(workspace: StoryWorkspace) -> None:
    """PUT 校验 → 落盘 → 会话热生效（apply_writing_language 被调用）。"""
    session = StubSession()
    client = _client_with_session(workspace.root, session)

    response = client.put(
        f"/api/{SLUG}/settings",
        json={"writing_language": "日本語"},
    )

    assert response.status_code == 200
    assert response.json() == {"writing_language": "日本語"}
    assert load_settings(workspace).writing_language == "日本語"
    assert session.applied_languages == ["日本語"]


def test_put_settings_invalid_returns_422_three_elements(
    workspace: StoryWorkspace,
) -> None:
    """非法取值不在预设集内 → 422 三要素，未落盘、未热生效。"""
    session = StubSession()
    client = _client_with_session(workspace.root, session)

    response = client.put(
        f"/api/{SLUG}/settings",
        json={"writing_language": "Klingon"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "写作语言取值不合法" in detail
    assert "为什么错" in detail
    assert "怎样算对" in detail
    assert not workspace.settings_path.exists()
    assert session.applied_languages == []
