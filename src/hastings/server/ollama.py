"""Ollama 本地模型接入（服务级）。

一个模块收敛三件事：
- 预置模型清单（Qwen 小杯系列，纯静态数据——体积/定位由服务端管理，
  前端只展示，与 PROVIDER_CATALOG 同一治理口径）；
- OllamaClient：对 Ollama 本地服务（默认 localhost:11434）的 async HTTP
  封装——version / tags / pull（NDJSON 流）/ delete；
- 三态检测：running（/api/version 通）/ installed_not_running（连不通但
  本机装了 ollama）/ not_installed。检测是数据不是错误——前端据此切换
  「安装引导 / 启动提示 / 可用」三种 UI。

泄压阀三情形的落点：
- 参数违规（模型名不合法）→ 上抛带 422 的 OllamaError（REST 层转 HTTP）；
- 合法但 Ollama 侧报错（pull 的 error 帧、delete 404）→ 归因透传不吞；
- 环境故障（连不上、响应非 JSON）→ 502 三要素（哪里错/为什么错/怎样算对）。

transport 参数仅供测试注入 httpx.MockTransport（镜像 model_transport 惯例），
installed_probe 仅供测试钉三态（默认探测 PATH 与 Windows 安装位置）。

所有请求 trust_env=False：Windows 上 httpx 经 urllib.getproxies() 会读到
系统代理注册表（Clash 等开启「系统代理」时），localhost 请求被路由进代理
而超时——误判为「服务未运行」。本地回环永远不该走代理。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import httpx

DEFAULT_OLLAMA_BASE_URL: Final = "http://localhost:11434"
OLLAMA_INSTALL_URL: Final = "https://ollama.com/download"

# 常规请求（version/tags/delete）超时；pull 是流式长任务，读超时单独放宽
# （Ollama 每个下载块都会推帧，块间隔不会久于读超时）。
_OLLAMA_TIMEOUT: Final = httpx.Timeout(10.0)
_PULL_READ_TIMEOUT: Final = httpx.Timeout(10.0, read=60.0)

# Ollama 模型名：`name[:tag]`，name 允许字母数字开头 + . _ / -（如
# qwen3:4b、llama3.2、hf.co/Qwen/Qwen3-4B）。杜绝空白与注入字符。
_MODEL_NAME_PATTERN: Final = r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}(:[A-Za-z0-9._-]{1,63})?"


@dataclass(frozen=True, slots=True)
class OllamaPreset:
    """预置清单条目：一键下载卡片的展示数据（纯公开信息）。"""

    name: str
    params: str
    size_label: str
    note: str


OLLAMA_MODEL_CATALOG: Final[tuple[OllamaPreset, ...]] = (
    OllamaPreset(name="qwen3:4b", params="4B", size_label="约 2.6 GB", note="质速平衡，推荐默认"),
    OllamaPreset(name="qwen2.5:3b", params="3B", size_label="约 1.9 GB", note="低配机器友好"),
    OllamaPreset(name="qwen3:1.7b", params="1.7B", size_label="约 1.4 GB", note="轻量快速"),
)

OllamaState = Literal["running", "installed_not_running", "not_installed"]


def is_valid_model_name(name: str) -> bool:
    """模型名守门：pull/delete 入口共用同一正则（422 指引用）。"""
    return re.fullmatch(_MODEL_NAME_PATTERN, name) is not None


@dataclass(frozen=True, slots=True)
class OllamaModel:
    """一个已安装模型：/api/tags 的 name + size。"""

    name: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class PullProgress:
    """pull 流的一帧归一化进度（Ollama 原始帧的 status/completed/total）。"""

    status: str
    completed: int | None
    total: int | None


class OllamaError(Exception):
    """Ollama 交互失败：携带拟采用的 HTTP 状态码与三要素文案（REST 层透传）。"""

    def __init__(self, *, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _default_installed_probe() -> bool:
    """本机是否装有 Ollama：PATH 命中，或 Windows 默认安装位置存在。"""
    if shutil.which("ollama"):
        return True
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            ollama_exe = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
            if ollama_exe.exists():
                return True
    return False


class OllamaClient:
    """Ollama 本地服务的 async HTTP 封装（无状态，可按请求临时构造）。"""

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    @property
    def base_url(self) -> str:
        return self._base_url

    async def version(self) -> str:
        """GET /api/version → 版本串；连不通/上游故障上抛 OllamaError（502）。"""
        try:
            async with httpx.AsyncClient(
                trust_env=False, timeout=_OLLAMA_TIMEOUT, transport=self._transport
            ) as client:
                response = await client.get(f"{self._base_url}/api/version")
        except httpx.HTTPError as exc:
            raise _unreachable_error(self._base_url, exc) from exc
        if response.status_code >= 400:
            raise OllamaError(
                status_code=502,
                detail=(
                    f"Ollama 探测失败（HTTP {response.status_code}）。"
                    f"为什么错：{self._base_url} 返回了 {response.status_code}。"
                    "怎样算对：确认该地址运行的是 Ollama 服务后重试。"
                ),
            )
        try:
            payload: object = response.json()
        except ValueError as exc:
            raise OllamaError(
                status_code=502,
                detail=(
                    f"Ollama 探测失败（{self._base_url}/api/version）。"
                    "为什么错：响应不是合法 JSON，端点可能不是 Ollama 服务。"
                    "怎样算对：确认 base_url 指向 Ollama 后重试。"
                ),
            ) from exc
        version = payload.get("version") if isinstance(payload, dict) else None
        return version if isinstance(version, str) else ""

    async def tags(self) -> list[OllamaModel]:
        """GET /api/tags → 已安装模型列表；空列表是合法空结果。"""
        try:
            async with httpx.AsyncClient(
                trust_env=False, timeout=_OLLAMA_TIMEOUT, transport=self._transport
            ) as client:
                response = await client.get(f"{self._base_url}/api/tags")
        except httpx.HTTPError as exc:
            raise _unreachable_error(self._base_url, exc) from exc
        if response.status_code >= 400:
            raise OllamaError(
                status_code=502,
                detail=(
                    f"已装模型列表读取失败（HTTP {response.status_code}）。"
                    f"为什么错：{self._base_url} 返回了 {response.status_code}。"
                    "怎样算对：确认 Ollama 服务正常后重试。"
                ),
            )
        try:
            payload: object = response.json()
        except ValueError as exc:
            raise OllamaError(
                status_code=502,
                detail=(
                    f"已装模型列表读取失败（{self._base_url}/api/tags）。"
                    "为什么错：响应不是合法 JSON。"
                    "怎样算对：确认 Ollama 服务正常后重试。"
                ),
            ) from exc
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return []  # 合法但空结果：指引去下载页拉取
        result: list[OllamaModel] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            size = item.get("size")
            if isinstance(name, str):
                result.append(
                    OllamaModel(name=name, size_bytes=size if isinstance(size, int) else 0)
                )
        return result

    async def pull(self, model: str) -> AsyncIterator[PullProgress]:
        """POST /api/pull → NDJSON 进度流归一化；Ollama error 帧 → OllamaError。

        调用方（REST 层）把每帧转成 JSON 行推给前端；流自然结束即下载完成
        （Ollama 最后一帧 status="success"）。
        """
        body: dict[str, object] = {"model": model, "stream": True}
        try:
            async with (
                httpx.AsyncClient(
                    trust_env=False, timeout=_PULL_READ_TIMEOUT, transport=self._transport
                ) as client,
                client.stream("POST", f"{self._base_url}/api/pull", json=body) as response,
            ):
                if response.status_code >= 400:
                    text = (await response.aread()).decode("utf-8", errors="replace")
                    raise OllamaError(
                        status_code=502,
                        detail=(
                            f"模型拉取请求被拒绝（HTTP {response.status_code}）。"
                            f"为什么错：Ollama 返回——{text.strip()[:300]}。"
                            "怎样算对：检查模型名是否拼写正确"
                            "（可在 ollama.com/library 查询），修正后重试。"
                        ),
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    frame = _parse_frame(line)
                    if isinstance(frame, PullProgress):
                        yield frame
        except httpx.HTTPError as exc:
            raise _unreachable_error(self._base_url, exc) from exc

    async def delete_model(self, model: str) -> None:
        """DELETE /api/delete：404（未装）/ 其余上游故障 502 / 成功静默返回。"""
        try:
            async with httpx.AsyncClient(
                trust_env=False, timeout=_OLLAMA_TIMEOUT, transport=self._transport
            ) as client:
                response = await client.request(
                    "DELETE", f"{self._base_url}/api/delete", json={"model": model}
                )
        except httpx.HTTPError as exc:
            raise _unreachable_error(self._base_url, exc) from exc
        if response.status_code == 404:
            raise OllamaError(
                status_code=404,
                detail=(
                    f"模型 {model!r} 未安装，无法删除。"
                    "为什么错：Ollama 侧没有这个模型。"
                    "怎样算对：刷新已装模型列表后再操作。"
                ),
            )
        if response.status_code >= 400:
            raise OllamaError(
                status_code=502,
                detail=(
                    f"模型 {model!r} 删除失败（HTTP {response.status_code}）。"
                    f"为什么错：Ollama 返回——{response.text.strip()[:300]}。"
                    "怎样算对：确认模型未被运行中的会话占用后重试。"
                ),
            )

    async def detect(
        self, installed_probe: Callable[[], bool] | None = None
    ) -> tuple[OllamaState, str]:
        """三态检测：返回 (state, version)。不可达是结果数据，不是异常。"""
        probe = installed_probe if installed_probe is not None else _default_installed_probe
        try:
            version = await self.version()
        except OllamaError:
            return ("installed_not_running" if probe() else "not_installed", "")
        return ("running", version)


def _parse_frame(line: str) -> PullProgress:
    """解析 pull 流的一行 JSON；error 帧转 OllamaError（三要素），坏帧忽略。"""
    try:
        payload: object = json.loads(line)
    except ValueError:
        return PullProgress(status="", completed=None, total=None)  # 坏帧不致命，跳过
    if not isinstance(payload, dict):
        return PullProgress(status="", completed=None, total=None)
    error = payload.get("error")
    if isinstance(error, str) and error:
        raise OllamaError(
            status_code=502,
            detail=(
                f"模型拉取失败：{error}。"
                "为什么错：Ollama 在下载过程中报错（磁盘空间不足/网络中断/模型名无效等）。"
                "怎样算对：按上述原因处理后重试——pull 支持断点续传，"
                "重新发起即可从已下载部分继续。"
            ),
        )
    status = payload.get("status")
    completed = payload.get("completed")
    total = payload.get("total")
    return PullProgress(
        status=status if isinstance(status, str) else "",
        completed=completed if isinstance(completed, int) else None,
        total=total if isinstance(total, int) else None,
    )


def _unreachable_error(base_url: str, exc: httpx.HTTPError) -> OllamaError:
    """连不上 Ollama 的统一 502 三要素归因。"""
    return OllamaError(
        status_code=502,
        detail=(
            f"无法连接 Ollama 服务（{base_url}）。"
            f"为什么错：环境故障——{exc}。"
            "怎样算对：确认 Ollama 已安装并正在运行（终端执行 `ollama serve`"
            "或启动 Ollama 桌面应用）后重试。"
        ),
    )
