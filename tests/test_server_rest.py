"""REST 面测试（P3 T4，P4 T4 slug 化，P5 T2 受管写端点）。

泄压阀三情形逐条钉住：
- 参数违规（坏 slug、未知账本类型、坏章节 id、坏标题）→ 422 + 怎样算对；
- 合法但空（空账本、无章节、无工作区、空 diff no-op）→ 200，不是错误；
- 撞名新建 / 过期提交 / 写作流冲突 → 409；数据故障（损坏 YAML）→ 422；
  环境故障（IO）→ 500 带归因。
红线（D3 演进）：写路径必须经守门管道——业务写入口仅有
PUT chapters/{id}（归档 + 锁 + 乐观并发）；POST /api/workspaces 只是资源创建。
"""

import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hastings.bible.models import Character, Clue, Method, OutlineEntry, TimelineEntry
from hastings.bible.store import StoryWorkspace, save_character, save_ledger, save_story_doc
from hastings.bible.story_state import StageTransition, StoryDoc
from hastings.context_manager import CompactionResult
from hastings.events_store import events_file
from hastings.server.app import create_app
from hastings.server.provider_config import ProviderBook, ProviderProfile, ServerConfig
from hastings.server.registry import WorkspaceRegistry
from hastings.session_persist import session_file
from hastings.session_store import (
    legacy_session_file,
    session_index_file,
    session_path,
)
from hastings.settings import StorySettings, load_settings, save_settings
from test_server_app import StubSession, stub_registry

SLUG = "orient-express"
BIBLE_TYPES = ("character", "clue", "foreshadow", "method", "timeline")

