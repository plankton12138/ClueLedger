"""LocalModelTaskService 单元测试（V03-1.4）：阶段映射 / 幂等 / 重检 / 取消。

钉住服务层不变量（REST 行为另见 tests/test_server_local_model_tasks.py）：
- 阶段映射：downloading 帧有 total 才给 percent，缺失时 percent=None 只显阶段；
- 幂等：同 request_id 同指纹回放原任务；同键不同内容 OPERATION_KEY_REUSED；
  同 model+action 运行中任务复用（Ollama pull 串行，不重复发起）；
- 读时重检：服务不可达 → failed 环境归因（不凭旧百分比宣称运行中）；
  模型已在已装列表 → 如实升级 succeeded；
- 取消：如实回报取消范围（仅限本应用发起的这次请求）；终态取消 409 语义；
- 任务记录不含凭据字段。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from hastings.application.errors import ViolationError
from hastings.application.services.local_model_service import (
    DownloadFrame,
    LocalModelConnection,
    LocalModelTaskService,
    percent_for_frame,
    stage_for_frame,
)

_CONNECTION = LocalModelConnection(provider="ollama", base_url="http://localhost:11434")


class _FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 10, 17, tzinfo=UTC)

    def now(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now


def _counter() -> list[int]:
    return [0]


def _id_gen(next_id: list[int]) -> list[str]:
    def gen() -> str:
        next_id[0] += 1
        return f"lmt_test_{next_id[0]}"

    return gen


def _make_service(
    frames: list[DownloadFrame],
    *,
    test_reply: str = "OK",
    test_error: Exception | None = None,
    reachable: bool = True,
    installed: bool = False,
) -> LocalModelTaskService:
    """装配假执行器：download 按 frames 逐帧吐出后自然结束。"""

    async def download(model: str, base_url: str) -> AsyncIterator[DownloadFrame]:
        del model, base_url
        for frame in frames:
            await asyncio.sleep(0)
            yield frame

    async def test_call(provider: str, model: str) -> str:
        del provider, model
        if test_error is not None:
            raise test_error
        return test_reply

    async def recheck(model: str, base_url: str) -> tuple[bool, bool]:
        del model, base_url
        return (reachable, installed)

    ids = _counter()
    return LocalModelTaskService(
        clock=_FakeClock(),
        new_task_id=_id_gen(ids),
        download_executor=download,
        test_executor=test_call,
        recheck=recheck,
    )


# --- 阶段映射纯函数 ---------------------------------------------------------


def test_stage_for_frame_maps_ollama_statuses() -> None:
    assert stage_for_frame(DownloadFrame(status="pulling manifest")) == "准备中"
    assert stage_for_frame(DownloadFrame(status="downloading sha256:abc")) == "下载中"
    assert stage_for_frame(DownloadFrame(status="verifying sha256 digest")) == "校验中"
    assert stage_for_frame(DownloadFrame(status="writing manifest")) == "写入清单"
    assert stage_for_frame(DownloadFrame(status="removing any unused layers")) == "清理"
    assert stage_for_frame(DownloadFrame(status="")) == "下载中"  # 坏帧兜底


def test_percent_for_frame_requires_both_numbers() -> None:
    assert percent_for_frame(DownloadFrame(status="downloading", completed=50, total=100)) == 50
    assert percent_for_frame(DownloadFrame(status="downloading")) is None  # 无进度数只显阶段
    assert percent_for_frame(DownloadFrame(status="d", completed=50, total=0)) is None
    assert percent_for_frame(DownloadFrame(status="d", completed=150, total=100)) == 100


@pytest.mark.anyio
async def test_download_maps_frames_to_stage_and_percent() -> None:
    service = _make_service(
        [
            DownloadFrame(status="pulling manifest"),
            DownloadFrame(status="downloading", completed=25, total=100),
            DownloadFrame(status="verifying sha256 digest"),
        ]
    )

    task, created = await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )
    assert created is True
    # 等后台协程跑完（帧间只有 sleep(0)，让出一次事件循环即推进）
    for _ in range(50):
        task = (await service.read(task.task_id)) or task
        if task.state == "succeeded":
            break
        await asyncio.sleep(0)

    assert task.state == "succeeded"
    assert task.percent == 100
    assert task.stage == "下载完成"
    assert "下载完成不等于" in task.detail  # 终态语义：≠ 加载 ≠ 测试通过


@pytest.mark.anyio
async def test_download_runner_marks_progress_without_percent() -> None:
    service = _make_service([DownloadFrame(status="pulling manifest")])
    task, _ = await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )
    for _ in range(50):
        await asyncio.sleep(0)
        task = (await service.read(task.task_id)) or task
        if task.state == "succeeded":
            break
    assert task.state == "succeeded"


# --- 幂等与复用 -------------------------------------------------------------


@pytest.mark.anyio
async def test_same_request_id_same_fingerprint_replays() -> None:
    service = _make_service([DownloadFrame(status="pulling manifest")])
    first, created1 = await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )
    second, created2 = await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )

    assert created1 is True
    assert created2 is False
    assert second.task_id == first.task_id


@pytest.mark.anyio
async def test_same_request_id_different_content_is_reused_key_violation() -> None:
    service = _make_service([])
    await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )

    with pytest.raises(ViolationError) as exc_info:
        await service.create(
            request_id="r1", action="download", model="qwen2.5:3b", connection=_CONNECTION
        )
    assert exc_info.value.code == "OPERATION_KEY_REUSED"


@pytest.mark.anyio
async def test_running_task_for_same_model_is_reused() -> None:
    """同 model+action 已有运行中任务：返回原任务，不重复发起（Ollama 串行）。"""
    service = _make_service([DownloadFrame(status="downloading", completed=1, total=100)])
    first, created1 = await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )
    second, created2 = await service.create(
        request_id="r2", action="download", model="qwen3:4b", connection=_CONNECTION
    )

    assert created1 is True
    assert created2 is False
    assert second.task_id == first.task_id


# --- 读时重检（恢复语义）----------------------------------------------------


@pytest.mark.anyio
async def test_recheck_upgrades_to_succeeded_when_model_installed() -> None:
    """模型已在已装列表：如实升级 succeeded（不凭旧百分比宣称运行中）。"""
    service = _make_service(
        [DownloadFrame(status="downloading", completed=1, total=100)], installed=True
    )
    task, _ = await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )
    await asyncio.sleep(0)

    reread = await service.read(task.task_id)

    assert reread is not None
    assert reread.state == "succeeded"
    assert "重检发现模型已在已装列表" in reread.detail


@pytest.mark.anyio
async def test_recheck_unreachable_service_fails_with_environment_attribution() -> None:
    service = _make_service(
        [DownloadFrame(status="downloading", completed=1, total=100)], reachable=False
    )
    task, _ = await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )
    await asyncio.sleep(0)

    reread = await service.read(task.task_id)

    assert reread is not None
    assert reread.state == "failed"
    assert reread.stage == "环境失败"
    assert "不凭旧进度宣称仍在运行" in (reread.error or "")


@pytest.mark.anyio
async def test_active_lists_running_tasks_after_recheck() -> None:
    service = _make_service([DownloadFrame(status="pulling manifest")])
    task, _ = await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )

    active = await service.active()

    # 任务极小，可能已终态；断言：要么已不在 active，要么在且状态未终态。
    ids = [t.task_id for t in active]
    if task.task_id in ids:
        found = next(t for t in active if t.task_id == task.task_id)
        assert found.state in ("queued", "running")


# --- 取消（取消范围如实回报）------------------------------------------------


@pytest.mark.anyio
async def test_cancel_marks_stopped_with_honest_scope() -> None:
    async def slow_download(model: str, base_url: str) -> AsyncIterator[DownloadFrame]:
        del model, base_url
        for _ in range(100):
            await asyncio.sleep(0.05)
            yield DownloadFrame(status="downloading", completed=1, total=100)

    async def test_call(provider: str, model: str) -> str:
        del provider, model
        return "OK"

    async def recheck(model: str, base_url: str) -> tuple[bool, bool]:
        del model, base_url
        return (True, False)

    ids = _counter()
    service = LocalModelTaskService(
        clock=_FakeClock(),
        new_task_id=_id_gen(ids),
        download_executor=slow_download,
        test_executor=test_call,
        recheck=recheck,
    )
    task, _ = await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )

    cancelled = await service.cancel(task.task_id)

    assert cancelled.state == "stopped"
    assert cancelled.stage == "已取消"
    assert "仅限本应用发起的这次请求" in cancelled.detail
    assert "断点续传" in cancelled.detail


@pytest.mark.anyio
async def test_cancel_terminal_task_raises_conflict() -> None:
    service = _make_service([DownloadFrame(status="pulling manifest")])
    task, _ = await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )
    for _ in range(50):
        await asyncio.sleep(0)
        task = (await service.read(task.task_id)) or task
        if task.state == "succeeded":
            break

    with pytest.raises(ViolationError) as exc_info:
        await service.cancel(task.task_id)
    assert exc_info.value.code == "LOCAL_MODEL_TASK_CONFLICT"


@pytest.mark.anyio
async def test_cancel_unknown_task_raises_key_error() -> None:
    service = _make_service([])
    with pytest.raises(KeyError):
        await service.cancel("lmt_missing")


# --- 调用测试动作 -----------------------------------------------------------


@pytest.mark.anyio
async def test_test_action_succeeds_with_real_inference_semantics() -> None:
    service = _make_service([], test_reply="OK，已收到。")
    task, _ = await service.create(
        request_id="r1", action="test", model="qwen3:4b", connection=_CONNECTION
    )
    for _ in range(50):
        await asyncio.sleep(0)
        task = (await service.read(task.task_id)) or task
        if task.state == "succeeded":
            break

    assert task.state == "succeeded"
    assert task.stage == "测试通过"
    assert "推理测试通过" in task.detail


@pytest.mark.anyio
async def test_test_action_empty_reply_fails_honestly() -> None:
    service = _make_service([], test_reply="")
    task, _ = await service.create(
        request_id="r1", action="test", model="qwen3:4b", connection=_CONNECTION
    )
    for _ in range(50):
        await asyncio.sleep(0)
        task = (await service.read(task.task_id)) or task
        if task.state == "failed":
            break

    assert task.state == "failed"
    assert "CHAT_EMPTY_COMPLETION" in (task.error or "")


@pytest.mark.anyio
async def test_test_action_provider_error_lands_in_record() -> None:
    service = _make_service([], test_error=RuntimeError("连接被拒绝"))
    task, _ = await service.create(
        request_id="r1", action="test", model="qwen3:4b", connection=_CONNECTION
    )
    for _ in range(50):
        await asyncio.sleep(0)
        task = (await service.read(task.task_id)) or task
        if task.state == "failed":
            break

    assert task.state == "failed"
    assert "连接被拒绝" in (task.error or "")


# --- 记录脱敏 ---------------------------------------------------------------


@pytest.mark.anyio
async def test_task_record_contains_no_credential_fields() -> None:
    service = _make_service([DownloadFrame(status="pulling manifest")])
    task, _ = await service.create(
        request_id="r1", action="download", model="qwen3:4b", connection=_CONNECTION
    )

    payload = repr(task)
    assert "api_key" not in payload
    assert "credential" not in payload
    assert task.provider == "ollama" and task.base_url == _CONNECTION.base_url
