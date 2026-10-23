"""一轮请求的执行与记录（0.4 M05）。

作者发出一次修改请求，可能只讨论、可能写零个到多个文件，也可能中途停止或失败。
这一层负责把「这一轮实际发生了什么」翻译成三件事：

- **内容基线**：首次真正写文件之前才准备。受管文件已有未记录的外部改动时，先单独
  记一笔「外部修改」，让作者改动和模型改动不混在同一个提交里；只读讨论不准备基线，
  也不要求仓库可写。
- **结果记录**：结束时按磁盘上的实际变化提交一次；没有变化不提交。停止或失败时
  已写入的内容仍然记一笔并标明「部分完成」。Git 失败不掩盖文件已保存的事实，
  结果记为 ``unrecorded``，可以单独补记，不重跑模型。
- **会话事件**：一轮的启动、工具过程、回复与结果是可回读的事件，界面据此补齐；
  一个作品同时只允许一轮在写，阅读不受限。

执行状态（完成/部分/失败/停止/中断）与记录状态（已提交/无变化/未记录）是两条轴：
「文件写进去了但历史没记上」必须能同时表达，不能合并成一个成功/失败。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.chapter_index import (
    MAX_INDEX_CHAPTERS,
    ChapterIndexStore,
    index_injection,
)
from hastings.workspace.character_states import CharacterStateStore
from hastings.workspace.chat_stream import ChatStreamBridge, repair_interrupted_stream
from hastings.workspace.clues import ClueStore
from hastings.workspace.context import materials_section
from hastings.workspace.disclosure import DisclosureStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.glossary import GlossaryStore
from hastings.workspace.materials import (
    ContextRequest,
    ContextSnapshot,
    MaterialLedger,
    Reference,
    freeze_context,
    prepare_references,
    verify_context,
)
from hastings.workspace.review import ReviewStore, target_digests
from hastings.workspace.state import ConversationStore
from hastings.workspace.state_store import state_chapter
from hastings.workspace.timeline import TimelineStore
from tau_agent.events import (
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnStartEvent,
)
from tau_agent.harness import AgentHarness
from tau_agent.messages import AssistantMessage

if TYPE_CHECKING:
    from hastings.workspace.agent import RunContext

from tau_agent.provider_events import (
    TextDeltaEvent,
    TextEndEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
)

__all__ = [
    "EXTERNAL_CHANGE_MESSAGE",
    "ActiveTurn",
    "ProviderFactory",
    "Recording",
    "RunResult",
    "RunStatus",
    "TurnInfo",
    "TurnService",
    "WriteSession",
]

RunStatus = Literal["completed", "partial", "failed", "stopped", "interrupted"]
Recording = Literal["committed", "no_change", "unrecorded"]

#: 取得「这一轮用哪个连接、哪个模型、哪个实例」；未配置时抛带 code 的错误。
#:
#: 工厂在受理一轮时调用一次并固定结果：受理之后切换或编辑连接只影响下一轮。
ProviderFactory = Callable[[], ModelSnapshot]

#: 外部编辑形成的独立提交：让作者的改动先有归属，再开始这一轮。
EXTERNAL_CHANGE_MESSAGE = "外部修改：应用之外保存的内容"

_STATUS_LABEL: dict[str, str] = {
    "partial": "部分完成",
    "failed": "失败",
    "stopped": "已停止",
    "interrupted": "中断",
}

_MAX_SUMMARY = 40


@dataclass(frozen=True, slots=True)
class RunResult:
    """一轮的结果：执行状态、记录状态、实际改动路径与提交。"""

    status: RunStatus
    recording: Recording
    changed_paths: tuple[str, ...]
    commit: str | None
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "recording": self.recording,
            "changed_paths": list(self.changed_paths),
            "commit": self.commit,
            "note": self.note,
        }


class WriteSession:
    """一轮写入的 Git 记录。调用顺序：``prepare()`` → 写文件 → ``finish(status)``。"""

    def __init__(self, workspace: Workspace, *, instruction: str = "") -> None:
        self.workspace = workspace
        self.repo = GitRepo(workspace.root)
        self.instruction = instruction
        self.baseline_head: str | None = None
        self.external_commit: str | None = None
        self.prepared = False

    def prepare(self) -> str | None:
        """首次实际修改前调用，返回这一轮的内容基线（提交 ID）。

        仓库不可写、有冲突或作者预暂存内容时抛错：此时还没有写任何文件，
        调用方应当停止写入，但阅读与讨论仍然可用。
        """
        self.repo.require_ready()
        self.baseline_head = self.repo.head()
        pending = self.repo.changes()
        if pending:
            # 作者在应用之外改过：先单独记一笔，避免混进本轮模型改动。
            self.external_commit = self.repo.record(EXTERNAL_CHANGE_MESSAGE)
            if self.external_commit is not None:
                self.baseline_head = self.external_commit
        self.prepared = True
        return self.baseline_head

    def finish(self, status: RunStatus = "completed") -> RunResult:
        """结束一轮：按实际变化提交一次，并如实报告记录状态。"""
        if not self.prepared:
            # 只读讨论：没有准备基线，也就没有要记录的内容。
            return RunResult(status=status, recording="no_change", changed_paths=(), commit=None)

        changes = self.repo.changes()
        paths = tuple(sorted({change.path for change in changes}))
        if not paths:
            return RunResult(status=status, recording="no_change", changed_paths=(), commit=None)

        head = self.repo.head()
        if head != self.baseline_head:
            # 运行期间历史被别人推进：不把未知改动并进本轮提交。
            return RunResult(
                status=status,
                recording="unrecorded",
                changed_paths=paths,
                commit=None,
                note="运行期间历史被外部改动，未自动提交；文件已保存，可单独补记",
            )

        try:
            commit = self.repo.record(self._message(status), paths)
        except WorkspaceError as exc:
            return RunResult(
                status=status,
                recording="unrecorded",
                changed_paths=paths,
                commit=None,
                note=f"文件已保存，历史尚未记录：{exc.why}",
            )
        if commit is None:
            return RunResult(status=status, recording="no_change", changed_paths=(), commit=None)
        return RunResult(
            status=status,
            recording="committed",
            changed_paths=paths,
            commit=commit,
        )

    def record_pending(self, message: str = EXTERNAL_CHANGE_MESSAGE) -> str | None:
        """单独补记当前未记录的改动：只执行 Git，不调用模型。"""
        self.repo.require_ready()
        return self.repo.record(message)

    def _message(self, status: RunStatus) -> str:
        summary = _summary(self.instruction)
        if status == "completed":
            return f"写作：{summary}" if summary else "写作：记录本轮修改"
        label = _STATUS_LABEL.get(status, status)
        return f"写作（{label}）：{summary}" if summary else f"写作（{label}）：记录已写入的内容"


def _summary(instruction: str) -> str:
    collapsed = " ".join(instruction.split())
    return collapsed[:_MAX_SUMMARY]


def _tool_mode(purpose: str) -> Literal["write", "index", "review", "state"]:
    if purpose == "update_chapter_index":
        return "index"
    if purpose == "review":
        return "review"
    if purpose in ("update_disclosure", "update_state"):
        return "state"
    return "write"


def _written_targets(run: RunContext) -> tuple[str, ...]:
    """这一轮实际写过的路径：没有显式指定检查目标时按范围里的新建/修改项推断。"""
    scope = run.scope.current
    if scope is None:
        return ()
    return tuple(
        dict.fromkeys(
            grant.path for grant in scope.draft.grants if grant.action in ("create", "update")
        )
    )


def _append_event(
    store: ConversationStore,
    conversation_id: str,
    turn_id: str,
    kind: str,
    payload: dict[str, Any],
) -> None:
    """材料与范围事件先落盘再推送；返回值不参与判断。"""
    store.append_batch(conversation_id, [{"type": kind, "turn_id": turn_id, **payload}])


def _same_materials(
    previous: dict[str, object] | None,
    request: ContextRequest,
    previous_purpose: str,
    previous_targets: Sequence[str],
    previous_review: Sequence[str],
    purpose: str,
    targets: Sequence[str],
    review: Sequence[str],
) -> bool:
    """同键重试必须带同一份材料与用途；旧日志没有新字段时按空材料与 chat 处理。"""
    paths = _dedupe(request.reference_paths)
    observed = dict(sorted(request.observed_digests.items()))
    if previous_purpose != purpose:
        return False
    if tuple(previous_targets) != tuple(targets) or tuple(previous_review) != tuple(review):
        return False
    if previous is None:
        return (
            not paths
            and request.selected_path is None
            and not observed
            and request.state_chapter is None
        )
    return (
        previous.get("state_chapter") == request.state_chapter
        and previous.get("selected_path") == request.selected_path
        and previous.get("reference_paths") == list(paths)
        and previous.get("observed_digests") == observed
    )


def _dedupe(paths: Sequence[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for path in paths:
        if path not in ordered:
            ordered.append(path)
    return tuple(ordered)


# -- 一轮请求 ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnInfo:
    turn_id: str
    conversation_id: str
    started_at: str
    #: 这一轮受理前的历史位置：恢复范围止于此，当前提示不会重复进入模型上下文。
    history_seq: int = 0
    title: str = ""
    #: 受理时冻结的材料身份；执行阶段按它复核版本，不反读当前界面。
    context: ContextSnapshot | None = None
    #: 本轮用途：普通对话、更新章节索引，或只读检查。
    purpose: str = "chat"
    index_paths: tuple[str, ...] = ()
    review_paths: tuple[str, ...] = ()


ActiveState = Literal["accepted", "running", "stop_requested"]


@dataclass
class ActiveTurn:
    """本作品当前被占用的那一轮；只放公开信息，不含实例与凭据。"""

    conversation_id: str
    turn_id: str
    state: ActiveState
    model: str
    connection: str
    started_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "state": self.state,
            "model": self.model,
            "connection": self.connection,
            "started_at": self.started_at,
        }


class TurnService:
    """执行一轮对话：串行写入、事件记录、停止与中断。

    一个作品同一时刻只允许一轮——**包括已受理但还没开始执行的轮次**。受理锁覆盖
    「查重 → 忙碌判断 → 写初始记录 → 占用」，不能等后台任务拿到执行锁才算忙碌：
    否则两次几乎同时的发送都会通过检查，第二个在队列里等待，作者却以为它没被接受。

    停止在受理阶段就有效：还没调用模型的轮次直接收尾为「已停止」，不必先有一个 harness。
    """

    def __init__(
        self,
        workspace: Workspace,
        store: ConversationStore,
        provider_factory: ProviderFactory,
        *,
        max_turns: int = 24,
    ) -> None:
        self.workspace = workspace
        self.store = store
        self.provider_factory = provider_factory
        self.max_turns = max_turns
        self._admission = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._harnesses: dict[str, AgentHarness] = {}
        self._started: set[str] = set()
        self._stopped: set[str] = set()
        self._active: ActiveTurn | None = None

    @property
    def busy(self) -> bool:
        return self._active is not None

    def active_turn(self) -> ActiveTurn | None:
        return self._active

    async def accept(
        self,
        text: str,
        request_key: str,
        *,
        conversation_id: str | None = None,
        context: ContextRequest | None = None,
        purpose: str = "chat",
        index_paths: Sequence[str] = (),
        review_paths: Sequence[str] = (),
    ) -> TurnInfo:
        """受理一轮并立即返回；``conversation_id`` 为空表示「首次发送即建会话」。

        ``context`` 是作者这次的当前阅读位置与显式参考。受理时只冻结路径与服务端指纹；
        自然语言定位和后来发现的材料属于执行阶段记录，不回写这份请求身份。
        """
        async with self._admission:
            previous = self.store.find_accepted(request_key)
            if previous is not None:
                if conversation_id is not None and previous.conversation_id != conversation_id:
                    raise WorkspaceError(
                        code="REQUEST_KEY_CONFLICT",
                        where="turns.accept",
                        why="这个请求标识已经属于另一个会话",
                        fix="换一个请求标识，或回到原会话继续",
                        details={"conversation": previous.conversation_id},
                    )
                if self.store.prompt_of(previous.conversation_id, previous.turn_id) != text.strip():
                    raise WorkspaceError(
                        code="REQUEST_KEY_CONFLICT",
                        where="turns.accept",
                        why="这个请求标识已经用在另一条要求上",
                        fix="要发新的要求就换一个请求标识；要重试上一次，请保持文字不变",
                        details={"conversation": previous.conversation_id},
                    )
                if not _same_materials(
                    previous.context,
                    context or ContextRequest(),
                    previous.purpose,
                    previous.index_paths,
                    previous.review_paths,
                    purpose,
                    tuple(index_paths),
                    tuple(review_paths),
                ):
                    raise WorkspaceError(
                        code="REQUEST_KEY_CONFLICT",
                        where="turns.accept",
                        why="这个请求标识已经用在另一份材料上",
                        fix="参考或当前路径变了就换一个请求标识；重试请保持原来的选择",
                        details={"conversation": previous.conversation_id},
                    )
                return TurnInfo(
                    turn_id=previous.turn_id,
                    conversation_id=previous.conversation_id,
                    started_at=previous.started_at,
                    history_seq=previous.last_seq - 1,
                    title=previous.title,
                )
            if self._active is not None:
                raise WorkspaceError(
                    code="WORKSPACE_BUSY_CONFLICT",
                    where="turns.accept",
                    why="这个作品还有一轮正在写入",
                    fix="等当前这轮结束或停止它，再发送新的要求",
                    details={
                        "conversation": self._active.conversation_id,
                        "turn_id": self._active.turn_id,
                    },
                )
            if conversation_id is None:
                # 先做空提示校验，再决定是否生成会话 ID：拒绝时不留任何记录。
                clean = text.strip()
                if not clean:
                    raise WorkspaceError(
                        code="PROMPT_EMPTY",
                        where="turns.accept",
                        why="提示词是空的",
                        fix="写下要让模型做什么，再发送",
                    )
            # 冻结材料版本：缺失或超限在这里失败，此时还没有写任何记录。
            frozen = freeze_context(self.workspace, context or ContextRequest())
            targets, review_targets = self._purpose_targets(purpose, index_paths, review_paths)
            history_seq = self._history_cursor(conversation_id)
            turn_id = f"t_{os.urandom(4).hex()}"
            # 受理时固定这一轮的模型事实：之后切换、编辑或删除连接都不影响它。
            snapshot: ModelSnapshot | None
            failure: Exception | None = None
            try:
                snapshot = self.provider_factory()
            except Exception as exc:  # 未配置模型等情况在受理时就已经知道
                snapshot, failure = None, exc
            model_facts = snapshot.describe() if snapshot is not None else {}
            if conversation_id is None:
                accepted = self.store.create_with_first_turn(
                    text=text,
                    request_key=request_key,
                    turn_id=turn_id,
                    facts=model_facts,
                    context=frozen.to_dict(),
                    purpose=purpose,
                    index_paths=targets,
                    review_paths=review_targets,
                )
            else:
                accepted = self.store.accept_turn(
                    conversation_id,
                    text=text,
                    request_key=request_key,
                    turn_id=turn_id,
                    facts=model_facts,
                    context=frozen.to_dict(),
                    purpose=purpose,
                    index_paths=targets,
                    review_paths=review_targets,
                )
            # 旧记录的占位标题在首次受理时补上；作者改过的标题不动。
            if accepted.title == "新的对话":
                with suppress(WorkspaceError):
                    self.store.rename(accepted.conversation_id, _summary(text) or "新的对话")
            info = TurnInfo(
                turn_id=turn_id,
                conversation_id=accepted.conversation_id,
                started_at=accepted.started_at,
                history_seq=history_seq,
                title=accepted.title,
                context=frozen,
                purpose=purpose,
                index_paths=targets,
                review_paths=review_targets,
            )
            self._active = ActiveTurn(
                conversation_id=accepted.conversation_id,
                turn_id=turn_id,
                state="accepted",
                model=str(model_facts.get("model", "")),
                connection=str(model_facts.get("connection_name", "")),
                started_at=accepted.started_at,
            )
            # 初始轮次事件里的模型字段由 store 与提示一起原子写入。
            self._tasks[turn_id] = asyncio.create_task(self._run(info, text, snapshot, failure))
            return info

    async def wait(self, turn_id: str) -> None:
        """测试与需要同步结果的调用方使用。"""
        task = self._tasks.get(turn_id)
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task

    async def stop(self, turn_id: str) -> bool:
        """请求停止：覆盖「已受理但还没调模型」与「正在跑」两种情况。"""
        async with self._admission:
            active = self._active
            if active is None or active.turn_id != turn_id:
                return False
            if turn_id in self._stopped:
                return True
            self._stopped.add(turn_id)
            active.state = "stop_requested"
            harness = self._harnesses.get(turn_id)
            if harness is not None:
                harness.cancel()
                return True
            if turn_id in self._started:
                # 已经进了 _run 但还没建好 harness：标志位足以让它跳过模型调用。
                return True
            # 还没开始执行：取消排队的任务，由这里直接写终态——别无第二处会写。
            task = self._tasks.pop(turn_id, None)
            if task is not None:
                task.cancel()
            self._record_terminal(
                active.conversation_id,
                turn_id,
                "stopped",
                note="在调用模型之前就停止了，没有产生请求",
            )
            self._active = None
            self._stopped.discard(turn_id)
            return True

    def mark_interrupted(self) -> list[str]:
        """进程重启后把没有正常结束的轮次标为中断；不自动继续。

        按轮次配对，而不是只看最后一条：崩在工具开始、工具结束或助理回复之后的轮次
        同样要被识别。已经有终态的轮次不会再补第二遍。
        """
        interrupted: list[str] = []
        for conversation in self.store.conversations():
            for turn_id in self.store.unfinished_turns(conversation.id):
                if turn_id in self._tasks:
                    continue
                # 先按已保存的增量补齐没有结束的内容块与响应，再补本轮中断结果：
                # 这样界面看到的是「写到一半的回复 + 中断」，不是一条断在空中的记录。
                repair_interrupted_stream(self.store, conversation.id, turn_id)
                self.store.append(
                    conversation.id,
                    {
                        "type": "run_finished",
                        "turn_id": turn_id,
                        "status": "interrupted",
                        "recording": "unrecorded",
                        "changed_paths": [],
                        "commit": None,
                        "note": (
                            "进程重启时这一轮没有正常结束；文件以磁盘为准，可查看差异后单独补记"
                        ),
                    },
                )
                interrupted.append(conversation.id)
        return interrupted

    def _purpose_targets(
        self, purpose: str, index_paths: Sequence[str], review_paths: Sequence[str]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """校验用途与目标：索引 1–10 章、检查 1–20 个文件，普通对话不能带任何目标。"""
        if purpose == "review":
            if index_paths:
                raise WorkspaceError(
                    code="REVIEW_SCOPE_INVALID",
                    where="turns.accept",
                    why="检查任务不接受索引目标",
                    fix="检查时只选择要核对的章节或设定文件",
                )
            return (), self._review_targets(review_paths)
        targets = _dedupe(tuple(index_paths))
        if purpose == "chat":
            if targets or review_paths:
                raise WorkspaceError(
                    code="CHAPTER_INDEX_SCOPE_INVALID",
                    where="turns.accept",
                    why="普通对话不接受索引或检查目标",
                    fix="要更新索引或发起检查时使用对应用途，并选择目标",
                )
            return (), ()
        if purpose in ("update_disclosure", "update_state"):
            if not targets:
                raise WorkspaceError(
                    code="STATE_SCOPE_INVALID",
                    where="turns.accept",
                    why="核对状态需要选择要读的角色小传",
                    fix="选择 1 到 10 份小传后再发起",
                )
            if len(targets) > MAX_INDEX_CHAPTERS:
                raise WorkspaceError(
                    code="STATE_SCOPE_INVALID",
                    where="turns.accept",
                    why=f"一次最多核对 {MAX_INDEX_CHAPTERS} 份小传，这次有 {len(targets)} 份",
                    fix="分几次核对，每次不超过 10 份",
                    details={"limit": MAX_INDEX_CHAPTERS, "count": len(targets)},
                )
            return targets, ()
        if purpose != "update_chapter_index":
            raise WorkspaceError(
                code="CHAPTER_INDEX_SCOPE_INVALID",
                where="turns.accept",
                why=f"不支持的用途：{purpose}",
                fix="使用 chat 或 update_chapter_index",
                details={"purpose": purpose},
            )
        if not targets:
            raise WorkspaceError(
                code="CHAPTER_INDEX_SCOPE_INVALID",
                where="turns.accept",
                why="索引任务没有选择章节",
                fix="选择 1 到 10 个章节后再发起",
            )
        if len(targets) > MAX_INDEX_CHAPTERS:
            raise WorkspaceError(
                code="CHAPTER_INDEX_SCOPE_INVALID",
                where="turns.accept",
                why=f"一次最多更新 {MAX_INDEX_CHAPTERS} 章，这次有 {len(targets)} 章",
                fix="分几次更新，每次不超过 10 章",
                details={"limit": MAX_INDEX_CHAPTERS, "count": len(targets)},
            )
        for path in targets:
            if not path.startswith("正文/") or not path.endswith(".md"):
                raise WorkspaceError(
                    code="CHAPTER_INDEX_SCOPE_INVALID",
                    where="turns.accept",
                    why=f"索引只针对正文章节：{path}",
                    fix="只选择 正文/ 下的章节文件",
                    details={"path": path},
                )
            try:
                self.workspace.read(path)
            except WorkspaceError as exc:
                raise WorkspaceError(
                    code="CONTEXT_SOURCE_NOT_FOUND",
                    where="turns.accept",
                    why=f"要建索引的章节当前不可用：{path}（{exc.why}）",
                    fix="刷新目录后重新选择章节",
                    details={"path": path},
                ) from exc
        return targets, ()

    def _review_targets(self, review_paths: Sequence[str]) -> tuple[str, ...]:
        """检查目标：1–20 个存在的受管文件；检查本身只读。"""
        targets = _dedupe(tuple(review_paths))
        if not targets:
            raise WorkspaceError(
                code="REVIEW_SCOPE_INVALID",
                where="turns.accept",
                why="检查任务没有选择文件",
                fix="选择要核对的章节或设定文件后再发起",
            )
        if len(targets) > 20:
            raise WorkspaceError(
                code="REVIEW_SCOPE_INVALID",
                where="turns.accept",
                why="一次最多检查 20 个文件",
                fix="分几次检查，每次不超过 20 个文件",
                details={"count": len(targets)},
            )
        target_digests(self.workspace, targets)
        return targets

    def _history_cursor(self, conversation_id: str | None) -> int:
        if conversation_id is None:
            return 0
        events = self.store.events(conversation_id)
        return int(events[-1]["seq"]) if events else 0

    def _prepare_materials(self, info: TurnInfo) -> MaterialLedger:
        """登记受理快照里的显式参考，并按预算预读。

        预读是「应用实际提供了多少原文」的记录：作者选择的参考默认为必需，写作品前要读完全文；
        这一轮先给开头一段，模型用 ``read_file`` 继续读剩下的部分。
        """
        ledger = MaterialLedger()
        snapshot = info.context
        if snapshot is None or (snapshot.selected_path is None and not snapshot.references):
            # 没有参考也没有当前阅读：不写空事件，事件流保持与只讨论时一致。
            return ledger
        for ref in snapshot.references:
            ledger.register(Reference(ref=ref, kind="author_selected", required=True))
        prepared = prepare_references(self.workspace, ledger, snapshot.reference_paths)
        self.store.append_batch(
            info.conversation_id,
            [
                {
                    "type": "context_prepared",
                    "turn_id": info.turn_id,
                    "selected_path": snapshot.selected_path,
                    "references": [
                        {"path": ref.path, "digest": ref.digest} for ref in snapshot.references
                    ],
                    "provided": [
                        {
                            "path": item.path,
                            "start": item.start,
                            "end": item.end,
                            "length": item.length,
                        }
                        for item in prepared
                    ],
                }
            ],
        )
        return ledger

    def _record_terminal(
        self, conversation_id: str, turn_id: str, status: RunStatus, *, note: str = ""
    ) -> None:
        self.store.append(
            conversation_id,
            {
                "type": "run_finished",
                "turn_id": turn_id,
                "status": status,
                "recording": "no_change",
                "changed_paths": [],
                "commit": None,
                "note": note,
            },
        )

    async def _run(
        self,
        info: TurnInfo,
        text: str,
        snapshot: ModelSnapshot | None = None,
        failure: Exception | None = None,
    ) -> None:
        # 局部导入：agent 依赖本模块的 WriteSession，顶层导入会成环。
        from hastings.workspace.agent import RunContext, build_harness, build_turn_assembly
        from hastings.workspace.conversation_context import (
            execution_facts,
            replay_public_messages,
        )

        # _started 与 accept() 里的 _active 不是一回事：
        # 它表示后台任务已经真正开始执行，stop() 据此决定要不要等待收尾。
        self._started.add(info.turn_id)
        conversation_id = info.conversation_id
        # 先假定正常完成，异常分支再改写；finally 用这个值决定终态。
        status: RunStatus = "completed"
        # 一轮对应一个 WriteSession：Git 基线、外部改动和提交都在这里收口。
        # 「执行是否成功」和「内容是否入库」是两个独立维度，不要合并成一个状态。
        session = WriteSession(self.workspace, instruction=text)
        try:
            # stop() 可能发生在任务真正被调度之前，此时还没有发出任何模型请求。
            if info.turn_id in self._stopped:
                status = "stopped"
                return
            # snapshot 由 accept() 固定后传入；为 None 说明受理时模型就不可用。
            # 复用受理时保存的 failure，避免等到调用模型才暴露配置问题。
            if snapshot is None:
                raise failure or WorkspaceError(
                    code="MODEL_NOT_CONFIGURED",
                    where="turn.model",
                    why="当前没有可用的模型来源或模型名，这一轮不会调用模型",
                    fix="在模型设置里选择连接与模型，或补齐 config.yaml 的连接配置",
                )
            # 执行锁覆盖整轮模型运行，保证同一作品同时只有一轮在改文件。
            # 它与 accept()/stop() 用的受理锁分开，避免状态查询被长任务阻塞。
            async with self._lock:
                if self._active is not None and self._active.turn_id == info.turn_id:
                    # 把运行状态暴露给前端轮询；真正的中断由 stop() 调用 harness.cancel()。
                    self._active.state = (
                        "stop_requested" if info.turn_id in self._stopped else "running"
                    )
                repo = GitRepo(self.workspace.root)
                if repo.is_repository():
                    # 仓库已初始化时先确认可写：暂存区/冲突/分离头都在调用模型前挡住。
                    repo.require_ready()
                # 只回放到受理时的 history_seq：本轮新写入的事件不能被再次喂给模型。
                history_events = tuple(
                    event
                    for event in self.store.events(conversation_id)
                    if int(event["seq"]) <= info.history_seq
                )
                # 受理之后参考文件被改动：这一轮直接失败，不按旧版本继续，也不偷偷换引用。
                if info.context is not None:
                    verify_context(self.workspace, info.context)
                ledger = self._prepare_materials(info)
                # RunContext 是工具层能看到的运行上下文；
                # 第一次写文件时它会调用 session.prepare()，只读讨论不会产生提交。
                # 作者消息只来自 turn_started：助手回复与作品内容都不能作为授权来源。
                author_messages = tuple(
                    (int(event["seq"]), str(event["text"]))
                    for event in self.store.events(conversation_id)
                    if event.get("type") == "turn_started"
                    and isinstance(event.get("text"), str)
                    and str(event["text"]).strip()
                )
                index_store = ChapterIndexStore(self.store.root)
                # 索引任务只读原文生成；普通写作才注入有效索引，避免递归压缩旧摘要。
                index_notes = (
                    ""
                    if info.purpose == "update_chapter_index"
                    else index_injection(index_store.valid(self.workspace))
                )
                cutoff = state_chapter(
                    self.workspace,
                    selected_path=info.context.selected_path if info.context else None,
                    targets=info.review_paths,
                    explicit=info.context.state_chapter if info.context else None,
                )
                run = RunContext(
                    state_chapter=cutoff
                    if cutoff or (info.context and info.context.state_chapter is not None)
                    else None,
                    workspace=self.workspace,
                    session=session,
                    materials=ledger,
                    author_messages=author_messages,
                    turn_id=info.turn_id,
                    emit=lambda kind, payload: _append_event(
                        self.store,
                        conversation_id,
                        info.turn_id,
                        kind,
                        payload,
                    ),
                )
                review_store = ReviewStore(self.store.root)
                if info.purpose == "review":
                    # 作者直接要求检查：先记下请求，再进入只读检查。
                    _append_event(
                        self.store,
                        conversation_id,
                        info.turn_id,
                        "review_requested",
                        {
                            "requirement": text.strip(),
                            "authorization": text.strip(),
                            "targets": list(info.review_paths),
                        },
                    )
                # 装配一次，记一次：事件里的注入内容与实际发给模型的内容必须是同一份结果。
                mode = _tool_mode(info.purpose)
                disclosure_store = DisclosureStore(self.store.root)
                glossary_store = GlossaryStore(self.store.root)
                roster_store = CharacterStateStore(self.store.root)
                timeline_store = TimelineStore(self.store.root)
                clue_store = ClueStore(self.store.root)
                assembly = build_turn_assembly(
                    self.workspace,
                    as_of_chapter=run.state_chapter,
                    mode=mode,
                    history_facts=execution_facts(history_events),
                    materials=materials_section(info.context, ledger.summaries()),
                    index_notes=index_notes,
                    disclosure_store=disclosure_store,
                    glossary_store=glossary_store,
                    roster_store=roster_store,
                    timeline_store=timeline_store,
                    clue_store=clue_store,
                )
                _append_event(
                    self.store,
                    conversation_id,
                    info.turn_id,
                    "state_injected",
                    assembly.summary(),
                )
                # provider、model、system、工具清单和历史在这里合并成一次可执行配置。
                # 工具清单由 build_harness 内部调用 build_tools() 注入，_run 不直接持有。
                harness = build_harness(
                    provider=snapshot.provider,
                    model=snapshot.model,
                    workspace=self.workspace,
                    run=run,
                    max_turns=self.max_turns,
                    history=replay_public_messages(history_events),
                    history_facts=execution_facts(history_events),
                    materials=materials_section(info.context, ledger.summaries()),
                    index_notes=index_notes,
                    mode=mode,
                    assembly=assembly,
                    index_allowed=info.index_paths,
                    index_store=index_store,
                    disclosure_store=disclosure_store,
                    glossary_store=glossary_store,
                    roster_store=roster_store,
                    timeline_store=timeline_store,
                    clue_store=clue_store,
                    review_targets=info.review_paths,
                    review_store=review_store,
                )
                # stop() 依靠这张表找到 harness，再通过它的取消令牌中断模型循环。
                self._harnesses[info.turn_id] = harness
                if info.turn_id in self._stopped:
                    # 建 harness 期间收到了停止请求：停在发出模型请求之前。
                    status = "stopped"
                    return
                # 阻塞消费事件流，直到模型循环自然结束或被取消；
                # 模型调用和工具执行本身都在 harness 内部的 run_agent_loop 里完成。
                turns_used = [0]
                truncated = await self._consume(harness, info, text, turns_used=turns_used)
                # 正常返回也可能是停止：取消令牌会让模型循环正常收尾。
                if info.turn_id in self._stopped:
                    status = "stopped"
                elif truncated:
                    # 回复被输出上限截断：文字保留，但整轮不能算作全部成功。
                    status = "partial"
                else:
                    status = "completed"
                # 只读检查没有报告，就等于没有检查结论：记为部分完成并说明原因。
                if (
                    status == "completed"
                    and info.purpose == "review"
                    and not self._has_review_report(info)
                ):
                    self._append_review_incomplete(info, "这一轮没有生成检查报告")
                    status = "partial"
                if status == "completed" and run.review_request is not None:
                    # 作者在修改任务里明确要求「改完检查」：同一轮内进入只读检查阶段。
                    published = await self._review_after_write(
                        info, run, snapshot, turns_used=turns_used[0]
                    )
                    if not published:
                        status = "partial"
        except asyncio.CancelledError:
            # 任务被外部取消（进程关闭、测试清理）。harness 已补过中断的工具结果，
            # 这里只补终态并继续向上抛，让调用方知道任务确实被取消。
            status = "stopped"
            raise
        except WorkspaceError as exc:
            # 已知业务错误，自带稳定 code；作者主动停止时抛出的收尾错误不记为失败。
            status = "stopped" if info.turn_id in self._stopped else "failed"
            if status == "failed":
                self.store.append(
                    conversation_id,
                    {
                        "type": "error",
                        "turn_id": info.turn_id,
                        "code": exc.code,
                        "message": exc.message,
                    },
                )
        except Exception as exc:  # 一轮的失败要落到事件里，不能吞掉进程
            status = "stopped" if info.turn_id in self._stopped else "failed"
            if status == "failed":
                # 未知异常也必须落成事件：否则前端只会看到一直「进行中」。
                # 配置类错误自带稳定 code 与可读文案，原样透传比包一层更有用。
                code = getattr(exc, "code", None) or "TURN_FAILED"
                message = getattr(exc, "message", None) or f"这一轮没有完成：{exc}"
                self.store.append(
                    conversation_id,
                    {
                        "type": "error",
                        "turn_id": info.turn_id,
                        "code": code,
                        "message": message,
                    },
                )
        finally:
            if snapshot is not None:
                # 完成、失败、停止都关闭本轮实例：下一轮重新构造，不复用可能已失效的连接。
                await snapshot.aclose()
            # 按磁盘实际变化提交：停止或失败时已写入的内容也会入库，
            # 是否成功由 status 标明，不在这一步丢弃作者已经得到的结果。
            self.store.append_batch(
                conversation_id,
                [{"type": "run_phase", "turn_id": info.turn_id, "phase": "record"}],
            )
            result = session.finish(status)
            # 终态事件：前端据此结束「进行中」状态并刷新会话与工作区。
            self.store.append(
                conversation_id,
                {"type": "run_finished", "turn_id": info.turn_id, **result.to_dict()},
            )
            # 清理四张按 turn_id 索引的表，防止旧轮次的状态泄漏到后续轮次。
            self._harnesses.pop(info.turn_id, None)
            self._started.discard(info.turn_id)
            self._stopped.discard(info.turn_id)
            self._tasks.pop(info.turn_id, None)
            # 只释放自己占用的那一轮：后面受理的新轮次不能被前一轮的收尾清掉。
            if self._active is not None and self._active.turn_id == info.turn_id:
                self._active = None

    async def _review_after_write(
        self,
        info: TurnInfo,
        run: RunContext,
        snapshot: ModelSnapshot,
        *,
        turns_used: int = 0,
    ) -> bool:
        """修改完成后的只读检查：同一轮、同一模型连接，工具换成检查工具集。

        修改已经落盘，检查失败不能把它撤掉：这里只决定整轮状态与报告内容。检查是同一轮
        的第二阶段，回合上限要接着写作阶段已用的部分算，不能重新拿满。
        """
        from hastings.workspace.agent import build_harness

        request = run.review_request
        if request is None:
            return False
        remaining = max(self.max_turns - turns_used, 0)
        if remaining <= 0:
            self._append_review_incomplete(info, "写作阶段已用完本轮的回合上限，没有进入检查阶段")
            return False
        targets = request.targets or _written_targets(run)
        if not targets:
            self._append_review_incomplete(info, "没有可检查的目标")
            return False
        try:
            from hastings.workspace.agent import build_turn_assembly

            cutoff = state_chapter(
                self.workspace,
                targets=targets,
                explicit=info.context.state_chapter if info.context else None,
            )
            stores: dict[str, Any] = {
                "disclosure_store": DisclosureStore(self.store.root),
                "glossary_store": GlossaryStore(self.store.root),
                "roster_store": CharacterStateStore(self.store.root),
                "timeline_store": TimelineStore(self.store.root),
                "clue_store": ClueStore(self.store.root),
            }
            assembly = build_turn_assembly(
                self.workspace, mode="review", as_of_chapter=cutoff, **stores
            )
            _append_event(
                self.store, info.conversation_id, info.turn_id, "state_injected", assembly.summary()
            )
            harness = build_harness(
                provider=snapshot.provider,
                model=snapshot.model,
                workspace=self.workspace,
                run=run,
                max_turns=remaining,
                mode="review",
                assembly=assembly,
                **stores,
                review_targets=targets,
                review_store=ReviewStore(self.store.root),
            )
            self._harnesses[info.turn_id] = harness
            prompt = (
                f"请按作者要求检查这一轮的改动：{request.requirement}\n"
                f"检查目标：{'、'.join(targets)}"
            )
            await self._consume(harness, info, prompt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 检查失败不能吞掉已完成的修改
            self._append_review_incomplete(info, f"检查没有完成：{exc}")
            return False
        if not self._has_review_report(info):
            self._append_review_incomplete(info, "检查阶段没有生成报告")
            return False
        return True

    def _has_review_report(self, info: TurnInfo) -> bool:
        for event in self.store.events(info.conversation_id):
            if (
                event.get("type") == "review_finished"
                and event.get("turn_id") == info.turn_id
                and event.get("status") != "incomplete"
            ):
                return True
        return False

    def _append_review_incomplete(self, info: TurnInfo, note: str) -> None:
        """没有报告也要留下记录：界面据此说明「检查未完成」，不冒充通过。"""
        _append_event(
            self.store,
            info.conversation_id,
            info.turn_id,
            "review_finished",
            {
                "report_id": None,
                "status": "incomplete",
                "targets": list(info.review_paths),
                "findings": [],
                "uncovered": [note],
            },
        )

    async def _consume(
        self,
        harness: AgentHarness,
        info: TurnInfo,
        text: str,
        *,
        turns_used: list[int] | None = None,
    ) -> bool:
        """把模型事件翻译成会话事件：增量合批落盘，工具按调用 ID 配对。

        模型侧失败必须变成这一轮的失败：厂商报错时消息里没有正文、也没有工具调用，
        如果只是「记了个空回复」，作者看到的是「已完成、文件没改」，而真正的原因是
        端点或 key 不对。这里直接抛出，由 ``_run`` 落成 ``error`` 事件与失败状态。

        读到的是搬运任务放进队列的事件，而不是直接迭代模型输出：这样在事件之间
        可以按间隔提交缓冲，空闲时也把已收到的文字写下来，不必等下一个 token。

        返回本轮的回复是否被输出上限截断。``turns_used`` 累计本次消费用掉的模型回合，
        供同一轮的后续阶段接着算剩余预算。
        """
        bridge = ChatStreamBridge(self.store, info.conversation_id, info.turn_id)
        queue: asyncio.Queue[object] = asyncio.Queue()
        done = object()
        failure: list[Exception] = []

        async def pump() -> None:
            try:
                async for item in harness.prompt(text):
                    await queue.put(item)
            except Exception as exc:  # provider 自己抛错：由读循环统一落成失败
                failure.append(exc)
            finally:
                await queue.put(done)

        reader = asyncio.create_task(pump())
        tools: dict[str, tuple[dict[str, Any], float]] = {}
        truncated = False
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=bridge.pending_delay())
                except TimeoutError:
                    bridge.flush_due()
                    continue
                if event is done:
                    break
                truncated = (
                    self._handle_agent_event(event, info, bridge, tools, turns_used=turns_used)
                    or truncated
                )
                bridge.flush_due()
            # 模型循环自己抛错时，先封口已收到的内容再向上抛，由 _run 记为失败。
            if failure:
                raise failure[0]
        finally:
            if not reader.done():
                reader.cancel()
                with suppress(asyncio.CancelledError):
                    await reader
            # 停止或失败：已保存的文字继续显示，块与响应标明未完整生成。
            bridge.close_open("stopped" if info.turn_id in self._stopped else "failed")
        return truncated

    def _handle_agent_event(
        self,
        event: Any,
        info: TurnInfo,
        bridge: ChatStreamBridge,
        tools: dict[str, tuple[dict[str, Any], float]],
        *,
        turns_used: list[int] | None = None,
    ) -> bool:
        """翻译单条模型事件；返回这一条是否让回复被截断。"""
        if isinstance(event, TurnStartEvent):
            # 每个模型回合开始一次；这是「本轮用掉了几个回合」的唯一依据。
            if turns_used is not None:
                turns_used[0] += 1
            return False
        if isinstance(event, MessageStartEvent):
            if isinstance(event.message, AssistantMessage):
                bridge.start_response()
            return False
        if isinstance(event, MessageUpdateEvent):
            delta = event.assistant_message_event
            if isinstance(delta, TextDeltaEvent):
                bridge.append("text", delta.content_index, delta.delta)
            elif isinstance(delta, ThinkingDeltaEvent):
                bridge.append("thinking", delta.content_index, delta.delta)
            elif isinstance(delta, TextEndEvent):
                bridge.finish_block("text", delta.content_index, delta.content)
            elif isinstance(delta, ThinkingEndEvent):
                bridge.finish_block("thinking", delta.content_index, delta.content)
            return False
        if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
            message = event.message
            if message.stop_reason == "aborted":
                bridge.close_open("stopped")
                return False
            if message.stop_reason == "error" or message.error_message:
                # 作者停止时，harness 会补一条「没有可用回复」的收尾消息：
                # 那是停止，不是厂商失败，不能把已写出的内容标成失败。
                if info.turn_id in self._stopped:
                    bridge.close_open("stopped")
                    return False
                # 先封口已收到的内容，再让这一轮落成失败。
                bridge.close_open("failed")
                raise WorkspaceError(
                    code="MODEL_CALL_FAILED",
                    where="turn.model",
                    why=f"模型调用失败：{message.error_message or '厂商没有返回可用回复'}",
                    fix="检查 config.yaml 里该档案的 base_url、api_key 与模型名，"
                    "或换成可用的模型后重试这一轮",
                    details={"model": message.model},
                )
            return bridge.finish_response(message) == "truncated"
        if isinstance(event, ToolExecutionStartEvent):
            # 工具开始前提交缓冲：界面上的文字顺序与工具位置一致。
            bridge.flush()
            bridge.phase("tool")
            tools[event.tool_call_id] = (_short_args(event.args), time.monotonic())
            self.store.append_batch(
                info.conversation_id,
                [
                    {
                        "type": "tool_started",
                        "turn_id": info.turn_id,
                        "tool_call_id": event.tool_call_id,
                        "tool": event.tool_name,
                        "args": _short_args(event.args),
                    }
                ],
            )
            return False
        if isinstance(event, ToolExecutionEndEvent):
            started = tools.pop(event.tool_call_id, None)
            payload: dict[str, Any] = {
                "type": "tool_finished",
                "turn_id": info.turn_id,
                "tool_call_id": event.tool_call_id,
                "tool": event.tool_name,
                # 结束事件带同一份定位参数：界面据此显示「写入 正文/…」而不是解析 JSON。
                "args": started[0] if started is not None else {},
                "ok": not event.is_error,
                "summary": _excerpt(event.result.text),
            }
            if started is not None:
                payload["duration_ms"] = int(max(0.0, time.monotonic() - started[1]) * 1000)
            self.store.append_batch(info.conversation_id, [payload])
            return False
        return False


def _short_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """工具参数只保留定位信息，不把整章正文写进事件日志。"""
    kept: dict[str, Any] = {}
    for key in ("path", "source", "target", "query", "offset", "limit"):
        if key in arguments:
            kept[key] = arguments[key]
    return kept


def _excerpt(text: str, limit: int = 240) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:limit]
