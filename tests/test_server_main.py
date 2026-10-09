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
    assert args.config is not None

    args = server_main.build_parser().parse_args(
        ["--port", "9000", "--host", "0.0.0.0", "--model", "glm-4-plus", "--config", "my.yaml"]
    )
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.model == "glm-4-plus"
    assert args.config == Path("my.yaml")


def test_find_static_dir(tmp_path: Any) -> None:
    """dist 存在则托管、不存在则 None（纯 API 模式）。"""
    assert server_main.find_static_dir(tmp_path) is None
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    assert server_main.find_static_dir(tmp_path) == dist


@pytest.fixture
def clean_env(monkeypatch: MonkeyPatch) -> Iterator[None]:
    """隔离 .env 注入：打桩 load_dotenv 并清空相关变量。"""
    monkeypatch.setattr(server_main, "load_dotenv", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("HASTINGS_MODEL", raising=False)
    yield


def test_main_exits_with_env_hint_when_env_missing(
    clean_env: None, capsys: CaptureFixture[str], tmp_path: Path
) -> None:
    exit_code = server_main.main(["--config", str(tmp_path / "absent.yaml")])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "OPENAI_API_KEY" in out  # 中文指引：缺哪个变量、怎样配置
    assert ".env" in out


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
            str(tmp_path),
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
    assert registry.root == tmp_path
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


def test_main_exits_when_no_profile_has_key(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *a, **k: None)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "providers:\n  - name: nokey\n    base_url: https://api.example.com/v1\n",
        encoding="utf-8",
    )

    exit_code = server_main.main(["--config", str(config_path), "--stories-root", str(tmp_path)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "没有可用档案" in out
    assert "为什么错" in out
    assert "怎样算对" in out


def test_main_exits_when_boot_profile_lacks_model(
    clean_env: None, monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *a, **k: None)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "providers:\n"
        "  - name: keyless-model\n"
        "    base_url: https://api.example.com/v1\n"
        "    api_key: sk-direct-12345678\n",
        encoding="utf-8",
    )

    exit_code = server_main.main(["--config", str(config_path), "--stories-root", str(tmp_path)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "没有可用的模型名" in out
    assert "default_model" in out


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
