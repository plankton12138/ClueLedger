"""本地模型任务记录（V03-1.4）：下载 / 调用测试的应用侧协调服务。

计划 §6.5 补充合同：现有 ``/api/ollama/pull`` 是纯转发流——前端断开即断流，
应用侧没有任务记录，「离开后恢复真实阶段」无从谈起。本服务把底层真实进度
映射为任务阶段，核心不变量：

- **下载完成 ≠ 加载完成 ≠ 测试通过**：download 终态只说明模型文件已就位；
  可用性由 test 动作（经已保存连接跑一次最小推理）证实；
- **阶段无进度数时只显示阶段**：``percent=None`` 是合法值，不是 0 也不是 100；
- **恢复时重检实际状态**：读路径对未终态任务重探 Ollama（服务不可达 → 环境
  失败归因；模型已出现在已装列表 → 如实升级 succeeded），不凭旧百分比宣称
  运行中；
- **任务记录不含凭据**（连接快照脱敏白名单：provider/base_url/model）。

幂等对齐 ``application/idempotency.py``：同 request_id 同指纹回放原任务；
同键不同内容 ``OPERATION_KEY_REUSED``。同一 model+action+连接已有运行中任务
时返回原任务（Ollama 侧 pull 本就串行，重复发起只会排队）。

任务记录为进程内存储（与 JobStore 先例一致）：进程重启即清零，恢复语义由
读路径重检兜底；跨重启持久化归 V03-8 恢复演练（留白）。

执行器全部以 callable 注入（download/test/recheck），本模块不依赖任何 HTTP
客户端——REST 层负责把 OllamaClient 与 build_provider 适配进来。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from hastings.application.errors import ViolationError
from hastings.application.idempotency import operation_key_reused, request_fingerprint
from hastings.application.ports.clock import Clock

__all__ = [
    "CANCEL_SCOPE_TEST_DETAIL",
    "DownloadFrame",
    "LocalModelConnection",
    "LocalModelTask",
    "LocalModelTaskService",
    "percent_for_frame",
    "stage_for_frame",
]

TaskAction = Literal["download", "test"]
TaskState = Literal["queued", "running", "succeeded", "failed", "stopped"]
_TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "stopped"})

DOWNLOAD_DONE_DETAIL = (
    "模型文件下载完成。注意：下载完成不等于加载完成，更不等于测试通过——"
    "可发起「调用测试」实际跑一次推理验证可用性。"
)
_RECHECK_FOUND_DETAIL = (
    "重检发现模型已在已装列表中（此前下载在 Ollama 侧继续并完成），"
    "任务如实升级为完成；是否可用仍以调用测试为准。"
)
_CANCEL_SCOPE_DOWNLOAD = (
    "应用侧已停止等待本次下载；Ollama 侧可能仍在继续（已下载分块保留，"
    "重新发起同名下载将从断点续传）。取消范围仅限本应用发起的这次请求，"
    "不影响其他进程或已完成的下载。"
)
_CANCEL_SCOPE_TEST = (
    "应用侧已停止等待本次调用测试；Ollama 侧可能仍在生成。取消范围仅限本应用发起的这次测试请求。"
)
#: test 动作取消范围文案（REST 层 cancel 响应共用同一口径，导出供测试对照）。
CANCEL_SCOPE_TEST_DETAIL = _CANCEL_SCOPE_TEST


@dataclass(frozen=True, slots=True)
class DownloadFrame:
    """底层下载进度的一帧归一化数据（REST 层从 OllamaClient.pull 适配）。"""

    status: str
    completed: int | None = None
    total: int | None = None


@dataclass(frozen=True, slots=True)
class LocalModelConnection:
    """任务绑定的本地连接快照：只含非敏感定位信息，绝不含 Key。"""

    provider: str  # 连接（provider 档案）名，如 "ollama"
    base_url: str  # Ollama 服务根地址（不含 /v1 后缀）


@dataclass(frozen=True, slots=True)
class LocalModelTask:
    """一条本地模型任务记录：阶段即事实，百分比只是可有可无的展示数据。"""

    task_id: str
    request_id: str
    fingerprint: str
    action: TaskAction
    model: str
    provider: str
    base_url: str
    state: TaskState
    stage: str
    percent: int | None
    detail: str
    created_at: datetime
    updated_at: datetime
    error: str | None = None


def stage_for_frame(frame: DownloadFrame) -> str:
    """底层 status → 展示阶段：无进度数时只显示阶段（四透镜③：未知进度不编造）。"""
    status = frame.status
    if status.startswith("pulling"):
        return "准备中"
    if status.startswith("downloading"):
        return "下载中"
    if status.startswith("verifying"):
        return "校验中"
    if status.startswith("writing"):
        return "写入清单"
    if status.startswith("removing"):
        return "清理"
    return status or "下载中"


def percent_for_frame(frame: DownloadFrame) -> int | None:
    """completed/total → 百分比；缺任一（或 total 非正）返回 None，不伪造进度。"""
    if frame.completed is None or frame.total is None or frame.total <= 0:
        return None
    return min(100, max(0, round(frame.completed / frame.total * 100)))


#: 下载执行器：(model, base_url) → 进度帧异步流；异常上抛由服务归入 failed。
DownloadExecutor = Callable[[str, str], AsyncIterator[DownloadFrame]]
#: 调用测试执行器：(provider, model) → 模型回复文本；异常上抛由服务归入 failed。
TestExecutor = Callable[[str, str], Awaitable[str]]
#: 状态重检探针：(model, base_url) → (服务可达, 模型已装)。
RecheckProbe = Callable[[str, str], Awaitable[tuple[bool, bool]]]
#: 任务 ID 生成器（REST 层注入 UUID 工厂，保持应用层无随机源约定）。
IdGenerator = Callable[[], str]


class LocalModelTaskService:
    """进程内任务注册表 + 后台执行：所有读路径都先重检再回答。"""

    def __init__(
        self,
        *,
        clock: Clock,
        new_task_id: IdGenerator,
        download_executor: DownloadExecutor,
        test_executor: TestExecutor,
        recheck: RecheckProbe,
    ) -> None:
        self._clock = clock
        self._new_task_id = new_task_id
        self._download_executor = download_executor
        self._test_executor = test_executor
        self._recheck = recheck
        self._tasks: dict[str, LocalModelTask] = {}
        self._receipts: dict[str, tuple[str, str]] = {}  # request_id → (指纹, task_id)
        self._running: dict[str, asyncio.Task[None]] = {}

    # -- 写入口 ---------------------------------------------------------------

    async def create(
        self,
        *,
        request_id: str,
        action: TaskAction,
        model: str,
        connection: LocalModelConnection,
    ) -> tuple[LocalModelTask, bool]:
        """创建任务并后台执行；返回 (任务, 是否新建)。

        幂等：同 request_id 同指纹 → 原任务回放（created=False）；同键不同
        内容 → ``OPERATION_KEY_REUSED``。同 model+action+连接已有运行中任务 →
        返回原任务（Ollama pull 串行，重复发起只排队，不如复用）。
        """
        fingerprint = request_fingerprint(
            {
                "action": action,
                "model": model,
                "connection": {"provider": connection.provider, "base_url": connection.base_url},
            }
        )
        prior = self._receipts.get(request_id)
        if prior is not None:
            prior_fingerprint, prior_task_id = prior
            if prior_fingerprint != fingerprint:
                raise operation_key_reused(
                    request_id,
                    where=f"local-model-task/{prior_task_id}",
                    original={"task_id": prior_task_id},
                )
            return self._tasks[prior_task_id], False
        for task in list(self._tasks.values()):
            if (
                task.action == action
                and task.model == model
                and task.provider == connection.provider
                and task.state not in _TERMINAL
            ):
                self._receipts[request_id] = (fingerprint, task.task_id)
                return task, False
        now = self._clock.now()
        task = LocalModelTask(
            task_id=self._new_task_id(),
            request_id=request_id,
            fingerprint=fingerprint,
            action=action,
            model=model,
            provider=connection.provider,
            base_url=connection.base_url,
            state="queued",
            stage="排队中",
            percent=None,
            detail="",
            created_at=now,
            updated_at=now,
        )
        self._tasks[task.task_id] = task
        self._receipts[request_id] = (fingerprint, task.task_id)
        self._spawn(task.task_id)
        return task, True

    async def cancel(self, task_id: str) -> LocalModelTask:
        """取消任务；终态任务不可取消（409 语义由 REST 层映射）。"""
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        if task.state in _TERMINAL:
            raise ViolationError(
                code="LOCAL_MODEL_TASK_CONFLICT",
                where=f"local-model-task/{task_id}",
                why=f"任务已是终态（{task.state}），无可取消的内容",
                fix="重新读取任务状态确认结果；需要新操作请用新的 request_id 发起",
            )
        running = self._running.get(task_id)
        if running is not None:
            running.cancel()
            # 等待执行协程收尾，保证返回给调用方的记录已是 stopped（取消范围
            # 文案由执行协程的取消分支写入——取消语义只在一处定义）。
            with contextlib.suppress(BaseException):
                await running  # 取消/执行异常都已在记录中归因
        if self._tasks[task_id].state not in _TERMINAL:
            # 兜底：任务尚未起跑即被取消（协程未执行，取消分支没机会落账），
            # 或取消完成后记录仍非终态——直接写入，取消语义同样只此一处。
            cancel_scope = (
                _CANCEL_SCOPE_DOWNLOAD if task.action == "download" else _CANCEL_SCOPE_TEST
            )
            self._update(task_id, state="stopped", stage="已取消", detail=cancel_scope)
        return self._tasks[task_id]

    # -- 读路径（读时重检）-----------------------------------------------------

    async def read(self, task_id: str) -> LocalModelTask | None:
        """按 id 回读；不存在返回 None（合法空结果）。运行中任务先重检。"""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if task.state in ("queued", "running"):
            task = await self._recheck_task(task)
        return task

    async def active(self) -> list[LocalModelTask]:
        """未终态任务列表（恢复入口）：逐个重检实际状态后返回。"""
        result: list[LocalModelTask] = []
        for task in list(self._tasks.values()):
            if task.state in ("queued", "running"):
                result.append(await self._recheck_task(task))
        return result

    async def _recheck_task(self, task: LocalModelTask) -> LocalModelTask:
        """重检：服务不可达 → 环境失败归因；模型已在已装列表 → 如实升级。

        探针自身故障不改变记录（如实保留当前状态，不凭猜测升降级）。
        """
        try:
            reachable, installed = await self._recheck(task.model, task.base_url)
        except Exception:  # noqa: BLE001 —— 探针故障不改变记录
            return task
        if not reachable:
            return self._update(
                task.task_id,
                state="failed",
                stage="环境失败",
                error=(
                    f"重检无法连接本地模型服务（{task.base_url}）。"
                    "为什么错：任务进行中服务变得不可达（Ollama 退出或端口变更）。"
                    "怎样算对：启动 Ollama 后重新发起任务；本次不凭旧进度宣称仍在运行。"
                ),
            )
        if task.action == "download" and installed and task.state != "succeeded":
            return self._update(
                task.task_id,
                state="succeeded",
                stage="下载完成",
                percent=100,
                detail=_RECHECK_FOUND_DETAIL,
            )
        return task

    # -- 后台执行 --------------------------------------------------------------

    def _spawn(self, task_id: str) -> None:
        task = self._tasks[task_id]
        runner = self._run_download if task.action == "download" else self._run_test
        aio_task = asyncio.create_task(runner(task_id), name=f"local-model-task/{task_id}")
        self._running[task_id] = aio_task

        def _done(t: asyncio.Task[None]) -> None:
            self._running.pop(task_id, None)
            if not t.cancelled() and t.exception() is not None:
                t.exception()  # 取出异常避免 "never retrieved" 告警（已落记录）

        aio_task.add_done_callback(_done)

    async def _run_download(self, task_id: str) -> None:
        task = self._tasks[task_id]
        self._update(task_id, state="running", stage="准备中")
        try:
            async for frame in self._download_executor(task.model, task.base_url):
                if frame.status == "success":
                    continue  # 终态由流自然结束统一落账，避免双写口径
                if not frame.status:
                    continue  # 坏帧不致命
                self._update(
                    task_id,
                    state="running",
                    stage=stage_for_frame(frame),
                    percent=percent_for_frame(frame),
                )
            self._update(
                task_id,
                state="succeeded",
                stage="下载完成",
                percent=100,
                detail=DOWNLOAD_DONE_DETAIL,
            )
        except asyncio.CancelledError:
            self._update(
                task_id,
                state="stopped",
                stage="已取消",
                detail=_CANCEL_SCOPE_DOWNLOAD,
            )
            raise
        except Exception as exc:  # noqa: BLE001 —— 执行失败统一落记录（三要素由适配层保证）
            self._update(task_id, state="failed", stage="下载失败", error=str(exc))

    async def _run_test(self, task_id: str) -> None:
        task = self._tasks[task_id]
        self._update(task_id, state="running", stage="调用测试中")
        try:
            reply = await self._test_executor(task.provider, task.model)
            if not reply.strip():
                self._update(
                    task_id,
                    state="failed",
                    stage="测试未通过",
                    error=(
                        "[CHAT_EMPTY_COMPLETION] 在 local-model-test："
                        "模型正常结束但没有返回任何正文。为什么错：连接与推理可达，"
                        "输出为空。怎样算对：重试一次；仍为空则检查该模型是否适合对话任务。"
                    ),
                )
            else:
                self._update(
                    task_id,
                    state="succeeded",
                    stage="测试通过",
                    detail=(
                        f"推理测试通过（模型返回 {len(reply)} 字符）。"
                        "该连接经真实调用验证可用，可用于写作任务。"
                    ),
                )
        except asyncio.CancelledError:
            self._update(task_id, state="stopped", stage="已取消", detail=_CANCEL_SCOPE_TEST)
            raise
        except Exception as exc:  # noqa: BLE001
            self._update(task_id, state="failed", stage="测试未通过", error=str(exc))

    # -- 内部 ------------------------------------------------------------------

    def _update(self, task_id: str, **changes: object) -> LocalModelTask:
        task = self._tasks[task_id]
        updated = replace(task, updated_at=self._clock.now(), **changes)  # type: ignore[arg-type]
        self._tasks[task_id] = updated
        return updated