# 确定性档案（模块级只读复用）：default 为引导档案，deepseek 供跨档案切换用。
_DEFAULT_PROFILE = ProviderProfile(
    name="default",
    base_url="https://api.example.com/v1",
    api_key="sk-default-12345678",
    default_model="story-model",
)
_DEEPSEEK_PROFILE = ProviderProfile(
    name="deepseek",
    base_url="https://api.deepseek.com/v1",
    api_key="sk-deepseek-12345678",
    default_model="deepseek-chat",
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def workspace(root: Path) -> StoryWorkspace:
    return StoryWorkspace(root=root, slug=SLUG)


def _client(root: Path) -> TestClient:
    return TestClient(create_app(stub_registry(root)))


class _RunningSession(StubSession):
    """is_running 恒真的会话：写作流冲突场景。"""

    def __init__(self) -> None:
        super().__init__()
        self._running = True


def _client_with_session(root: Path, session: StubSession) -> TestClient:
    return TestClient(create_app(stub_registry(root, session=session)))


class _RunningSession(StubSession):
    """is_running 恒真的会话：写作流冲突场景。"""

    def __init__(self) -> None:
        super().__init__()
        self._running = True


def _client_with_session(root: Path, session: StubSession) -> TestClient:
    return TestClient(create_app(stub_registry(root, session=session)))


def _book(
    *profiles: ProviderProfile, boot_name: str = "default", boot_model: str = ""
) -> ProviderBook:
    return ProviderBook(
        ServerConfig(providers=list(profiles)), boot_name=boot_name, boot_model=boot_model
    )


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


# --- provider-catalog：内建目录（P7c T15） ---


def test_rest_provider_catalog_lists_builtin_entries(root: Path) -> None:
    body = _client(root).get("/api/provider-catalog").json()

    catalog = body["catalog"]
    names = [e["name"] for e in catalog]
    assert "deepseek" in names
    deepseek = catalog[names.index("deepseek")]
    assert deepseek["display_name"] == "DeepSeek"
    assert deepseek["base_url"].startswith("https://")
    assert deepseek["default_model"] in deepseek["models"]
    assert deepseek["api_key_env"] == "DEEPSEEK_API_KEY"
    assert deepseek["key_url"].startswith("https://")


def test_rest_provider_catalog_leaks_no_key_fields(root: Path) -> None:
    """目录是纯数据：响应不得出现密钥承载字段（key 只进不出红线）。

    api_key_env 是合法字段（env 变量名，非密钥），用带引号的 JSON 键匹配避误伤。
    """
    text = _client(root).get("/api/provider-catalog").text

    assert '"api_key"' not in text
    assert "key_hint" not in text


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
    client = TestClient(
        create_app(stub_registry(root), book=_book(*server_config.providers, boot_name="deepseek"))
    )

    body = client.get("/api/providers").json()

    assert body == {
        "providers": [
            {
                "name": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "default_model": "deepseek-chat",
                "key_hint": "…5678",
                "available": True,
                "no_auth": False,
            },
            {
                "name": "nokey",
                "base_url": "https://api.example.com/v1",
                "default_model": "",
                "key_hint": "",
                "available": False,
                "no_auth": False,
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


# --- providers/{name}/models：模型发现（P6 T2，D3） ---


def _models_client(
    root: Path, transport: httpx.AsyncBaseTransport, *, book: ProviderBook
) -> TestClient:
    return TestClient(create_app(stub_registry(root), book=book, model_transport=transport))


def test_provider_models_returns_candidate_list(root: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "story-model"}, {"id": "deepseek-chat"}]})

    client = _models_client(root, httpx.MockTransport(handler), book=_book(_DEEPSEEK_PROFILE))

    body = client.get("/api/providers/deepseek/models").json()

    assert body == {"models": ["story-model", "deepseek-chat"]}
    # 直连标准的 {base_url}/models，并带上 Bearer key
    assert seen[0].url.path == "/v1/models"
    assert seen[0].headers["authorization"] == "Bearer sk-deepseek-12345678"


def test_provider_models_unknown_profile_returns_404(root: Path) -> None:
    client = _models_client(
        root, httpx.MockTransport(lambda _: httpx.Response(200)), book=_book(_DEFAULT_PROFILE)
    )

    response = client.get("/api/providers/ghost/models")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "档案不存在" in detail
    assert "为什么错" in detail
    assert "怎样算对" in detail


def test_provider_models_without_key_returns_422(root: Path) -> None:
    client = _models_client(
        root,
        httpx.MockTransport(lambda _: httpx.Response(200)),
        book=_book(ProviderProfile(name="nokey", base_url="https://api.example.com/v1")),
    )

    response = client.get("/api/providers/nokey/models")

    assert response.status_code == 422
    assert "缺少 API key" in response.json()["detail"]


def test_provider_models_unauthorized_returns_422(root: Path) -> None:
    client = _models_client(
        root, httpx.MockTransport(lambda _: httpx.Response(401)), book=_book(_DEEPSEEK_PROFILE)
    )

    response = client.get("/api/providers/deepseek/models")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "API key 无效" in detail
    assert "为什么错" in detail


def test_provider_models_empty_list_is_ok(root: Path) -> None:
    """合法但空（端点无模型）：200 + 空列表，指引前端手动输入。"""
    client = _models_client(
        root,
        httpx.MockTransport(lambda _: httpx.Response(200, json={"data": []})),
        book=_book(_DEEPSEEK_PROFILE),
    )

    assert client.get("/api/providers/deepseek/models").json() == {"models": []}


def test_provider_models_network_error_returns_502(root: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _models_client(root, httpx.MockTransport(handler), book=_book(_DEEPSEEK_PROFILE))

    response = client.get("/api/providers/deepseek/models")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "环境故障" in detail
    assert "手动输入" in detail  # 泄压阀：给替代路径


# --- providers CRUD（P7 T12）：UI 增删改档案，写回 config.yaml ---


def _crud_client(root: Path, *profiles: ProviderProfile, boot_name: str = "default") -> TestClient:
    """注入带 config_path 的 book：写回路径落在 tmp root，不污染仓库 cwd。"""
    book = ProviderBook(
        ServerConfig(providers=list(profiles)),
        boot_name=boot_name,
        boot_model="",
        config_path=root / "config.yaml",
    )
    return TestClient(create_app(stub_registry(root), book=book))


def test_upsert_provider_creates_and_persists(root: Path) -> None:
    client = _crud_client(root, _DEFAULT_PROFILE)

    response = client.put(
        "/api/providers/openrouter",
        json={
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-openrouter-9999",
            "api_key_env": "",
            "default_model": "anthropic/claude-sonnet-4",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "openrouter"
    assert body["key_hint"] == "…9999"  # key 只进不出：尾 4 位
    assert body["available"] is True
    # 落盘 config.yaml + 进程目录同步可见
    config_text = (root / "config.yaml").read_text(encoding="utf-8")
    assert "openrouter" in config_text
    assert [p["name"] for p in client.get("/api/providers").json()["providers"]] == [
        "default",
        "openrouter",
    ]


def test_upsert_provider_update_with_blank_key_keeps_old_secret(root: Path) -> None:
    """修改时 api_key 留空 = 保留旧 key（明文不可回显，只能原值保留）。"""
    client = _crud_client(root, _DEFAULT_PROFILE, _DEEPSEEK_PROFILE)

    response = client.put(
        "/api/providers/deepseek",
        json={"base_url": "https://api.deepseek.com/v2", "api_key": "", "default_model": "m2"},
    )

    assert response.status_code == 200
    assert response.json()["key_hint"] == "…5678"  # 旧 key 尾 4 位
    assert response.json()["base_url"] == "https://api.deepseek.com/v2"
    config_text = (root / "config.yaml").read_text(encoding="utf-8")
    assert "sk-deepseek-12345678" in config_text  # 旧 key 原样保留


def test_upsert_provider_update_with_new_key_replaces_secret(root: Path) -> None:
    client = _crud_client(root, _DEFAULT_PROFILE, _DEEPSEEK_PROFILE)

    response = client.put(
        "/api/providers/deepseek",
        json={"base_url": "https://api.deepseek.com/v1", "api_key": "sk-new-99999999"},
    )

    assert response.status_code == 200
    assert response.json()["key_hint"] == "…9999"
    config_text = (root / "config.yaml").read_text(encoding="utf-8")
    assert "sk-new-99999999" in config_text
    assert "sk-deepseek-12345678" not in config_text


def test_upsert_provider_blank_base_url_rejected(root: Path) -> None:
    client = _crud_client(root, _DEFAULT_PROFILE)

    response = client.put("/api/providers/ghost", json={"base_url": ""})

    assert response.status_code == 422
    assert "base_url" in response.json()["detail"][0]["loc"]


def test_delete_provider_boot_profile_rejected(root: Path) -> None:
    client = _crud_client(root, _DEFAULT_PROFILE, _DEEPSEEK_PROFILE)

    response = client.delete("/api/providers/default")

    assert response.status_code == 409
    assert "引导档案" in response.json()["detail"]


def test_delete_provider_last_profile_rejected(root: Path) -> None:
    """引导档案不在列表（配置被改坏）时，删唯一档案撞「最后一个」保护。"""
    client = _crud_client(root, _DEEPSEEK_PROFILE, boot_name="default")

    response = client.delete("/api/providers/deepseek")

    assert response.status_code == 409
    assert "最后一个" in response.json()["detail"]


def test_delete_provider_in_use_rejected_with_slugs(root: Path, workspace: StoryWorkspace) -> None:
    save_settings(
        workspace,
        StorySettings(provider_profile="deepseek", writing_language="中文", model="m"),
    )
    client = _crud_client(root, _DEFAULT_PROFILE, _DEEPSEEK_PROFILE)

    response = client.delete("/api/providers/deepseek")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "deepseek" in detail
    assert SLUG in detail


def test_delete_provider_succeeds_and_persists(root: Path) -> None:
    client = _crud_client(root, _DEFAULT_PROFILE, _DEEPSEEK_PROFILE)

    response = client.delete("/api/providers/deepseek")

    assert response.status_code == 204
    assert [p["name"] for p in client.get("/api/providers").json()["providers"]] == ["default"]
    config_text = (root / "config.yaml").read_text(encoding="utf-8")
    assert "deepseek" not in config_text


def test_delete_provider_unknown_returns_404(root: Path) -> None:
    client = _crud_client(root, _DEFAULT_PROFILE)

    assert client.delete("/api/providers/ghost").status_code == 404


# --- providers/test：未保存表单的连通测试（P7 T12） ---


def test_provider_test_returns_models(root: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-inline-12345678"
        return httpx.Response(200, json={"data": [{"id": "m1"}]})

    client = TestClient(
        create_app(
            stub_registry(root),
            book=ProviderBook(
                ServerConfig(providers=[_DEFAULT_PROFILE]),
                boot_name="default",
                boot_model="",
                config_path=root / "config.yaml",
            ),
            model_transport=httpx.MockTransport(handler),
        )
    )

    body = client.post(
        "/api/providers/test",
        json={"base_url": "https://api.example.com/v1", "api_key": "sk-inline-12345678"},
    ).json()

    assert body == {"models": ["m1"]}


def test_provider_test_resolves_env_key(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_ENV_KEY", "sk-from-env-9999")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-from-env-9999"
        return httpx.Response(200, json={"data": []})

    client = TestClient(
        create_app(
            stub_registry(root),
            book=ProviderBook(
                ServerConfig(providers=[_DEFAULT_PROFILE]),
                boot_name="default",
                boot_model="",
                config_path=root / "config.yaml",
            ),
            model_transport=httpx.MockTransport(handler),
        )
    )

    body = client.post(
        "/api/providers/test",
        json={"base_url": "https://api.example.com/v1", "api_key_env": "TEST_ENV_KEY"},
    ).json()

    assert body == {"models": []}  # 合法空结果：连通成功但端点无模型列表


def test_provider_test_without_any_key_returns_422(root: Path) -> None:
    client = _crud_client(root, _DEFAULT_PROFILE)

    response = client.post("/api/providers/test", json={"base_url": "https://api.example.com/v1"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "缺少 API key" in detail
    assert "怎样算对" in detail


# --- state：按 slug 解析 ---


def test_rest_state_reports_session_and_bible_types(root: Path) -> None:
    body = _client(root).get(f"/api/{SLUG}/state").json()
    assert body == {
        "slug": SLUG,
        "model": "fake",
        "is_running": False,
        "tools": ["bible_query", "read_chapter"],
        "bible_types": ["character", "clue", "foreshadow", "method", "outline", "timeline"],
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


# --- story：生命周期快照（P8 T7，D8）；V1 弃用后 story.yaml 只读 ---


def test_rest_story_reports_stage_counts_and_chapters(workspace: StoryWorkspace) -> None:
    workspace.ensure_dirs()
    save_character(workspace, Character(name="波洛", identity="比利时侦探"))
    save_character(workspace, Character(name="黑斯廷斯", identity="叙事者"))
    save_ledger(workspace.method_path, [Method(name="毒杀诡计", status="locked")])
    save_ledger(workspace.outline_path, [OutlineEntry(name="案发之夜", chapter=1)])
    save_story_doc(
        workspace,
        StoryDoc(
            slug=SLUG,
            stage="writing",
            stage_history=[
                StageTransition(
                    from_stage="bible_confirmed",
                    to_stage="writing",
                    reason="作者确认 Bible",
                    at="2026-10-10T00:00:00",
                )
            ],
        ),
    )

    body = _client(workspace.root).get(f"/api/{SLUG}/story").json()

    assert body["slug"] == SLUG
    assert body["stage"] == "writing"
    assert body["counts"] == {
        "characters": 2,
        "clues": 0,
        "foreshadows": 0,
        "methods": 1,
        "methods_locked": 1,
        "outlines": 1,
    }
    assert body["chapters"] == {"written": 0, "planned": 1}
    assert body["stage_history"] == [
        {
            "from_stage": "bible_confirmed",
            "to_stage": "writing",
            "reason": "作者确认 Bible",
            "at": "2026-10-10T00:00:00",
        }
    ]


def test_rest_story_defaults_to_intake_for_legacy_workspace(
    workspace: StoryWorkspace,
) -> None:
    """旧工作区无 story.yaml 按 intake 兜底读取（D1），全零计数是合法空结果。"""
    body = _client(workspace.root).get(f"/api/{SLUG}/story").json()

    assert body["stage"] == "intake"
    assert body["stage_history"] == []
    assert body["counts"]["characters"] == 0
    assert body["chapters"] == {"written": 0, "planned": 0}


def test_rest_story_corrupt_ledger_returns_422(workspace: StoryWorkspace) -> None:
    workspace.ensure_dirs()
    workspace.outline_path.write_text(": [broken", encoding="utf-8")

    response = _client(workspace.root).get(f"/api/{SLUG}/story")

    assert response.status_code == 422
    assert "怎样算对" in response.json()["detail"]


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


# --- GET/PUT settings：写作语言 + 模型切换（P6，D1/D2/D3/D4） ---


def _settings_client(
    root: Path,
    *,
    session: StubSession | None = None,
    book: ProviderBook | None = None,
) -> TestClient:
    return TestClient(create_app(stub_registry(root, session=session), book=book))


def test_get_settings_defaults_for_legacy_workspace(workspace: StoryWorkspace) -> None:
    """旧工作区无 settings.yaml → 默认值；返回生效档案/模型（配置资源非正文写路径）。"""
    response = _settings_client(workspace.root, book=_book(_DEFAULT_PROFILE)).get(
        f"/api/{SLUG}/settings"
    )

    assert response.status_code == 200
    assert response.json() == {
        "writing_language": "中文",
        "provider_profile": "default",
        "model": "story-model",  # 空 model 解析到档案 default_model
    }
    assert not workspace.settings_path.exists()  # 读不落盘


def test_get_settings_reads_saved_value(workspace: StoryWorkspace) -> None:
    save_settings(
        workspace,
        StorySettings(writing_language="English", provider_profile="deepseek"),
    )

    response = _settings_client(
        workspace.root, book=_book(_DEFAULT_PROFILE, _DEEPSEEK_PROFILE)
    ).get(f"/api/{SLUG}/settings")

    assert response.json() == {
        "writing_language": "English",
        "provider_profile": "deepseek",
        "model": "deepseek-chat",
    }


def test_get_settings_corrupt_yaml_returns_422_with_path(
    workspace: StoryWorkspace,
) -> None:
    workspace.ensure_dirs()
    workspace.settings_path.write_text("- 只是列表\n", encoding="utf-8")

    response = _settings_client(workspace.root, book=_book(_DEFAULT_PROFILE)).get(
        f"/api/{SLUG}/settings"
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "settings.yaml" in detail
    assert "怎样算对" in detail


def test_put_settings_same_profile_hot_swaps_model(workspace: StoryWorkspace) -> None:
    """同档案换模型：热切（apply_model），不重建会话；写作语言一并热生效。"""
    session = StubSession()
    client = _settings_client(workspace.root, session=session, book=_book(_DEFAULT_PROFILE))

    response = client.put(
        f"/api/{SLUG}/settings",
        json={"writing_language": "日本語", "model": "story-model-2"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "writing_language": "日本語",
        "provider_profile": "default",
        "model": "story-model-2",
    }
    saved = load_settings(workspace)
    assert saved.writing_language == "日本語"
    assert saved.model == "story-model-2"
    assert session.applied_languages == ["日本語"]
    assert session.applied_models == ["story-model-2"]


def test_put_settings_partial_update_keeps_current_profile(workspace: StoryWorkspace) -> None:
    """局部更新：只传 writing_language，档案/模型保持当前值（不回落空）。"""
    save_settings(workspace, StorySettings(provider_profile="deepseek", model="deepseek-chat"))
    session = StubSession()
    session.provider_profile = "deepseek"
    client = _settings_client(
        workspace.root, session=session, book=_book(_DEFAULT_PROFILE, _DEEPSEEK_PROFILE)
    )

    response = client.put(f"/api/{SLUG}/settings", json={"writing_language": "English"})

    assert response.status_code == 200
    assert response.json() == {
        "writing_language": "English",
        "provider_profile": "deepseek",
        "model": "deepseek-chat",
    }


def test_put_settings_cross_profile_rebuilds_session(workspace: StoryWorkspace) -> None:
    """跨档案切换：落盘 → registry.rebuild（新 provider/新 harness，对话恢复）。"""
    created: list[StubSession] = []

    async def factory(_: StoryWorkspace) -> StubSession:
        session = StubSession()
        created.append(session)
        return session

    registry = WorkspaceRegistry(root=workspace.root, session_factory=factory)
    client = TestClient(create_app(registry, book=_book(_DEFAULT_PROFILE, _DEEPSEEK_PROFILE)))
    client.get(f"/api/{SLUG}/state")  # 首次装配
    assert len(created) == 1

    response = client.put(
        f"/api/{SLUG}/settings",
        json={"writing_language": "中文", "provider_profile": "deepseek", "model": "deepseek-chat"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "writing_language": "中文",
        "provider_profile": "deepseek",
        "model": "deepseek-chat",
    }
    assert len(created) == 2  # 跨档案触发重建
    assert load_settings(workspace).provider_profile == "deepseek"


def test_put_settings_same_profile_does_not_rebuild(workspace: StoryWorkspace) -> None:
    """同档案换模型不触发 registry.rebuild（原地热切）。"""
    created: list[StubSession] = []

    async def factory(_: StoryWorkspace) -> StubSession:
        session = StubSession()
        created.append(session)
        return session

    registry = WorkspaceRegistry(root=workspace.root, session_factory=factory)
    client = TestClient(create_app(registry, book=_book(_DEFAULT_PROFILE)))
    client.get(f"/api/{SLUG}/state")
    assert len(created) == 1

    response = client.put(
        f"/api/{SLUG}/settings",
        json={"writing_language": "中文", "provider_profile": "default", "model": "story-model-2"},
    )

    assert response.status_code == 200
    assert len(created) == 1  # 未重建
    assert created[0].applied_models == ["story-model-2"]


def test_put_settings_invalid_returns_422_three_elements(
    workspace: StoryWorkspace,
) -> None:
    """非法取值不在预设集内 → 422 三要素，未落盘、未热生效。"""
    session = StubSession()
    client = _settings_client(workspace.root, session=session, book=_book(_DEFAULT_PROFILE))

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


def test_put_settings_unknown_profile_returns_422(workspace: StoryWorkspace) -> None:
    session = StubSession()
    client = _settings_client(
        workspace.root, session=session, book=_book(_DEFAULT_PROFILE, _DEEPSEEK_PROFILE)
    )

    response = client.put(
        f"/api/{SLUG}/settings",
        json={"writing_language": "中文", "provider_profile": "ghost"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "档案不存在" in detail
    assert "default、deepseek" in detail  # 列出可用档案
    assert not workspace.settings_path.exists()
    assert session.applied_models == []


def test_put_settings_profile_without_key_returns_422(workspace: StoryWorkspace) -> None:
    keyless = ProviderProfile(
        name="nokey", base_url="https://api.example.com/v1", default_model="m"
    )
    client = _settings_client(workspace.root, book=_book(_DEFAULT_PROFILE, keyless))

    response = client.put(
        f"/api/{SLUG}/settings",
        json={"writing_language": "中文", "provider_profile": "nokey"},
    )

    assert response.status_code == 422
    assert "缺少 API key" in response.json()["detail"]


def test_put_settings_no_model_resolvable_returns_422(workspace: StoryWorkspace) -> None:
    """档案可用但既无 default_model 也未传 model → 422 三要素，不落盘。"""
    nomodel = ProviderProfile(
        name="nomodel", base_url="https://api.example.com/v1", api_key="sk-x-12345678"
    )
    session = StubSession()
    client = _settings_client(
        workspace.root, session=session, book=_book(_DEFAULT_PROFILE, nomodel)
    )

    response = client.put(
        f"/api/{SLUG}/settings",
        json={"writing_language": "中文", "provider_profile": "nomodel"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "没有可用的模型名" in detail
    assert "为什么错" in detail
    assert not workspace.settings_path.exists()


def test_put_settings_while_running_returns_409(workspace: StoryWorkspace) -> None:
    """写作流进行中切换 → 409 三要素；未落盘、未热生效。"""
    session = _RunningSession()
    client = _settings_client(workspace.root, session=session, book=_book(_DEFAULT_PROFILE))

    response = client.put(
        f"/api/{SLUG}/settings",
        json={"writing_language": "English", "model": "story-model-2"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "写作流" in detail
    assert "为什么错" in detail
    assert "怎样算对" in detail
    assert not workspace.settings_path.exists()
    assert session.applied_models == []


# --- GET events：事件流历史查询（P6 T4/D5） ---


def _write_events(workspace: StoryWorkspace, records: list[dict[str, object]]) -> None:
    workspace.ensure_dirs()
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    events_file(workspace).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _event(seq: int, event_type: str, data: dict[str, object] | None = None) -> dict[str, object]:
    return {"ts": "2026-10-09T00:00:00+00:00", "seq": seq, "type": event_type, "data": data or {}}


def test_rest_events_paged_newest_first(workspace: StoryWorkspace) -> None:
    _write_events(workspace, [_event(i, "agent_start") for i in (1, 2, 3)])

    body = _client(workspace.root).get(f"/api/{SLUG}/events?limit=2").json()

    assert [event["seq"] for event in body["events"]] == [3, 2]
    assert body["has_more"] is True
    assert body["total"] == 3  # 统计不受分页影响
    assert body["counts"] == {"other": 3}

    older = _client(workspace.root).get(f"/api/{SLUG}/events?limit=2&before_seq=2").json()
    assert [event["seq"] for event in older["events"]] == [1]
    assert older["has_more"] is False
    assert older["total"] == 3


def test_rest_events_empty_is_ok(workspace: StoryWorkspace) -> None:
    """新工作区无 events.jsonl：合法空结果 → 200 空列表。"""
    assert _client(workspace.root).get(f"/api/{SLUG}/events").json() == {
        "events": [],
        "has_more": False,
        "total": 0,
        "counts": {},
    }


def test_rest_events_category_filter(workspace: StoryWorkspace) -> None:
    _write_events(
        workspace,
        [
            _event(1, "message_start"),
            _event(2, "tool_execution_start", {"toolName": "bible_upsert"}),
            _event(3, "tool_execution_end", {"isError": True}),
            _event(4, "agent_end"),
        ],
    )

    client = _client(workspace.root)

    def types_for(category: str) -> list[str]:
        body = client.get(f"/api/{SLUG}/events?category={category}").json()
        return [event["type"] for event in body["events"]]

    assert types_for("message") == ["message_start"]
    assert types_for("error") == ["tool_execution_end"]

    # 分类计数是整账口径，不随 category 过滤变化（前端 Tab 徽标数据源）
    counts = client.get(f"/api/{SLUG}/events").json()["counts"]
    assert counts == {"message": 1, "tool": 1, "error": 1, "other": 1}


# --- GET events/export：事件账本导出（P6 T5/D5） ---


def test_rest_events_export_downloads_jsonl(workspace: StoryWorkspace) -> None:
    _write_events(workspace, [_event(1, "agent_start"), _event(2, "agent_end")])

    response = _client(workspace.root).get(f"/api/{SLUG}/events/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in response.headers["content-disposition"]
    assert f"hastings-{SLUG}-events.jsonl" in response.headers["content-disposition"]
    lines = [json.loads(line) for line in response.text.splitlines()]
    assert [line["seq"] for line in lines] == [1, 2]  # 原样下载，不做倒序重排


def test_rest_events_export_without_history_returns_404(workspace: StoryWorkspace) -> None:
    response = _client(workspace.root).get(f"/api/{SLUG}/events/export")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "尚无事件历史" in detail
    assert "为什么错" in detail
    assert "怎样算对" in detail


@pytest.mark.parametrize("query", ["limit=0", "limit=5000"])
def test_rest_events_bad_limit_returns_422(workspace: StoryWorkspace, query: str) -> None:
    response = _client(workspace.root).get(f"/api/{SLUG}/events?{query}")

    assert response.status_code == 422
    assert "分页参数不合法" in response.json()["detail"]


def test_rest_events_bad_category_returns_422(workspace: StoryWorkspace) -> None:
    response = _client(workspace.root).get(f"/api/{SLUG}/events?category=nope")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "分类不合法" in detail
    assert "怎样算对" in detail


# --- GET export：工作区全量备份（P6 D10） ---


def test_rest_export_zip_contains_workspace_files(workspace: StoryWorkspace) -> None:
    """zip 内容清单：bible/*、chapters/*.md、settings/session.jsonl，不含 .revisions。"""
    workspace.ensure_dirs()
    save_character(workspace, Character(name="陆远", identity="侦探"))
    save_ledger(workspace.clues_path, [Clue(name="车票", content="北上的车票")])
    (workspace.chapters_dir / "ch01.md").write_text("# 第1章 夜车\n\n雪落无声。", encoding="utf-8")
    # 归档目录（.revisions）——不应出现在导出 zip（D10 体积考量，已知限制）
    revision_dir = workspace.chapters_dir / ".revisions" / "ch01"
    revision_dir.mkdir(parents=True)
    (revision_dir / "1.md").write_text("旧稿", encoding="utf-8")
    save_settings(workspace, StorySettings(writing_language="中文"))
    session_file(workspace).write_text('{"kind":"message_end"}\n', encoding="utf-8")

    response = _client(workspace.root).get(f"/api/{SLUG}/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert f"hastings-{SLUG}-" in response.headers["content-disposition"]
    assert "attachment" in response.headers["content-disposition"]
    names = set(zipfile.ZipFile(io.BytesIO(response.content)).namelist())
    assert "bible/characters/陆远.yaml" in names
    assert "bible/clues.yaml" in names
    assert "chapters/ch01.md" in names
    assert "settings.yaml" in names
    assert "session.jsonl" in names
    assert not any(".revisions" in name for name in names)


def test_rest_export_empty_workspace_is_ok(workspace: StoryWorkspace) -> None:
    """新工作区（目录空、无任何文件）：合法空结果 → 200 空 zip，不是错误。"""
    workspace.ensure_dirs()

    response = _client(workspace.root).get(f"/api/{SLUG}/export")

    assert response.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert names == []


# --- DELETE /api/workspaces/{slug}：工作区删除（P7 T13） ---


def test_rest_delete_workspace_removes_directory(root: Path) -> None:
    (root / SLUG).mkdir()
    (root / SLUG / "bible").mkdir()
    (root / SLUG / "bible" / "characters.yaml").write_text("x", encoding="utf-8")

    response = _client(root).delete(f"/api/workspaces/{SLUG}?confirm={SLUG}")

    assert response.status_code == 204
    assert not (root / SLUG).exists()
    assert [s for s in _client(root).get("/api/workspaces").json()["workspaces"]] == []


def test_rest_delete_workspace_confirm_mismatch_returns_422(root: Path) -> None:
    (root / SLUG).mkdir()

    response = _client(root).delete(f"/api/workspaces/{SLUG}?confirm=wrong-slug")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "confirm" in detail
    assert "为什么错" in detail
    assert "怎样算对" in detail
    assert (root / SLUG).exists()  # 未被误删


def test_rest_delete_workspace_unknown_returns_404(root: Path) -> None:
    response = _client(root).delete(f"/api/workspaces/{SLUG}?confirm={SLUG}")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "工作区不存在" in detail
    assert "怎样算对" in detail


@pytest.mark.parametrize("bad_slug", ["CON", "bad.name"])
def test_rest_delete_workspace_invalid_slug_returns_422(root: Path, bad_slug: str) -> None:
    """路径参数只能承载合法 URL 片段，非法 slug 用保留名/含点字符覆盖。"""
    response = _client(root).delete(f"/api/workspaces/{bad_slug}?confirm={bad_slug}")

    assert response.status_code == 422
    assert "slug 不合法" in response.json()["detail"]
    assert not (root / "escape").exists()


def test_rest_delete_workspace_while_running_returns_409(root: Path) -> None:
    """运行中删除 → 409；先 GET state 预热装配会话（ensure_dirs 顺带建目录）。"""
    client = TestClient(create_app(stub_registry(root, session=_RunningSession())))
    client.get(f"/api/{SLUG}/state")  # 触发 get_or_create 装配 + 建目录

    response = client.delete(f"/api/workspaces/{SLUG}?confirm={SLUG}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "写作流" in detail
    assert "为什么错" in detail
    assert "怎样算对" in detail
    assert (root / SLUG).exists()


def test_rest_delete_workspace_not_assembled_skips_running_check(root: Path) -> None:
    """未装配过会话（peek 为 None）→ 不可能运行中，直接放行删除。"""
    (root / SLUG).mkdir()

    response = _client(root).delete(f"/api/workspaces/{SLUG}?confirm={SLUG}")

    assert response.status_code == 204


# --- /api/{slug}/sessions 会话 CRUD（P10 T14，D13）：列表/新建/切换/删除/重命名 ---


def _counting_registry(root: Path) -> tuple[WorkspaceRegistry, Callable[[], int]]:
    """工厂调用计数注册表（验证会话变更后发生 rebuild 重装配）。"""
    calls = 0

    async def factory(workspace: StoryWorkspace) -> StubSession:
        nonlocal calls
        del workspace
        calls += 1
        return StubSession()

    return WorkspaceRegistry(root=root, session_factory=factory), lambda: calls


def _sessions_body(client: TestClient, slug: str = SLUG) -> dict[str, Any]:
    response = client.get(f"/api/{slug}/sessions")
    assert response.status_code == 200
    return response.json()


def test_list_sessions_migrates_legacy_single_session(root: Path) -> None:
    """旧单会话格式：首次访问把 session.jsonl 归位为 sessions/<id>.jsonl 并建索引。"""
    workspace = StoryWorkspace(root=root, slug=SLUG)
    workspace.ensure_dirs()
    legacy = legacy_session_file(workspace)
    legacy.write_text('{"kind":"message_end"}\n{"kind":"message_end"}\n', encoding="utf-8")

    body = _sessions_body(_client(root))

    assert len(body["sessions"]) == 1
    meta = body["sessions"][0]
    assert meta["active"] is True
    assert body["active_id"] == meta["id"]
    assert meta["message_count"] == 2
    assert not legacy.exists()  # 旧文件已归位
    assert session_path(workspace, meta["id"]).exists()
    assert session_index_file(workspace).exists()


def test_list_sessions_creates_first_session_for_new_workspace(root: Path) -> None:
    body = _sessions_body(_client(root))
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["active"] is True
    assert body["sessions"][0]["message_count"] == 0


def test_create_session_adds_and_activates(root: Path) -> None:
    registry, factory_calls = _counting_registry(root)
    client = TestClient(create_app(registry))
    first = _sessions_body(client)["active_id"]

    response = client.post(f"/api/{SLUG}/sessions", json={"title": "第二稿"})

    assert response.status_code == 200
    created = response.json()
    assert created["title"] == "第二稿"
    assert created["active"] is True
    assert created["id"] != first
    body = _sessions_body(client)
    assert {item["id"] for item in body["sessions"]} == {first, created["id"]}  # 旧会话保留
    assert body["active_id"] == created["id"]
    assert factory_calls() == 2  # get_or_create + rebuild


def test_activate_session_switches_active(root: Path) -> None:
    registry, factory_calls = _counting_registry(root)
    client = TestClient(create_app(registry))
    first = _sessions_body(client)["active_id"]
    client.post(f"/api/{SLUG}/sessions")

    response = client.post(f"/api/{SLUG}/sessions/{first}/activate")

    assert response.status_code == 200
    assert response.json()["active"] is True
    assert _sessions_body(client)["active_id"] == first
    assert factory_calls() == 3  # list + create rebuild + activate rebuild


def test_rename_session_updates_title(root: Path) -> None:
    client = _client(root)
    target = _sessions_body(client)["active_id"]

    response = client.patch(f"/api/{SLUG}/sessions/{target}", json={"title": "改名"})

    assert response.status_code == 200
    assert response.json()["title"] == "改名"
    assert _sessions_body(client)["sessions"][0]["title"] == "改名"


def test_delete_active_session_switches_and_removes_file(root: Path) -> None:
    client = _client(root)
    first = _sessions_body(client)["active_id"]
    created = client.post(f"/api/{SLUG}/sessions").json()["id"]
    workspace = StoryWorkspace(root=root, slug=SLUG)
    session_path(workspace, created).write_text("x\n", encoding="utf-8")

    response = client.delete(f"/api/{SLUG}/sessions/{created}")

    assert response.status_code == 200
    assert response.json() == {"slug": SLUG, "active_id": first}
    assert not session_path(workspace, created).exists()
    body = _sessions_body(client)
    assert body["active_id"] == first
    assert [item["id"] for item in body["sessions"]] == [first]


def test_delete_last_session_creates_empty_replacement(root: Path) -> None:
    client = _client(root)
    only = _sessions_body(client)["active_id"]

    response = client.delete(f"/api/{SLUG}/sessions/{only}")

    assert response.status_code == 200
    new_active = response.json()["active_id"]
    assert new_active != only
    body = _sessions_body(client)
    assert body["active_id"] == new_active
    assert [item["id"] for item in body["sessions"]] == [new_active]


def test_activate_unknown_session_returns_404(root: Path) -> None:
    client = _client(root)
    _sessions_body(client)  # 确保索引存在

    response = client.post(f"/api/{SLUG}/sessions/session-00000000-000000-0000/activate")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "会话不存在" in detail
    assert "怎样算对" in detail


def test_session_actions_while_running_return_409(root: Path) -> None:
    """running → 新建/切换/删除全部 409（文案三要素）。"""
    client = _client_with_session(root, _RunningSession())
    client.get(f"/api/{SLUG}/state")  # 预热装配

    responses = (
        client.post(f"/api/{SLUG}/sessions"),
        client.post(f"/api/{SLUG}/sessions/some-id/activate"),
        client.delete(f"/api/{SLUG}/sessions/some-id"),
    )
    for response in responses:
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "写作流进行中" in detail
        assert "为什么错" in detail
        assert "怎样算对" in detail


# --- POST /api/{slug}/session/compact（P10 T13，D12）：手动压缩两档原语 ---


class _CompactSession(StubSession):
    """compact_context 返回预设结果的桩：钉住 REST → 会话方法的透传。"""

    def __init__(self, result: CompactionResult) -> None:
        super().__init__()
        self._result = result
        self.compact_calls = 0

    async def compact_context(self) -> CompactionResult:
        self.compact_calls += 1
        return self._result


def test_compact_session_returns_stats(root: Path) -> None:
    """压缩发生 → 200 且统计与 CompactionResult 同形透出。"""
    session = _CompactSession(
        CompactionResult(True, "manual", 5000, 1200, (1, 2), True, "session-20260101-000000.jsonl")
    )
    response = _client_with_session(root, session).post(f"/api/{SLUG}/session/compact")

    assert response.status_code == 200
    assert response.json() == {
        "compacted": True,
        "reason": "manual",
        "tokens_before": 5000,
        "tokens_after": 1200,
        "stripped_chapters": [1, 2],
        "summarized": True,
        "archived": "session-20260101-000000.jsonl",
    }
    assert session.compact_calls == 1


def test_compact_session_nothing_to_strip_is_ok(root: Path) -> None:
    """无可剥离内容 → 200 + compacted=false（合法空结果，非错误）。"""
    response = _client(root).post(f"/api/{SLUG}/session/compact")

    assert response.status_code == 200
    body = response.json()
    assert body["compacted"] is False
    assert body["reason"] == "nothing_to_strip"


def test_compact_session_while_running_returns_409(root: Path) -> None:
    """running → 409（文案对齐 reset）：会话方法不被调用。"""
    session = _RunningSession()
    client = _client_with_session(root, session)
    client.get(f"/api/{SLUG}/state")  # 预热装配

    response = client.post(f"/api/{SLUG}/session/compact")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "压缩被拒" in detail
    assert "写作流进行中" in detail
    assert "为什么错" in detail
    assert "怎样算对" in detail
