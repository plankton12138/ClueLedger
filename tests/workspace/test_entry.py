"""文件工作区启动入口（0.4 V04-1）。

契约：``--workspace <目录>`` 只装配文件工作区应用——不读 config.yaml、不打开旧工作台
数据库、不挂 ``/api/v2``；与 ``--v2-db`` 同时出现属于意图冲突，按参数错误退出（2）。
默认入口仍是旧工作台，直到 V04-5 切换。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hastings.server.__main__ as server_main
from hastings.server.__main__ import StartupError, assemble_file_workspace


def test_parser_accepts_workspace() -> None:
    assert server_main.build_parser().parse_args([]).workspace is None
    parsed = server_main.build_parser().parse_args(["--workspace", "作品"])
    assert parsed.workspace == Path("作品")


def test_assembly_serves_only_file_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("HASTINGS_STATE_DIR", str(state))
    root = tmp_path / "作品"
    root.mkdir()

    assembled = assemble_file_workspace(workspace=root)
    client = TestClient(assembled.app)

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/file-workspaces").status_code == 200
    # 旧工作台与旧模型路由都不在装配范围：新应用不因模型设置挂载整套旧业务。
    assert client.get("/api/v2/projects").status_code == 404
    assert client.get("/api/model-selection").status_code == 404
    assert assembled.workspace == root.resolve()
    assert assembled.binding_id
    assert (state / "workspaces.json").is_file()
    # 作品目录与状态目录之外没有数据库；文件工作区模式不打开旧工作台。
    assert not [path for path in root.iterdir() if path.suffix in {".db", ".sqlite"}]


def test_assembly_rejects_missing_workspace(tmp_path: Path) -> None:
    with pytest.raises(StartupError):
        assemble_file_workspace(workspace=tmp_path / "不存在")


def test_main_rejects_workspace_with_v2_db(tmp_path: Path) -> None:
    root = tmp_path / "作品"
    root.mkdir()

    assert server_main.main(["--workspace", str(root), "--v2-db", str(tmp_path / "v2.db")]) == 2


def test_main_serves_file_workspace_without_old_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HASTINGS_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "作品"
    root.mkdir()
    captured: dict[str, object] = {}

    def fake_run(app: FastAPI, **kwargs: object) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(server_main.uvicorn, "run", fake_run)

    assert server_main.main(["--workspace", str(root), "--port", "8123"]) == 0
    assert isinstance(captured["app"], FastAPI)
    assert captured["kwargs"] == {"host": "127.0.0.1", "port": 8123, "log_level": "info"}
    assert not (tmp_path / "data").exists()
