"""Ollama REST 端点测试（本地模型，D11）：三态 status / 目录 / 已装 / 删除。

下载与调用测试走 /api/local-model-tasks 任务记录端点（V03-1.4，见
test_server_local_model_tasks.py）——原 /api/ollama/pull 纯转发流已删除收敛。
泄压阀三情形在 HTTP 层的落点逐条钉住：
- 参数违规（模型名不合法）→ 422 三要素；
- 环境故障（Ollama 不可达 / 上游报错）→ 502 归因；
- status 恒 200——三态是结果数据不是错误。
「设为当前模型」衔接：PUT /api/providers/ollama（no_auth）→ available 无 key 也为真。
"""

from collections.abc import Callable
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from hastings.server.app import create_app
from test_server_app import stub_registry

Handler = Callable[[httpx.Request], httpx.Response]

_INSTALL_URL = "https://ollama.com/download"


def _ollama_app(handler: Handler, installed_probe: Callable[[], bool] | None = None) -> TestClient:
    return TestClient(
        create_app(
            stub_registry(Path(".")),
            ollama_transport=httpx.MockTransport(handler),
            installed_probe=installed_probe,
        )
    )


def _version_ok(handler: Handler) -> Handler:
    """包一层：/api/version 探活通，其余路径交给原 handler。"""

    def wrapped(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.7"})
        return handler(request)

    return wrapped


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


# --- GET /api/ollama/status：三态是结果数据，恒 200 ---


def test_ollama_status_running(tmp_path: Path) -> None:
    client = _ollama_app(lambda _: httpx.Response(200, json={"version": "0.5.7"}))

    response = client.get("/api/ollama/status")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "running"
    assert body["version"] == "0.5.7"
    assert body["install_url"] == _INSTALL_URL
    assert body["base_url"] == "http://localhost:11434"


def test_ollama_status_installed_not_running(tmp_path: Path) -> None:
    client = _ollama_app(_refuse, installed_probe=lambda: True)

    body = client.get("/api/ollama/status").json()

    assert body == {
        "state": "installed_not_running",
        "version": "",
        "base_url": "http://localhost:11434",
        "install_url": _INSTALL_URL,
    }


def test_ollama_status_not_installed(tmp_path: Path) -> None:
    client = _ollama_app(_refuse, installed_probe=lambda: False)

    assert client.get("/api/ollama/status").json()["state"] == "not_installed"


# --- GET /api/ollama/catalog：纯静态预置清单 ---


def test_ollama_catalog_lists_qwen_presets(tmp_path: Path) -> None:
    body = _ollama_app(_refuse).get("/api/ollama/catalog").json()

    names = [entry["name"] for entry in body["catalog"]]
    assert "qwen3:4b" in names
    assert all("size_label" in entry and "params" in entry for entry in body["catalog"])


# --- GET /api/ollama/models：已装列表；不可达 502 ---


def test_ollama_models_lists_installed(tmp_path: Path) -> None:
    client = _ollama_app(
        _version_ok(
            lambda _: httpx.Response(200, json={"models": [{"name": "qwen3:4b", "size": 100}]})
        )
    )

    assert client.get("/api/ollama/models").json() == {
        "models": [{"name": "qwen3:4b", "size_bytes": 100}]
    }


def test_ollama_models_unreachable_returns_502(tmp_path: Path) -> None:
    response = _ollama_app(_refuse).get("/api/ollama/models")

    assert response.status_code == 502
    assert "无法连接" in response.json()["detail"]


# --- DELETE /api/ollama/models/{name}：204 / 404 / 422 ---


def test_ollama_delete_success_returns_204(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(200)

    response = _ollama_app(_version_ok(handler)).delete("/api/ollama/models/qwen3:4b")

    assert response.status_code == 204


def test_ollama_delete_missing_model_returns_404(tmp_path: Path) -> None:
    response = _ollama_app(_version_ok(lambda _: httpx.Response(404))).delete(
        "/api/ollama/models/ghost"
    )

    assert response.status_code == 404
    assert "未安装" in response.json()["detail"]


def test_ollama_delete_invalid_name_returns_422(tmp_path: Path) -> None:
    response = _ollama_app(_version_ok(lambda _: httpx.Response(200))).delete(
        "/api/ollama/models/bad name"
    )

    assert response.status_code == 422


# --- provider 衔接：no_auth 档案无 key 也 available ---


def test_upsert_ollama_profile_no_auth_is_available(tmp_path: Path) -> None:
    client = _ollama_app(_refuse)

    saved = client.put(
        "/api/providers/ollama",
        json={
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "api_key_env": "",
            "default_model": "qwen3:4b",
            "no_auth": True,
        },
    )

    assert saved.status_code == 200
    body = saved.json()
    assert body["available"] is True
    assert body["no_auth"] is True
    assert body["key_hint"] == ""
    names = [p["name"] for p in client.get("/api/providers").json()["providers"]]
    assert "ollama" in names
