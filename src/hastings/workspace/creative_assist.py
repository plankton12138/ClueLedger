"""目标化创作辅助：按目标生成候选，不覆盖作者草稿（1.0.9 P3）。

一次辅助请求只描述「目标、基础修订和可选补充」；服务端据此冻结材料、形成可审计的
提示，并复用已配置的模型连接。生成结果写入候选存储，不直接写正式作品。基础修订在
生成结束时再次核对，过期结果标记为 ``stale``。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from hastings.prompts.self_check import (
    prose_draft_system_prompt,
    prose_draft_user_prompt,
    self_check_error,
    split_self_check,
)
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.creative_candidates import (
    CandidateBase,
    CreativeCandidate,
    CreativeCandidateStore,
    CreativeTarget,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_models import FORMAT_VERSION, StoryVariant
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore
from hastings.workspace.style_io import read_style_state, style_partition
from tau_agent.messages import UserMessage, message_text
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    TextDeltaEvent,
)

__all__ = ["AssistKind", "CreativeAssistInput", "CreativeAssistService"]

AssistKind = Literal[
    "chapter_direction",
    "title",
    "prose_draft",
    "prose_continue",
    "alignment_resolution",
    "idea",
    "module_content",
    "next_action",
]

_KINDS = frozenset(
    {
        "chapter_direction",
        "title",
        "prose_draft",
        "prose_continue",
        "alignment_resolution",
        "idea",
        "module_content",
        "next_action",
    }
)

_RUNNING: dict[str, asyncio.Task[None]] = {}


class CreativeAssistInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    target: CreativeTarget
    base: CandidateBase = Field(default_factory=CandidateBase)
    selection: dict[str, Any] | None = None
    author_note: str | None = Field(default=None, max_length=4000)
    draft_text: str | None = Field(default=None, max_length=20000)
    request_key: str = Field(min_length=1, max_length=200)


class CreativeAssistService:
    def __init__(
        self,
        *,
        state_root: Path,
        workspace: Workspace | None,
        candidates: CreativeCandidateStore,
        provider_factory: Callable[[], ModelSnapshot],
        workspace_id: str,
    ) -> None:
        self.state_root = state_root
        self.workspace = workspace
        self.candidates = candidates
        self.provider_factory = provider_factory
        self.workspace_id = workspace_id

    async def start(self, request: CreativeAssistInput) -> CreativeCandidate:
        if request.kind not in _KINDS:
            raise WorkspaceError(
                code="ASSIST_TARGET_INVALID",
                where="creative-assist",
                why=f"不支持的辅助类型：{request.kind}",
                fix="按目标重新选择模型动作",
            )
        variant, plan, path, digest = self._resolve(request)
        snapshot: ModelSnapshot
        try:
            snapshot = self.provider_factory()
        except Exception as exc:
            raise WorkspaceError(
                code="MODEL_UNAVAILABLE",
                where="creative-assist",
                why=f"没有可用模型：{exc}",
                fix="先在设置中完成模型连接与选择",
            ) from exc
        candidate = self.candidates.create(
            workspace_id=self.workspace_id or None,
            target=request.target,
            kind=request.kind,
            base=request.base,
            request_key=request.request_key,
        )
        if candidate.status != "streaming" or candidate.candidate_id in _RUNNING:
            return candidate
        try:
            system, prompt = self._prompt(request, variant, plan)
        except WorkspaceError as exc:
            await snapshot.aclose()
            return self.candidates.update(
                candidate.candidate_id, status="failed", error=exc.message
            )
        task = asyncio.create_task(self._run(candidate, snapshot, system, prompt, request))
        _RUNNING[candidate.candidate_id] = task
        task.add_done_callback(lambda _: _RUNNING.pop(candidate.candidate_id, None))
        return candidate

    async def stop(self, candidate_id: str) -> CreativeCandidate:
        task = _RUNNING.get(candidate_id)
        if task is not None:
            task.cancel()
        return await asyncio.to_thread(
            self.candidates.update,
            candidate_id,
            status="failed",
            error="作者停止了这次生成",
        )

    def recover(self, candidate: CreativeCandidate) -> CreativeCandidate:
        """进程重启后没有对应任务：不要把旧候选继续显示为正在生成。"""
        if candidate.status == "streaming" and candidate.candidate_id not in _RUNNING:
            return self.candidates.update(
                candidate.candidate_id,
                status="failed",
                error="生成在进程重启后中断，请重新生成",
            )
        return candidate

    async def _run(
        self,
        candidate: CreativeCandidate,
        snapshot: ModelSnapshot,
        system: str,
        prompt: str,
        request: CreativeAssistInput,
    ) -> None:
        text = ""
        last_saved = 0.0
        final: str | None = None
        try:
            async with aclosing(
                cast(
                    AsyncGenerator[AssistantMessageEvent, None],
                    snapshot.provider.stream_response(
                        model=snapshot.model,
                        system=system,
                        messages=[UserMessage(content=prompt)],
                        tools=[],
                        signal=None,
                    ),
                )
            ) as stream:
                async for event in stream:
                    if isinstance(event, TextDeltaEvent):
                        text += event.delta
                        now = time.monotonic()
                        if now - last_saved > 0.5:
                            last_saved = now
                            await asyncio.to_thread(
                                self.candidates.update, candidate.candidate_id, text=text
                            )
                    elif isinstance(event, AssistantErrorEvent):
                        raise WorkspaceError(
                            code="ASSIST_FAILED",
                            where="creative-assist",
                            why=event.error.error_message or "模型请求未完成",
                            fix="保留作者草稿，稍后重试或继续手写",
                        )
                    elif isinstance(event, AssistantDoneEvent):
                        if event.reason != "stop":
                            raise WorkspaceError(
                                code="ASSIST_FAILED",
                                where="creative-assist",
                                why="模型生成没有完整结束",
                                fix="保留作者草稿，稍后重试或继续手写",
                            )
                        final = message_text(event.message) or final
            if final:
                text = final
            # 写后自查（M5）：起草/续写候选必须带自查段，缺失按确定性失败归因。
            self_check: dict[str, object] | None = None
            if request.kind in {"prose_draft", "prose_continue"}:
                block = split_self_check(text)
                if not block.present:
                    error = self_check_error("creative-assist", block.missing_layers)
                    await asyncio.to_thread(
                        self.candidates.update,
                        candidate.candidate_id,
                        text=text,
                        status="failed",
                        error=error.message,
                    )
                    return
                self_check = block.to_rows()
            stale = not self._base_current(candidate)
            await asyncio.to_thread(
                self.candidates.update,
                candidate.candidate_id,
                text=text,
                status="stale" if stale else "ready",
                self_check=self_check,
            )
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self.candidates.update,
                candidate.candidate_id,
                text=text,
                status="failed",
                error="作者停止了这次生成",
            )
            return
        except WorkspaceError as exc:
            await asyncio.to_thread(
                self.candidates.update,
                candidate.candidate_id,
                text=text,
                status="failed",
                error=f"[{exc.code}] {exc.why}",
            )
        except Exception as exc:  # 网络或供应方异常；保留部分文本
            await asyncio.to_thread(
                self.candidates.update,
                candidate.candidate_id,
                text=text,
                status="failed",
                error=f"生成失败：{exc}",
            )
        finally:
            await snapshot.aclose()

    def _drafting_injection(
        self, plan: Any, variant: StoryVariant | None
    ) -> tuple[str, str, str, int | None, int | None]:
        """起草任务的确定性注入（M5/P2）：铁律/结构全文、台账、线索报告与清单。

        装配层确定性对账是主防线：这里把对账结论写进提示词并把改派目标告诉
        模型（第二道保险的强制流程）；两处用同一份台账事实。
        返回 ``(结构文本, 台账文本, 线索清单文本, 改派目标章, 原目标章)``。
        """
        from hastings.workspace.prose_ledger import (
            LEDGER_FILENAME,
            load_ledger,
            reconcile,
        )
        from hastings.workspace.structure_assembly import (
            ledger_section,
            reconcile_note,
            split_structure_layers,
            structure_section_text,
        )
        from hastings.workspace.structure_io import read_structure_state

        assert self.workspace is not None and plan is not None
        state = read_structure_state(self.workspace)
        structure_text = (
            structure_section_text(split_structure_layers(state.text)) if state.exists else ""
        )
        ledger = load_ledger(self.workspace.root / LEDGER_FILENAME)
        target = plan.order + 1
        decision = reconcile(ledger, target)
        chapter = decision.target_to if decision.action == "redirect" else target
        ledger_text = ledger_section(ledger)
        note = reconcile_note(decision)
        if note:
            ledger_text = f"{ledger_text}\n\n{note}" if ledger_text else note
        report_text, context_text = self._clue_sections(state.text, ledger, variant, chapter)
        if report_text:
            ledger_text = f"{ledger_text}\n\n{report_text}" if ledger_text else report_text
        redirected = decision.target_to if decision.action == "redirect" else None
        return structure_text, ledger_text, context_text, redirected, target

    def _clue_sections(
        self,
        structure_text: str,
        ledger: Any,
        variant: StoryVariant | None,
        chapter: int,
    ) -> tuple[str, str]:
        """线索对账报告与清单（P1/P2）。

        报告说「哪里不对」（哑火/悬枪/失联/空响/计划未落实）；清单说「现在手上
        还压着什么」（未回收 / 本章计划 / 失联待重锚）。失联检测只读有线索登记
        的章节正文——不为了对账把全书正文都读一遍。
        """
        from hastings.workspace.chapter_clues import (
            aggregate_clues,
            clues_path,
            open_clue_states,
            parse_planned_clues,
        )
        from hastings.workspace.structure_assembly import (
            chapter_number_of,
            clue_context_section,
            clue_report_section,
        )

        assert self.workspace is not None
        plans = parse_planned_clues(structure_text)
        chapters = sorted(ledger.chapters)
        if not chapters and not plans:
            return "", ""
        prose_by_chapter: dict[int, str] = {}
        if variant is not None:
            for chapter_plan in variant.chapter_plans.values():
                prose_path = chapter_plan.prose_path
                if prose_path is None:
                    continue
                number = chapter_number_of(prose_path)
                if number is None or not clues_path(self.workspace.root, number).exists():
                    continue
                try:
                    prose_by_chapter[number] = self.workspace.read(prose_path).text
                except WorkspaceError:
                    continue
        aggregate = aggregate_clues(
            self.workspace.root,
            chapters,
            prose_by_chapter=prose_by_chapter,
            plans=plans,
        )
        report = clue_report_section(aggregate.findings)
        context = ""
        if plans or aggregate.states:
            states, total = open_clue_states(aggregate.states, chapter, plans)
            context = clue_context_section(
                states,
                plans,
                chapter,
                aggregate.findings,
                open_total=total,
            )
        return report, context

    def _resolve(
        self, request: CreativeAssistInput
    ) -> tuple[StoryVariant | None, Any, str | None, str | None]:
        """校验目标归属与基础修订；返回故事快照、章节计划、正文路径与摘要。"""
        target = request.target
        if target.workspace_id not in (None, self.workspace_id):
            raise _target_error("目标不属于当前作品")
        variant: StoryVariant | None = None
        plan = None
        path: str | None = None
        digest: str | None = None
        needs_story = (
            request.kind
            in {
                "chapter_direction",
                "prose_draft",
                "prose_continue",
                "alignment_resolution",
            }
            or target.chapter_id is not None
        )
        if needs_story:
            if self.workspace is None:
                raise _target_error("这类辅助需要当前作品上下文")
            variant = (
                StoryStore(self.state_root, self.workspace, format_version=FORMAT_VERSION)
                .read()
                .variant(MAIN_VARIANT_ID)
            )
        if variant is None and needs_story:
            raise WorkspaceError(
                code="STORY_FORMAT_UNSUPPORTED",
                where="creative-assist",
                why="当前作品还没有章节事实层",
                fix="先在对话中开始作品或建立章节，再请求章节辅助",
            )
        if variant is not None and variant.format_version != FORMAT_VERSION:
            raise WorkspaceError(
                code="STORY_FORMAT_UNSUPPORTED",
                where="creative-assist",
                why="当前作品仍使用旧构思格式",
                fix="新建作品后使用写作工作台",
            )
        if target.chapter_id is not None:
            if variant is None or (plan := variant.chapter(target.chapter_id)) is None:
                raise _target_error("章节构思不存在")
            if plan.prose_path is not None:
                path = plan.prose_path
                try:
                    assert self.workspace is not None
                    view = self.workspace.read(path)
                    digest = view.digest
                except WorkspaceError:
                    path, digest = None, None
        if request.kind in {"idea", "module_content", "next_action"} and not request.draft_text:
            raise _target_error("这类辅助需要作者提供当前草稿或指令")
        expected = request.base
        if variant is not None:
            if expected.plan_revision and expected.plan_revision != variant.revision:
                raise _stale_error("作品构思已经变化")
            if expected.prose_digest and digest and expected.prose_digest != digest:
                raise _stale_error("正文已经变化")
        return variant, plan, path, digest

    def _base_current(self, candidate: CreativeCandidate) -> bool:
        if candidate.kind in {"idea", "module_content", "next_action"}:
            return True
        request = CreativeAssistInput(
            kind=candidate.kind,
            target=candidate.target,
            base=candidate.base,
            request_key=candidate.request_key or candidate.candidate_id,
        )
        try:
            variant, plan, path, digest = self._resolve(request)
        except WorkspaceError:
            return False
        if candidate.base.plan_revision:
            actual = variant.revision if variant is not None else "empty"
            if actual != candidate.base.plan_revision:
                return False
        return not (candidate.base.prose_digest and digest != candidate.base.prose_digest)

    def _prompt(
        self,
        request: CreativeAssistInput,
        variant: StoryVariant | None,
        plan: Any,
    ) -> tuple[str, str]:
        note = (request.author_note or "").strip()
        draft = (request.draft_text or "").strip()
        structure = self._structure_text()
        if request.kind == "chapter_direction":
            title = plan.title if plan is not None else (request.target.draft_id or "本章")
            done = ""
            if variant is not None and plan is not None:
                done = "\n".join(
                    f"- 第{row.order + 1}章："
                    f"{_chapter_moves_text(variant, row) or '（无已入账信息）'}"
                    for row in sorted(variant.chapter_plans.values(), key=lambda item: item.order)
                    if row.order < plan.order
                )
            return (
                "你是悬疑小说创作助手。只给可以往下写的方向参考，不写正文，不列属性表。",
                f"全书结构：\n{structure or '（未建立）'}\n章节：{title}\n"
                f"已完成章节：\n{done or '（无）'}\n"
                f"作者补充：{note or '（无）'}\n"
                "给出 2—3 个本章可以推进的方向，每个方向用一句话说明会发生什么变化；"
                "不替作者决定真相或结局，也不要求作者填写任何字段。",
            )
        if request.kind in {"prose_draft", "prose_continue"}:
            assert self.workspace is not None
            style_section = style_partition(read_style_state(self.workspace).text).render()
            text = ""
            if plan is not None and plan.prose_path:
                try:
                    text = self.workspace.read(plan.prose_path).text
                except WorkspaceError:
                    text = ""
            moves = _chapter_moves_text(variant, plan)
            selection = request.selection or {}
            quote = str(selection.get("quote") or "")
            structure_text, ledger_text, clue_context, redirected, target = (
                self._drafting_injection(plan, variant)
            )
            return (
                prose_draft_system_prompt(),
                prose_draft_user_prompt(
                    structure_text=structure_text,
                    ledger_text=ledger_text,
                    moves=moves,
                    prose=text,
                    quote=quote,
                    note=note,
                    continue_mode=request.kind == "prose_continue",
                    style_section=style_section,
                    clue_context=clue_context,
                    redirected_to=redirected,
                    target_chapter=target,
                ),
            )
        if request.kind == "title":
            return (
                "你是悬疑小说命名助手。只输出候选标题，每行一个，不解释。",
                f"内容参考：{draft or note or '（空白）'}\n请给出 5 个简洁标题。",
            )
        if request.kind == "alignment_resolution":
            return (
                "你是悬疑小说编辑助手。只给处理建议，不替作者决定真相，也不直接改写。",
                f"全书结构：\n{structure or '（未建立）'}\n"
                f"本章已入账信息：{_chapter_moves_text(variant, plan) or '（无）'}\n"
                f"作者补充：{note or '（无）'}\n"
                "列出正文与已入账信息的具体差异，并给出 2—3 种处理方向；"
                "保留作者隐藏真相与角色谎言。",
            )
        if request.kind == "idea":
            return (
                "你是创作灵感助手。只输出改写后的灵感，不解释。",
                f"作者草稿：{draft}\n补充方向：{note or '（无）'}",
            )
        if request.kind == "module_content":
            return (
                "你是写作资料助手。只输出模块内容，不解释。",
                f"当前模块内容：{draft}\n补充方向：{note or '（无）'}",
            )
        return (
            "你是写作助手。只给具体可执行的下一步建议，每行一条，不自动执行。",
            f"当前章节：{plan.title if plan is not None else '（未选）'}\n"
            f"当前已入账信息：{_chapter_moves_text(variant, plan) or '（无）'}\n"
            f"作者指令草稿：{draft or note or '（空白）'}",
        )

    def _structure_text(self) -> str:
        if self.workspace is None:
            return ""
        from hastings.workspace.structure_io import read_structure_state

        state = read_structure_state(self.workspace)
        return state.text if state.exists else ""


def _chapter_moves_text(variant: StoryVariant | None, plan: Any) -> str:
    if variant is None or plan is None:
        return ""
    return "\n".join(
        f"- {move.statement}" for move in variant.information_moves_for_chapter(plan.id)
    )


def _target_error(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="ASSIST_TARGET_INVALID",
        where="creative-assist",
        why=why,
        fix="重新选择当前作品或章节后再请求模型候选",
    )


def _stale_error(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="ASSIST_TARGET_STALE",
        where="creative-assist",
        why=why,
        fix="重新读取当前内容后再生成",
    )
