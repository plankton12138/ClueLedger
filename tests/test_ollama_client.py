"""OllamaClient 单测（本地模型，D11）：MockTransport 钉住帧解析与三态检测。

泄压阀三情形逐条钉住：
- 参数违规（模型名不合法）→ is_valid_model_name 拒绝；
- Ollama 侧报错（pull error 帧 / delete 404）→ OllamaError 三要素归因透传；
- 环境故障（连不上）→ 502「无法连接」归因。
detect 三态是结果数据不是异常：running / installed_not_running / not_installed。
"""

from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from hastings.server.ollama import (
    DEFAULT_OLLAMA_BASE_URL,
    OllamaClient,
    OllamaError,
    is_valid_model_name,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> OllamaClient:
    return OllamaClient(DEFAULT_OLLAMA_BASE_URL, transport=httpx.MockTransport(handler))


def _refusing_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


# --- is_valid_model_name：pull/delete 共用的入口守门 ---


@pytest.mark.parametrize(
    "name", ["qwen3:4b", "llama3.2", "qwen2.5:3b-instruct", "hf.co/Qwen/Qwen3-4B"]
)
def test_valid_model_names(name: str) -> None:
    assert is_valid_model_name(name)


@pytest.mark.parametrize("name", ["", ":tag", "has space", "../escape", "模型"])
def test_invalid_model_names(name: str) -> None:
    assert not is_valid_model_name(name)


# --- version：探活 ---


@pytest.mark.anyio
async def test_version_returns_version_string() -> None:
    client = _client(lambda _: httpx.Response(200, json={"version": "0.5.7"}))

    assert await client.version() == "0.5.7"


@pytest.mark.anyio
async def test_version_unreachable_raises_502_with_three_parts() -> None:
    client = _client(_refusing_handler)

    with pytest.raises(OllamaError) as excinfo:
        await client.version()

    assert excinfo.value.status_code == 502
    assert "无法连接" in excinfo.value.detail
    assert "怎样算对" in excinfo.value.detail  # 三要素齐备


# --- tags：已装模型列表（空列表是合法空结果） ---


@pytest.mark.anyio
async def test_tags_parses_models_with_size() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen3:4b", "size": 2_600_000_000}]})

    models = await _client(handler).tags()

    assert [(m.name, m.size_bytes) for m in models] == [("qwen3:4b", 2_600_000_000)]


@pytest.mark.anyio
async def test_tags_missing_models_key_is_legal_empty() -> None:
    models = await _client(lambda _: httpx.Response(200, json={"unexpected": True})).tags()

    assert models == []


# --- pull：NDJSON 进度流归一化 + error 帧 ---


def _pull_response(lines: list[str]) -> httpx.Response:
    return httpx.Response(200, content=("\n".join(lines) + "\n").encode("utf-8"))


async def _collect(iterator: AsyncIterator[object]) -> list[object]:
    return [frame async for frame in iterator]


@pytest.mark.anyio
async def test_pull_yields_normalized_progress_frames() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pull"
        body = request.read()
        assert b"qwen3:4b" in body
        return _pull_response(
            [
                '{"status": "pulling manifest"}',
                '{"status": "downloading sha256:abc", "total": 100, "completed": 50}',
                '{"status": "success"}',
            ]
        )

    frames = await _collect(_client(handler).pull("qwen3:4b"))

    assert [(f.status, f.completed, f.total) for f in frames] == [  # type: ignore[attr-defined]
        ("pulling manifest", None, None),
        ("downloading sha256:abc", 50, 100),
        ("success", None, None),
    ]


@pytest.mark.anyio
async def test_pull_error_frame_raises_with_ollama_message() -> None:
    client = _client(
        lambda _: _pull_response(['{"error": "pull model manifest: file does not exist"}'])
    )

    with pytest.raises(OllamaError) as excinfo:
        await _collect(client.pull("no-such:model"))

    assert excinfo.value.status_code == 502
    assert "pull model manifest" in excinfo.value.detail
    assert "断点续传" in excinfo.value.detail


@pytest.mark.anyio
async def test_pull_http_404_maps_to_502_with_hint() -> None:
    client = _client(lambda _: httpx.Response(404, json={"error": "model not found"}))

    with pytest.raises(OllamaError) as excinfo:
        await _collect(client.pull("ghost"))

    assert excinfo.value.status_code == 502
    assert "拉取请求被拒绝" in excinfo.value.detail


# --- delete：404 / 上游故障 / 成功 ---


@pytest.mark.anyio
async def test_delete_success_is_silent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(200)

    await _client(handler).delete_model("qwen3:4b")


@pytest.mark.anyio
async def test_delete_missing_model_raises_404() -> None:
    client = _client(lambda _: httpx.Response(404))

    with pytest.raises(OllamaError) as excinfo:
        await client.delete_model("ghost")

    assert excinfo.value.status_code == 404
    assert "未安装" in excinfo.value.detail


@pytest.mark.anyio
async def test_delete_upstream_failure_raises_502() -> None:
    client = _client(lambda _: httpx.Response(500, text="locked"))

    with pytest.raises(OllamaError) as excinfo:
        await client.delete_model("qwen3:4b")

    assert excinfo.value.status_code == 502


# --- detect：三态是结果数据 ---


@pytest.mark.anyio
async def test_detect_running_when_version_reachable() -> None:
    client = _client(lambda _: httpx.Response(200, json={"version": "0.5.7"}))

    state, version = await client.detect(lambda: False)

    assert (state, version) == ("running", "0.5.7")


@pytest.mark.anyio
async def test_detect_installed_not_running() -> None:
    client = _client(_refusing_handler)

    state, version = await client.detect(lambda: True)

    assert (state, version) == ("installed_not_running", "")


@pytest.mark.anyio
async def test_detect_not_installed() -> None:
    client = _client(_refusing_handler)

    state, version = await client.detect(lambda: False)

    assert (state, version) == ("not_installed", "")
