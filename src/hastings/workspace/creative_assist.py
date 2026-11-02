"""目标化创作辅助：按目标生成候选，不覆盖作者草稿（1.0.9 P3）。

一次辅助请求只描述「目标、基础修订和可选补充」；服务端据此冻结材料、形成可审计的
提示，并复用已配置的模型连接。生成结果写入候选存储，不直接写正式作品。基础修订在
生成结束时再次核对，过期结果标记为 ``stale``。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import aclosing
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.creative_candidates import (
    CandidateBase,
    CreativeCandidate,
    CreativeCandidateStore,
    CreativeTarget,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_models import StoryVariant
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore
from tau_agent.messages import UserMessage, message_text
from tau_agent.provider_events import AssistantDoneEvent, AssistantErrorEvent, TextDeltaEvent

__all__ = ["AssistKind", "CreativeAssistInput", "CreativeAssistService"]

AssistKind = Literal[
    "book_intent",
    "chapter_plan",
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
        "book_intent",
        "chapter_plan",
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
        state_root,
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
        system, prompt = self._prompt(request, variant, plan)
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
                snapshot.provider.stream_response(
                    model=snapshot.model,
                    system=system,
                    messages=[UserMessage(content=prompt)],
                    tools=[],
                    signal=None,
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
            stale = not self._base_current(candidate)
            await asyncio.to_thread(
                self.candidates.update,
                candidate.candidate_id,
                text=text,
                status="stale" if stale else "ready",
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
                "book_intent",
                "chapter_plan",
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
                StoryStore(self.state_root, self.workspace, format_version=3)
                .read()
                .variant(MAIN_VARIANT_ID)
            )
        if variant is None and needs_story and request.kind != "book_intent":
            raise WorkspaceError(
                code="STORY_FORMAT_UNSUPPORTED",
                where="creative-assist",
                why="当前作品没有当前格式的写作构思",
                fix="先建立作品意图与章节构思，再请求模型辅助",
            )
        if variant is not None and variant.format_version < 3:
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
        if request.kind in {"prose_draft", "prose_continue"}:
            assert variant is not None and plan is not None
            problem = variant.chapter_start_problem(plan.id)
            if problem is not None:
                raise WorkspaceError(
                    code="CHAPTER_PLAN_REQUIRED",
                    where=f"章节/{plan.id}",
                    why=problem,
                    fix="先确认本章最小构思，再请求模型起草或续写",
                )
        expected = request.base
        if variant is not None:
            if expected.plan_revision and expected.plan_revision != variant.revision:
                raise _stale_error("作品构思已经变化")
            if (
                plan is not None
                and expected.chapter_revision
                and expected.chapter_revision != plan.revision
            ):
                raise _stale_error("章节构思已经变化")
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
        if candidate.base.chapter_revision and (
            plan is None or plan.revision != candidate.base.chapter_revision
        ):
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
        intent = variant.book_intent.text if variant is not None and variant.book_intent else ""
        if request.kind == "book_intent":
            return (
                "你是悬疑小说创作助手。只输出候选文字，不解释过程。",
                f"当前作品意图：{intent or '（空白）'}\n作者补充：{note or '（无）'}\n"
                "请给出 3 个可继续发展的悬疑起点；不确定凶手、真相或结局。每行一个。",
            )
        if request.kind == "chapter_plan":
            title = plan.title if plan is not None else (request.target.draft_id or "本章")
            return (
                "你是悬疑小说创作助手。只输出最小章节构思，不列属性表。",
                f"全书意图：{intent or '（未写）'}\n章节：{title}\n"
                f"已有构思：{plan.intent if plan is not None else '（空白）'}\n"
                f"作者补充：{note or '（无）'}\n"
                "用一句话写清本章让事件或关系发生什么变化，以及读者或角色理解到什么。",
            )
        if request.kind in {"prose_draft", "prose_continue"}:
            text = ""
            if plan is not None and plan.prose_path:
                try:
                    assert self.workspace is not None
                    text = self.workspace.read(plan.prose_path).text
                except WorkspaceError:
                    text = ""
            moves = ""
            if variant is not None and plan is not None:
                moves = "\n".join(
                    move.statement for move in variant.information_moves_for_chapter(plan.id)
                )
            selection = request.selection or {}
            quote = str(selection.get("quote") or "")
            return (
                "你是悬疑小说写作助手。只输出正文，不写解释或标题。保持人物知情边界。",
                f"全书意图：{intent or '（未写）'}\n"
                f"本章构思：{plan.intent if plan else '（未写）'}\n"
                f"本章信息变化：{moves or '（未写）'}\n"
                f"当前正文：\n{text[-4000:] if text else '（空白）'}\n"
                f"作者选段：{quote or '（无）'}\n作者补充：{note or '（无）'}\n"
                + (
                    "请续写选段之后的内容。"
                    if request.kind == "prose_continue"
                    else "请起草本章正文。"
                ),
            )
        if request.kind == "title":
            return (
                "你是悬疑小说命名助手。只输出候选标题，每行一个，不解释。",
                f"内容参考：{draft or intent or note or '（空白）'}\n请给出 5 个简洁标题。",
            )
        if request.kind == "alignment_resolution":
            return (
                "你是悬疑小说编辑助手。只给处理建议，不替作者决定真相，也不直接改写。",
                f"全书意图：{intent or '（未写）'}\n"
                f"本章构思：{plan.intent if plan else '（未写）'}\n"
                f"作者补充：{note or '（无）'}\n"
                "列出正文与构思的具体差异，并给出 2—3 种处理方向；保留作者隐藏真相与角色谎言。",
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
            f"当前构思：{plan.intent if plan is not None else '（未写）'}\n"
            f"作者指令草稿：{draft or note or '（空白）'}",
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
