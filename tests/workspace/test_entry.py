"""服务启动入口（0.4 默认启动）。

契约：``python -m hastings.server`` 只装配文件工作区应用——不读 ``config.yaml``、
不打开旧工作台数据库、不挂 ``/api/v2`` 与旧模型路由，也没有回到旧应用的参数。
``--workspace`` 只是预先打开一个目录，不是启用开关。退出码：环境故障 1、参数错误 2。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hastings.server.__main__ as server_main
from hastings.infrastructure.credentials import InMemoryCredentialStore
from hastings.server.__main__ import StartupError, assemble_server


@pytest.fixture(autouse=True)
def _isolated_model_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不读仓库里的真实 config.yaml、不碰系统凭据存储、不受本机环境变量影响。"""
    monkeypatch.setattr(server_main, "DEFAULT_CONFIG_PATH", tmp_path / "absent.yaml")
    monkeypatch.setattr(server_main, "build_credential_store", lambda: InMemoryCredentialStore())
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "HASTINGS_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _directory(tmp_path: Path, name: str = "作品") -> Path:
    target = tmp_path / name
    target.mkdir()
    return target


def test_parser_defaults_and_overrides() -> None:
    args = server_main.build_parser().parse_args([])
    assert args.workspace is None
    assert args.static_dir is None
    assert args.host == "127.0.0.1"
    assert args.port == 8000

    args = server_main.build_parser().parse_args(
        ["--workspace", "作品", "--static-dir", "dist", "--port", "9123", "--host", "0.0.0.0"]
    )
    assert args.workspace == Path("作品")
    assert args.static_dir == Path("dist")
    assert args.port == 9123
    assert args.host == "0.0.0.0"


def test_parser_rejects_retired_options() -> None:
    """旧工作台参数已退出：传进来按参数错误处理，不静默忽略。"""
    for retired in ("--v2-db", "--stories-root", "--corpus-root"):
        with pytest.raises(SystemExit) as exited:
            server_main.build_parser().parse_args([retired, "x"])
        assert exited.value.code == 2


def test_parser_accepts_model_options() -> None:
    """模型来源是 0.4 保留的唯一复用：--config 与 --model 仍然可用。"""
    args = server_main.build_parser().parse_args(["--config", "cfg.yaml", "--model", "glm-4"])

    assert args.config == Path("cfg.yaml")
    assert args.model == "glm-4"


def test_find_and_resolve_static_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert server_main.find_static_dir(tmp_path) is None
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    assert server_main.find_static_dir(tmp_path) == dist

    assert server_main.resolve_static_dir(dist) == dist
    monkeypatch.setattr(server_main, "default_static_dir", lambda: None)
    assert server_main.resolve_static_dir(None) is None


def test_assembly_without_workspace_serves_empty_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("HASTINGS_STATE_DIR", str(state))

    assembled = assemble_server()

    assert assembled.workspace is None
    assert assembled.binding_id is None
    assert assembled.state_root == state.resolve()
    client = TestClient(assembled.app)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/file-workspaces").json() == []


def test_assembly_binds_requested_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("HASTINGS_STATE_DIR", str(state))
    root = _directory(tmp_path)

    assembled = assemble_server(workspace=root)

    assert assembled.workspace == root.resolve()
    assert assembled.binding_id
    client = TestClient(assembled.app)
    listed = client.get("/api/file-workspaces").json()
    assert [item["path"] for item in listed] == [str(root.resolve())]


def test_assembly_serves_only_file_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HASTINGS_STATE_DIR", str(tmp_path / "state"))
    root = _directory(tmp_path)

    client = TestClient(assemble_server(workspace=root).app)

    # 旧工作台与旧模型路由都不在装配范围：新应用不因模型设置挂载整套旧业务。
    assert client.get("/api/v2/projects").status_code == 404
    assert client.get("/api/model-selection").status_code == 404
    assert client.get("/api/workspaces").status_code == 404


def test_assembly_creates_no_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASTINGS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    root = _directory(tmp_path)

    assemble_server(workspace=root)

    assert not (tmp_path / "data").exists()


def test_assembly_rejects_missing_workspace(tmp_path: Path) -> None:
    with pytest.raises(StartupError, match="作品目录不存在"):
        assemble_server(workspace=tmp_path / "不存在")


def test_assembly_rejects_file_as_workspace(tmp_path: Path) -> None:
    target = tmp_path / "a.md"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(StartupError, match="不是目录"):
        assemble_server(workspace=target)


def test_assembly_rejects_missing_static_dir(tmp_path: Path) -> None:
    with pytest.raises(StartupError, match="前端目录不存在"):
        assemble_server(static_dir=tmp_path / "nope")


def test_main_runs_uvicorn_with_file_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HASTINGS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(server_main, "default_static_dir", lambda: None)
    root = _directory(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(app: FastAPI, **kwargs: object) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(server_main.uvicorn, "run", fake_run)

    assert server_main.main(["--workspace", str(root), "--port", "8123"]) == 0
    assert isinstance(captured["app"], FastAPI)
    assert captured["kwargs"] == {"host": "127.0.0.1", "port": 8123, "log_level": "info"}
    out = capsys.readouterr().out
    assert str(root.resolve()) in out
    assert "8123" in out


def test_main_starts_without_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HASTINGS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(server_main, "default_static_dir", lambda: None)
    monkeypatch.setattr(server_main.uvicorn, "run", lambda app, **kwargs: None)

    assert server_main.main([]) == 0
    assert "未打开" in capsys.readouterr().out


def test_main_exits_with_environment_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *args, **kwargs: None)

    assert server_main.main(["--workspace", str(tmp_path / "不存在")]) == 1
    assert "怎样算对" in capsys.readouterr().out
