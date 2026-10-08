"""Web 服务入口测试（P3 T5）：``python -m hastings.server``。

只测可确定性验证的部分：参数解析、env 缺失时中文指引退出（复用 CLI
的 env_error 模式）、env 齐全时装配链正确并把 app 交给 uvicorn（打桩，
不真启动）。HTTP/WS 行为已由 app/ws/rest 测试覆盖，此处只验接线。
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from pytest import CaptureFixture, MonkeyPatch

import hastings.server.__main__ as server_main
from hastings.bible.store import StoryWorkspace


def test_parser_defaults_and_overrides() -> None:
    args = server_main.build_parser().parse_args(["orient-express"])
    assert args.slug == "orient-express"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.model is None

    args = server_main.build_parser().parse_args(
        ["ghost-train", "--port", "9000", "--host", "0.0.0.0", "--model", "glm-4-plus"]
    )
    assert args.slug == "ghost-train"
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.model == "glm-4-plus"


@pytest.fixture
def clean_env(monkeypatch: MonkeyPatch) -> Iterator[None]:
    """隔离 .env 注入：打桩 load_dotenv 并清空相关变量。"""
    monkeypatch.setattr(server_main, "load_dotenv", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("HASTINGS_MODEL", raising=False)
    yield


def test_main_exits_with_env_hint_when_env_missing(
    clean_env: None, capsys: CaptureFixture[str]
) -> None:
    exit_code = server_main.main(["orient-express"])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "OPENAI_API_KEY" in out  # 中文指引：缺哪个变量、怎样配置
    assert ".env" in out


def test_main_boots_uvicorn_with_assembled_app(
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
        ["orient-express", "--stories-root", str(tmp_path), "--port", "9123"]
    )

    assert exit_code == 0
    assert len(calls) == 1
    app = calls[0]["app"]
    assert isinstance(app, FastAPI)
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 9123
    # 装配链：app.state.session/workspace 绑定同一故事工作区
    session = app.state.session
    workspace = app.state.workspace
    assert session.slug == "orient-express"
    assert session.model == "fake-model"
    assert isinstance(workspace, StoryWorkspace)
    assert workspace.root == tmp_path
    # 三组路由真实可请求（health / REST state；WS 挂载由 test_server_ws 覆盖）
    from fastapi.testclient import TestClient

    client = TestClient(app)
    assert client.get("/api/health").json()["story"] == "orient-express"
    assert client.get("/api/state").json()["bible_types"][0] == "character"
