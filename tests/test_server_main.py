"""Web 服务入口测试（P3 T5，P4 T4 多工作区化，P6 D1 多档案）。

只测可确定性验证的部分：参数解析、env 缺失时中文指引退出（复用 CLI
的 env_error 模式）、env 齐全时装配链正确并把 app 交给 uvicorn（打桩，
不真启动）。HTTP/WS 行为已由 app/ws/rest 测试覆盖，此处只验接线。
P6：config.yaml 存在时为权威配置（启动档案/模型来自档案），
损坏三要素退出；缺失则回落 env（既有行为零破坏）。
所有 main 用例显式传 --config 指向 tmp——不依赖 cwd 是否有 config.yaml。
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from pytest import CaptureFixture, MonkeyPatch

import hastings.server.__main__ as server_main
from hastings.server.registry import WorkspaceRegistry

CONFIG_YAML = """\
providers:
  - name: deepseek
    base_url: https://api.deepseek.com/v1
    api_key: sk-direct-12345678
    default_model: deepseek-chat
  - name: nokey
    base_url: https://api.example.com/v1
    default_model: other-model
"""


def test_parser_defaults_and_overrides() -> None:
    args = server_main.build_parser().parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.model is None
    assert args.stories_root is not None
    # P9 T9：config/static/config-dir 缺省均为 None，由 main 内解析
    assert args.config is None
    assert args.config_dir is None
    assert args.static_dir is None

    args = server_main.build_parser().parse_args(
        ["--port", "9000", "--host", "0.0.0.0", "--model", "glm-4-plus", "--config", "my.yaml"]
    )
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.model == "glm-4-plus"
    assert args.config == Path("my.yaml")


def test_parser_explicit_v2_repository() -> None:
    assert server_main.build_parser().parse_args([]).v2_db is None
    args = server_main.build_parser().parse_args(["--v2-db", "work/v2.sqlite"])
    assert args.v2_db == Path("work/v2.sqlite")


def test_parser_config_dir_and_static_dir() -> None:
    """P9 T9：桌面参数 --config-dir / --static-dir。"""
    args = server_main.build_parser().parse_args(["--config-dir", "cfgdir", "--static-dir", "dist"])
    assert args.config_dir == Path("cfgdir")
    assert args.static_dir == Path("dist")


def test_resolve_config_path(tmp_path: Path) -> None:
    """config 路径解析：--config > --config-dir/config.yaml > 默认 ./config.yaml。"""
    assert server_main.resolve_config_path(None, None) == server_main.DEFAULT_CONFIG_PATH
    assert server_main.resolve_config_path(Path("x.yaml"), None) == Path("x.yaml")
    assert server_main.resolve_config_path(None, tmp_path) == tmp_path / "config.yaml"
    with pytest.raises(ValueError, match="只能传一个"):
        server_main.resolve_config_path(Path("x.yaml"), tmp_path)


def test_resolve_static_dir(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """static 解析：显式目录优先；缺省走仓库探测；探测不到为 None。"""
    assert server_main.resolve_static_dir(tmp_path) == tmp_path
    monkeypatch.setattr(server_main, "default_static_dir", lambda: None)
    assert server_main.resolve_static_dir(None) is None


def test_find_static_dir(tmp_path: Any) -> None:
    """dist 存在则托管、不存在则 None（纯 API 模式）。"""
    assert server_main.find_static_dir(tmp_path) is None
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    assert server_main.find_static_dir(tmp_path) == dist


@pytest.fixture
def clean_env(monkeypatch: MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """隔离 .env 注入：打桩 load_dotenv 并清空相关变量。"""
    monkeypatch.setattr(server_main, "load_dotenv", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("HASTINGS_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)
    yield


def test_default_start_exposes_v2_and_reopens_projects(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    from fastapi.testclient import TestClient

    apps: list[FastAPI] = []
    monkeypatch.setattr(server_main.uvicorn, "run", lambda app, **kwargs: apps.append(app))
    monkeypatch.setattr(server_main, "resolve_static_dir", lambda _path: None)
    assert server_main.main([]) == 0
    client = TestClient(apps[-1])
    created = client.post("/api/v2/projects", json={"name": "默认启动项目"})
    assert created.status_code == 200, created.text
    pid = created.json()["project_id"]
    root = f"/api/v2/projects/{pid}"
    for route in (
        "manuscript",
        "story",
        "changes",
        "author-model/preferences",
        "agent-jobs",
        "conversations",
    ):
        response = client.get(root + "/" + route)
        assert response.status_code == 200, (route, response.text)
    with client.websocket_connect(f"/ws/v2/{pid}") as ws:
        ws.send_json({"type": "subscribe", "after_seq": 0})
        assert ws.receive_json()["type"] == "replayed"
    assert (tmp_path / "data" / "v2.db").is_file()
    assert str((tmp_path / "data" / "v2.db").resolve()) in capsys.readouterr().out
    assert server_main.main([]) == 0
    restored = TestClient(apps[-1]).get(root + "/manuscript")
    assert restored.status_code == 200, restored.text


def test_default_database_reuses_existing_conventional_path(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert server_main.resolve_v2_db(None) == Path("data/v2.db")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/v2.sqlite").touch()
    assert server_main.resolve_v2_db(None) == Path("data/v2.sqlite")
    (tmp_path / "data/v2.db").touch()
    with pytest.raises(ValueError, match="--v2-db"):
        server_main.resolve_v2_db(None)
    assert server_main.resolve_v2_db(Path("custom/store.db")) == Path("custom/store.db")


def test_ambiguous_default_database_exits_before_starting(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    (tmp_path / "data").mkdir()
    for name in ("v2.db", "v2.sqlite"):
        (tmp_path / "data" / name).touch()
    calls: list[FastAPI] = []
    monkeypatch.setattr(server_main.uvicorn, "run", lambda app, **kwargs: calls.append(app))
    assert server_main.main([]) == 2
    assert not calls
    assert "--v2-db" in capsys.readouterr().out


def test_main_boots_unconfigured_with_env_hint_when_env_missing(
    clean_env: None,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """空启动：无 config.yaml 且 env 全缺（首次使用原貌）——env 指引转为告警，服务照起。"""
    calls: list[FastAPI] = []
    monkeypatch.setattr(server_main.uvicorn, "run", lambda app, **k: calls.append(app))

    exit_code = server_main.main(
        ["--config", str(tmp_path / "absent.yaml"), "--stories-root", str(tmp_path)]
    )

    assert exit_code == 0
    assert calls, "首次空启动服务应照常拉起"
    out = capsys.readouterr().out
    assert "OPENAI_API_KEY" in out  # env 指引：缺哪个变量、怎样配置
    assert ".env" in out
    assert "未配置模式" in out
    assert "模型：未配置" in out


def test_main_boots_uvicorn_with_registry_app(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("HASTINGS_MODEL", "fake-model")

    calls: list[dict[str, Any]] = []

    def fake_run(app: FastAPI, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(server_main.uvicorn, "run", fake_run)

    exit_code = server_main.main(
        [
            "--config",
            str(tmp_path / "absent.yaml"),
            "--stories-root",
            str(tmp_path / "stories"),
            "--port",
            "9123",
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    app = calls[0]["app"]
    assert isinstance(app, FastAPI)
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 9123
    # 装配链：app.state.registry 是多工作区注册表，根指向 stories-root
    registry = app.state.registry
    assert isinstance(registry, WorkspaceRegistry)
    assert registry.root == tmp_path / "stories"
    # 全局探活与工作区列表真实可请求（slug 路由由 rest/ws 测试覆盖）
    from fastapi.testclient import TestClient

    client = TestClient(app)
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/workspaces").json() == {"workspaces": []}


# --- P6 D1：config.yaml 权威 / 损坏三要素 ---


def test_main_boots_from_config_yaml_first_available_profile(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """config.yaml 存在 → 第一个有 key 的档案作为启动档案（env 不再必需）。"""
    calls: list[dict[str, Any]] = []

    def fake_run(app: FastAPI, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(server_main.uvicorn, "run", fake_run)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML, encoding="utf-8")

    exit_code = server_main.main(["--config", str(config_path), "--stories-root", str(tmp_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "deepseek-chat" in out
    assert "deepseek" in out
    # /api/providers 呈现档案目录：key 脱敏、无 key 档案 available=False
    from fastapi.testclient import TestClient

    client = TestClient(calls[0]["app"])
    body = client.get("/api/providers").json()
    assert [p["name"] for p in body["providers"]] == ["deepseek", "nokey"]
    deepseek = body["providers"][0]
    assert deepseek["available"] is True
    assert deepseek["key_hint"] == "…5678"
    assert "sk-direct-12345678" not in str(body)  # key 只进不出
    assert body["providers"][1]["available"] is False
    # 启动模型来自档案 default_model（state 端点经真实 registry 装配会话）
    (tmp_path / "snowstorm").mkdir()
    state = client.get("/api/snowstorm/state").json()
    assert state["model"] == "deepseek-chat"


def test_main_boots_unconfigured_when_no_profile_has_key(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """空启动：config.yaml 档案都缺 key 时服务照起（未配置模式），设置页配置后再对话。"""
    calls: list[FastAPI] = []
    monkeypatch.setattr(server_main.uvicorn, "run", lambda app, **k: calls.append(app))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "providers:\n  - name: nokey\n    base_url: https://api.example.com/v1\n",
        encoding="utf-8",
    )

    exit_code = server_main.main(["--config", str(config_path), "--stories-root", str(tmp_path)])

    assert exit_code == 0
    assert calls, "服务应照常启动"
    out = capsys.readouterr().out
    assert "未配置模式" in out
    assert "怎样算对" in out
    assert "模型：未配置" in out


def test_main_boots_unconfigured_when_boot_profile_lacks_model(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """空启动：引导档案有 key 但没配 default_model——服务照起，告警给指引。"""
    calls: list[FastAPI] = []
    monkeypatch.setattr(server_main.uvicorn, "run", lambda app, **k: calls.append(app))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "providers:\n"
        "  - name: keyless-model\n"
        "    base_url: https://api.example.com/v1\n"
        "    api_key: sk-direct-12345678\n",
        encoding="utf-8",
    )

    exit_code = server_main.main(["--config", str(config_path), "--stories-root", str(tmp_path)])

    assert exit_code == 0
    assert calls, "服务应照常启动"
    out = capsys.readouterr().out
    assert "没有配置模型名" in out
    assert "未配置模式" in out
    assert "--model" in out


def test_main_boots_with_config_dir_and_static_dir(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """P9 T9：--config-dir 提供档案、--static-dir 托管前端产物。"""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><title>HastingsShell</title>", encoding="utf-8"
    )

    calls: list[FastAPI] = []
    monkeypatch.setattr(server_main.uvicorn, "run", lambda app, **k: calls.append(app))

    exit_code = server_main.main(
        [
            "--config-dir",
            str(config_dir),
            "--static-dir",
            str(dist),
            "--stories-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    from fastapi.testclient import TestClient

    client = TestClient(calls[0])
    assert "HastingsShell" in client.get("/").text
    # API 仍优先于静态挂载
    assert client.get("/api/health").json() == {"status": "ok"}


def test_main_exits_when_config_and_config_dir_both_given(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """两参数互斥：退出码非 0 且给出口径。"""
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *a, **k: None)
    exit_code = server_main.main(
        [
            "--config",
            "a.yaml",
            "--config-dir",
            str(tmp_path),
            "--stories-root",
            str(tmp_path),
        ]
    )
    assert exit_code == 2
    assert "只能传一个" in capsys.readouterr().out


def test_main_exits_when_static_dir_missing(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """显式 static 目录不存在：环境故障口径退出，不静默退化为纯 API。"""
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *a, **k: None)
    exit_code = server_main.main(
        [
            "--config",
            str(tmp_path / "absent.yaml"),
            "--static-dir",
            str(tmp_path / "nope"),
            "--stories-root",
            str(tmp_path),
        ]
    )
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "前端目录不存在" in out
    assert "怎样算对" in out


def test_main_exits_with_three_element_message_on_corrupt_config(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *a, **k: None)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("- a\n- b\n", encoding="utf-8")

    exit_code = server_main.main(["--config", str(config_path), "--stories-root", str(tmp_path)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "根节点" in out
    assert "为什么错" in out
    assert "怎样算对" in out
