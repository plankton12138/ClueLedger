"""HastingsSession：WritingSession 的标准实现（P3 T2）。

绑定一个 StoryWorkspace 与一个 AgentHarness，负责：
- busy 防护：同一会话同时只允许一个 prompt 流（AgentBusyError 三要素）；
- steer / follow_up / cancel 命令直通 harness；
- 事件订阅直通 harness（每连接一个 listener，多标签页天然 fan-out）。

本模块不 import fastapi——协议无关，纯 asyncio + FakeProvider 可测。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from typing import TYPE_CHECKING

from hastings.bible.store import StoryWorkspace
from hastings.events_store import EventStore, events_file, make_event_persister
from hastings.manuscript.diff import DiffHunk
from hastings.manuscript.write_guard import EditMarkRegistry, RunKindGuard
from hastings.server.provider_config import ProviderBook
from hastings.server.reconcile import NoticeListener, ReconcileNotice, ReconcileQueue
from hastings.server.registry import SessionFactory
from hastings.server.session import AgentBusyError, WritingSession
from hastings.settings import DEFAULT_PROVIDER_PROFILE
from tau_agent.events import AgentEvent
from tau_agent.harness import AgentHarness, AgentHarnessConfig, EventListener
from tau_agent.messages import AgentMessage
from tau_agent.provider import ModelProvider
from tau_agent.types import JSONValue

BUSY_MESSAGE = (
    "prompt 被拒绝：当前有写作流正在进行。"
    "为什么错：同一个故事会话同时只能运行一个写作流。"
    "怎样算对：用 steer 在当前流中插话、用 follow_up 排队下一轮，或先 abort 中止当前流。"
)

COMPACTING_MESSAGE = (
    "prompt 被拒绝：当前正在压缩上下文。"
    "为什么错：压缩会替换会话消息，期间启动写作流会读到不完整的上下文。"
    "怎样算对：等待压缩完成后再发送；压缩失败会保留原会话，可直接重试。"
)


class HastingsSession:
    """WritingSession 的标准实现：绑定一个 StoryWorkspace 与一个 AgentHarness。"""

    def __init__(
        self,
        harness: AgentHarness,
        workspace: StoryWorkspace,
        edit_marks: EditMarkRegistry | None = None,
        run_kind: RunKindGuard | None = None,
        *,
        reconcile_debounce: float = 3.0,
        profile_name: str = DEFAULT_PROVIDER_PROFILE,
    ) -> None:
        self._harness = harness
        self._workspace = workspace
        # 当前 provider 档案名（P6 T2）：跨档案切换的判定依据（同档案换模型热切）。
        self._profile_name = profile_name
        self._compaction_lock = asyncio.Lock()
        # 作者草稿锁注册表（P5 D4）：from_workspace 会把同一实例闭包进
        # write_chapter（agent 侧守门）；REST 锁端点与 PUT 保存经本属性读写。
        # 直接构造（无实参）得到空注册表——CLI 等单 agent 场景锁永不触发。
        self._edit_marks = edit_marks if edit_marks is not None else EditMarkRegistry()
        # 运行类别守卫（P5 T4，D4 末条）：from_workspace 把同一实例闭包进
        # write_chapter——对账轮期间模型写正文被工具层拒绝。
        self._run_kind = run_kind if run_kind is not None else RunKindGuard()
        # 对账队列（P5 T4，D5）：订阅 harness 事件流等空闲，出队即以紧凑
        # 消息 prompt 同一 harness——会话记忆/持久化/事件流零成本复用。
        self._reconcile = ReconcileQueue(
            session=self,
            workspace=workspace,
            run_kind=self._run_kind,
            debounce_seconds=reconcile_debounce,
        )
        # 事件持久化（P6 T4，D4）：events.jsonl 是全量事件帧的可观测性账本，
        # 与 session.jsonl（会话记忆）职责分离；构造时挂载，CLI/Web 共用同一单点
        # （F4 起入库策略收敛到 make_event_persister）。
        self._events = EventStore(events_file(workspace))
        self._unsubscribe_events = harness.subscribe(make_event_persister(self._events))
        self._unsubscribe_notice_events = self._reconcile.subscribe(self._persist_notice)
        # token 用量落账（P10 T12，D11）：每轮 agent_end 记一条 token_usage 事件，
        # 与压缩触发、前端占用共用唯一计数源（context_usage 同一函数）。
        self._unsubscribe_token_events = harness.subscribe(self._on_agent_end)

    @classmethod
    def from_workspace(
        cls,
        workspace: StoryWorkspace,
        provider: ModelProvider,
        model: str,
        *,
        reconcile_debounce: float = 3.0,
        profile_name: str = DEFAULT_PROVIDER_PROFILE,
    ) -> HastingsSession:
        """按 CLI 同款方式装配工具集、system prompt 与 harness。"""
        from hastings.cli import create_writing_tools
        from hastings.manuscript.guards import attach_guards
        from hastings.prompts import build_system_prompt
        from hastings.settings import load_settings, workspace_architecture_version

        edit_marks = EditMarkRegistry()
        run_kind = RunKindGuard()
        # 与 CLI build_harness 同款：项目架构版本在这一处解析并传入，V2 项目装配
        # 拒绝桩。缺 settings.yaml → v1，V1 行为不变（ADR §2.2）。
        tools = create_writing_tools(
            workspace,
            edit_marks=edit_marks,
            run_kind=run_kind,
            architecture_version=workspace_architecture_version(workspace),
        )
        language = load_settings(workspace).writing_language
        harness = AgentHarness(
            AgentHarnessConfig(
                provider=provider,
                model=model,
                system=build_system_prompt(tools, language=language, workspace=workspace),
                tools=tools,
            )
        )
        attach_guards(harness, workspace)
        return cls(
            harness=harness,
            workspace=workspace,
            edit_marks=edit_marks,
            run_kind=run_kind,
            reconcile_debounce=reconcile_debounce,
            profile_name=profile_name,
        )

    @property
    def workspace(self) -> StoryWorkspace:
        return self._workspace

    @property
    def harness(self) -> AgentHarness:
        return self._harness

    @property
    def slug(self) -> str:
        return self._workspace.slug

    @property
    def model(self) -> str:
        return self._harness.config.model

    @property
    def provider_profile(self) -> str:
        return self._profile_name

    @property
    def is_running(self) -> bool:
        return self._harness.is_running

    @property
    def is_compacting(self) -> bool:
        return self._compaction_lock.locked()

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self._harness.config.tools)

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        return self._harness.messages

    @property
    def edit_marks(self) -> EditMarkRegistry:
        return self._edit_marks

    @property
    def run_kind(self) -> RunKindGuard:
        return self._run_kind

    @property
    def events(self) -> EventStore:
        """events.jsonl 账本（P6 T4）：只读分页由 REST 端点直读，此处暴露供接线/测试。"""
        return self._events

    def context_usage(self) -> TokenUsage:
        """当前上下文占用快照（P10 D11）：唯一计数源 count_context_tokens 的封装。

        与 events.jsonl 的 token_usage 事件、压缩触发同源；get_state 与
        每轮 token_usage 推送帧都读它，保证单一事实源。
        """
        from hastings.context_manager import build_token_usage

        return build_token_usage(self._harness, self._workspace)

    async def _on_agent_end(self, event: AgentEvent) -> None:
        """每轮 agent_end 落一条 token_usage 事件（D11）：可观测性的账。

        落账失败绝不能反杀写作流（持久化只是事件流的消费者）——除了
        EventStore 内部的 OSError 兜底，这里再兜一层 settings/计数异常。
        """
        if event.type != "agent_end":
            return
        from hastings.context_manager import record_token_usage

        try:
            await record_token_usage(self._events, self._harness, self._workspace)
        except Exception as exc:  # noqa: BLE001 — 观测失败不断流（四透镜③）
            print(
                "[token] 本轮 token 用量落账失败，写作继续但用量账不完整。"
                f"为什么错：{exc}。"
                "怎样算对：检查 settings.yaml 与磁盘状态；下一轮会重新记录。",
                file=sys.stderr,
            )

    async def _persist_notice(self, notice: ReconcileNotice) -> None:
        """对账进度通知落进事件流（可观测性账本记全貌，含对账三态）。"""
        payload: dict[str, JSONValue] = {
            "kind": notice.kind,
            "chapter": notice.chapter,
            "hunks": notice.hunks,
            "updates": notice.updates,
        }
        if notice.status is not None:
            payload["status"] = notice.status
        await self._events.append("notice", payload)

    async def request_reconcile(self, chapter_id: str, hunks: Sequence[DiffHunk]) -> None:
        await self._reconcile.enqueue(chapter_id, hunks)

    async def resync_reconcile(self, chapter_id: str) -> bool:
        """手动重跑：复用该章最近一次保存的差异（对账失败重试 / 同步按钮）。"""
        return await self._reconcile.resync(chapter_id)

    def apply_writing_language(self, language: str) -> None:
        """切换写作语言并立即重建 system prompt（P6 D3）。

        AgentHarnessConfig 是非 frozen dataclass，且 harness 每轮运行时才读
        config.system——直接赋值即下一轮生效，无需重建会话（守住不改 tau 内核）。
        """
        from hastings.prompts import build_system_prompt

        self._harness.config.system = build_system_prompt(
            self._harness.config.tools, language=language, workspace=self._workspace
        )

    def _refresh_system(self) -> None:
        """每轮 prompt 前刷新 system（P8 T5，B1）：状态注入的唯一刷新点。

        委托 ``prompts.refresh_system_prompt``（P10 D6/L7 三端共享单一事实源）：
        prompt 入口重算即把 story.yaml 阶段与账本实况注入下一轮。运行中不刷新——
        prompt 入口本就有 busy 防护，与「运行中 system 不变」同语义；reconcile
        队列经 session.prompt 同入口，自动获得同一刷新。
        """
        from hastings.prompts import refresh_system_prompt

        refresh_system_prompt(self._harness, self._workspace)

    def apply_model(self, model: str) -> None:
        """同档案热切换模型（P6 D2，与 apply_writing_language 同一手法）。

        config.model 每轮运行时读取，直接赋值即下一轮生效；对话历史原地保留。
        """
        self._harness.config.model = model

    def subscribe_notice(self, listener: NoticeListener) -> Callable[[], None]:
        return self._reconcile.subscribe(listener)

    def prompt(self, content: str) -> AsyncIterator[AgentEvent]:
        if self._harness.is_running:
            raise AgentBusyError(BUSY_MESSAGE)
        if self.is_compacting:
            raise AgentBusyError(COMPACTING_MESSAGE)
        self._refresh_system()
        return self._harness.prompt(content)

    async def auto_compact(self) -> CompactionResult:
        """prompt 入口的两档自动压缩（P10 D8/L9）：超阈值才动，摘要失败不进流。

        ``prompt`` 是同步返回迭代器的签名（WS/reconcile 依赖），故压缩在此独立成
        async 方法，由 WS prompt 入口 await。刷新 system 后再压缩，保证任何正文
        剥离都经会话文件持久化；同一会话的压缩串行，避免并发摘要覆盖彼此结果。
        """
        from hastings.context_manager import compact_context

        async with self._compaction_lock:
            self._refresh_system()
            return await compact_context(self._harness, self._workspace, manual=False)

    async def compact_context(self) -> CompactionResult:
        """手动压缩（P10 T13，D12）：绕过阈值走同一两档原语（含归档重写、事件留痕）。

        与 ``auto_compact`` 共用实现与持久化，手动路径不新增分支——差别仅在
        ``manual=True`` 跳过阈值判断（作者主动回收上下文，即使未超阈值也尽力压缩）。
        """
        from hastings.context_manager import compact_context as run_compaction

        async with self._compaction_lock:
            self._refresh_system()
            return await run_compaction(self._harness, self._workspace, manual=True)

    def steer(self, content: str) -> None:
        self._harness.steer(content)

    def follow_up(self, content: str) -> None:
        self._harness.follow_up(content)

    def cancel(self) -> None:
        self._harness.cancel()

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        return self._harness.subscribe(listener)


# 静态结构自检：实现与 WritingSession 协议漂移时 mypy strict 即报错
if TYPE_CHECKING:
    from hastings.context_manager import CompactionResult, TokenUsage

    def _conforms(session: HastingsSession) -> WritingSession:
        return session


def default_session_factory(book: ProviderBook) -> SessionFactory:
    """server 入口的默认装配工厂（P4 D1/D4，P6 T2 档案解析）。

    registry 只管生命周期；本工厂负责"一个 slug 怎么变成可用会话"——
    按工作区设置解析 provider 档案与模型（未知或缺 key 的档案回落引导档案，
    服务重启后仍能打开），会话历史从 stories/<slug>/session.jsonl 回放，
    新消息经 message_end 落盘。
    """

    async def factory(workspace: StoryWorkspace) -> WritingSession:
        from hastings.session_persist import restore_and_attach
        from hastings.settings import load_settings

        settings = load_settings(workspace)
        resolved = book.resolve(settings.provider_profile, settings.model)
        session = HastingsSession.from_workspace(
            workspace,
            provider=resolved.provider,
            model=resolved.model,
            profile_name=resolved.profile_name,
        )
        await restore_and_attach(workspace, session.harness, on_warning=print)
        return session

    return factory


__all__ = ["BUSY_MESSAGE", "COMPACTING_MESSAGE", "HastingsSession", "default_session_factory"]
